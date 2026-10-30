# -*- coding: utf-8 -*-
"""摸清 OpenAIRE 渠道的增量价值：对 journals 待抓文章，OpenAIRE 能否给出
Unpaywall/OpenAlex 之外的仓储位置。只读探测，不下载。"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, r'D:/PostNews')
sys.path.insert(0, r'D:/PostNews/.workbuddy')
sys.path.insert(0, r'D:/ZSK/geokb/scripts')


def get(url, timeout=30):
    req = urllib.request.Request(
        url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Edge/131.0'})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=timeout) as r:
            return r.status, r.read()
    except Exception as e:
        return None, str(e).encode()


def _txt(v):
    if isinstance(v, dict):
        return str(v.get('$') or v.get('@classname') or '')
    return str(v) if v is not None else ''


def _aslist(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def from_openaire(doi):
    """OpenAIRE：children.result[].instance.webresource.url"""
    if not doi:
        return []
    st, b = get('https://api.openaire.eu/search/publications?doi=%s&format=json'
                % urllib.parse.quote(doi))
    if st != 200:
        return []
    try:
        j = json.loads(b)
    except Exception:
        return []
    resp = j.get('response') or {}
    results = (resp.get('results') if isinstance(resp, dict) else None) or {}
    r0 = _aslist(results.get('result') if isinstance(results, dict) else None)
    if not r0:
        return []
    md = (r0[0].get('metadata', {}).get('oaf:entity', {})
          .get('oaf:result', {}))
    out = []
    for ch in _aslist(md.get('children', {}).get('result')):
        for inst in _aslist(ch.get('instance') if isinstance(ch, dict) else ch):
            if not isinstance(inst, dict):
                continue
            ar = _txt(inst.get('accessright'))[:24]
            for w in _aslist(inst.get('webresource')):
                u = _txt(w.get('url') if isinstance(w, dict) else w)
                if u.startswith('http'):
                    out.append((u, ar, ''))
    return out


def main():
    import journal_feed
    import journal_pdf_oa as oa

    feed = journal_feed.build_feed(r'D:/PostNews')
    seen = {}
    for g in feed.get('groups') or []:
        for j in g.get('journals') or []:
            for a in j.get('articles') or []:
                u = a.get('url') or ''
                if u and u not in seen:
                    seen[u] = a.get('title', '')

    # 取当前未归档、且现有四渠道搞不定的文章（按源站分层，每站 ≤4 篇）
    import journal_pdf_fetch
    ledger = journal_feed.load_ledger()
    by_src = {}
    for u, t in seen.items():
        m = journal_feed.lookup_ledger(ledger, journal_feed.norm_title(t))
        if m is None:
            by_src.setdefault(journal_pdf_fetch.source_of(u), []).append((u, t))
    print('未归档源站分布:', {k: len(v) for k, v in sorted(by_src.items(), key=lambda x: -len(x[1]))})
    todo = []
    for s, lst in sorted(by_src.items(), key=lambda x: -len(x[1])):
        todo.extend(lst[:4])
    print('分层样本:', len(todo))

    n_oa = n_inc = n_only = 0
    src_stat = {}
    only_samples = []
    for u, t in todo:
        doi = oa.doi_of(u)
        if not doi:
            continue
        import journal_pdf_fetch
        src = journal_pdf_fetch.source_of(u)
        src_stat[src] = src_stat.get(src, 0) + 1
        # 现有渠道候选
        have = {c[1] for c in oa.collect_candidates(t, doi, u)}
        # OpenAIRE 候选
        try:
            oa_cands = from_openaire(doi)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print('  exc', doi, type(e).__name__, e)
            continue
        oa_urls = {c[0] for c in oa_cands}
        inc = oa_urls - have
        if inc:
            n_inc += 1
            if not have:
                n_only += 1
            only_samples.append((doi, t[:40], list(inc)[:3]))
        if oa_urls:
            n_oa += 1
        time.sleep(1.2)
    print('OpenAIRE 有位置: %d/%d · 相对现有渠道有增量: %d · 现有无而 OpenAIRE 独有: %d'
          % (n_oa, sum(src_stat.values()), n_inc, n_only))
    print('样本源站分布:', src_stat)
    for doi, t, inc in only_samples[:6]:
        print('  +', doi, '|', t)
        for u in inc:
            print('      ', u[:100])


if __name__ == '__main__':
    main()
