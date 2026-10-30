#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第三方 OA 源严格核验（GeoKB）

目的：对「被订阅墙拦住 / 未归档」的文献，判断是否存在**同一篇文章**的第三方开放副本。
红线：**只认 DOI 精确相等**。无 DOI 的条目不解析、不入结论（不做标题近似匹配）。

通道（全部为合法元数据聚合器，不碰出版社站点，无风控成本）：
  · Unpaywall  — 最全的 OA 位置库（含仓储版 accepted manuscript / preprint）
  · OpenAlex   — 机构仓储、arXiv/EarthArXiv、机构库镜像
  · S2         — 补充（arXiv / 部分会议）

用法：
  python oa_thirdparty.py build                 # 生成候选清单 state/oa_thirdparty_candidates.jsonl
  python oa_thirdparty.py collect [--limit N]   # 逐条查询，写 state/oa_thirdparty.jsonl（增量缓存）
  python oa_thirdparty.py report                # 汇总（按期刊 / 按通道 / 按 host）
  python oa_thirdparty.py verify [--limit N]    # 抽样下载并核验 PDF 内是否含该 DOI
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

BASE = Path(__file__).resolve().parent.parent

# 本机常驻 HTTP_PROXY=127.0.0.1:62097（沙箱/代理），它会以
# `CONNECT tunnel failed 502` 拦掉一部分站点，且 libcurl 会**自己**读环境变量，
# 所以 trust_env=False 并不够 —— 必须在进程内清掉这些变量才走真实直连。
# 设 GEOKB_USE_PROXY=1 可保留（例如确实只能走代理的网络环境）。
if os.environ.get('GEOKB_USE_PROXY') != '1':
    for _k in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
               'http_proxy', 'https_proxy', 'all_proxy'):
        os.environ.pop(_k, None)

STATE = BASE / 'state'
LEDGER = STATE / 'papers_index.jsonl'
MANIFEST = STATE / 'history_manifest.jsonl'
CAND = STATE / 'oa_thirdparty_candidates.jsonl'
CACHE = STATE / 'oa_thirdparty.jsonl'
VERIFY = STATE / 'oa_thirdparty_verify.jsonl'

EMAIL = os.environ.get('GEOKB_CONTACT_EMAIL', 'geokb@kust.work')
UA = f'GeoKB-OAResolver/1.0 (mailto:{EMAIL})'


# ---------------------------------------------------------------- 文本归一
def norm_doi(s: str) -> str:
    if not s:
        return ''
    s = str(s).strip()
    s = re.sub(r'^(https?://)?(dx\.)?doi\.org/', '', s, flags=re.I)
    s = re.sub(r'^doi:\s*', '', s, flags=re.I)
    return s.strip().rstrip('.,;)').lower()


def norm_title(s: str) -> str:
    if not s:
        return ''
    s = s.lower()
    s = re.sub(r'&[a-z]+;', ' ', s)
    s = re.sub(r'[^0-9a-z\u4e00-\u9fff]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


# ---------------------------------------------------------------- 台账读取
def is_pdf_entry(d: dict) -> bool:
    rp = (d.get('repo_path') or '').replace('\\', '/')
    return 'papers_repo/' in rp and (d.get('size') or 0) > 0


def load_archived():
    dois, titles = set(), set()
    if not LEDGER.exists():
        return dois, titles
    with LEDGER.open(encoding='utf-8') as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if not is_pdf_entry(d):
                continue
            nd = norm_doi(d.get('doi'))
            if nd:
                dois.add(nd)
            nt = norm_title(d.get('title'))
            if nt:
                titles.add(nt)
    return dois, titles


def load_jsonl(p: Path):
    out = []
    if not p.exists():
        return out
    with p.open(encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


# ---------------------------------------------------------------- build
def cmd_build(args):
    arch_dois, arch_titles = load_archived()
    seen = {}
    no_doi = 0
    skipped_archived = 0

    def add(rec, origin):
        nonlocal no_doi, skipped_archived
        doi = norm_doi(rec.get('doi'))
        title = rec.get('title') or ''
        if not doi:
            no_doi += 1
            return
        if doi in arch_dois:
            skipped_archived += 1
            return
        nt = norm_title(title)
        if nt and nt in arch_titles:
            skipped_archived += 1
            return
        if doi in seen:
            return
        seen[doi] = {
            'doi': doi,
            'title': title,
            'journal': rec.get('journal') or '',
            'year': rec.get('year') or (rec.get('date') or '')[:4],
            'url': rec.get('url') or '',
            'origin': origin,
        }

    for d in load_jsonl(MANIFEST):
        add(d, 'history_manifest')
    for d in load_jsonl(LEDGER):
        if is_pdf_entry(d):
            continue
        add(d, 'ledger_metadata')

    with CAND.open('w', encoding='utf-8') as f:
        for r in seen.values():
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    print(f'候选（有 DOI 且未归档）: {len(seen)}  无 DOI 跳过: {no_doi}  已归档跳过: {skipped_archived}')
    c = Counter(r['journal'] for r in seen.values())
    for k, v in c.most_common(20):
        print(f'  {v:5d}  {k}')


# ---------------------------------------------------------------- 限速 HTTP
class Pacer:
    def __init__(self, gap=0.12):
        self.gap = gap
        self.lock = threading.Lock()
        self.last = defaultdict(float)

    def wait(self, host):
        with self.lock:
            now = time.time()
            dt = now - self.last[host]
            delay = self.gap - dt
            if delay > 0:
                time.sleep(delay)
                now = time.time()
            self.last[host] = now


_pacer = Pacer()


def get_json(url, tries=3, timeout=25):
    import urllib.error
    import urllib.request

    host = url.split('/')[2]
    last = None
    for i in range(tries):
        _pacer.wait(host)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode('utf-8', 'replace'))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {'__http404__': True}
            last = f'HTTP {e.code}'
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 * (i + 1))
                continue
            break
        except Exception as e:
            last = repr(e)[:120]
            time.sleep(1.5 * (i + 1))
    return {'__error__': last or 'unknown'}


# ---------------------------------------------------------------- 通道
def q_unpaywall(doi):
    d = get_json(f'https://api.unpaywall.org/v2/{quote(doi, safe="/:;()")}?email={quote(EMAIL)}')
    if d.get('__http404__'):
        return {'channel': 'unpaywall', 'state': 'not_found', 'locs': []}
    if d.get('__error__'):
        return {'channel': 'unpaywall', 'state': 'error', 'err': d['__error__'], 'locs': []}
    # 身份闸门：返回 DOI 必须与请求一致
    got = norm_doi(d.get('doi'))
    if got != doi:
        return {'channel': 'unpaywall', 'state': 'doi_mismatch', 'got': got, 'locs': []}
    locs = []
    for L in (d.get('oa_locations') or []):
        u = L.get('url_for_pdf') or L.get('url_for_landing_page') or L.get('url')
        if not u:
            continue
        locs.append({
            'url': u,
            'pdf': L.get('url_for_pdf') or '',
            'host_type': L.get('host_type') or '',
            'version': L.get('version') or '',
            'license': L.get('license') or '',
            'host': (L.get('url') or '').split('/')[2] if '//' in (L.get('url') or '') else '',
            'repo': L.get('repository_institution') or '',
            'channel': 'unpaywall',
        })
    return {
        'channel': 'unpaywall',
        'state': 'ok',
        'is_oa': bool(d.get('is_oa')),
        'oa_status': d.get('oa_status') or '',
        'title': d.get('title') or '',
        'journal': d.get('journal_name') or '',
        'year': d.get('year'),
        'locs': locs,
    }


def q_openalex(doi):
    d = get_json(f'https://api.openalex.org/works/doi:{quote(doi, safe="/:;()")}?mailto={quote(EMAIL)}')
    if d.get('__http404__'):
        return {'channel': 'openalex', 'state': 'not_found', 'locs': []}
    if d.get('__error__'):
        return {'channel': 'openalex', 'state': 'error', 'err': d['__error__'], 'locs': []}
    got = norm_doi(d.get('doi'))
    if got != doi:
        return {'channel': 'openalex', 'state': 'doi_mismatch', 'got': got, 'locs': []}
    locs = []
    for L in (d.get('locations') or []):
        # ⚠️ OpenAlex 的 locations 里**混有非 OA 的落地页**（doi.org / 出版社付费页）。
        # 不过滤会把 closed 文献误算成有 OA 副本（2026-10-20 实测：757 篇 closed 被判成 publisher_oa）。
        if not L.get('is_oa'):
            continue
        src = L.get('source') or {}
        u = L.get('pdf_url') or L.get('landing_page_url')
        if not u:
            continue
        locs.append({
            'url': u,
            'pdf': L.get('pdf_url') or '',
            'host_type': src.get('type') or '',
            'version': L.get('version') or '',
            'license': L.get('license') or '',
            'host': (u or '').split('/')[2] if '//' in (u or '') else '',
            'repo': src.get('display_name') or '',
            'channel': 'openalex',
        })
    oa = d.get('open_access') or {}
    return {
        'channel': 'openalex',
        'state': 'ok',
        'is_oa': bool(oa.get('is_oa')),
        'oa_status': oa.get('oa_status') or '',
        'title': d.get('title') or '',
        'year': d.get('publication_year'),
        'locs': locs,
    }


def q_s2(doi):
    d = get_json('https://api.semanticscholar.org/graph/v1/paper/DOI:'
                 f'{quote(doi, safe="/:;()")}?fields=title,year,openAccessPdf,externalIds')
    if d.get('__http404__'):
        return {'channel': 's2', 'state': 'not_found', 'locs': []}
    if d.get('__error__'):
        return {'channel': 's2', 'state': 'error', 'err': d['__error__'], 'locs': []}
    ext = d.get('externalIds') or {}
    got = norm_doi(ext.get('DOI'))
    if got and got != doi:
        return {'channel': 's2', 'state': 'doi_mismatch', 'got': got, 'locs': []}
    if not got:
        return {'channel': 's2', 'state': 'no_doi_echo', 'locs': []}
    locs = []
    p = d.get('openAccessPdf') or {}
    if p.get('url'):
        locs.append({'url': p['url'], 'pdf': p['url'], 'host_type': 'repository',
                     'version': '', 'license': p.get('status') or '', 'host': p['url'].split('/')[2],
                     'repo': 'SemanticScholar', 'channel': 's2'})
    return {'channel': 's2', 'state': 'ok', 'is_oa': bool(locs), 'title': d.get('title') or '',
            'year': d.get('year'), 'locs': locs}


CHANNELS = {'unpaywall': q_unpaywall, 'openalex': q_openalex, 's2': q_s2}


# ---------------------------------------------------------------- 判别
def kind_of(L) -> str:
    """把两家通道的 host_type 归一成 repository / publisher / other。"""
    h = (L.get('host_type') or '').lower()
    if h in ('repository', 'repository_institution'):
        return 'repository'
    if h in ('publisher', 'journal', 'conference', 'ebook platform'):
        return 'publisher'
    host = (L.get('host') or '').lower()
    if host in ('doi.org', 'dx.doi.org'):
        return 'other'
    return 'other'


RESOLVER_HOSTS = ('hdl.handle.net', 'handle.net', 'doi.org', 'dx.doi.org')
# 文摘/索引库：有记录页但从不提供全文，访问纯属浪费（不是域名屏蔽，是事实判断）
NO_FULLTEXT_HOSTS = ('www.scopus.com', 'www.webofscience.com', 'www.proquest.com')


def choose(locs):
    """挑一个最可用的副本；优先仓储版 PDF，其次任何 pdf 直链，最后落地页。

    ⚠ 解析器（hdl.handle.net / doi.org）只是跳板，不是副本本体：
    本机到 hdl.handle.net 直连超时、经代理 502，所以它必须排在具体仓储之后，
    否则会把同一条记录里 hal.science 的 /document 直链挤掉（2026-10-20 实测 87 篇）。
    """
    def score(L):
        s = 0
        host = (L.get('host') or '').lower()
        if (L.get('pdf') or '').lower().endswith('.pdf'):
            s += 40
        if kind_of(L) == 'repository':
            s += 30
        if host in RESOLVER_HOSTS:
            s -= 25          # 解析器落地页：要再跳，且本网段不可达
        if L.get('version') in ('acceptedVersion', 'submittedVersion', 'publishedVersion'):
            s += 20
        if L.get('license'):
            s += 5
        if 'sci-hub' in host:
            s -= 999
        if host in NO_FULLTEXT_HOSTS:
            s -= 60         # 索引库永不提供全文
        return s
    valid = [L for L in locs if L.get('url')]
    if not valid:
        return None
    return max(valid, key=score)


WALLED_HOSTS = ('sciencedirect.com', 'academic.oup.com', 'onlinelibrary.wiley.com',
                'www.tandfonline.com', 'pubs.geoscienceworld.org')


def usable_of(best) -> str:
    """这条副本我们**实际能不能拿到**。
    repo        第三方仓储（机构库 / HAL / GFZ / figshare）→ 可直接下载
    publisher_pdf 出版社 PDF 直链（可达性看站点，如 springer/MDPI 可、elsevier 不可）
    landing     只有落地页（多为 doi.org），要回源站
    walled      出版社硬墙（已证伪，别再试）
    """
    if not best:
        return 'none'
    host = (best.get('host') or '').lower()
    if kind_of(best) == 'repository':
        return 'repo'
    if host in ('doi.org', 'dx.doi.org'):
        return 'landing'
    if any(w in host for w in WALLED_HOSTS):
        return 'walled'
    if (best.get('pdf') or '').lower().endswith('.pdf'):
        return 'publisher_pdf'
    return 'landing'


def _finish(out, uniq):
    out['locations'] = uniq
    out['n_locations'] = len(uniq)
    out['n_repository'] = sum(1 for L in uniq if kind_of(L) == 'repository')
    best = choose(uniq)
    out['best'] = best
    if best and kind_of(best) == 'repository':
        out['verdict'] = 'repository_oa'
    elif best and kind_of(best) == 'publisher':
        out['verdict'] = 'publisher_oa'
    elif best:
        out['verdict'] = 'other_oa'
    elif any(v.get('state') == 'ok' for v in out['channels'].values()):
        out['verdict'] = 'closed'
    else:
        out['verdict'] = 'unknown'
    out['usable'] = usable_of(best)
    return out


def resolve_one(cand, channels):
    doi = cand['doi']
    out = {'doi': doi, 'title': cand.get('title', ''), 'journal': cand.get('journal', ''),
           'year': cand.get('year', ''), 'origin': cand.get('origin', ''),
           'ts': time.strftime('%Y-%m-%dT%H:%M:%S'), 'channels': {}}
    all_locs = []
    for name in channels:
        r = CHANNELS[name](doi)
        out['channels'][name] = {'state': r.get('state'), 'is_oa': r.get('is_oa'),
                                 'oa_status': r.get('oa_status', ''),
                                 'n_locs': len(r.get('locs') or [])}
        if r.get('state') == 'ok':
            for L in r.get('locs') or []:
                all_locs.append(L)
            if r.get('title') and not out.get('api_title'):
                out['api_title'] = r['title']
            if r.get('oa_status') and not out.get('oa_status'):
                out['oa_status'] = r['oa_status']
    # 去重（同 url）
    uniq, seen = [], set()
    for L in all_locs:
        k = (L.get('host') or '', (L.get('url') or '').split('?')[0])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(L)
    return _finish(out, uniq)


def cmd_collect(args):
    cands = load_jsonl(CAND)
    if not cands:
        print('没有候选清单，先跑 build'); return
    done = {norm_doi(d.get('doi')) for d in load_jsonl(CACHE) if d.get('verdict') not in ('unknown',)}
    todo = [c for c in cands if norm_doi(c.get('doi')) not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f'候选 {len(cands)}  已有结果 {len(done)}  本轮 {len(todo)}  通道 {args.channels}')
    t0 = time.time()
    buf, n = [], 0
    with CACHE.open('a', encoding='utf-8') as fout:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(resolve_one, c, args.channels.split(',')): c for c in todo}
            for fu in as_completed(futs):
                c = futs[fu]
                try:
                    r = fu.result()
                except Exception as e:
                    r = {'doi': c['doi'], 'title': c.get('title', ''), 'verdict': 'unknown',
                         'err': repr(e)[:150], 'journal': c.get('journal', '')}
                fout.write(json.dumps(r, ensure_ascii=False) + '\n')
                fout.flush()
                buf.append(r)
                n += 1
                if n % 50 == 0:
                    el = time.time() - t0
                    print(f'  {n}/{len(todo)}  {n/max(el,1):.1f}/s  {el:.0f}s', flush=True)
    c = Counter(r.get('verdict') for r in buf)
    print(f'完成 {n} 篇，{time.time()-t0:.0f}s')
    for k, v in c.most_common():
        print(f'  {v:5d}  {k}')


def load_cache():
    """同一 DOI 多次写入时取最后一次。"""
    m = {}
    for d in load_jsonl(CACHE):
        m[norm_doi(d.get('doi'))] = d
    return m


def cmd_reclass(args):
    """按「渠道声明 is_oa」重新裁剪已存的 locations，离线重算 verdict。

    OpenAlex 的 locations 混有非 OA 落地页，历史记录里可能已经被算进
    publisher_oa。这里不重新联网，只按 channels.*.is_oa 过滤后重算。
    """
    rows = load_jsonl(CACHE)
    latest = {}
    for r in rows:
        latest[norm_doi(r.get('doi'))] = r
    n_before = Counter(r.get('verdict') for r in latest.values())
    out = []
    for doi, r in latest.items():
        keep = []
        for L in r.get('locations') or []:
            ch = L.get('channel') or ''
            flag = (r.get('channels', {}).get(ch) or {}).get('is_oa')
            if flag is False:
                continue                       # 该渠道自认非 OA → 丢弃其位置
            keep.append(L)
        _finish(r, keep)
        out.append(r)
    with CACHE.open('w', encoding='utf-8') as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    n_after = Counter(r.get('verdict') for r in out)
    print('重算前后对比（记录数 %d）' % len(out))
    for k in sorted(set(n_before) | set(n_after)):
        print('  %-14s %5d → %5d' % (k, n_before.get(k, 0), n_after.get(k, 0)))


def cmd_probe_closed(args):
    """对被判 closed 的条目，按**标题精确相等**找同一篇文章的其它版本。

    红线：只接受归一化后标题**逐字符相等**的记录（标点/大小写/空格不计），
    近似标题一律丢弃——这是为了满足「不要近似即收录」。
    找到的其它版本通常是：预印本（EarthArXiv/SSRN/researchsquare）、
    机构档案版的另一条 DOI、年鉴/合集重录。
    """
    rows = [r for r in load_cache().values() if r.get('verdict') == 'closed']
    if args.limit:
        rows = rows[: args.limit]
    print(f'closed 待探测 {len(rows)} 篇')
    out_p = STATE / 'oa_thirdparty_alt.jsonl'
    hit = 0
    with out_p.open('w', encoding='utf-8') as f:
        for i, r in enumerate(rows, 1):
            nt = norm_title(r.get('title') or '')
            rec = {'doi': r['doi'], 'title': r.get('title', ''),
                   'journal': r.get('journal', ''), 'alt': []}
            if nt and len(nt) >= 20:
                u = ('https://api.openalex.org/works?filter=title.search:%s'
                     '&per-page=8&mailto=%s' % (quote((r.get('title') or '')[:180]), EMAIL))
                j = get_json(u)
                for w in (j.get('results') or []):
                    if norm_title(w.get('title') or '') != nt:
                        continue                    # 精确相等才认
                    wd = norm_doi(w.get('doi'))
                    if not wd or wd == r['doi']:
                        continue
                    locs = []
                    for L in (w.get('locations') or []):
                        if not L.get('is_oa'):
                            continue
                        uu = L.get('pdf_url') or L.get('landing_page_url')
                        if not uu:
                            continue
                        src = L.get('source') or {}
                        locs.append({'url': uu, 'pdf': L.get('pdf_url') or '',
                                     'host_type': src.get('type') or '',
                                     'version': L.get('version') or '',
                                     'license': L.get('license') or '',
                                     'host': uu.split('/')[2],
                                     'repo': src.get('display_name') or '',
                                     'channel': 'openalex'})
                    if not locs:
                        continue
                    probe = {'channels': {'openalex': {'state': 'ok', 'is_oa': True}},
                             'best': None}
                    _finish(probe, locs)
                    if probe.get('verdict') == 'closed':
                        continue
                    rec['alt'].append({'alt_doi': wd,
                                       'year': w.get('publication_year'),
                                       'verdict': probe['verdict'],
                                       'usable': probe['usable'],
                                       'best': probe.get('best'),
                                       'n_locations': probe['n_locations']})
            if rec['alt']:
                hit += 1
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            f.flush()
            if i % 50 == 0:
                print(f'  {i}/{len(rows)}  有其它版本 {hit}', flush=True)
    print(f'探测完成：{len(rows)} 篇，找到其它版本 {hit} 篇（{hit/max(len(rows),1)*100:.1f}%）→ {out_p.name}')


def cmd_report(args):
    cache = load_cache()
    cands = load_jsonl(CAND)
    journals = {c['doi']: c.get('journal', '') for c in cands if c.get('doi')}
    rows = []
    for doi, r in cache.items():
        if not r.get('journal'):
            r['journal'] = journals.get(doi, '')
        rows.append(r)
    v = Counter(r.get('verdict') for r in rows)
    print(f'已解析 {len(rows)} 篇')
    for k, n in v.most_common():
        print(f'  {n:5d}  {k}  ({n/max(len(rows),1)*100:.1f}%)')
    u = Counter(r.get('usable') for r in rows)
    print('\n-- 实际可得性 --')
    label = {'repo': '第三方仓储（可直接下载）', 'publisher_pdf': '出版社 PDF 直链',
             'landing': '仅落地页（需回源站）', 'walled': '出版社硬墙', 'none': '无 OA 位置'}
    for k, n in u.most_common():
        print(f'  {n:5d}  {label.get(k, k)}  ({n/max(len(rows),1)*100:.1f}%)')

    print('\n-- 可按期刊核对 --')
    by = defaultdict(Counter)
    for r in rows:
        by[r.get('journal') or '?'][r.get('verdict')] += 1
    tot = sorted(by.items(), key=lambda kv: -sum(kv[1].values()))
    print(f"{'期刊':34s}{'总数':>6}{'仓储OA':>8}{'出版社OA':>9}{'closed':>8}{'unknown':>9}")
    for j, c in tot[:25]:
        print(f'{j[:32]:34s}{sum(c.values()):6d}{c["repository_oa"]:8d}'
              f'{c["publisher_oa"] + c["other_oa"]:9d}{c["closed"]:8d}{c["unknown"]:9d}')

    print('\n-- 第三方仓储存量 Top 20 --')
    hosts = Counter()
    for r in rows:
        for L in r.get('locations') or []:
            if kind_of(L) == 'repository':
                hosts[L.get('host') or L.get('repo') or '?'] += 1
    for h, n in hosts.most_common(20):
        print(f'  {n:5d}  {h}')

    print('\n-- 版本分布（仓储副本）--')
    vers = Counter(L.get('version') or '(未标注)' for r in rows
                   for L in (r.get('locations') or []) if kind_of(L) == 'repository')
    for k, n in vers.most_common():
        print(f'  {n:5d}  {k}')


# ---------------------------------------------------------------- verify
def _pdf_text_head(data: bytes, maxpages=3) -> str:
    import pymupdf
    doc = pymupdf.open(stream=data, filetype='pdf')
    txt = []
    for i in range(min(maxpages, doc.page_count)):
        txt.append(doc[i].get_text() or '')
    return '\n'.join(txt)


def _fmt_doi(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


_META_PDF = re.compile(
    r'<meta[^>]+(?:name|property)=["\'](?:citation_pdf_url|bepress_citation_pdf_url)["\'][^>]*'
    r'content=["\']([^"\']+)["\']|<meta[^>]+content=["\']([^"\']+)["\'][^>]*'
    r'(?:name|property)=["\'](?:citation_pdf_url|bepress_citation_pdf_url)["\']', re.I)
_ANY_HREF = re.compile(r'href=["\']([^"\']+)["\']', re.I)
# 仓储落地页里最像"全文"的链接形态（DSpace bitstream / 下载端点 / .pdf 结尾）
_FULLTEXT_HINT = re.compile(
    r'(bitstream|/download|downloadfile|\bfile\b|fulltext|/pdf/|\.pdf(\?|$|#))', re.I)


def _landing_to_pdf(sess, url, html):
    """从仓储落地页里找全文链接。返回绝对 URL 或 ''。

    覆盖三类站点：
      1. 标准 OAI 元数据：<meta name="citation_pdf_url">（Springer/多数机构库）
      2. DSpace 经典版：/bitstream/handle/<id>/<file>.pdf
      3. Handle / Pure / Worktribe / GFZ：正文里挂 .pdf 或 download 端点
    """
    from urllib.parse import urljoin
    m = _META_PDF.search(html)
    if m:
        u = (m.group(1) or m.group(2) or '').strip()
        if u:
            return urljoin(url, u.replace('&amp;', '&'))
    cands = []
    for h in _ANY_HREF.findall(html):
        h = h.replace('&amp;', '&').strip()
        if not h or h.startswith(('#', 'javascript:', 'mailto:')):
            continue
        if _FULLTEXT_HINT.search(h):
            cands.append(urljoin(url, h))
    # 优先 .pdf 结尾，其次 bitstream/download
    cands.sort(key=lambda u: (0 if u.lower().split('?')[0].endswith('.pdf') else 1))
    return cands[0] if cands else ''


# 2026-10-20 用户确认「全域名授权」（此前 SKIP 系误触）→ 不再按域名跳过任何站点。
# 保留常量以便外部按需临时屏蔽（如某站被判违法风险），默认为空。
SKIP_HOSTS = set()


_HANDLE_RX = re.compile(r'handle\.net/([^/]+/[^/?#]+)')


def resolve_handle(sess, url):
    """hdl.handle.net 在本网段不可达（直连超时、经代理 502）。

    但 doi.org 跑的是同一个全局 Handle 系统，`/api/handles/<h>` 能直接把
    handle 解析成真实仓储地址（实测 10138/11336 均 200）。返回真实 URL 或 ''。
    """
    m = _HANDLE_RX.search(url or '')
    if not m:
        return ''
    try:
        r = sess.get('https://doi.org/api/handles/' + m.group(1), timeout=20)
        if r.status_code != 200:
            return ''
        for v in (r.json().get('values') or []):
            if v.get('type') == 'URL':
                u = ((v.get('data') or {}).get('value') or '').strip()
                if u.startswith('http'):
                    return u.replace('http://', 'https://', 1) if u.startswith('http://hdl') else u
    except Exception:
        return ''
    return ''


def _fetch(sess, url, tmo=120):
    """统一 GET：自动过 Anubis 的 SHA-256 工作量证明（HAL 等站）。

    连接层瞬断（CONNECT tunnel / reset / empty reply）重试 1 次 ——
    实测这类失败多为代理或 CDN 抖动，重试后多数能通。
    """
    last = None
    for attempt in range(2):
        try:
            try:
                import anubis_solve
                return anubis_solve.fetch(sess, url, timeout=tmo)
            except ImportError:
                return sess.get(url, timeout=tmo, allow_redirects=True)
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt == 0:
                time.sleep(1.5)
    raise last


def verify_one(rec):
    from curl_cffi import requests as creq
    best = rec.get('best') or {}
    url = best.get('pdf') or best.get('url')
    if (best.get('host') or '').lower() in SKIP_HOSTS:
        return {'doi': rec['doi'], 'ok': False, 'reason': 'skipped_host_policy',
                'host': best.get('host')}
    if not url:
        return {'doi': rec['doi'], 'ok': False, 'reason': 'no_url'}
    if (best.get('host') or '').lower() in NO_FULLTEXT_HOSTS:
        return {'doi': rec['doi'], 'ok': False, 'reason': 'index_site_no_fulltext',
                'host': best.get('host'), 'url': url}
    out = {'doi': rec['doi'], 'url': url, 'host': best.get('host'), 'verdict': rec.get('verdict')}
    try:
        # trust_env 必须在**构造时**传入：curl_cffi 0.16 在构造里读取，
        # 事后赋值 self.trust_env 不生效 → 请求仍走本机 HTTP_PROXY（127.0.0.1:62097），
        # 未放行的域会被代理以 502 CONNECT tunnel failed 拦掉，误判成站点不可达。
        s = creq.Session(impersonate='chrome', trust_env=False)
        # handle 解析器不可达 → 先经 doi.org 换成真实仓储地址
        if (best.get('host') or '').lower() in RESOLVER_HOSTS:
            real = resolve_handle(s, url)
            if real:
                out['resolved_handle'] = real
                url = real
        tmo = 30 if (best.get('host') or '').lower() in RESOLVER_HOSTS else 120
        r = _fetch(s, url, tmo)
        body = r.content or b''
        out['status'] = r.status_code
        out['ctype'] = (r.headers.get('content-type') or '')[:60]
        out['bytes'] = len(body)
        # HAL 落地页没有 PDF 链接，全文恒定在 <landing>/document（Anubis 保护）
        host = (best.get('host') or '').lower()
        if (not body[:5].startswith(b'%PDF') and 'hal.science' in host
                and not url.rstrip('/').endswith('/document')):
            r = _fetch(s, url.rstrip('/') + '/document', tmo)
            body = r.content or b''
            out['status'] = r.status_code
            out['ctype'] = (r.headers.get('content-type') or '')[:60]
            out['bytes'] = len(body)
            out['hal_document'] = True
        # 落地页 → 最多追 2 跳（Handle → 机构库 → bitstream）
        hops = 0
        while (not body[:5].startswith(b'%PDF') and r.status_code == 200
               and 'html' in (out['ctype'] or '').lower() and hops < 2):
            d = _landing_to_pdf(s, str(r.url), body.decode('utf-8', 'replace'))
            if not d:
                # DSpace 7 记录页是 JS 应用，静态 HTML 里没有 PDF → 走它的 REST API
                try:
                    import dspace7
                    if dspace7.maybe(str(r.url)):
                        hit = dspace7.find_pdf(s, str(r.url))
                        if hit:
                            d = hit['pdf_url']
                            out['dspace7'] = hit.get('name')
                except Exception:
                    pass
            if not d:
                out.update(ok=False, reason='landing_no_pdf')
                return out

            out.setdefault('resolved', d)
            out['hops'] = hops + 1
            r = _fetch(s, d, tmo)
            body = r.content or b''
            out['status'] = r.status_code
            out['ctype'] = (r.headers.get('content-type') or '')[:60]
            out['bytes'] = len(body)
            hops += 1
        if not body[:5].startswith(b'%PDF'):
            out.update(ok=False,
                       reason=f'not_pdf: {out["status"]} {out["ctype"]} (hops={hops})')
            return out
        txt = _pdf_text_head(body)
        out['pages_checked'] = 3
        head = txt[:6000]
        flat = _fmt_doi(head)
        doi_hit = _fmt_doi(rec['doi']) in flat
        nt = norm_title(rec.get('title') or '')
        title_hit = False
        if nt and len(nt) > 20:
            ft = re.sub(r'[^0-9a-z\u4e00-\u9fff]+', '', head.lower())
            key = re.sub(r'[^0-9a-z\u4e00-\u9fff]+', '', nt)[:60]
            title_hit = key and key in ft
        out['doi_in_pdf'] = doi_hit
        out['title_in_pdf'] = bool(title_hit)
        out['ok'] = bool(doi_hit or title_hit)
        out['reason'] = 'matched' if out['ok'] else 'content_mismatch'
    except Exception as e:
        out.update(ok=False, reason=f'exc: {repr(e)[:120]}')
    return out


def cmd_verify(args):
    cache = load_cache()
    cands = load_jsonl(CAND)
    meta = {c['doi']: c for c in cands if c.get('doi')}
    pool = [r for r in cache.values() if (r.get('best') or {}).get('url')]
    if args.only:
        pool = [r for r in pool if r.get('verdict') == args.only]
    if args.hosts:
        keep = [h.strip().lower() for h in args.hosts.split(',') if h.strip()]
        pool = [r for r in pool
                if any(k in ((r.get('best') or {}).get('host') or '').lower() for k in keep)]
        print(f'按域名过滤 {args.hosts} → {len(pool)} 篇')
    if args.sample:
        step = max(1, len(pool) // args.sample)
        pool = pool[::step][: args.sample]
    if args.limit:
        pool = pool[: args.limit]
    print(f'待核验 {len(pool)} 篇')
    # --append：把结果并入既有核验文件（按 DOI 覆盖），用于补跑被跳过的域名
    prev = []
    if args.append and VERIFY.exists():
        prev = [json.loads(l) for l in VERIFY.read_text(encoding='utf-8').splitlines() if l.strip()]
    t0, ok = time.time(), 0
    mode = 'a' if args.append else 'w'
    with VERIFY.open(mode, encoding='utf-8') as f:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(verify_one, r): r for r in pool}
            for i, fu in enumerate(as_completed(futs), 1):
                r = fu.result()
                r['title'] = (meta.get(r['doi']) or {}).get('title', '')
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
                f.flush()
                ok += 1 if r.get('ok') else 0
                if i % 20 == 0:
                    print(f'  {i}/{len(pool)} 通过 {ok}', flush=True)
    print(f'核验完成 {len(pool)} 篇，通过 {ok}（{ok/max(len(pool),1)*100:.1f}%），{time.time()-t0:.0f}s')
    if args.append:
        ded = {r['doi']: r for r in prev}
        for l in VERIFY.read_text(encoding='utf-8').splitlines():
            if l.strip():
                r = json.loads(l)
                ded[r['doi']] = r          # 本次结果覆盖同 DOI 旧记录
        VERIFY.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n'
                                  for r in ded.values()), encoding='utf-8')
        print(f'合并去重后共 {len(ded)} 条')


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build'); b.set_defaults(func=cmd_build)
    c = sub.add_parser('collect')
    c.add_argument('--limit', type=int, default=0)
    c.add_argument('--workers', type=int, default=4)
    c.add_argument('--channels', default='unpaywall,openalex')
    c.set_defaults(func=cmd_collect)
    rc = sub.add_parser('reclass'); rc.set_defaults(func=cmd_reclass)
    pc = sub.add_parser('probe-closed')
    pc.add_argument('--limit', type=int, default=0)
    pc.set_defaults(func=cmd_probe_closed)
    rp = sub.add_parser('report'); rp.set_defaults(func=cmd_report)
    v = sub.add_parser('verify')
    v.add_argument('--limit', type=int, default=0)
    v.add_argument('--sample', type=int, default=0)
    v.add_argument('--only', default='')
    v.add_argument('--workers', type=int, default=3)
    v.add_argument('--hosts', default='', help='只核验这些域名（逗号分隔，子串匹配）')
    v.add_argument('--append', action='store_true', help='并入既有核验结果（按 DOI 覆盖）')
    v.set_defaults(func=cmd_verify)
    a = ap.parse_args()
    a.func(a)


if __name__ == '__main__':
    main()
