# -*- coding: utf-8 -*-
"""用修好的 collect_candidates 重新评估各源站 OA 潜力。

2026-10-18 背景：修复「首渠即停」（Unpaywall 只给墙域名候选时会挡住
OpenAlex 的仓储位置）与「非墙优先排序」后，需要重新量化：
  · 每站多少篇有**非墙域名**候选（真有机会）
  · 多少篇只剩墙域名候选（大概率失败）
  · 多少篇连候选都没有（真 closed）
只调公共学术 API + 不下载墙域名，避免浪费时间。
"""
import json
import sys
import time
from collections import Counter

sys.path.insert(0, r'D:/PostNews')
sys.path.insert(0, r'D:/PostNews/.workbuddy')


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

    targets = sys.argv[1:] or ['gsw', 'geojournals', 'oup']
    limit = 12
    for src in targets:
        lst = by_src.get(src) or []
        print('=' * 70)
        print('%s: 未归档 %d 篇，抽样 %d' % (src, len(lst), min(limit, len(lst))))
        stat = Counter()
        nonwall_samples = []
        for u, t in lst[:limit]:
            doi = oa.doi_of(u) or (oa.doi_by_title(t) if src == 'geojournals' else None)
            if not doi:
                stat['无DOI'] += 1
                continue
            try:
                cands = oa.collect_candidates(t, doi, u)
            except Exception as e:
                stat['渠道异常'] += 1
                continue
            if not cands:
                stat['无候选'] += 1
                continue
            nw = [c for c in cands if oa._host_of(c[1]) not in oa.HARD_WALL_HOSTS]
            if nw:
                stat['有非墙候选'] += 1
                nonwall_samples.append((t, doi, nw[:3]))
            else:
                stat['仅墙域名候选'] += 1
            time.sleep(1.0)
        print('  统计:', dict(stat))
        for t, doi, nw in nonwall_samples[:5]:
            print('  + %s | %s' % (doi, t[:44]))
            for ch, u2, ht, ver in nw:
                print('      [%s] %s' % (ch, u2[:92]))


if __name__ == '__main__':
    main()
