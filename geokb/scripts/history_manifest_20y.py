# -*- coding: utf-8 -*-
"""目标期刊「近 20 年」待抓取清单（只列清单，不爬取）

与既有脚本的分工
----------------
· `history_manifest.py`      : 近 5 年全量重建，直接覆盖生产清单。
· `history_manifest_10y.py`  : 近 10 年增量，写暂存区（**仍未并入，5736 条**）。
· `history_manifest_region.py`: 南亚东南亚 24 刊，双门槛判据。
· 本脚本                     : **近 20 年 × 34 刊**，按「能否自动下载」分五桶。

为什么必须分桶（2026-10-27 实测）
--------------------------------
34 刊近 20 年共 **115,228 篇**题录，其中：
  · SD（10.1016）65,940 篇 = 57.2%，政策暂停，一条都下不了；
  · Wiley / T&F / Lyell / GSW ≈ 2.0 万，Cloudflare 人机挑战，已冻结；
  · Springer 9,019 + Cambridge 2,175 + J-STAGE 797 + 三个 OA 小站 1,375
    ≈ 1.3 万，才是真正能下的。
全部倒进生产队列 → 队列 87% 变死重、每篇收工全量回写十几 MB。
所以按桶落不同文件，**生产队列只收「现在就能下」的那部分**，其余留归档，
条件具备时一条命令注回（`--merge-backlog`）。

五桶判据（顺序固定，先到先判）
----------------------------
1. `sd`          DOI 前缀 10.1016 / 刊名命中 SD 表 → 政策暂停（`sd_policy`）
2. `blocked`     `source_freeze` 冻结表中的源站 → 冷却期不碰
3. `quota`       OUP（日配额 25 篇）→ 单列，按天滴灌，不挤占当日新文
4. `unsupported` 无 handler 的源站 → 接 handler 后注回
5. `auto`        其余 → 并入生产队列

三条口径（沿用 10 年清单，用户 2026-10-26 定）
--------------------------------------------
1. 与团队方向有联系就算要抓：core（score>=6）+ ext（3<=score<6 且命中矿种/成矿词）
2. 剔除非正刊稿：唯一判据 `journal_filter.classify_article`
3. 全量去重：生产清单 / 已完成 / 冻结备份 / 台账（有 PDF + 仅题录）/
   `content/papers` 词条 / 当日未下到 / 待入库 / 人工标记

输出
----
state/history_manifest_20y_auto.jsonl        可自动下载（并入生产队列）
state/history_manifest_20y_sd.jsonl          SD 政策暂停
state/history_manifest_20y_blocked.jsonl     冻结站（冷却后注回）
state/history_manifest_20y_quota.jsonl       OUP 配额滴灌
state/history_manifest_20y_unsupported.jsonl 无 handler
state/history_manifest_20y_auto.html         可下部分的人读清单
state/history_manifest_20y_stats.json
state/history_manifest_20y_skipped.jsonl     非正刊稿复核
logs/清单20年_YYYYMMDD.md

用法
----
    python scripts/history_manifest_20y.py                 # 生成（写暂存区）
    python scripts/history_manifest_20y.py --dry-run       # 只统计
    python scripts/history_manifest_20y.py --refresh       # 忽略题录缓存
    python scripts/history_manifest_20y.py --merge --force # 只把 auto 桶并入生产队列
    python scripts/history_manifest_20y.py --merge-backlog quota --limit 25
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
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
RAW_CACHE = STATE / 'manifest_raw'

MANIFEST = STATE / 'history_manifest.jsonl'
DONE = STATE / 'history_done.jsonl'
FROZEN = STATE / 'history_manifest_frozen.jsonl'
NO_PDF = STATE / 'journal_no_pdf.jsonl'
PENDING = STATE / 'pending_papers_ingest.jsonl'
MARKS = STATE / 'manual_marks.json'
LEDGER = STATE / 'papers_index.jsonl'
PAPERS_DIR = BASE / 'content' / 'papers'

STAGE = {
    'auto': STATE / 'history_manifest_20y_auto.jsonl',
    'sd': STATE / 'history_manifest_20y_sd.jsonl',
    'blocked': STATE / 'history_manifest_20y_blocked.jsonl',
    'quota': STATE / 'history_manifest_20y_quota.jsonl',
    'unsupported': STATE / 'history_manifest_20y_unsupported.jsonl',
}
STAGE_HTML = STATE / 'history_manifest_20y_auto.html'
STAGE_STATS = STATE / 'history_manifest_20y_stats.json'
SKIPPED = STATE / 'history_manifest_20y_skipped.jsonl'


def apply_stage_suffix(suffix):
    """把五个桶文件换成带后缀的一组（`--stage-suffix _cn`）。

    为什么需要：每次运行都会**整组重写**五个桶文件。中文刊走官网枚举、
    耗时长且容易单刊卡住，必须能单独跑一趟而不覆盖已生成的主暂存区。
    """
    global STAGE_HTML, STAGE_STATS, SKIPPED
    if not suffix:
        return
    for b in list(STAGE):
        STAGE[b] = STAGE[b].with_name(STAGE[b].stem + suffix + '.jsonl')
    STAGE_HTML = STAGE_HTML.with_name(STAGE_HTML.stem + suffix + '.html')
    STAGE_STATS = STAGE_STATS.with_name(STAGE_STATS.stem + suffix + '.json')
    SKIPPED = SKIPPED.with_name(SKIPPED.stem + suffix + '.jsonl')

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, 'D:/PostNews')

import history_manifest as hm                      # noqa: E402
import history_manifest_region as hr               # noqa: E402
from journal_filter import classify_article        # noqa: E402
import journal_filter as jf                        # noqa: E402

# 复用 10 年脚本的打分内核与垃圾标题正则（同一口径，避免两套规则漂移）
import history_manifest_10y as h10                 # noqa: E402

# ── geo 层：补「低分但方向对口」的召回缺口（2026-10-27 实测）─────────────
# 现象：`ext` 层要求命中矿种/成矿词，于是一批**构造/成矿带**论文被漏掉 ——
# 实测未达层 15,145 条里 70% 命中用户点名的方向词（俯冲带 2,414、同位素
# 2,386、地化 2,035、锆石 1,309…），其中 1,095 条当下就能下。
# 口径：3<=score<6 **且** 命中「成矿词 ∪ 构造词」。刻意不含纯地区词
# （青藏高原/云南…）—— 实测只提地区会把水文/滑坡/地震台网捞进来。
GEO_KWS = set(h10.ORE_KWS) | set(k for k, _ in jf.TECTONIC_KEYWORDS)

# 矿物定年/示踪词：团队做石榴石—白钨矿矽卡岩，锆石/独居石/辉钼矿 U-Pb、
# 石榴石 Lu-Hf、辉钼矿 Re-Os 都是常规手段，这类标题不沾成矿/构造词却必须收。
# ⚠️ 词形必须与 `score_loose()` 返回的**规范名**完全一致（实测 'U-Pb' 不是 'u-pb'），
# 否则永远匹配不上、白加。
# ⚠️ 刻意不放 `geochemistry`（1,519 条，含大量环境/水文）与纯地区词
# （Tibetan Plateau 835 / Tethys 218 / Yunnan 131）—— 那两个层级另行决定。
GEOCRON_KWS = {
    'geochronology', '年代学', '定年', '锆石U-Pb', 'U-Pb', 'Re-Os', 'Lu-Hf',
    'Sm-Nd', 'Rb-Sr', 'Ar-Ar', 'fission track', '裂变径迹',
    'Pb同位素', 'Sr-Nd同位素', 'sulfur isotope', '硫同位素',
    'mineralogy', '矿物学', 'fluid inclusion', '流体包裹体',
}
GEO_KWS |= GEOCRON_KWS

MAILTO = 'kust.work@qq.com'
S = requests.Session()
S.trust_env = False                                # 关键：绕开本机 HTTP_PROXY 劫持
S.headers.update({'User-Agent': 'GeoKB-Manifest20y/1.0 (mailto:%s)' % MAILTO})


def log(msg):
    print(msg, flush=True)


# ── 期刊表：核心 10 刊 ∪ 区域 24 刊（去重，保持顺序）────────────────────
def target_journals():
    out, seen = [], set()
    for name, issn in list(hm.CROSSREF_JOURNALS) + list(hr.REGION_JOURNALS):
        k = issn.replace('-', '').upper()
        if k in seen:
            continue
        seen.add(k)
        out.append((name, issn))
    return out


# ── ① 枚举（分年段 + 缓存，键必须带 ISSN）────────────────────────────────
def cache_get(key):
    p = RAW_CACHE / (key + '.json')
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return None


def cache_put(key, data):
    RAW_CACHE.mkdir(parents=True, exist_ok=True)
    p = RAW_CACHE / (key + '.json')
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, p)


def fetch_chunk(issn, frm, until, cap=9000):
    """CrossRef cursor 分页。单段 ≤5 年，实测单段最多约 3,000 条，cap 够用。"""
    url = 'https://api.crossref.org/journals/%s/works' % issn
    params = {
        'filter': 'from-pub-date:%s,until-pub-date:%s,type:journal-article' % (frm, until),
        'rows': '200', 'cursor': '*', 'mailto': MAILTO,
        'select': 'DOI,title,published,issued,container-title,URL,volume,issue',
    }
    out, guard = [], 0
    while len(out) < cap and guard < 60:
        guard += 1
        ok = False
        for attempt in range(3):
            try:
                r = S.get(url, params=params, timeout=60, verify=False,
                          proxies={'http': None, 'https': None})
                if r.status_code == 200:
                    ok = True
                    break
                if r.status_code in (429, 503):
                    time.sleep(3 * (attempt + 1))
                    continue
                log('    [warn] %s HTTP %s' % (issn, r.status_code))
                break
            except Exception as e:
                if attempt == 2:
                    raise
                log('    [retry] %s %s' % (issn, str(e)[:50]))
                time.sleep(2)
        if not ok:
            break
        msg = r.json().get('message', {})
        items = msg.get('items') or []
        if not items:
            break
        for it in items:
            titles = it.get('title') or []
            if not titles:
                continue
            title = hm.clean_title(titles[0])
            if not title or len(title) < 8:
                continue
            parts = ((it.get('published') or it.get('issued') or {}).get('date-parts')
                     or [[None]])[0]
            date = '-'.join('%02d' % int(x) for x in parts) if parts and parts[0] else ''
            out.append({
                'title': title,
                'url': it.get('URL') or ('https://doi.org/' + (it.get('DOI') or '')),
                'doi': it.get('DOI') or '',
                'date': date,
                'volume': str(it.get('volume') or ''),
                'issue': str(it.get('issue') or ''),
            })
        params['cursor'] = msg.get('next-cursor')
        if not params['cursor']:
            break
        time.sleep(0.25)
    return out


def chunks(frm, until, years_per_chunk):
    out, cur = [], frm
    while cur < until:
        nxt = min(cur + timedelta(days=365 * years_per_chunk), until)
        out.append((cur, nxt))
        cur = nxt
    return out


# ── ② 去重索引（O(1) 前缀匹配，别用 O(N) 全表扫）────────────────────────
class Deduper:
    def __init__(self):
        self.titles = set()
        self.prefix = set()          # 已知标题前 48 字
        self.dois = set()
        self.urls = set()
        self.meta = set()
        self.meta_prefix = set()
        self.stat = Counter()

    def add(self, title='', doi='', url='', meta_only=False):
        nt = hm.norm_title(title)
        nd = hm.norm_doi(doi)
        nu = (url or '').strip().lower()
        if nt:
            if meta_only:
                self.meta.add(nt)
                if len(nt) >= 48:
                    self.meta_prefix.add(nt[:48])
            else:
                self.titles.add(nt)
                if len(nt) >= 48:
                    self.prefix.add(nt[:48])
        if nd:
            self.dois.add(nd)
        if nu:
            self.urls.add(nu)

    def hit(self, rec):
        """返回命中类别或空串。"""
        nt = hm.norm_title(rec.get('title') or '')
        if nt:
            if nt in self.titles:
                return 'titles'
            if len(nt) >= 48 and nt[:48] in self.prefix:
                return 'titles'
            if nt in self.meta:
                return 'meta_titles'
            if len(nt) >= 48 and nt[:48] in self.meta_prefix:
                return 'meta_titles'
        nd = hm.norm_doi(rec.get('doi') or '')
        if nd and nd in self.dois:
            return 'dois'
        nu = (rec.get('url') or '').strip().lower()
        if nu and nu in self.urls:
            return 'urls'
        return ''


def _jsonl(path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding='utf-8', errors='ignore').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def build_dedup():
    d = Deduper()
    n = Counter()

    for r in _jsonl(MANIFEST):
        d.add(r.get('title'), r.get('doi'), r.get('url'))
        n['manifest'] += 1
    for r in _jsonl(DONE):
        d.add(r.get('title'), r.get('doi'), r.get('url'))
        n['done'] += 1
    for r in _jsonl(FROZEN):
        d.add(r.get('title'), r.get('doi'), r.get('url'))
        n['frozen'] += 1
    for r in _jsonl(NO_PDF):
        d.add(r.get('title'), r.get('doi'), r.get('url'))
        n['no_pdf'] += 1
    for r in _jsonl(PENDING):
        d.add(r.get('title'), r.get('doi'), r.get('url'))
        n['pending'] += 1

    # 台账：有真 PDF 的算已入库；只有题录的单独一档（不重复入列）
    for r in _jsonl(LEDGER):
        real = ('papers_repo/' in (r.get('repo_path') or '')
                and float(r.get('size') or 0) > 0)
        d.add(r.get('title'), r.get('doi'), r.get('url'), meta_only=not real)
        n['ledger_pdf' if real else 'ledger_meta'] += 1

    # 知识库词条
    if PAPERS_DIR.exists():
        import yaml
        cnt = 0
        for p in PAPERS_DIR.glob('*.md'):
            try:
                raw = p.read_text(encoding='utf-8', errors='ignore')[:3000]
                mm = re.match(r'^---\n(.*?)\n---', raw, re.S)
                if not mm:
                    continue
                fm = yaml.safe_load(mm.group(1)) or {}
                if fm.get('title'):
                    d.add(fm['title'], fm.get('doi'), fm.get('url'))
                    cnt += 1
            except Exception:
                continue
        n['papers_md'] = cnt

    # 人工标记（跳过/已下载）——避免把人工确认过的又塞回队列
    if MARKS.exists():
        try:
            marks = json.loads(MARKS.read_text(encoding='utf-8'))
        except Exception:
            marks = {}
        cnt = 0
        for k, v in (marks or {}).items():
            if not isinstance(v, dict) or v.get('state') not in ('skipped', 'downloaded'):
                continue
            if k.startswith('d:'):
                d.add(doi=k[2:])
            elif k.startswith('t:'):
                d.add(title=k[2:])
            elif k.startswith('u:'):
                d.add(url=k[2:])
            cnt += 1
        n['marks'] = cnt

    # ⚠️ 绝不能把本脚本自己的暂存区放进去重索引：
    # 重跑时全部条目会被判成「已存在」，暂存区被写成空文件（数据丢失）。
    # 重跑幂等靠「同样的缓存 + 同样的规则 → 同样的结果」，不靠自去重。
    return d, n


# ── ③ 分桶 ──────────────────────────────────────────────────────────────
def _load_downloader():
    p = Path('D:/PostNews/.workbuddy/journal_pdf_downloader.py')
    spec = importlib.util.spec_from_file_location('_jpd20', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def bucket_of(rec, host, frozen_tab, minfo):
    """五桶判据，顺序不可换（冻结 > 政策 > 配额 > 无 handler > 可下）。"""
    if rec.get('sd'):
        return 'sd', 'ScienceDirect 政策暂停'
    ok, why = minfo['sf'].is_frozen(host, frozen_tab)
    if ok:
        return 'blocked', '冻结中：%s' % (why or '风控/故障')
    if host == 'academic.oup.com':
        return 'quota', 'OUP 日配额 25 篇，需滴灌'
    src = minfo['dl'].source_of(minfo['dl'].normalize_url(rec.get('url') or ''))
    if src == 'unknown' or src not in minfo['dl'].HANDLERS:
        return 'unsupported', '无 handler（%s）' % src
    return 'auto', ''


def _neg_date(d):
    return '' if not d else ''.join(chr(0x10FFFF - ord(c)) for c in d)


# ── ④ 主流程 ────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description='目标期刊近 20 年待抓清单（只列不爬）')
    ap.add_argument('--years', type=int, default=20)
    ap.add_argument('--chunk-years', type=int, default=5, help='CrossRef 分段年数')
    ap.add_argument('--min-score', type=int, default=6)
    ap.add_argument('--ext-min-score', type=int, default=3)
    ap.add_argument('--geo-min-score', type=int, default=3,
                    help='geo 层门槛：分数落在 [geo-min-score, min-score) 且命中'
                         '「成矿词 ∪ 构造词」即收。0 = 关闭（退回 10 年口径）')
    ap.add_argument('--journal', default='')
    ap.add_argument('--skip', default='')
    ap.add_argument('--cn-site', action='store_true',
                    help='额外枚举中文四刊官网（地学前缘/地质学报/矿床地质/大地构造与成矿学）')
    ap.add_argument('--stage-suffix', default='',
                    help='暂存区文件名后缀（如 _cn）：单跑中文刊时不要覆盖主暂存区')
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--merge', action='store_true', help='auto 桶并入生产队列')
    ap.add_argument('--merge-backlog', default='', help='sd|blocked|quota|unsupported')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args()
    apply_stage_suffix(args.stage_suffix)

    if args.merge or args.merge_backlog:
        return do_merge(args)

    import sd_policy
    import source_freeze as sf

    until = datetime.now().date()
    frm = until - timedelta(days=365 * args.years)
    log('窗口 %s ~ %s（%d 年）｜ core>=%d / ext>=%d ｜ 分段 %d 年'
        % (frm, until, args.years, args.min_score, args.ext_min_score, args.chunk_years))

    dl = _load_downloader()
    minfo = {'sf': sf, 'dl': dl}
    frozen_tab = sf.load()

    jobs = [('crossref', n, i, None) for n, i in target_journals()
            if (not args.journal or args.journal in n)
            and not (args.skip and any(s in n for s in [x.strip() for x in args.skip.split(',') if x.strip()]))]

    # 中文四刊走官网枚举（CrossRef 题录不全）。冻结/故障站直接跳过，不白跑。
    SITE_JOBS = [('地学前缘', 'www.earthsciencefrontiers.net.cn', hm.fetch_esf),
                 ('地质学报', 'www.geojournals.cn', hm.fetch_dzxb),
                 ('矿床地质', 'www.kcdz.ac.cn', hm.fetch_kcdz),
                 ('大地构造与成矿学', 'dgyk.cbpt.cnki.net', hm.fetch_dgyk)]
    if args.cn_site:
        for nm, host, fn in SITE_JOBS:
            if args.journal and args.journal not in nm:
                continue
            ok, why = sf.is_frozen(host, frozen_tab)
            if ok:
                log('  [跳过] %s：%s' % (nm, why or '源站冻结中'))
                continue
            jobs.append(('site', nm, '', fn))
    log('目标期刊 %d 种（其中官网枚举 %d）｜ 已冻结源站 %d 个'
        % (len(jobs), sum(1 for j in jobs if j[0] == 'site'), len(frozen_tab)))

    # 去重索引先建：单篇判定 O(1)
    ded, dstat = build_dedup()
    log('去重索引：%s' % dict(dstat))

    buckets = {k: [] for k in STAGE}
    stats = Counter()
    nonarticle, fails = [], []
    window = chunks(frm, until, args.chunk_years)

    for kind, name, issn, fn in jobs:
        t0 = time.time()
        raw = []
        if kind == 'site':
            key = 'v3_20y_site_%s_%s_%s' % (
                re.sub(r'\W+', '', name)[:16], frm.strftime('%Y%m%d'), until.strftime('%Y%m%d'))
            items = None if args.refresh else cache_get(key)
            if items is None:
                try:
                    items = fn(list(range(frm.year, until.year + 1)))
                    cache_put(key, items)
                except Exception as e:
                    fails.append({'journal': name, 'err': '%s: %s'
                                  % (type(e).__name__, str(e)[:90]), 'window': 'site'})
                    items = []
            raw.extend(items)
        else:
            for cf, cu in window:
                key = 'v3_20y_%s_%s_%s' % (issn.replace('-', ''), cf.strftime('%Y%m%d'),
                                           cu.strftime('%Y%m%d'))
                items = None if args.refresh else cache_get(key)
                if items is None:
                    try:
                        items = fetch_chunk(issn, cf.isoformat(), cu.isoformat())
                        cache_put(key, items)
                    except Exception as e:
                        fails.append({'journal': name, 'err': '%s: %s' % (type(e).__name__, str(e)[:90]),
                                      'window': [cf.isoformat(), cu.isoformat()]})
                        items = []
                raw.extend(items)
        stats['raw'] += len(raw)

        kept = 0
        for r in raw:
            r['journal'] = name
            r['source'] = kind
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
                  and (set(kws) & GEO_KWS)):
                r['tier'] = 'geo'
            else:
                stats['below_tier'] += 1
                continue
            r['score'] = sc
            r['matched'] = kws[:8]
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
            # 占位，防止同批重复
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
                'id': 'hm-' + hashlib.sha1(
                    (nd or nk).encode('utf-8')).hexdigest()[:12],
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
                'source': kind,
                'reach': reach,
                'reach_p': reach_p,
                'sd': bool(is_sd),
                'status': 'pending',
                'attempts': 0,
                'last_error': '',
                'batch': 'add20y',
                'added_at': datetime.now().isoformat(timespec='seconds'),
                'host': host,
            }
            b, why = bucket_of(rec, host, frozen_tab, minfo)
            rec['bucket'] = b
            rec['bucket_why'] = why
            buckets[b].append(rec)
            added += 1

        stats['candidates'] += kept
        stats['new'] += added
        log('  %-44s %5d 篇 → 候选 %4d ｜ 新增 %4d ｜ %.0fs'
            % (name[:44], len(raw), kept, added, time.time() - t0))

    # 排序：core 在前 → 易下的先 → 高分 → 新文
    for b in buckets:
        buckets[b].sort(key=lambda x: (x['tier'] != 'core', -x['reach_p'],
                                       -x['score'], _neg_date(x['date']), x['journal']))

    total = sum(len(v) for v in buckets.values())
    log('\n分桶：%s ｜ 合计 %d' % ({k: len(v) for k, v in buckets.items()}, total))
    log('漏斗：原始 %d ｜ 窗外 %d ｜ 垃圾标题 %d ｜ 非正刊 %d ｜ 无关 %d ｜ 未达层 %d ｜ 重复 %d → 新增 %d'
        % (stats['raw'], stats['out_window'], stats['junk_title'], stats['nonarticle'],
           stats['irrelevant'], stats['below_tier'],
           stats['dup_kb'] + stats['dup_meta'] + stats['dup_doi'] + stats['dup_url'],
           stats['new']))

    by_journal = Counter(x['journal'] for v in buckets.values() for x in v)
    by_year = Counter(x['year'] or '?' for v in buckets.values() for x in v)
    by_tier = Counter(x['tier'] for v in buckets.values() for x in v)
    by_host = Counter(x['host'] for v in buckets.values() for x in v)

    payload = {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'window': [frm.isoformat(), until.isoformat()],
        'years': args.years,
        'journals': len(jobs),
        'min_score': args.min_score, 'ext_min_score': args.ext_min_score,
        'funnel': {k: stats[k] for k in ('raw', 'out_window', 'junk_title', 'nonarticle',
                                         'irrelevant', 'below_tier', 'candidates', 'new',
                                         'dup_kb', 'dup_meta', 'dup_doi', 'dup_url')},
        'buckets': {k: len(v) for k, v in buckets.items()},
        'by_journal': dict(by_journal.most_common()),
        'by_year': dict(sorted(by_year.items())),
        'by_tier': dict(by_tier),
        'by_host': dict(by_host.most_common()),
        'nonarticle_kind': dict(Counter(r['kind'] for r in nonarticle).most_common()),
        'dedup_index': dict(dstat),
        'fetch_failed': fails,
    }

    log('\n按桶 × 期刊（auto 桶前 15）：')
    for k, v in sorted(by_journal.items(), key=lambda kv: -kv[1])[:15]:
        auto_n = sum(1 for x in buckets['auto'] if x['journal'] == k)
        log('  %-42s 总 %5d ｜ 可下 %4d' % (k[:42], v, auto_n))
    log('按年份：%s' % dict(sorted(by_year.items())))
    log('去重命中：知识库 %d ｜ 仅题录 %d ｜ DOI %d ｜ URL %d'
        % (stats['dup_kb'], stats['dup_meta'], stats['dup_doi'], stats['dup_url']))

    if args.dry_run:
        log('\n[dry-run] 未写文件')
        return 0

    STATE.mkdir(parents=True, exist_ok=True)
    for b, items in buckets.items():
        tmp = STAGE[b].with_suffix('.tmp')
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            for x in items:
                f.write(json.dumps(x, ensure_ascii=False) + '\n')
        os.replace(tmp, STAGE[b])
        log('已写 %-58s %6d 条' % (STAGE[b].name, len(items)))

    STAGE_STATS.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    with open(SKIPPED, 'w', encoding='utf-8', newline='\n') as f:
        for r in nonarticle:
            f.write(json.dumps({'journal': r['journal'], 'title': r['title'],
                                'year': (r.get('date') or '')[:4], 'doi': r.get('doi') or '',
                                'kind': r['kind'], 'why': r['why']}, ensure_ascii=False) + '\n')
    hm.write_html(buckets['auto'], payload, out=STAGE_HTML,
                  heading='目标期刊近 %d 年 · 可自动下载（%d 条）'
                          % (args.years, len(buckets['auto'])))
    write_report(payload, frm, until, args)
    log('已写 %s' % STAGE_HTML)
    log('已写 %s' % STAGE_STATS)
    return 0


def write_report(st, frm, until, args):
    LOGS.mkdir(parents=True, exist_ok=True)
    p = LOGS / ('清单20年_%s.md' % datetime.now().strftime('%Y%m%d'))
    L = ['# 目标期刊近 %d 年待抓清单' % args.years, '',
         '- 生成：%s' % st['generated_at'],
         '- 窗口：%s ~ %s' % tuple(st['window']),
         '- 期刊：%d 种 ｜ core>=%d / ext>=%d'
         % (st['journals'], st['min_score'], st['ext_min_score']), '']
    L += ['## 漏斗', '', '| 环节 | 条数 |', '|---|---|']
    names = {'raw': '原始题录', 'out_window': '时间窗外', 'junk_title': '垃圾标题',
             'nonarticle': '非正刊稿', 'irrelevant': '与方向无关', 'below_tier': '未达层级',
             'candidates': '候选', 'dup_kb': '已在知识库', 'dup_meta': '台账仅题录',
             'dup_doi': 'DOI 重复', 'dup_url': 'URL 重复', 'new': '**新增**'}
    for k, v in names.items():
        L.append('| %s | %d |' % (v, st['funnel'].get(k, 0)))
    L += ['', '## 分桶', '', '| 桶 | 条数 | 去向 |', '|---|---|---|']
    dest = {'auto': '并入生产队列（现在就能下）',
            'sd': 'SD 政策暂停（恢复后注回）',
            'blocked': '冻结站（冷却后注回）',
            'quota': 'OUP 配额滴灌（--merge-backlog quota --limit 25）',
            'unsupported': '无 handler（接上后注回）'}
    for k, v in st['buckets'].items():
        L.append('| %s | %d | %s |' % (k, v, dest.get(k, '')))
    L += ['', '## 按期刊', '', '| 期刊 | 条数 |', '|---|---|']
    for k, v in st['by_journal'].items():
        L.append('| %s | %d |' % (k, v))
    L += ['', '## 按源站', '', '| 源站 | 条数 |', '|---|---|']
    for k, v in st['by_host'].items():
        L.append('| %s | %d |' % (k, v))
    L += ['', '## 按年份', '', '| 年 | 条数 |', '|---|---|']
    for k, v in st['by_year'].items():
        L.append('| %s | %d |' % (k, v))
    if st['fetch_failed']:
        L += ['', '## 失败的源', '']
        for f in st['fetch_failed']:
            L.append('- %s %s：%s' % (f['journal'], f.get('window', ''), f['err']))
    p.write_text('\n'.join(L) + '\n', encoding='utf-8')
    log('已写 %s' % p)


# ── ⑤ 并入（独占协议）───────────────────────────────────────────────────
def do_merge(args):
    import history_queue as hq
    import park_frozen as pf

    if args.merge_backlog:
        b = args.merge_backlog
        if b not in STAGE:
            log('未知桶：%s（可选 %s）' % (b, '/'.join(STAGE)))
            return 2
        if b in ('blocked', 'sd') and not args.force:
            log('⚠️ %s 桶是「条件不具备」的条目，注回会立刻空转。确认请加 --force。' % b)
            return 3
        src_items = _jsonl(STAGE[b])
        if not src_items:
            log('%s 桶为空' % b)
            return 0
        take = src_items[:args.limit] if args.limit else src_items
        rows = hq.load_manifest(repair=True)
        have_t = {hm.norm_title(r['title']) for r in rows if r.get('title')}
        have_d = {hm.norm_doi(r['doi']) for r in rows if r.get('doi')}
        add = [x for x in take
               if hm.norm_title(x.get('title') or '') not in have_t
               and hm.norm_doi(x.get('doi') or '') not in have_d]
        left = src_items[len(take):]
        log('%s 桶 %d 条 ｜ 取 %d ｜ 与清单重复 %d ｜ 实际并入 %d'
            % (b, len(src_items), len(take), len(take) - len(add), len(add)))
        if args.dry_run:
            log('[dry-run] 未写盘，清单将变为 %d 条' % (len(rows) + len(add)))
            return 0
        try:
            pf.take_lock()
        except RuntimeError as e:
            log('⚠️ %s' % e)
            return 4
        try:
            n = pf.stop_queue()
            if n:
                log('已停调度器 %d 个进程' % n)
            hq.save_manifest(rows + add, backup=True)
            tmp = STAGE[b].with_suffix('.tmp')
            with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
                for x in left:
                    f.write(json.dumps(x, ensure_ascii=False) + '\n')
            os.replace(tmp, STAGE[b])
            log('已并入 %d 条 → 清单 %d ｜ 桶余 %d' % (len(add), len(rows) + len(add), len(left)))
        finally:
            pf.drop_lock()
        return 0

    # --merge：auto 桶
    src_items = _jsonl(STAGE['auto'])
    if not src_items:
        log('auto 桶为空或尚未生成')
        return 2
    rows = hq.load_manifest(repair=True)
    have_t = {hm.norm_title(r['title']) for r in rows if r.get('title')}
    have_d = {hm.norm_doi(r['doi']) for r in rows if r.get('doi')}
    # ⚠️ 还要查已下载台账：下载完成即从清单物理移除，只查清单会让已入库论文重下一遍
    done_p = STATE / 'history_done.jsonl'
    if done_p.exists():
        for _ln in done_p.read_text(encoding='utf-8').splitlines():
            _ln = _ln.strip()
            if not _ln:
                continue
            try:
                _x = json.loads(_ln)
            except Exception:
                continue
            if _x.get('title'):
                have_t.add(hm.norm_title(_x['title']))
            if _x.get('doi'):
                have_d.add(hm.norm_doi(_x['doi']))
    add, dup = [], 0
    for x in src_items:
        nt = hm.norm_title(x.get('title') or '')
        nd = hm.norm_doi(x.get('doi') or '')
        if (nt and nt in have_t) or (nd and nd in have_d):
            dup += 1
            continue
        if nt:
            have_t.add(nt)
        if nd:
            have_d.add(nd)
        add.append(x)
    log('auto 桶 %d 条 ｜ 与清单重复 %d ｜ 待并入 %d ｜ 清单 %d → %d'
        % (len(src_items), dup, len(add), len(rows), len(rows) + len(add)))
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
            log('已停调度器 %d 个进程，等守护 10s 内用新清单拉起' % n)
        hq.save_manifest(rows + add, backup=True)
        log('已写清单 %d 条' % (len(rows) + len(add)))
    finally:
        pf.drop_lock()
    return 0


if __name__ == '__main__':
    sys.exit(main())
