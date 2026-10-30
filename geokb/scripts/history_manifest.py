# -*- coding: utf-8 -*-
"""近 5 年目标期刊「待爬取」清单生成器（一次性快照）

做的事
------
1. 枚举 14 种目标期刊近 5 年（2021-10-19 ~ 今天）**全部**论文题录：
   · 国际 8 刊 + 2 本有 ISSN 的中文刊 → CrossRef cursor 分页（精确日期）
   · 矿床地质 / 地学前缘 / 地质学报 / 大地构造与成矿学 → 官网按期卷枚举
     （这 4 本 CrossRef 未登记或指向英文版，实测官网可枚举）
2. 用 `journal_filter.calc_relevance_score` 按团队方向（三江/西南三江区域 +
   Cu-Mo-Sn-W-Au-PbZn 矿种 + 斑岩/矽卡岩/热液矿床类型 + 成矿理论与找矿预测）
   打分，保留 score >= MIN_SCORE。
3. 剔除「已经下载过 / 已入库」的重复论文（比对台账、papers 词条、NAS 实际文件）。
4. 输出 **一份**待办清单（后续逐条消费，不再重复生成）：
   · state/history_manifest.jsonl   机器可读，一行一篇，消费后物理移除
   · state/history_manifest.html    人可读可搜索（浏览器直接打开）
   · state/history_manifest_stats.json  统计快照

用法
----
    python history_manifest.py                    # 近 5 年，全量重建
    python history_manifest.py --years 5 --min-score 6
    python history_manifest.py --dry-run          # 只统计不写文件
    python history_manifest.py --only crossref    # 只跑某一层（调试）

注意
----
- 这是**一次性**清单：脚本会覆盖写 history_manifest.jsonl。日常增长的新论文
  由既有推送管线（journal_auto_push / daily_run）处理，与本清单无关。
- 已完成的消费记录在 state/history_done.jsonl（append-only，留痕用）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib3
from datetime import datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, 'D:/PostNews')
sys.path.insert(0, str(Path(__file__).resolve().parent))
from journal_filter import calc_relevance_score, is_valid_paper, title_key  # noqa: E402
from sd_policy import is_sd  # noqa: E402  ScienceDirect 判据（单一口径）

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = Path('D:/ZSK/geokb')
STATE = BASE / 'state'
MANIFEST = STATE / 'history_manifest.jsonl'
DONE_FILE = STATE / 'history_done.jsonl'
STATS = STATE / 'history_manifest_stats.json'
HTML_OUT = STATE / 'history_manifest.html'

LEDGER = STATE / 'papers_index.jsonl'
PAPERS_DIR = BASE / 'content' / 'papers'
REPO = Path('S:/GeoKB/papers_repo')

MAILTO = 'kust-research@example.org'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36')
CR_H = {'User-Agent': 'GeoKB-Manifest/1.0 (mailto:%s)' % MAILTO,
        'Accept-Encoding': 'identity'}
WEB_H = {'User-Agent': UA, 'Accept-Language': 'zh-CN,zh;q=0.9'}

MIN_SCORE = 6          # 与 journal_filter.STRONG_RELEVANCE_MIN_SCORE 一致
THROTTLE_CR = 0.35
THROTTLE_WEB = 1.1

# ── 期刊定义 ────────────────────────────────────────────────────────────
# (清单显示名, 源类型, 标识)
CROSSREF_JOURNALS = [
    ('Earth and Planetary Science Letters', '0012-821X'),
    ('Geology', '0091-7613'),
    ('Economic Geology', '0361-0128'),
    ('Mineralium Deposita', '0026-4598'),
    ('Ore Geology Reviews', '0169-1368'),
    ('Journal of Petrology', '0022-3530'),
    ('Lithos', '0024-4937'),
    ('American Mineralogist', '0003-004X'),
    ('地球科学', '1000-2383'),
    ('矿物岩石地球化学通报', '1007-2802'),
]
CN_SITE_JOURNALS = ['矿床地质', '地学前缘', '地质学报', '大地构造与成矿学']


# ── 工具 ────────────────────────────────────────────────────────────────
def norm_title(s):
    """标题指纹：去非字母数字 + 小写（与 journal_filter.title_key 同源）"""
    return re.sub(r'\W+', '', (s or '').lower())[:100]


def norm_doi(s):
    s = (s or '').strip().lower()
    s = re.sub(r'^https?://(dx\.)?doi\.org/', '', s)
    return s


def _req(url, params=None, headers=None, timeout=20, tries=2):
    for k in range(tries):
        try:
            r = requests.get(url, params=params, headers=headers or WEB_H,
                             timeout=timeout, verify=False)
            if r.status_code == 200:
                return r
            if r.status_code in (429, 503):
                time.sleep(3 * (k + 1))
                continue
            return r
        except Exception:
            if k + 1 < tries:
                time.sleep(1.5)
    return None


def clean_title(t):
    import html as _h
    t = re.sub(r'</?su[bp]>', '', t or '')
    for _ in range(2):
        t = _h.unescape(t)
    t = re.sub(r'<[^>]+>', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    # 中文刊官网列表常见两种脏标题（2026-10-27 实测地质学报）：
    #   ① 作者前缀「陈衍景等：」② 附件后缀「……——附件」
    # 附件条在清单里**没有对应的正文条**（实测 6/6 查不到），所以只清标题、不能剔除，
    # 否则这些论文会整体从清单里消失。
    t = re.sub(r'^[\u4e00-\u9fff]{2,4}等\s*[：:]\s*', '', t)
    t = re.sub(r'\s*[-—–]{1,2}\s*附\s*件\s*$', '', t)
    return t.strip()


# ── ① CrossRef ─────────────────────────────────────────────────────────
def fetch_crossref(issn, frm, until, cap=4000):
    url = 'https://api.crossref.org/journals/%s/works' % issn
    params = {
        'filter': 'from-pub-date:%s,until-pub-date:%s,type:journal-article' % (frm, until),
        'rows': '200',
        'select': 'DOI,title,published,issued,container-title,URL,volume,issue',
        'mailto': MAILTO,
        'cursor': '*',
    }
    out, guard = [], 0
    while len(out) < cap and guard < 40:
        guard += 1
        r = _req(url, params=params, headers=CR_H, timeout=45)
        if r is None or r.status_code != 200:
            print('    [warn] crossref %s HTTP %s' % (issn, getattr(r, 'status_code', 'ERR')))
            break
        try:
            msg = r.json().get('message', {})
        except Exception:
            break
        items = msg.get('items') or []
        if not items:
            break
        for it in items:
            titles = it.get('title') or []
            if not titles:
                continue
            title = clean_title(titles[0])
            if not title or len(title) < 8:
                continue
            parts = ((it.get('published') or it.get('issued') or {}).get('date-parts') or [[None]])[0]
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
        time.sleep(THROTTLE_CR)
    return out


# ── ② 中文刊官网枚举 ───────────────────────────────────────────────────
def fetch_kcdz(years):
    """矿床地质：issue_browser → issue_list?year_id&quarter_id → view_abstract"""
    base = 'https://www.kcdz.ac.cn/kcdz/ch/reader/'
    out, seen = [], set()
    for y in years:
        for q in range(1, 7):
            r = _req(base + 'issue_list.aspx', params={'year_id': y, 'quarter_id': q})
            if r is None or r.status_code != 200:
                continue
            r.encoding = 'utf-8'
            txt = r.text
            if 'view_abstract' not in txt:
                continue
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(txt, 'html.parser')
            for a in soup.find_all('a', href=re.compile(r'view_abstract\.aspx\?file_no=')):
                t = clean_title(a.get_text(strip=True))
                if not t or len(t) < 8 or t in seen:
                    continue
                m = re.search(r'file_no=(\d{4})(\d{2})(\d{2})', a.get('href') or '')
                if not m:
                    continue
                seen.add(t)
                out.append({
                    'title': t,
                    'url': base + 'view_abstract.aspx?file_no=%s&flag=1' % m.group(0)[8:],
                    'doi': '',
                    'date': '%s-%02d-01' % (m.group(1), int(m.group(2))),
                    'volume': '', 'issue': str(int(m.group(2))),
                })
            time.sleep(THROTTLE_WEB)
    return out


def fetch_esf(years):
    """地学前缘：/CN/Y{year}/V{vol}/I{issue}（2023 = V30 → vol = year - 1993）"""
    from bs4 import BeautifulSoup
    base = 'https://www.earthsciencefrontiers.net.cn'
    out, seen = [], set()
    for y in years:
        vol = y - 1993
        for i in range(1, 7):
            u = '%s/CN/Y%d/V%d/I%d' % (base, y, vol, i)
            r = _req(u)
            if r is None or r.status_code != 200:
                continue
            r.encoding = 'utf-8'
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.select('a.txt_biaoti'):
                t = clean_title(a.get_text(strip=True))
                href = (a.get('href') or '').strip()
                if not t or len(t) < 8 or t in seen:
                    continue
                # ⚠️ 不能只收 `abstract` 形式的链接：该刊大量条目是 DOI 直链
                # （/CN/10.13745/j.esf...），按 abstract 过滤会把整期论文刷掉
                # （2026-10-18 实测：5 年只解析出 17 篇，实际每期 24-40 篇）。
                if not href or href.startswith('#') or href.lower().startswith('javascript'):
                    continue
                if any(k in t for k in ('目次', '封面', '编委会', '征稿', '启事', '导读')):
                    continue
                seen.add(t)
                out.append({
                    'title': t,
                    'url': href if href.startswith('http') else base + href,
                    'doi': '', 'date': '%d-%02d-01' % (y, i),
                    'volume': str(vol), 'issue': str(i),
                })
            time.sleep(THROTTLE_WEB)
    return out


def fetch_dzxb(years):
    """地质学报：/dzxb/article/issue/{year}_{vol}_{issue}（2024 = V98 → vol = year - 1926）"""
    from bs4 import BeautifulSoup
    base = 'https://www.geojournals.cn/dzxb/'
    out, seen = [], set()
    for y in years:
        vol = y - 1926
        for i in range(1, 13):
            u = '%sdzxb/article/issue/%d_%d_%d' % (base, y, vol, i)
            r = _req(u)
            if r is None or r.status_code != 200:
                continue
            r.encoding = 'utf-8'
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.find_all('a', href=re.compile(r'article/abstract/')):
                t = clean_title(a.get_text(strip=True))
                href = (a.get('href') or '').strip()
                if not t or len(t) < 8 or t in seen:
                    continue
                if any(k in t for k in ('目次', '封面', '编委会', '征稿', '启事', '导读')):
                    continue
                seen.add(t)
                if href.startswith('http'):
                    full = href
                elif href.startswith('/'):
                    full = 'https://www.geojournals.cn' + href
                else:
                    full = base + href
                out.append({'title': t, 'url': full, 'doi': '',
                            'date': '%d-%02d-01' % (y, i),
                            'volume': str(vol), 'issue': str(i)})
            time.sleep(THROTTLE_WEB)
    return out


def fetch_dgyk(years):
    """大地构造与成矿学：需 ASP.NET 会话；i 必须是两位（'05' 而非 5）"""
    from bs4 import BeautifulSoup
    s = requests.Session()
    s.headers.update(WEB_H)
    root = 'https://dgyk.cbpt.cnki.net/WKE2/WebPublication/'
    try:
        s.get(root + 'index.aspx?mid=dgyk', timeout=40, verify=False)
    except Exception:
        pass
    out, seen = [], set()
    for y in years:
        for i in range(1, 7):
            try:
                r = s.get(root + 'getThisIssuePaperInfo.ashx',
                          params={'y': y, 'i': '%02d' % i},
                          headers={'X-Requested-With': 'XMLHttpRequest',
                                   'Referer': root + 'index.aspx?mid=dgyk'},
                          timeout=40, verify=False)
                r.encoding = 'utf-8'
                if r.status_code != 200 or 'paperDigest' not in r.text:
                    continue
                soup = BeautifulSoup(r.text, 'html.parser')
                for a in soup.find_all('a', href=re.compile(r'paperDigest\.aspx\?paperID=')):
                    t = clean_title(a.get_text(strip=True))
                    if not t or len(t) < 8 or t in seen:
                        continue
                    m = re.search(r'paperID=([0-9a-zA-Z\-]+)', a.get('href') or '')
                    if not m:
                        continue
                    seen.add(t)
                    out.append({'title': t,
                                'url': root + 'paperDigest.aspx?paperID=' + m.group(1),
                                'doi': '', 'date': '%d-%02d-01' % (y, i),
                                'volume': '', 'issue': str(i)})
            except Exception:
                continue
            time.sleep(THROTTLE_WEB)
    return out


# ── ③ 去重索引（已下载 / 已入库）────────────────────────────────────────
def _has_pdf(meta):
    """台账条目是否真的对应 NAS 上的 PDF。

    ⚠️ 不能用 `'papers_repo/' in repo_path` 直接判：台账里混着两种写法
    `S:/GeoKB/papers_repo\\by_journal\\...`（正斜杠开头 + 反斜杠续）与
    `S:\\GeoKB\\papers_repo\\...`。必须先把分隔符归一，否则会把 988 篇
    已下载误判成"仅题录"，进而重复下载（2026-10-18 实测踩到）。
    """
    p = (meta.get('repo_path') or '').replace('\\', '/')
    return 'papers_repo/' in p and p.lower().endswith('.pdf')


# ── ③ 源站可达性预判（决定爬取顺序：先易后难，让清单能持续缩短）──────────
# 依据 2026-10-18 实测：elsevier 四渠道全 403 无仓储副本；OUP/GSA 有 Cloudflare
# 但偶有 OA；Springer 部分 OA；中文刊官网基本直下。
REACH_RULES = [
    # 大地构造与成矿学：CNKI 镜像要验证码、paperonce 域名被 TLS 重置
    # （2026-10-18 实测 ConnectionReset），两条路都断 → 压到最低优先级，
    # 避免占着夜间配额空转。等源站恢复或换通道后再提上来。
    (('cbpt.cnki.net',), 'cnki', 0.12),
    (('kcdz.ac.cn', 'earthsciencefrontiers', 'geojournals.cn',
      'earth-science.net', 'doi.org/10.3799', 'doi.org/10.19700'), 'cn_site', 0.92),
    (('doi.org/10.1007', 'springer', 'link.springer'), 'springer', 0.65),
    (('doi.org/10.5382', 'segweb'), 'seg', 0.45),
    (('doi.org/10.1130', 'doi.org/10.2113', 'doi.org/10.2138', 'geoscienceworld'), 'gsw', 0.50),
    (('doi.org/10.1093', 'academic.oup'), 'oup', 0.40),
    (('doi.org/10.1016', 'sciencedirect'), 'elsevier', 0.25),
]


def reach_of(url):
    u = (url or '').lower()
    for keys, tag, p in REACH_RULES:
        for k in keys:
            if k in u:
                return tag, p
    return 'other', 0.60


def build_dedup():
    """返回 (titles, dois, urls, stats)。判据与 journals 页一致：有 PDF 才算已下载。"""
    import yaml
    titles, dois, urls = set(), set(), set()
    n_pdf = n_meta = 0

    if LEDGER.exists():
        for line in LEDGER.read_text(encoding='utf-8', errors='ignore').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except Exception:
                continue
            has_pdf = _has_pdf(m)
            if not has_pdf:
                n_meta += 1
                continue
            n_pdf += 1
            if m.get('title'):
                titles.add(norm_title(m['title']))
            if m.get('doi'):
                dois.add(norm_doi(m['doi']))
            if m.get('url'):
                urls.add(m['url'].strip().lower())

    # papers 词条（有 eid 即已入库，即便台账行缺失）
    n_papers = 0
    if PAPERS_DIR.exists():
        for p in PAPERS_DIR.glob('*.md'):
            try:
                raw = p.read_text(encoding='utf-8', errors='ignore')[:3000]
                m = re.match(r'^---\n(.*?)\n---', raw, re.S)
                if not m:
                    continue
                fm = yaml.safe_load(m.group(1)) or {}
                t = fm.get('title') or ''
                if t:
                    titles.add(norm_title(t))
                    n_papers += 1
            except Exception:
                continue

    return titles, dois, urls, {'ledger_pdf': n_pdf, 'ledger_meta_only': n_meta,
                                'papers_entries': n_papers}


def is_duplicate(rec, titles, dois, urls):
    nk = norm_title(rec['title'])
    if nk:
        if nk in titles:
            return True
        # 前缀容错（不同源截断长度不同，≥48 字符才放宽）
        for t in titles:
            n = min(len(nk), len(t))
            if n >= 48 and (nk[:n] == t[:n]):
                return True
    d = norm_doi(rec.get('doi'))
    if d and d in dois:
        return True
    u = (rec.get('url') or '').strip().lower()
    if u and u in urls:
        return True
    return False


# ── ④ 主流程 ───────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--years', type=int, default=5)
    ap.add_argument('--min-score', type=int, default=MIN_SCORE)
    ap.add_argument('--only', default='', help='crossref / site（调试用）')
    ap.add_argument('--journal', default='', help='只跑指定期刊（显示名，调试用）')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    until = datetime.now().date()
    frm = until - timedelta(days=365 * args.years)
    years = list(range(frm.year, until.year + 1))
    print('时间窗 %s ~ %s | 期刊年 %s | 阈值 score>=%d'
          % (frm, until, years, args.min_score), flush=True)

    raw = []
    if args.only in ('', 'crossref'):
        for name, issn in CROSSREF_JOURNALS:
            if args.journal and args.journal not in name:
                continue
            t0 = time.time()
            items = fetch_crossref(issn, frm.isoformat(), until.isoformat())
            print('  [CrossRef] %-42s %4d 篇  %.0fs' % (name, len(items), time.time() - t0), flush=True)
            for it in items:
                it['journal'] = name
                it['source'] = 'crossref'
            raw.extend(items)

    if args.only in ('', 'site'):
        site_fns = [('矿床地质', fetch_kcdz), ('地学前缘', fetch_esf),
                    ('地质学报', fetch_dzxb), ('大地构造与成矿学', fetch_dgyk)]
        for name, fn in site_fns:
            if args.journal and args.journal not in name:
                continue
            t0 = time.time()
            try:
                items = fn(years)
            except Exception as e:
                print('  [官网] %-10s 失败 %s: %s' % (name, type(e).__name__, str(e)[:70]), flush=True)
                items = []
            print('  [官网] %-38s %4d 篇  %.0fs' % (name, len(items), time.time() - t0), flush=True)
            for it in items:
                it['journal'] = name
                it['source'] = 'site'
            raw.extend(items)

    # 时间窗二次过滤（官网只能精确到月）
    out_of_window = 0
    kept = []
    for r in raw:
        d = r.get('date') or ''
        if d and d[:7] < frm.isoformat()[:7]:
            out_of_window += 1
            continue
        kept.append(r)
    raw = kept
    print('原始题录 %d 条（时间窗外剔除 %d）' % (len(raw), out_of_window), flush=True)

    # 相关度筛选
    scored, excluded_invalid, low_score = [], 0, 0
    for r in raw:
        if not is_valid_paper(r['title']):
            excluded_invalid += 1
            continue
        sc, kws = calc_relevance_score(r['title'])
        if sc < args.min_score:
            low_score += 1
            continue
        r['score'] = sc
        r['matched'] = kws[:8]
        scored.append(r)
    print('相关度筛后 %d 条（非论文 %d / 低分 %d）'
          % (len(scored), excluded_invalid, low_score), flush=True)

    # 去重
    titles, dois, urls, dstats = build_dedup()
    print('去重索引：台账有PDF %d / 仅题录 %d / papers词条 %d'
          % (dstats['ledger_pdf'], dstats['ledger_meta_only'], dstats['papers_entries']), flush=True)

    todo, dup, seen_keys = [], 0, set()
    for r in scored:
        nk = norm_title(r['title'])
        if nk in seen_keys:              # 同一次枚举内的重复（跨源/分期）
            continue
        seen_keys.add(nk)
        if is_duplicate(r, titles, dois, urls):
            dup += 1
            continue
        rid = 'hm-' + hashlib.sha1((norm_doi(r.get('doi')) or nk).encode('utf-8')).hexdigest()[:12]
        reach, reach_p = reach_of(r['url'])
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
            'source': r['source'],
            'reach': reach,
            'reach_p': reach_p,
            'sd': is_sd(r.get('doi') or '', r['url'], r['journal']),
            'status': 'pending',
            'attempts': 0,
            'last_error': '',
            'added_at': datetime.now().isoformat(timespec='seconds'),
        })
    print('已下载/已入库剔除 %d 条 → 待爬取 %d 条' % (dup, len(todo)), flush=True)

    # 排序：**先易后难（源站可达性）→ 同组高分优先 → 新文优先**
    # 但 ScienceDirect 一律沉到最后（2026-10-20）：SD 的 /pdf /pdfft 被 CF
    # 内容防护 1000S 硬拦，重试只会加深 IP 标记，不能让它在前排吃掉夜间配额。
    # 稳定分区保证组内仍是「易→难、高分→低分」的顺序。
    todo.sort(key=lambda x: (-x['reach_p'], -x['score'], x['date'] or '', x['journal']))
    _non_sd = [x for x in todo if not x.get('sd')]
    _sd = [x for x in todo if x.get('sd')]
    todo = _non_sd + _sd
    print('其中 ScienceDirect %d 条 → 已沉到清单末尾（暂停期不消费）' % len(_sd), flush=True)

    stats = {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'window': [frm.isoformat(), until.isoformat()],
        'years': years,
        'min_score': args.min_score,
        'raw': len(raw),
        'after_score': len(scored),
        'dup_removed': dup,
        'todo': len(todo),
        'excluded_invalid': excluded_invalid,
        'low_score': low_score,
        'dedup_index': dstats,
        'by_journal': {},
        'by_year': {},
        'by_score': {},
        'by_reach': {},
        'sd_todo': len(_sd),
    }
    for x in todo:
        stats['by_journal'][x['journal']] = stats['by_journal'].get(x['journal'], 0) + 1
        stats['by_year'][x['year'] or '?'] = stats['by_year'].get(x['year'] or '?', 0) + 1
        b = '9+' if x['score'] >= 9 else ('7-8' if x['score'] >= 7 else '6')
        stats['by_score'][b] = stats['by_score'].get(b, 0) + 1
        stats['by_reach'][x['reach']] = stats['by_reach'].get(x['reach'], 0) + 1

    print('\n按期刊：')
    for k, v in sorted(stats['by_journal'].items(), key=lambda kv: -kv[1]):
        print('  %-42s %d' % (k, v))
    print('按年份：', dict(sorted(stats['by_year'].items())))
    print('按分数：', stats['by_score'])
    print('按源站可达性：', stats['by_reach'])

    if args.dry_run:
        print('\n[dry-run] 未写文件')
        return 0

    STATE.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        for x in todo:
            f.write(json.dumps(x, ensure_ascii=False) + '\n')
    os.replace(tmp, MANIFEST)
    STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding='utf-8')
    write_html(todo, stats)
    print('\n已写：%s' % MANIFEST)
    print('已写：%s' % STATS)
    print('已写：%s' % HTML_OUT)
    return 0


# ── ⑤ 人读清单（静态 HTML，可搜索/筛选）────────────────────────────────
def write_html(items, stats, out=None, heading=None):
    """人读清单（静态 HTML，可搜索/筛选）。

    out      : 输出路径（默认 state/history_manifest.html）
    heading  : 页面标题（默认「近 5 年待爬取文献清单」）
    items 带 `tier` 字段时自动多出一列「层级」（core/ext），供 10 年增量清单区分。
    """
    REACH_CN = {'cn_site': '中文刊官网', 'springer': 'Springer', 'gsw': 'GSA/GSW',
                'seg': 'Econ Geol', 'oup': 'OUP', 'elsevier': 'Elsevier', 'other': '其他'}
    TIER_CN = {'core': '核心', 'ext': '扩展'}
    out_path = Path(out) if out else HTML_OUT
    heading = heading or '近 5 年待爬取文献清单'
    has_tier = any('tier' in x for x in items)
    rows = []
    for x in items:
        _sd = bool(x.get('sd')) if 'sd' in x else is_sd(
            x.get('doi', ''), x.get('url', ''), x['journal'])
        _reach = REACH_CN.get(x.get('reach', ''), x.get('reach', ''))
        if _sd:
            _reach += '·SD暂停'
        _tier = ''
        if has_tier:
            _t = x.get('tier', '')
            _tier = '<td class="tr t-%s">%s</td>' % (_t, TIER_CN.get(_t, _t))
        rows.append(
            '<tr data-j="%s" data-y="%s" data-s="%s" data-r="%s" data-t="%s"%s>'
            '<td class="j">%s</td><td class="y">%s</td>'
            '<td class="t"><a href="%s" target="_blank" rel="noopener">%s</a></td>'
            '%s<td class="sc">%d</td><td class="r">%s</td><td class="kw">%s</td></tr>'
            % (x['journal'], x['year'], x['score'], x.get('reach', ''), x.get('tier', ''),
               ' data-sd="1"' if _sd else '',
               x['journal'], x['year'],
               x['url'].replace('"', '%22'), x['title'].replace('<', '&lt;'),
               _tier, x['score'], _reach,
               '、'.join(x['matched'][:5])))
    jopts = ''.join('<option>%s</option>' % j for j in sorted(stats['by_journal']))
    ropts = ''.join('<option value="%s">%s</option>' % (k, REACH_CN.get(k, k))
                    for k in sorted(stats.get('by_reach', {}),
                                    key=lambda k: -stats['by_reach'][k]))
    topts = ''
    th_tier = ''
    if has_tier:
        topts = ''.join('<option value="%s">%s</option>' % (k, TIER_CN[k])
                        for k in ('core', 'ext') if stats.get('by_tier', {}).get(k))
        th_tier = '<th>层级</th>'
    html = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>@@HEADING@@</title>
<style>
:root{--bg:#fbfbf9;--fg:#1f2328;--mut:#6b7280;--line:#e5e7eb;--acc:#2e7d4f;--card:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--fg:#e6e8eb;--mut:#9aa3ad;--line:#2b2f34;--acc:#4a9e6a;--card:#1c1f23}}
*{box-sizing:border-box}body{margin:0;padding:24px 28px;background:var(--bg);color:var(--fg);
font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
h1{font-size:20px;margin:0 0 4px}.sub{color:var(--mut);font-size:13px;margin-bottom:16px}
.bar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px;align-items:center}
input,select{padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);
color:var(--fg);font-size:13px}input{min-width:260px}
#cnt{color:var(--acc);font-weight:600}
table{border-collapse:collapse;width:100%;background:var(--card);border-radius:10px;overflow:hidden}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{position:sticky;top:0;background:var(--card);font-size:12px;color:var(--mut);font-weight:600}
td.j,td.y{white-space:nowrap;font-size:12px;color:var(--mut)}
td.t a{color:var(--fg);text-decoration:none}td.t a:hover{color:var(--acc);text-decoration:underline}
td.sc{font-weight:700;color:var(--acc);text-align:center}
td.tr{white-space:nowrap;font-size:12px;text-align:center}
td.tr.t-ext{color:#b45309}td.tr.t-core{color:var(--acc);font-weight:600}
td.kw{font-size:12px;color:var(--mut);max-width:280px}
tr:hover td{background:rgba(74,158,106,.06)}
</style></head><body>
<h1>@@HEADING@@</h1>
<div class="sub">窗口 @@W0@@ ~ @@W1@@ ｜ 已剔除已下载/已入库/非正刊稿 ｜ 生成于 @@AT@@</div>
<div class="bar">
  <input id="q" placeholder="搜索标题 / 期刊…">
  <select id="jf"><option value="">全部期刊（@@NJ@@）</option>@@JOPT@@</select>
  <select id="yf"><option value="">全部年份</option>@@YOPT@@</select>
  <select id="rf"><option value="">全部源站</option>@@ROPT@@</select>
  @@TF@@
  <span>命中 <span id="cnt">@@N@@</span> 条</span>
</div>
<table><thead><tr><th>期刊</th><th>年</th><th>标题</th>@@THT@@<th>分</th><th>源站</th><th>命中关键词</th></tr></thead>
<tbody id="tb">@@ROWS@@</tbody></table>
<script>
var q=document.getElementById('q'),jf=document.getElementById('jf'),yf=document.getElementById('yf'),
rf=document.getElementById('rf'),tf=document.getElementById('tf'),cnt=document.getElementById('cnt'),
rows=[].slice.call(document.querySelectorAll('#tb tr'));
function apply(){var s=q.value.trim().toLowerCase(),j=jf.value,y=yf.value,r=rf.value,n=0,
t=tf?tf.value:'';
rows.forEach(function(o){var ok=(!s||o.textContent.toLowerCase().indexOf(s)>=0)
&&(!j||o.dataset.j===j)&&(!y||o.dataset.y===y)&&(!r||o.dataset.r===r)
&&(!t||o.dataset.t===t);o.style.display=ok?'':'none';if(ok)n++;});
cnt.textContent=n;}
q.addEventListener('input',apply);jf.addEventListener('change',apply);
yf.addEventListener('change',apply);rf.addEventListener('change',apply);
if(tf)tf.addEventListener('change',apply);
</script></body></html>"""
    # ⚠️ 不能用 `%` 格式化：模板里 CSS 的 `width:100%;` 会被当成格式符
    # （2026-10-18 报 "unsupported format character ';'"）。一律用占位符替换。
    reps = {
        '@@HEADING@@': heading,
        '@@W0@@': stats['window'][0],
        '@@W1@@': stats['window'][1],
        '@@AT@@': stats['generated_at'],
        '@@NJ@@': str(len(stats['by_journal'])),
        '@@JOPT@@': jopts,
        '@@YOPT@@': ''.join('<option>%s</option>' % y
                            for y in sorted(stats['by_year'], reverse=True)),
        '@@ROPT@@': ropts,
        '@@TF@@': ('<select id="tf"><option value="">全部层级</option>%s</select>' % topts
                   if topts else ''),
        '@@THT@@': th_tier,
        '@@N@@': str(len(items)),
        '@@ROWS@@': '\n'.join(rows),
    }
    for k, v in reps.items():
        html = html.replace(k, v)
    out_path.write_text(html, encoding='utf-8')


if __name__ == '__main__':
    sys.exit(main())
