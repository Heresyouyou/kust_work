# -*- coding: utf-8 -*-
"""测 Europe PMC 渠道对 journals 待抓文章的增量。只读探测。"""
import json
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, r'D:/PostNews')
sys.path.insert(0, r'D:/PostNews/.workbuddy')


def get(url, timeout=30):
    req = urllib.request.Request(
        url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Edge/131.0'})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=timeout) as r:
            return r.status, r.read()
    except Exception:
        return None, b''


def from_europepmc(doi):
    """Europe PMC：fullTextUrlList 里的 pdf 位置。"""
    if not doi:
        return []
    st, b = get('https://www.ebi.ac.uk/europepmc/webservices/rest/search'
                '?query=DOI:%22' + urllib.parse.quote(doi) + '%22'
                + '&resultType=core&format=json')
    if st != 200:
        return []
    try:
        j = json.loads(b)
    except Exception:
        return []
    out = []
    for r in ((j.get('resultList') or {}).get('result') or []):
        for u in ((r.get('fullTextUrlList') or {}).get('fullTextUrl') or []):
            url = u.get('url') or ''
            style = (u.get('documentStyle') or '').lower()
            if url:
                out.append((url, style, u.get('site') or ''))
    return out


def main():
    import journal_feed
    import journal_pdf_oa as oa
    import journal_pdf_fetch

    feed = journal_feed.build_feed(r'D:/PostNews')
    seen = {}
    for g in feed.get('groups') or []:
        for j in g.get('journals') or []:
            for a in j.get('articles') or []:
                u = a.get('url') or ''
                if u and u not in seen:
                    seen[u] = a.get('title', '')

    ledger = journal_feed.load_ledger()
    by_src = {}
    for u, t in seen.items():
        if journal_feed.lookup_ledger(ledger, journal_feed.norm_title(t)) is None:
            by_src.setdefault(journal_pdf_fetch.source_of(u), []).append((u, t))

    todo = []
    for s, lst in sorted(by_src.items(), key=lambda x: -len(x[1])):
        todo.extend([(s, u, t) for u, t in lst[:6]])

    n_hit = n_pdf = n_inc = 0
    for s, u, t in todo:
        doi = oa.doi_of(u)
        if not doi:
            continue
        try:
            cands = from_europepmc(doi)
        except Exception:
            continue
        have = {c[1] for c in oa.collect_candidates(t, doi, u)}
        new = [c for c in cands if c[0] not in have]
        if cands:
            n_hit += 1
        pdfs = [c for c in cands if c[1] == 'pdf']
        if pdfs:
            n_pdf += 1
        if new:
            n_inc += 1
            print('  + [%s] %s | %s' % (s, doi, t[:44]))
            for c in new[:3]:
                print('       %s %s' % (c[1], c[0][:95]))
        time.sleep(1.0)
    print('Europe PMC: 有记录 %d/%d · 有 pdf 位置 %d · 相对现有渠道增量 %d'
          % (n_hit, len(todo), n_pdf, n_inc))


if __name__ == '__main__':
    main()
