# -*- coding: utf-8 -*-
"""近 10 年目标期刊「待抓取」增量清单（只列清单，不爬取）

与 `history_manifest.py` 的分工
------------------------------
· `history_manifest.py`  : 近 5 年、一次性**全量重建**，直接覆盖写
  `state/history_manifest.jsonl`（正在被 history_queue_loop 逐篇消费）。
· 本脚本                : 近 10 年、**增量**，只往**暂存区**写，不碰正在消费的
  待爬队列。核对无误后再 `--merge` 注入，避免清单刚生成就被调度器拿去爬。

三条口径（2026-10-26 用户要求）
------------------------------
1. **只要与团队方向有联系就算要抓**：相关度分两层
   · core = score >= --min-score（默认 6，沿用旧口径）
   · ext  = --ext-min-score <= score < core，且命中的词里至少有一个是
     「研究方向词」（区域/成因/成矿/矿床类型/矿种/构造），不是只有方法词
2. **剔除非正刊稿**：评论、回复、更正/勘误、撤稿声明、专辑导读、书评、
   讣告、总目次、征稿简则、会议消息一律不入清单（判据唯一来源 =
   `D:/PostNews/journal_filter.classify_article`），剔除项单独出复核表。
3. **先筛知识库与现有清单**：台账（有 PDF 的才算已下载）、`content/papers`
   词条、`history_manifest.jsonl`、`history_done.jsonl` 四路去重；
   台账里只有题录没有 PDF 的，单列「已在台账待补 PDF」，不重复入列。

输出
----
· state/history_manifest_add10y.jsonl    增量条目（机器可读，可直接 --merge）
· state/history_manifest_add10y.html     人读清单（可搜索/按期刊年份层级筛选）
· state/history_manifest_add10y_stats.json
· state/history_manifest_skipped10y.jsonl 被剔除非正刊稿明细（复核用）
· logs/清单10年_YYYYMMDD.md              汇总报告

用法
----
    python scripts/history_manifest_10y.py                 # 生成暂存区（默认 10 年）
    python scripts/history_manifest_10y.py --dry-run       # 只统计不落盘
    python scripts/history_manifest_10y.py --refresh       # 忽略原始题录缓存
    python scripts/history_manifest_10y.py --merge         # 暂存区 → 待爬队列（会开爬）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
SCRIPTS = BASE / 'scripts'
STATE = BASE / 'state'
LOGS = BASE / 'logs'
MANIFEST = STATE / 'history_manifest.jsonl'
DONE = STATE / 'history_done.jsonl'
STAGE = STATE / 'history_manifest_add10y.jsonl'
STAGE_HTML = STATE / 'history_manifest_add10y.html'
STAGE_STATS = STATE / 'history_manifest_add10y_stats.json'
SKIPPED = STATE / 'history_manifest_skipped10y.jsonl'
RAW_CACHE = STATE / 'manifest_raw'

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, 'D:/PostNews')

import history_manifest as hm                     # noqa: E402  # 复用枚举/可达性/去重内核
import journal_filter as jf                      # noqa: E402
from journal_filter import (                      # noqa: E402
    calc_relevance_score, classify_article, is_valid_paper,
    REGION_KEYWORDS, GENESIS_KEYWORDS, METALLOGENY_KEYWORDS,
    DEPOSIT_TYPE_KEYWORDS, COMMODITY_KEYWORDS, TECTONIC_KEYWORDS,
    METHOD_KEYWORDS,
)


def score_loose(title):
    """打分，但不走 is_valid_paper 黑名单。

    为什么：`is_valid_paper` 的 `^Review of` 等条目会把**综述**（正刊稿，
    团队最需要的类型之一）判成 -1 直接丢掉。类型已由 `classify_article`
    判过，这里只按内容打分。
    """
    orig = jf.is_valid_paper
    jf.is_valid_paper = lambda t: bool(t) and len(t) >= 8
    try:
        return calc_relevance_score(title)
    finally:
        jf.is_valid_paper = orig

# 「研究方向词」：命中任一才算「与团队方向有联系」（方法词不算，单独看）
TOPIC_KWS = {k for k, _ in (REGION_KEYWORDS + GENESIS_KEYWORDS + METALLOGENY_KEYWORDS +
                            DEPOSIT_TYPE_KEYWORDS + COMMODITY_KEYWORDS + TECTONIC_KEYWORDS)}
# 扩展层要更严一档：只提地区/构造（云南、青藏高原、造山带…）会把地震台网、
# 滑坡易发性、水文这类完全无关的论文捞进来（实测），所以必须沾「成矿类」词。
ORE_KWS = {k for k, _ in (GENESIS_KEYWORDS + METALLOGENY_KEYWORDS +
                          DEPOSIT_TYPE_KEYWORDS + COMMODITY_KEYWORDS)}
METHOD_KWS = {k for k, _ in METHOD_KEYWORDS}

# 明显不是标题的垃圾（CrossRef 不会给，官网枚举偶尔混进下载链接）
JUNK_TITLE = re.compile(
    r'^(?:PDF|doi:|DOI:|https?://|10\.\d{4,})|\(PDF\(|KKB\)|MKB\)|^摘要[：:]', re.I)


def log(msg):
    print(msg, flush=True)


def cache_get(key):
    p = RAW_CACHE / (key + '.json')
    if p.exists():
        try:
            return json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            return None
    return None


def cache_put(key, data):
    RAW_CACHE.mkdir(parents=True, exist_ok=True)
    p = RAW_CACHE / (key + '.json')
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, p)


# ── ① 枚举（带缓存，窗口一变就换 key）──────────────────────────────────
def fetch_all(frm, until, years, only='', journal='', refresh=False, skip=()):
    jobs = []
    for name, issn in hm.CROSSREF_JOURNALS:
        if journal and journal not in name:
            continue
        if skip and any(s in name for s in skip):
            continue
        if only not in ('', 'crossref'):
            continue
        jobs.append(('crossref', name, name,
                     lambda n=name, i=issn: hm.fetch_crossref(
                         i, frm.isoformat(), until.isoformat(), cap=9000)))
    site_fns = [('矿床地质', hm.fetch_kcdz), ('地学前缘', hm.fetch_esf),
                ('地质学报', hm.fetch_dzxb), ('大地构造与成矿学', hm.fetch_dgyk)]
    for name, fn in site_fns:
        if journal and journal not in name:
            continue
        if skip and any(s in name for s in skip):
            continue
        if only not in ('', 'site'):
            continue
        jobs.append(('site', name, name, lambda f=fn: f(years)))

    raw, fails = [], []
    for s in skip:
        fails.append({'journal': s, 'error': 'skipped: 本次跳过（源站限速/超时）'})
    for kind, name, src, fn in jobs:
        key = 'v2_%s_%s_%s' % (re.sub(r'\W+', '', name)[:24], frm.strftime('%Y%m%d'),
                               until.strftime('%Y%m%d'))
        items = None if refresh else cache_get(key)
        if items is not None:
            log('  [缓存] %-38s %4d 篇' % (name, len(items)))
        else:
            t0 = time.time()
            try:
                items = fn()
            except Exception as e:
                log('  [失败] %-10s %s: %s' % (name, type(e).__name__, str(e)[:70]))
                fails.append({'journal': name, 'error': '%s: %s' % (type(e).__name__, str(e)[:120])})
                items = []
            cache_put(key, items)
            log('  [%s] %-38s %4d 篇  %.0fs'
                % ('CrossRef' if kind == 'crossref' else '官网', name, len(items), time.time() - t0))
        for it in items:
            it['journal'] = name
            it['source'] = kind
        raw.extend(items)
    return raw, fails


# ── ② 去重索引（知识库四路）─────────────────────────────────────────────
def build_dedup_full():
    """返回 (有PDF的键集合, 仅题录键集合, 统计)。

    比 hm.build_dedup 多一类：台账里**只有题录没 PDF** 的也收一份，
    单独统计（这类已由 /geokb/inbox 待补清单管，不重复入列）。
    """
    import yaml
    pdf_titles, pdf_dois, pdf_urls = set(), set(), set()
    meta_titles = set()
    n_pdf = n_meta = n_papers = 0

    if hm.LEDGER.exists():
        for line in hm.LEDGER.read_text(encoding='utf-8', errors='ignore').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except Exception:
                continue
            if hm._has_pdf(m):
                n_pdf += 1
                if m.get('title'):
                    pdf_titles.add(hm.norm_title(m['title']))
                if m.get('doi'):
                    pdf_dois.add(hm.norm_doi(m['doi']))
                if m.get('url'):
                    pdf_urls.add(m['url'].strip().lower())
            else:
                n_meta += 1
                if m.get('title'):
                    meta_titles.add(hm.norm_title(m['title']))

    papers_titles = set()
    if hm.PAPERS_DIR.exists():
        for p in hm.PAPERS_DIR.glob('*.md'):
            try:
                raw = p.read_text(encoding='utf-8', errors='ignore')[:3000]
                mm = re.match(r'^---\n(.*?)\n---', raw, re.S)
                if not mm:
                    continue
                fm = yaml.safe_load(mm.group(1)) or {}
                t = fm.get('title') or ''
                if t:
                    papers_titles.add(hm.norm_title(t))
                    n_papers += 1
            except Exception:
                continue

    keys = {'titles': pdf_titles | papers_titles, 'dois': pdf_dois, 'urls': pdf_urls,
            'meta_titles': meta_titles}
    stats = {'ledger_pdf': n_pdf, 'ledger_meta_only': n_meta, 'papers_entries': n_papers,
             'papers_titles': len(papers_titles)}
    return keys, stats


def load_manifest_keys():
    """现有待爬清单 + 已完成清单的键（标题/DOI/URL）。"""
    titles, dois, urls = set(), set(), set()
    n = n_done = 0
    for path, is_done in ((MANIFEST, False), (DONE, True)):
        if not path.exists():
            continue
        for line in path.read_text(encoding='utf-8', errors='ignore').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except Exception:
                continue
            if is_done:
                n_done += 1
            else:
                n += 1
            if m.get('title'):
                titles.add(hm.norm_title(m['title']))
            if m.get('doi'):
                dois.add(hm.norm_doi(m['doi']))
            if m.get('url'):
                urls.add((m['url'] or '').strip().lower())
    return {'titles': titles, 'dois': dois, 'urls': urls}, {'manifest': n, 'done': n_done}


def hit_keys(rec, keys):
    """返回命中的键类别（titles/dois/urls/meta_titles 之一）或空串。"""
    nk = hm.norm_title(rec['title'])
    meta = keys.get('meta_titles') or set()
    if nk:
        if nk in keys['titles']:
            return 'titles'
        for t in keys['titles']:
            n = min(len(nk), len(t))
            if n >= 48 and nk[:n] == t[:n]:
                return 'titles'
        if nk in meta:
            return 'meta_titles'
        for t in meta:
            n = min(len(nk), len(t))
            if n >= 48 and nk[:n] == t[:n]:
                return 'meta_titles'
    d = hm.norm_doi(rec.get('doi'))
    if d and d in keys['dois']:
        return 'dois'
    u = (rec.get('url') or '').strip().lower()
    if u and u in keys['urls']:
        return 'urls'
    return ''


def hit_manifest(rec, keys):
    return bool(hit_keys(rec, keys))


# ── ③ 主流程 ───────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description='近 10 年目标期刊增量待爬清单（只列不爬）')
    ap.add_argument('--years', type=int, default=10)
    ap.add_argument('--min-score', type=int, default=6, help='core 层阈值')
    ap.add_argument('--ext-min-score', type=int, default=3, help='ext 层阈值（0 = 关闭扩展层）')
    ap.add_argument('--only', default='')
    ap.add_argument('--journal', default='')
    ap.add_argument('--skip', default='', help='跳过指定期刊（逗号分隔，源站限速时用）')
    ap.add_argument('--refresh', action='store_true', help='忽略原始题录缓存')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--merge', action='store_true', help='把暂存区并入待爬队列（会立即开爬）')
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args()

    if args.merge:
        return do_merge(args)

    until = datetime.now().date()
    frm = until - timedelta(days=365 * args.years)
    years = list(range(frm.year, until.year + 1))
    log('窗口 %s ~ %s ｜ 刊年 %s ｜ core>=%d / ext>=%d'
        % (frm, until, '%d-%d' % (years[0], years[-1]), args.min_score, args.ext_min_score))

    raw, fails = fetch_all(frm, until, years, args.only, args.journal, args.refresh,
                           [s.strip() for s in args.skip.split(',') if s.strip()])
    log('原始题录 %d 条（%d 个源失败）' % (len(raw), len(fails)))

    # 时间窗二次过滤（官网只到月）
    kept = [r for r in raw
            if not (r.get('date') and r['date'][:7] < frm.isoformat()[:7])]
    log('时间窗外剔除 %d 条 → %d' % (len(raw) - len(kept), len(kept)))

    # 类型分类：正刊稿 / 非正刊稿
    nonarticle, scored, invalid = [], [], 0
    for r in kept:
        t = (r.get('title') or '').strip()
        if len(t) < 8 or JUNK_TITLE.search(t):
            invalid += 1
            continue
        kind, why = classify_article(t)
        if kind != 'article':
            nonarticle.append(dict(r, kind=kind, why=why))
            continue
        sc, kws = score_loose(t)
        if sc <= 0:
            continue
        r['score'] = sc
        r['matched'] = kws[:8]
        scored.append(r)
    log('非正刊稿剔除 %d 条 ｜ 垃圾标题 %d ｜ 与方向无关 %d ｜ 候选 %d'
        % (len(nonarticle), invalid,
           len(kept) - invalid - len(nonarticle) - len(scored), len(scored)))

    # 分层
    core, ext = [], []
    for r in scored:
        if r['score'] >= args.min_score:
            r['tier'] = 'core'
            core.append(r)
        elif args.ext_min_score and r['score'] >= args.ext_min_score:
            if set(r['matched']) & ORE_KWS:
                r['tier'] = 'ext'
                ext.append(r)
    log('core %d ｜ ext %d（扩展层必须沾矿种/矿床类型/成矿/成因词，只提地区或方法的不算）'
        % (len(core), len(ext)))

    # 去重：知识库 + 现有清单
    kb, kbstats = build_dedup_full()
    mk, mkstats = load_manifest_keys()
    log('去重索引：台账有PDF %d / 台账仅题录 %d / papers词条 %d(%d 个标题) ｜ 待爬清单 %d / 已完成 %d'
        % (kbstats['ledger_pdf'], kbstats['ledger_meta_only'], kbstats['papers_entries'],
           kbstats['papers_titles'], mkstats['manifest'], mkstats['done']))

    todo, dup_kb, dup_meta, dup_manifest, seen = [], 0, 0, 0, set()
    for r in core + ext:
        nk = hm.norm_title(r['title'])
        if nk in seen:
            continue
        seen.add(nk)
        where = hit_keys(r, kb)
        if where == 'meta_titles':
            dup_meta += 1
            continue
        if where:
            dup_kb += 1
            continue
        if hit_manifest(r, mk):
            dup_manifest += 1
            continue
        rid = 'hm-' + hashlib.sha1(
            (hm.norm_doi(r.get('doi')) or nk).encode('utf-8')).hexdigest()[:12]
        reach, reach_p = hm.reach_of(r['url'])
        todo.append({
            'id': rid,
            'journal': r['journal'],
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
            'source': r['source'],
            'reach': reach,
            'reach_p': reach_p,
            'sd': hm.is_sd(r.get('doi') or '', r['url'], r['journal']),
            'status': 'pending',
            'attempts': 0,
            'last_error': '',
            'batch': 'add10y',
            'added_at': datetime.now().isoformat(timespec='seconds'),
        })
    log('已在知识库 %d（其中仅题录 %d）｜ 已在待爬/已完成清单 %d → 新增 %d 条'
        % (dup_kb, dup_meta, dup_manifest, len(todo)))

    # 排序：core 在前；同层先易后难 → 高分 → 新文；SD 沉底
    todo.sort(key=lambda x: (x['tier'] != 'core', -x['reach_p'], -x['score'],
                             _neg_date(x['date']), x['journal']))
    _non_sd = [x for x in todo if not x['sd']]
    _sd = [x for x in todo if x['sd']]
    todo = _non_sd + _sd
    log('其中 ScienceDirect %d 条已沉到末尾（暂停期不消费）' % len(_sd))

    stats = {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'window': [frm.isoformat(), until.isoformat()],
        'years': years,
        'min_score': args.min_score,
        'ext_min_score': args.ext_min_score,
        'raw': len(raw),
        'journals_fetched': len({r.get('journal') for r in raw}),
        'in_window': len(kept),
        'nonarticle_removed': len(nonarticle),
        'invalid_title': invalid,
        'core': len(core), 'ext': len(ext),
        'dup_kb': dup_kb, 'dup_meta_only': dup_meta, 'dup_manifest': dup_manifest,
        'todo': len(todo), 'sd_todo': len(_sd),
        'dedup_index': kbstats, 'manifest_index': mkstats,
        'fetch_failed': fails,
        'by_journal': {}, 'by_year': {}, 'by_tier': {}, 'by_reach': {}, 'by_kind': {},
    }
    for x in todo:
        stats['by_journal'][x['journal']] = stats['by_journal'].get(x['journal'], 0) + 1
        stats['by_year'][x['year'] or '?'] = stats['by_year'].get(x['year'] or '?', 0) + 1
        stats['by_tier'][x['tier']] = stats['by_tier'].get(x['tier'], 0) + 1
        stats['by_reach'][x['reach']] = stats['by_reach'].get(x['reach'], 0) + 1
    for r in nonarticle:
        stats['by_kind'][r['kind']] = stats['by_kind'].get(r['kind'], 0) + 1

    log('\n按期刊：')
    for k, v in sorted(stats['by_journal'].items(), key=lambda kv: -kv[1]):
        log('  %-40s %d' % (k, v))
    log('按年份：%s' % dict(sorted(stats['by_year'].items())))
    log('按层级：%s ｜ 按源站：%s' % (stats['by_tier'], stats['by_reach']))
    log('被剔除非正刊稿：%s' % stats['by_kind'])

    if args.dry_run:
        log('\n[dry-run] 未写文件')
        return 0

    STATE.mkdir(parents=True, exist_ok=True)
    tmp = STAGE.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        for x in todo:
            f.write(json.dumps(x, ensure_ascii=False) + '\n')
    os.replace(tmp, STAGE)
    STAGE_STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding='utf-8')
    with open(SKIPPED, 'w', encoding='utf-8', newline='\n') as f:
        for r in nonarticle:
            f.write(json.dumps({'journal': r['journal'], 'title': r['title'],
                                'year': r.get('year') or (r.get('date') or '')[:4],
                                'doi': r.get('doi') or '', 'kind': r['kind'],
                                'why': r['why']}, ensure_ascii=False) + '\n')
    hm.write_html(todo, stats, out=STAGE_HTML,
                  heading='近 %d 年待抓取文献增量清单（%s 条）' % (args.years, len(todo)))
    write_report(stats, nonarticle, todo, frm, until, args)
    log('\n已写：%s' % STAGE)
    log('已写：%s' % STAGE_HTML)
    log('已写：%s' % STAGE_STATS)
    log('已写：%s' % SKIPPED)
    return 0


def _neg_date(d):
    """按日期倒序排（空日期排最后）"""
    return '' if not d else ''.join(chr(0x10FFFF - ord(c)) for c in d)


def pid_alive():
    try:
        import psutil
        return psutil.pid_exists(int((STATE / 'history_queue_loop.pid').read_text().strip()))
    except Exception:
        return False


def do_merge(args):
    """暂存区 → 待爬队列。会立刻被 history_queue_loop 消费（= 开爬）。"""
    if not STAGE.exists():
        log('暂存区不存在：%s' % STAGE)
        return 2
    alive = pid_alive()
    if alive and not args.force:
        log('⚠️ history_queue_loop 正在运行：并入后会**立即开始爬取**。')
        log('   确认要并入请加 --force。')
        return 3
    add = [json.loads(l) for l in STAGE.read_text(encoding='utf-8').splitlines() if l.strip()]
    cur = [json.loads(l) for l in MANIFEST.read_text(encoding='utf-8').splitlines()
           if l.strip()] if MANIFEST.exists() else []
    keys, _ = load_manifest_keys()
    n0, skipped = len(cur), 0
    for x in add:
        if hit_manifest(x, keys):
            skipped += 1
            continue
        cur.append(x)
        keys['titles'].add(hm.norm_title(x['title']))
        if x.get('doi'):
            keys['dois'].add(hm.norm_doi(x['doi']))
        if x.get('url'):
            keys['urls'].add(x['url'].strip().lower())
    tmp = MANIFEST.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        for x in cur:
            f.write(json.dumps(x, ensure_ascii=False) + '\n')
    os.replace(tmp, MANIFEST)
    log('并入 %d 条（重复跳过 %d）｜ 清单 %d → %d' % (len(add) - skipped, skipped, n0, len(cur)))
    return 0


def write_report(stats, nonarticle, todo, frm, until, args):
    LOGS.mkdir(parents=True, exist_ok=True)
    p = LOGS / ('清单%d年_%s.md' % (args.years, datetime.now().strftime('%Y%m%d')))
    L = []
    L.append('# 近 %d 年目标期刊待抓取清单（只列清单，未爬取）\n' % args.years)
    L.append('生成时间：%s ｜ 窗口 %s ~ %s\n'
             % (stats['generated_at'], frm.isoformat(), until.isoformat()))
    L.append('## 一、总量\n')
    L.append('| 环节 | 条数 |\n|---|---|')
    L.append('| 原始题录（%d 刊 × %d 年） | %d |'
             % (stats.get('journals_fetched', 0), len(stats['years']), stats['raw']))
    L.append('| 时间窗内 | %d |' % stats['in_window'])
    L.append('| 剔除：非正刊稿（评论/回复/更正/撤稿/导读/目录/消息…） | %d |'
             % stats['nonarticle_removed'])
    L.append('| 剔除：垃圾标题 | %d |' % stats['invalid_title'])
    L.append('| 剔除：与团队方向无关（未达分层门槛） | %d |'
             % (stats['in_window'] - stats['nonarticle_removed']
                - stats['invalid_title'] - stats['core'] - stats['ext']))
    L.append('| 命中方向：核心层 core（score ≥ %d） | %d |' % (args.min_score, stats['core']))
    L.append('| 命中方向：扩展层 ext（score ≥ %d 且命中方向词） | %d |'
             % (args.ext_min_score, stats['ext']))
    L.append('| 剔除：知识库已有（台账有 PDF / 已入库词条） | %d |' % stats['dup_kb'])
    L.append('| 剔除：台账仅题录（待补 PDF，另由投放箱管） | %d |' % stats['dup_meta_only'])
    L.append('| 剔除：已在待爬/已完成清单 | %d |' % stats['dup_manifest'])
    L.append('| **本次新增待抓取** | **%d** |' % stats['todo'])
    L.append('| 其中 ScienceDirect（暂停期沉底） | %d |' % stats['sd_todo'])
    L.append('')
    L.append('## 二、按期刊\n')
    L.append('| 期刊 | 新增 |\n|---|---|')
    for k, v in sorted(stats['by_journal'].items(), key=lambda kv: -kv[1]):
        L.append('| %s | %d |' % (k, v))
    L.append('')
    L.append('## 三、按年份\n')
    L.append('| 年份 | 新增 |\n|---|---|')
    for k in sorted(stats['by_year']):
        L.append('| %s | %d |' % (k, stats['by_year'][k]))
    L.append('')
    L.append('## 四、按源站可达性（决定消费顺序）\n')
    L.append('| 源站 | 新增 | 说明 |\n|---|---|---|')
    note = {'cn_site': '中文刊官网直下', 'springer': '部分 OA', 'gsw': 'GSA/GSW 有 CF',
            'seg': 'Econ Geol', 'oup': 'OUP 有 CF', 'elsevier': 'SD 全线暂停',
            'other': '其它'}
    for k, v in sorted(stats['by_reach'].items(), key=lambda kv: -kv[1]):
        L.append('| %s | %d | %s |' % (k, v, note.get(k, '')))
    L.append('')
    L.append('## 五、被剔除非正刊稿明细（供复核）\n')
    L.append('| 类型 | 条数 |\n|---|---|')
    for k, v in sorted(stats['by_kind'].items(), key=lambda kv: -kv[1]):
        L.append('| %s | %d |' % (k, v))
    L.append('')
    L.append('抽样（每类最多 5 条，全量见 `state/history_manifest_skipped10y.jsonl`）：\n')
    bykind = {}
    for r in nonarticle:
        bykind.setdefault(r['kind'], []).append(r)
    for k, arr in sorted(bykind.items(), key=lambda kv: -len(kv[1])):
        L.append('- **%s**（%d）' % (k, len(arr)))
        for r in arr[:5]:
            L.append('  - %s ｜ %s' % (r['journal'], r['title'][:90]))
    L.append('')
    L.append('## 六、口径与红线\n')
    L.append('- **正刊稿判据**：`journal_filter.classify_article()`（唯一实现）。'
             '综述（Review）属正刊稿保留；评论/回复/更正/撤稿/导读/书评/讣告/目录/征稿/消息剔除。')
    L.append('- **扩展层判据**：`3 ≤ score < 6` 且必须命中矿种/矿床类型/成矿作用/成因机制任一词。'
             '只提地区（云南、青藏高原）或只命中方法词（in situ、LA-ICP-MS）的不要——'
             '实测只按地区放行会把地震台网、滑坡易发性、水文这类无关论文捞进来。')
    L.append('- **已归档判据**：台账 `repo_path` 含 `papers_repo/` 且 `.pdf`；只写 `metadata_only` 的视为待补 PDF。')
    L.append('- **未并入待爬队列**：结果在 `state/history_manifest_add10y.jsonl`（暂存区）。'
             '`history_queue_loop` 正在运行，直接并入会立刻开爬，故需 `--merge --force` 手动放行。')
    if stats['fetch_failed']:
        L.append('- **源站失败**：' + '；'.join('%s（%s）' % (f['journal'], f['error'])
                                          for f in stats['fetch_failed']))
    p.write_text('\n'.join(L) + '\n', encoding='utf-8')
    log('已写：%s' % p)


if __name__ == '__main__':
    sys.exit(main())
