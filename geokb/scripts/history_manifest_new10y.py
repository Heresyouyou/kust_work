# -*- coding: utf-8 -*-
"""新增期刊「近 10 年」待爬清单（**只列清单，不并入生产队列、不下载**）

为什么单独一支脚本
------------------
`history_manifest_20y.py` 每次运行都会整组重写五个桶并可能被 `--merge` 注回生产队列；
本次需求是「先看清单、暂不爬」，所以：

  · 期刊池独立（6 本 CrossRef + 1 本官网枚举），不碰 `CROSSREF_JOURNALS`；
  · 输出独立文件（`history_manifest_new10y_*`），**不提供 --merge**；
  · 去重仍走 20y 的全量索引（生产清单 / 台账 / 冻结备份 / 词条 / 人工标记），
    保证「知识库或清单里出现过的」一条都不重复收录。

期刊与依据（2026-10-28 定）
--------------------------
1. Journal of Asian Earth Sciences   三江 16.9% / 东南亚 8.0%，现有清单只覆盖 9%
2. Science China Earth Sciences      1 区 Top，三江 15.6%，现有 0 条
3. Journal of Earth Science          1 区 Top，矿床 8.8%，现有 0 条
4. Journal of Geochemical Exploration 矿床 31.3%（勘查地球化学），现有 0 条
5. 岩石学报                            矿床 21.3%，handler 早已可用，现有 0 条
6. Tectonics                        三江 12.0%，构造背景（矿床仅 0.8%，可选）
7. 沉积与特提斯地质                    特提斯域专刊，官网无 DOI 前缀 handler

输出
----
state/history_manifest_new10y_auto.jsonl         现在就能下
state/history_manifest_new10y_sd.jsonl           ScienceDirect 政策暂停
state/history_manifest_new10y_blocked.jsonl      源站冻结
state/history_manifest_new10y_quota.jsonl        OUP 日配额
state/history_manifest_new10y_unsupported.jsonl  无 handler
state/history_manifest_new10y_stats.json
state/清单新增10年.html                          人读清单（可筛）
logs/新增清单10年_YYYYMMDD.md

用法
----
    python scripts/history_manifest_new10y.py --dry-run
    python scripts/history_manifest_new10y.py
    python scripts/history_manifest_new10y.py --journal 岩石学报
    python scripts/history_manifest_new10y.py --site-only --refresh
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings()

BASE = Path('D:/ZSK/geokb')
SCRIPTS = BASE / 'scripts'
STATE = BASE / 'state'
LOGS = BASE / 'logs'
sys.path.insert(0, str(SCRIPTS))

import history_manifest as hm                      # noqa: E402
import history_manifest_10y as h10                 # noqa: E402
import history_manifest_20y as h20                 # noqa: E402
from journal_filter import classify_article        # noqa: E402

BUCKETS = ('auto', 'sd', 'blocked', 'quota', 'unsupported')
STAGE = {b: STATE / ('history_manifest_new10y_%s.jsonl' % b) for b in BUCKETS}
STATS = STATE / 'history_manifest_new10y_stats.json'
REPORT = LOGS / ('新增清单10年_%s.md' % datetime.now().strftime('%Y%m%d'))
HTML = STATE / '清单新增10年.html'

# (刊名, ISSN, 取数方式, 平台说明)
JOURNALS = [
    ('Journal of Asian Earth Sciences', '1367-9120', 'crossref', 'Elsevier / SD 暂停'),
    ('Science China Earth Sciences', '1674-7313', 'crossref', 'Springer'),
    ('Journal of Earth Science', '1674-487X', 'crossref', 'Springer'),
    ('Journal of Geochemical Exploration', '0375-6742', 'crossref', 'Elsevier / SD 暂停'),
    ('岩石学报', '1000-0569', 'crossref', 'ysxb.ac.cn 直链'),
    ('Tectonics', '0278-7407', 'crossref', 'AGU / Wiley'),
]

# 本次确认取不到的刊（写进报告，避免下次又白跑一遍）
NOT_INCLUDED = [
    ('沉积与特提斯地质',
     '官网 `/article/{年}/{期}` 命中腾讯验证码，英文页 `/en/article/{年}/{期}` 抓到第 2 年即被整站节流'
     '（返回 4.6 KB 空模板，3 次重试仍空）；CrossRef 无该刊（ISSN 1009-3850 查无记录），'
     'OpenAlex 当时限流。→ 需人工从官网/CNKI 导出题录后建档'),
]

S = requests.Session()
S.trust_env = False
S.headers.update({
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/120.0 Safari/537.36'),
    'Accept-Encoding': 'identity',
})


def log(msg):
    print(msg, flush=True)


# ── ① 官网枚举：《沉积与特提斯地质》─────────────────────────────────────
# 中文页 `/article/{年}/{期}` 会命中腾讯验证码（实测 2024/2 带 tac.min.js），
# 英文页 `/en/article/{年}/{期}` 是服务端渲染、无验证码 —— 只走英文页。
CJ_BASE = 'https://www.cjyttsdz.com.cn'
CJ_ANCHOR = re.compile(r"href=['\"](?:/en)?/article/(doi|id)/([^'\"]+)['\"][^>]*>(.*?)</a>", re.S | re.I)


def _cj_issue(year, issue):
    """返回 (items, ok)。ok=False 表示该期不存在/被节流，调用方决定是否继续探下一期。

    ⚠️ 该站连续快速请求会返回 4.6 KB 空模板（实测），必须放慢 + 多试几次：
       空模板和「真的没有这一期」在响应上无法区分，只能靠重试把节流挡掉。
    """
    url = '%s/en/article/%d/%d' % (CJ_BASE, year, issue)
    txt = ''
    for attempt in range(3):
        try:
            r = S.get(url, timeout=40, verify=False, proxies={'http': None, 'https': None})
            r.encoding = 'utf-8'
            txt = r.text
        except Exception:
            txt = ''
        if 'citation_title' in txt and len(txt) > 20000:
            break
        time.sleep(8 * (attempt + 1))
    if 'citation_title' not in txt or len(txt) < 20000:
        return [], False

    out, seen = [], set()
    for kind, tail, raw in CJ_ANCHOR.findall(txt):
        tail = tail.strip().strip("'\"")
        if not tail or tail.lower().endswith('.pdf'):
            continue
        title = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', raw)).strip()
        title = html.unescape(title)
        if len(title) < 6:
            continue
        if kind == 'doi':
            doi = re.sub(r'\?.*$', '', tail)
            link = '%s/article/doi/%s' % (CJ_BASE, doi)
        else:
            doi = ''
            link = '%s/en/article/id/%s' % (CJ_BASE, tail)
        key = (doi or link).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({'title': title, 'url': link, 'doi': doi, 'date': '', 'volume': '', 'issue': str(issue)})
    # 期次页不带日期，用年份兜底（清单只用于派发，日期取刊期年）
    for it in out:
        it['date'] = '%d-01' % year if not it['date'] else it['date']
    return out, True


def fetch_cjytts(years):
    out = []
    missing = []
    for y in years:
        got = 0
        for iss in range(1, 7):
            items, ok = _cj_issue(y, iss)
            if not ok:
                if iss <= 4:
                    missing.append('%d/%d' % (y, iss))
                continue
            out.extend(items)
            got += len(items)
            time.sleep(5.5)
        log('    [官网] %d 年 %3d 篇%s' % (y, got, '（有缺期）' if got == 0 else ''))
    if missing:
        log('    [官网] 缺期 %d 个：%s' % (len(missing), ','.join(missing[:12])))
    return out


# ── ② CrossRef 枚举（复用 20y 的分页与缓存）─────────────────────────────
def fetch_journal(issn, frm, until, chunk_years, refresh):
    out = []
    for cf, cu in h20.chunks(frm, until, chunk_years):
        key = 'v4_n10y_%s_%s_%s' % (issn.replace('-', ''), cf.strftime('%Y%m%d'), cu.strftime('%Y%m%d'))
        items = None if refresh else h20.cache_get(key)
        if items is None:
            try:
                items = h20.fetch_chunk(issn, cf.isoformat(), cu.isoformat())
                h20.cache_put(key, items)
            except Exception as e:
                log('    [err] %s %s~%s %s: %s' % (issn, cf, cu, type(e).__name__, str(e)[:70]))
                items = []
        out.extend(items)
    return out


# 区域命中：这些刊里「全球构造 / 其他大洲」的论文不少（尤其 Tectonics），
# 统一收录会稀释清单。不改变入选规则，只额外打标，页面上可一键「只看区域命中」。
ASIA_KWS = [
    '三江', '金沙江', '澜沧江', '怒江', '义敦', '格咱', '中甸', '藏东', '滇西', '滇东南',
    '青藏', '西藏', '云南', '四川', '青海', '甘肃', '新疆', '华南', '扬子', '华夏', '特提斯',
    'sanjiang', 'jinshajiang', 'lancang', 'nujiang', 'yidun', 'gezha', 'zhongdian',
    'tibet', 'himalaya', 'yunnan', 'sichuan', 'songpan', 'qiangtang', 'lhasa',
    'tethys', 'tethyan', 'tengchong', 'baoshan', 'ailaoshan', 'cathaysia',
    'south china', 'yangtze', 'indochina', 'southeast asia', 'myanmar', 'burma',
    'thailand', 'vietnam', 'laos', 'cambodia', 'malaysia', 'indonesia', 'sumatra',
    'borneo', 'sulawesi', 'philippines', 'hainan', 'hokkaido',
]


def region_hits(title):
    t = (title or '').lower()
    return [k for k in ASIA_KWS if k in t]


def do_merge(args):
    """把 auto 桶并入生产清单。

    ⚠️ 三条铁律（与 history_manifest_20y.do_merge 一致）：
    1. 必须持 state/manifest_op.lock 并停调度器 —— 调度器持内存副本、_settle
       全量写回，直接改盘会被覆盖；
    2. 除清单外还要对 history_done.jsonl 去重 —— 下载完成即从清单物理移除，
       只查清单会让已入库的论文被重下一遍；
    3. 只并 auto 桶。sd / blocked 是「条件不具备」，注回只会空转。
    """
    import history_queue as hq
    import park_frozen as pf

    if not STAGE['auto'].exists():
        log('auto 桶尚未生成：%s' % STAGE['auto'])
        return 2
    src = [json.loads(l) for l in STAGE['auto'].read_text(encoding='utf-8').splitlines() if l.strip()]
    if not src:
        log('auto 桶为空')
        return 0

    rows = hq.load_manifest(repair=True)
    have_i = {r['id'] for r in rows}
    have_t = {hm.norm_title(r['title']) for r in rows if r.get('title')}
    have_d = {hm.norm_doi(r['doi']) for r in rows if r.get('doi')}

    done_p = STATE / 'history_done.jsonl'
    done_t, done_d = set(), set()
    if done_p.exists():
        for ln in done_p.read_text(encoding='utf-8').splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                x = json.loads(ln)
            except Exception:
                continue
            if x.get('title'):
                done_t.add(hm.norm_title(x['title']))
            if x.get('doi'):
                done_d.add(hm.norm_doi(x['doi']))

    add, why = [], Counter()
    for x in src:
        nt = hm.norm_title(x.get('title') or '')
        nd = hm.norm_doi(x.get('doi') or '')
        if x['id'] in have_i:
            why['id 已在清单'] += 1
        elif nt and nt in have_t:
            why['标题已在清单'] += 1
        elif nd and nd in have_d:
            why['DOI 已在清单'] += 1
        elif nt and nt in done_t:
            why['标题已下载过'] += 1
        elif nd and nd in done_d:
            why['DOI 已下载过'] += 1
        else:
            have_i.add(x['id'])
            if nt:
                have_t.add(nt)
            if nd:
                have_d.add(nd)
            add.append(x)

    log('auto 桶 %d 条 ｜ 剔除 %s ｜ 待并入 %d ｜ 清单 %d → %d'
        % (len(src), dict(why) or '无', len(add), len(rows), len(rows) + len(add)))
    if not add:
        return 0
    if args.dry_run:
        log('[dry-run] 未写盘')
        return 0

    try:
        pf.take_lock()
    except RuntimeError as e:
        log('⚠️ %s' % e)
        return 4
    try:
        n = pf.stop_queue()
        if n:
            log('已停调度器 %d 个进程，守护 10s 内用新清单拉起' % n)
        # 停调度器后重读，避免拿停之前的旧副本
        rows = hq.load_manifest(repair=True)
        have = {r['id'] for r in rows}
        add = [x for x in add if x['id'] not in have]
        hq.save_manifest(rows + add, backup=True)
        left = [x for x in src if x['id'] not in {a['id'] for a in add}
                and x['id'] not in have]
        # auto 桶整份清空：并入的已进清单，被剔除的已确认在清单或台账里
        STAGE['auto'].write_text('', encoding='utf-8')
        log('已写清单 %d 条（+%d）｜ auto 桶已清空 ｜ 按刊 %s'
            % (len(rows) + len(add), len(add),
               dict(Counter(x['journal'] for x in add))))
    finally:
        pf.drop_lock()
    return 0


def main():
    ap = argparse.ArgumentParser(description='新增期刊近 10 年待爬清单（只列不爬，不并入队列）')
    ap.add_argument('--years', type=int, default=10)
    ap.add_argument('--chunk-years', type=int, default=5)
    ap.add_argument('--min-score', type=int, default=6)
    ap.add_argument('--ext-min-score', type=int, default=3)
    ap.add_argument('--geo-min-score', type=int, default=3)
    ap.add_argument('--journal', default='')
    ap.add_argument('--only', default='', help='crossref | site')
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--dry-run', action='store_true', help='只统计，不写盘')
    ap.add_argument('--warm-only', action='store_true',
                    help='只把题录抓进缓存，不做过滤/写盘（给分批抓取预热用）')
    ap.add_argument('--merge', action='store_true',
                    help='把 auto 桶并入生产清单（持 manifest_op 锁 + 停调度器 + 对台账去重）')
    args = ap.parse_args()

    if args.merge:
        return do_merge(args)

    import sd_policy
    import source_freeze as sf

    until = datetime.now().date()
    frm = until - timedelta(days=365 * args.years)
    log('窗口 %s ~ %s（%d 年）｜ core>=%d / ext>=%d / geo>=%d'
        % (frm, until, args.years, args.min_score, args.ext_min_score, args.geo_min_score))

    dl = h20._load_downloader()
    minfo = {'sf': sf, 'dl': dl}
    frozen_tab = sf.load()

    jobs = [j for j in JOURNALS
            if (not args.journal or args.journal in j[0])
            and (not args.only or j[2] == args.only)]
    log('目标期刊 %d 种：%s' % (len(jobs), '、'.join(j[0] for j in jobs)))

    if args.warm_only:
        for name, issn, mode, plat in jobs:
            if mode == 'site':
                key = 'v4_n10y_site_cjytts_%s_%s' % (frm.strftime('%Y%m%d'), until.strftime('%Y%m%d'))
                if h20.cache_get(key) is not None and not args.refresh:
                    log('  [缓存] %s' % name)
                    continue
                raw = fetch_cjytts(list(range(frm.year, until.year + 1)))
                h20.cache_put(key, raw)
                log('  [预热] %-40s %d 篇' % (name, len(raw)))
            else:
                raw = fetch_journal(issn, frm, until, args.chunk_years, args.refresh)
                log('  [预热] %-40s %d 篇' % (name, len(raw)))
        return 0

    ded, dstat = h20.build_dedup()
    log('去重索引：%s' % dict(dstat))

    buckets = {k: [] for k in BUCKETS}
    stats = Counter()
    nonarticle, fails = [], []
    by_journal_raw = {}

    for name, issn, mode, plat in jobs:
        t0 = time.time()
        if mode == 'site':
            key = 'v4_n10y_site_cjytts_%s_%s' % (frm.strftime('%Y%m%d'), until.strftime('%Y%m%d'))
            raw = None if args.refresh else h20.cache_get(key)
            if raw is None:
                try:
                    raw = fetch_cjytts(list(range(frm.year, until.year + 1)))
                    h20.cache_put(key, raw)
                except Exception as e:
                    fails.append({'journal': name, 'err': '%s: %s' % (type(e).__name__, str(e)[:80])})
                    raw = []
        else:
            raw = fetch_journal(issn, frm, until, args.chunk_years, args.refresh)

        by_journal_raw[name] = len(raw)
        stats['raw'] += len(raw)
        kept = 0

        for r in raw:
            r['journal'] = name
            r['source'] = mode
            d = (r.get('date') or '')[:7]
            if d and (d < frm.isoformat()[:7] or d > until.isoformat()[:7]):
                stats['out_window'] += 1
                continue
            t = (r.get('title') or '').strip()
            if len(t) < 8 or h10.JUNK_TITLE.search(t):
                stats['junk_title'] += 1
                continue
            kind, why = classify_article(t)
            if kind != 'article':
                nonarticle.append(dict(r, kind=kind, why=why))
                stats['nonarticle'] += 1
                continue
            sc, kws = h10.score_loose(t)
            if sc <= 0:
                stats['irrelevant'] += 1
                continue
            if sc >= args.min_score:
                r['tier'] = 'core'
            elif args.ext_min_score and sc >= args.ext_min_score and (set(kws) & h10.ORE_KWS):
                r['tier'] = 'ext'
            elif (args.geo_min_score and sc >= args.geo_min_score
                  and (set(kws) & h20.GEO_KWS)):
                r['tier'] = 'geo'
            else:
                stats['below_tier'] += 1
                continue
            r['score'] = sc
            r['matched'] = kws[:8]
            r['region'] = region_hits(t)
            kept += 1

        added = 0
        for r in [x for x in raw if x.get('tier')]:
            nk = hm.norm_title(r['title'])
            if nk in ded.titles or (len(nk) >= 48 and nk[:48] in ded.prefix):
                stats['dup_kb'] += 1
                continue
            hit = ded.hit(r)
            if hit == 'meta_titles':
                stats['dup_meta'] += 1
                continue
            if hit:
                stats['dup_kb'] += 1
                continue
            nd = hm.norm_doi(r.get('doi'))
            if nd and nd in ded.dois:
                stats['dup_doi'] += 1
                continue
            nu = (r.get('url') or '').strip().lower()
            if nu and nu in ded.urls:
                stats['dup_url'] += 1
                continue
            if nk:
                ded.titles.add(nk)
                if len(nk) >= 48:
                    ded.prefix.add(nk[:48])
            if nd:
                ded.dois.add(nd)
            if nu:
                ded.urls.add(nu)

            reach, reach_p = hm.reach_of(r['url'])
            is_sd = sd_policy.is_sd(r.get('doi') or '', r['url'], name)
            host = sf.host_of(r['url'], r.get('doi') or '')
            rec = {
                'id': 'hn-' + hashlib.sha1((nd or nk).encode('utf-8')).hexdigest()[:12],
                'journal': name,
                'title': r['title'],
                'url': r['url'],
                'doi': r.get('doi') or '',
                'date': r.get('date') or '',
                'year': (r.get('date') or '')[:4],
                'volume': r.get('volume') or '',
                'issue': r.get('issue') or '',
                'score': r['score'],
                'matched': r['matched'],
                'tier': r['tier'],
                'region': r.get('region') or [],
                'region_hit': bool(r.get('region')),
                'source': mode,
                'platform': plat,
                'reach': reach,
                'reach_p': reach_p,
                'sd': bool(is_sd),
                'status': 'pending',
                'attempts': 0,
                'last_error': '',
                'batch': 'new10y',
                'added_at': datetime.now().isoformat(timespec='seconds'),
                'host': host,
            }
            b, why = h20.bucket_of(rec, host, frozen_tab, minfo)
            # 局部把「无 handler」的原因写清楚，避免页面上只显示 unknown
            if b == 'unsupported' and rec['doi'].startswith('10.1029'):
                why = 'AGU 合集未确认（Wiley 平台单独计价）'
            elif b == 'unsupported' and (rec['doi'].startswith('10.19826')
                                         or 'cjyttsdz' in rec['url']):
                why = '无 handler（沉积与特提斯地质官网 10.19826）'
            rec['bucket'] = b
            rec['bucket_why'] = why
            buckets[b].append(rec)
            added += 1

        stats['candidates'] += kept
        stats['new'] += added
        log('  %-42s %5d 篇 → 候选 %4d ｜ 新增 %4d ｜ %.0fs'
            % (name[:42], len(raw), kept, added, time.time() - t0))

    for b in buckets:
        buckets[b].sort(key=lambda x: (x['tier'] != 'core', not x['region_hit'], -x['reach_p'],
                                       -x['score'], h20._neg_date(x['date']), x['journal']))

    total = sum(len(v) for v in buckets.values())
    log('\n分桶：%s ｜ 合计 %d' % ({k: len(v) for k, v in buckets.items()}, total))
    log('漏斗：原始 %d ｜ 窗外 %d ｜ 垃圾标题 %d ｜ 非正刊 %d ｜ 无关 %d ｜ 未达层 %d ｜ 重复 %d → 新增 %d'
        % (stats['raw'], stats['out_window'], stats['junk_title'], stats['nonarticle'],
           stats['irrelevant'], stats['below_tier'],
           stats['dup_kb'] + stats['dup_meta'] + stats['dup_doi'] + stats['dup_url'], stats['new']))

    by_journal = Counter(x['journal'] for v in buckets.values() for x in v)
    by_year = Counter(x['year'] or '?' for v in buckets.values() for x in v)
    by_tier = Counter(x['tier'] for v in buckets.values() for x in v)
    by_host = Counter(x['host'] or '?' for v in buckets.values() for x in v)

    payload = {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'window': [frm.isoformat(), until.isoformat()],
        'years': args.years,
        'merged': False,
        'journals': by_journal_raw,
        'funnel': {k: stats[k] for k in ('raw', 'out_window', 'junk_title', 'nonarticle',
                                         'irrelevant', 'below_tier', 'candidates', 'new',
                                         'dup_kb', 'dup_meta', 'dup_doi', 'dup_url')},
        'buckets': {k: len(v) for k, v in buckets.items()},
        'by_journal': dict(by_journal.most_common()),
        'by_year': dict(sorted(by_year.items())),
        'by_tier': dict(by_tier),
        'by_region': {j: sum(1 for v in buckets.values() for x in v
                             if x['journal'] == j and x['region_hit'])
                      for j in by_journal},
        'by_host': dict(by_host.most_common()),
        'fails': fails,
        'nonarticle': len(nonarticle),
    }

    if args.dry_run:
        log('\n[dry-run] 不写盘。统计预览：%s' % json.dumps(payload['buckets'], ensure_ascii=False))
        return 0

    for b, rows in buckets.items():
        tmp = STAGE[b].with_suffix('.tmp')
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            for rec in rows:
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        import os
        os.replace(tmp, STAGE[b])
    SK = STATE / 'history_manifest_new10y_skipped.jsonl'
    with open(SK, 'w', encoding='utf-8', newline='\n') as f:
        for rec in nonarticle:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    STATS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    write_report(payload, buckets)
    write_html(buckets, payload)
    write_csv(buckets)
    log('已写清单 %d 条（未并入生产队列）' % total)
    return 0


def build_sec(out):
    """章节号动态生成：有没有「失败」那一节，编号都不跳号。"""
    n = [0]
    cn = '一二三四五六七八九十'

    def sec(title):
        i = n[0]
        n[0] += 1
        out.append('## %s、%s' % (cn[i] if i < len(cn) else str(i + 1), title))
        out.append('')
    return sec


def write_report(payload, buckets):
    L = []
    w = L.append
    w('# 新增期刊近 10 年待爬清单')
    w('')
    w('- 生成：%s' % payload['generated_at'])
    w('- 窗口：%s ~ %s（%d 年）' % (payload['window'][0], payload['window'][1], payload['years']))
    w('- **未并入生产队列**，也没有触发任何下载')
    w('')
    sec = build_sec(L)
    sec('分刊结果')
    w('')
    w('| 期刊 | 题录 | 新增待爬 | 区域命中 | 平台 |')
    w('|---|---:|---:|---:|---|')
    for name, n in sorted(payload['by_journal'].items(), key=lambda x: -x[1]):
        plat = next((j[3] for j in JOURNALS if j[0] == name), '')
        w('| %s | %d | **%d** | %d | %s |'
          % (name, payload['journals'].get(name, 0), n, payload.get('by_region', {}).get(name, 0), plat))
    w('')
    sec('分桶（决定能不能自动下）')
    w('')
    w('| 桶 | 条数 | 含义 |')
    w('|---|---:|---|')
    why = {'auto': '现在就能下', 'sd': 'ScienceDirect 政策暂停',
           'blocked': '源站冻结中', 'quota': 'OUP 日配额 25 篇', 'unsupported': '无 handler'}
    for b in BUCKETS:
        w('| %s | %d | %s |' % (b, payload['buckets'][b], why[b]))
    w('')
    sec('漏斗')
    w('')
    f = payload['funnel']
    w('原始 %d ｜ 窗外 %d ｜ 垃圾标题 %d ｜ 非正刊 %d ｜ 无关 %d ｜ 未达层 %d ｜ 重复 %d → **新增 %d**'
      % (f['raw'], f['out_window'], f['junk_title'], f['nonarticle'], f['irrelevant'],
         f['below_tier'], f['dup_kb'] + f['dup_meta'] + f['dup_doi'] + f['dup_url'], f['new']))
    w('')
    sec('分层与年份')
    w('')
    w('层级：%s' % payload['by_tier'])
    w('')
    w('| 年 | 条数 |')
    w('|---|---:|')
    for y, n in payload['by_year'].items():
        w('| %s | %d |' % (y, n))
    w('')
    if payload.get('fails'):
        sec('失败')
        w('')
        for x in payload['fails']:
            w('- %s：%s' % (x['journal'], x['err']))
        w('')
    if NOT_INCLUDED:
        sec('本次未纳入')
        w('')
        w('| 期刊 | 原因 |')
        w('|---|---|')
        for nm, why in NOT_INCLUDED:
            w('| %s | %s |' % (nm, why))
        w('')
    sec('下一步')
    w('')
    w('- 本清单**没有并入** `state/history_manifest.jsonl`，因此队列不会去爬；')
    w('- 要开爬：把对应桶的行追加进生产清单（`auto` 桶即「现在就能下」那部分），或按桶批量注回；')
    w('- SD 桶（%d 条）要等 `sd_policy` 解除暂停；unsupported 桶要先补 handler。'
      % payload['buckets'].get('sd', 0))
    w('')
    REPORT.write_text('\n'.join(L) + '\n', encoding='utf-8')


HTML_HEAD = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>新增期刊近 10 年待爬清单</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1a1d21;--sub:#6b7280;--line:#e5e7eb;
--core:#b4232c;--ext:#c2681a;--geo:#2e6f9e;--ok:#17663a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{padding:22px 28px 8px}h1{margin:0 0 6px;font-size:20px;font-weight:650}
.sub{color:var(--sub);font-size:12.5px}
.wrap{padding:14px 28px 40px}
.cards{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:10px 14px;min-width:120px}.card b{display:block;font-size:19px}
.card span{color:var(--sub);font-size:12px}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
select,#q{background:#fff;border:1px solid var(--line);border-radius:8px;
padding:7px 10px;font-size:13px;color:var(--ink)}
#q{min-width:300px}
.chk input{width:14px;height:14px;min-width:0;margin:0}
.chk{font-size:12.5px;color:#374151;display:flex;align-items:center;gap:6px;
white-space:nowrap;background:#fff;border:1px solid var(--line);border-radius:8px;padding:6px 10px}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);
border-radius:10px;overflow:hidden;table-layout:fixed}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);
font-size:13px;vertical-align:top}
th{background:#fafbfc;font-weight:600;color:#374151;position:sticky;top:0}
tr:last-child td{border-bottom:0}
td.t{overflow:hidden;text-overflow:ellipsis}
a{color:#1d4ed8;text-decoration:none}a:hover{text-decoration:underline}
.pill{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11.5px;
border:1px solid var(--line);color:var(--sub)}
.p-core{color:var(--core);border-color:#f0c8cb;background:#fdf3f4}
.p-ext{color:var(--ext);border-color:#f0dcc2;background:#fdf8f0}
.p-geo{color:var(--geo);border-color:#cadff0;background:#f2f8fd}
.p-auto{color:var(--ok);border-color:#c3e2d1;background:#f2faf5}
#more{display:block;margin:14px auto;padding:8px 18px;border:1px solid var(--line);
background:#fff;border-radius:8px;cursor:pointer;font-size:13px}
</style></head><body>
<header><h1>新增期刊 · 近 10 年待爬清单</h1>
<div class="sub" id="sub"></div></header>
<div class="wrap">
<div class="cards" id="cards"></div>
<div class="bar">
<input id="q" placeholder="搜索标题 / 期刊 / DOI">
<select id="j"><option value="">全部期刊</option></select>
<select id="b"><option value="">全部桶</option></select>
<select id="t"><option value="">全部层级</option></select>
<label class="chk"><input type="checkbox" id="r"> 只看区域命中（三江/特提斯/华南/东南亚）</label>
</div>
<table><thead><tr>
<th style="width:44%">标题</th><th style="width:18%">期刊</th>
<th style="width:8%">年</th><th style="width:7%">层级</th>
<th style="width:9%">桶</th><th style="width:6%">分</th></tr></thead>
<tbody id="tb"></tbody></table>
<button id="more">显示更多</button>
</div>
<script>
const DATA = __DATA__;
const BN = {auto:'可自动下',sd:'SD 暂停',blocked:'冻结',quota:'配额',unsupported:'无 handler'};
const TN = {core:'core 核心',ext:'ext 扩展',geo:'geo 构造/定年'};
</script>
<script>
(function(){
  const rows = DATA.rows, g = id => document.getElementById(id);
  const $q=g('q'), $j=g('j'), $b=g('b'), $t=g('t'), $tb=g('tb'), $more=g('more'), $r=g('r');
  g('sub').textContent = '窗口 ' + DATA.window[0] + ' ~ ' + DATA.window[1]
    + ' ｜ 共 ' + rows.length + ' 条 ｜ 未并入生产队列、未触发下载 ｜ 生成 ' + DATA.generated;
  const cnt = {};
  rows.forEach(r => cnt[r.b] = (cnt[r.b]||0) + 1);
  g('cards').innerHTML = DATA.buckets.map(b =>
    '<div class="card"><b>' + (cnt[b[0]]||0) + '</b><span>' + b[1] + '</span></div>').join('');
  const uniq = (k) => [...new Set(rows.map(r => r[k]))];
  uniq('journal').sort().forEach(v => $j.insertAdjacentHTML('beforeend',
    '<option>' + v.replace(/&/g,'&amp;') + '</option>'));
  DATA.buckets.forEach(b => $b.insertAdjacentHTML('beforeend',
    '<option value="' + b[0] + '">' + b[1] + '</option>'));
  Object.keys(TN).forEach(k => $t.insertAdjacentHTML('beforeend',
    '<option value="' + k + '">' + TN[k] + '</option>'));

  let shown = 0, list = [];
  function render(reset){
    if (reset) { shown = 0; $tb.innerHTML = ''; }
    const slice = list.slice(shown, shown + 300);
    $tb.insertAdjacentHTML('beforeend', slice.map(r =>
      '<tr><td class="t"><a href="' + r.u + '" target="_blank">'
      + r.t.replace(/&/g,'&amp;').replace(/</g,'&lt;') + '</a></td>'
      + '<td>' + r.j + '</td><td>' + (r.y||'') + '</td>'
      + '<td><span class="pill p-' + r.k + '">' + r.k + '</span></td>'
      + '<td><span class="pill p-' + (r.b==='auto'?'auto':'') + '">' + (BN[r.b]||r.b) + '</span></td>'
      + '<td>' + r.s + '</td></tr>').join(''));
    shown += slice.length;
    $more.style.display = shown < list.length ? 'block' : 'none';
    $more.textContent = '显示更多（' + shown + ' / ' + list.length + '）';
  }
  function apply(){
    const q = $q.value.trim().toLowerCase(), j = $j.value, b = $b.value, t = $t.value;
    const onlyRegion = $r.checked;
    list = rows.filter(r => (!j || r.j === j) && (!b || r.b === b) && (!t || r.k === t)
      && (!onlyRegion || r.g)
      && (!q || (r.t + ' ' + r.a + ' ' + r.j).toLowerCase().includes(q)));
    render(true);
  }
  [$q,$j,$b,$t].forEach(el => el.addEventListener('input', apply));
  $r.addEventListener('change', apply);
  $more.addEventListener('click', () => render(false));
  apply();
})();
</script></body></html>
"""


def write_csv(buckets):
    """给 Excel 直接打开：UTF-8 BOM + 逗号分隔。"""
    import csv
    p = STATE / '清单新增10年.csv'
    with open(p, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['期刊', '标题', '年', '层级', '得分', '桶', '区域命中', 'DOI', '链接', '平台'])
        for b in BUCKETS:
            for r in buckets[b]:
                w.writerow([r['journal'], r['title'], r['year'], r['tier'], r['score'],
                            b, '是' if r.get('region_hit') else '', r['doi'], r['url'],
                            r.get('platform', '')])


def write_html(buckets, payload):
    rows = []
    for b in BUCKETS:
        for r in buckets[b]:
            rows.append({'t': r['title'], 'u': r['url'], 'j': r['journal'],
                         'y': r['year'], 'k': r['tier'], 'b': b, 's': r['score'],
                         'g': 1 if r.get('region_hit') else 0,
                         'a': (r.get('doi') or '')})
    data = {'rows': rows,
            'buckets': [[b, {'auto': '可自动下', 'sd': 'SD 暂停', 'blocked': '冻结',
                             'quota': '配额', 'unsupported': '无 handler'}[b]] for b in BUCKETS],
            'window': payload['window'], 'generated': payload['generated_at'][:19]}
    HTML.write_text(HTML_HEAD.replace('__DATA__',
                                      json.dumps(data, ensure_ascii=False)),
                    encoding='utf-8')


if __name__ == '__main__':
    sys.exit(main())
