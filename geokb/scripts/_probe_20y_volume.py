# -*- coding: utf-8 -*-
"""只读探测：目标期刊近 20 年在 CrossRef 的真实论文量（rows=0 取 total-results）。"""
import json
import sys
import time

import requests
import urllib3

urllib3.disable_warnings()

sys.path.insert(0, 'D:/ZSK/geokb/scripts')
sys.path.insert(0, 'D:/PostNews')
import history_manifest as hm                      # noqa: E402
import history_manifest_region as hr               # noqa: E402

FRM, UNTIL = '2006-10-27', '2026-10-27'
S = requests.Session()
S.trust_env = False                                # 关键：绕开本机 HTTP_PROXY
S.headers.update({'User-Agent': 'GeoKB/1.0 (mailto:kust.work@qq.com)'})

names = []
for n, i in list(hm.CROSSREF_JOURNALS) + list(hr.REGION_JOURNALS):
    if n not in [x[0] for x in names]:
        names.append((n, i))

rows, fail = [], 0
for n, issn in names:
    url = 'https://api.crossref.org/journals/%s/works' % issn
    p = {'filter': 'from-pub-date:%s,until-pub-date:%s,type:journal-article' % (FRM, UNTIL),
         'rows': '1', 'select': 'DOI,published', 'mailto': 'kust.work@qq.com'}
    total, sample = -1, ''
    for k in range(2):
        try:
            r = S.get(url, params=p, timeout=40, verify=False,
                      proxies={'http': None, 'https': None})
            if r.status_code == 200:
                m = r.json()['message']
                total = int(m.get('total-results') or 0)
                it = (m.get('items') or [{}])[0]
                sample = it.get('DOI') or ''
                break
        except Exception as e:
            if k:
                print('  [ERR] %-40s %s' % (n[:40], str(e)[:60]))
        time.sleep(1.5)
    if total < 0:
        fail += 1
        continue
    sd = sample.startswith('10.1016')
    rows.append((n, issn, total, sample.split('/')[0], sd))
    time.sleep(0.7)

print('\n%-42s %-10s %7s  %-12s %s' % ('期刊', 'ISSN', '20年篇数', 'DOI前缀', 'SD'))
print('-' * 88)
for n, issn, t, pre, sd in sorted(rows, key=lambda x: -x[2]):
    print('%-42s %-10s %7d  %-12s %s' % (n[:42], issn, t, pre, 'SD' if sd else ''))

sd_n = sum(t for _, _, t, _, sd in rows if sd)
all_n = sum(t for _, _, t, _, _ in rows)
print('-' * 88)
print('合计 %d 篇 ｜ SD(10.1016) %d (%.1f%%) ｜ 非SD %d ｜ 失败源 %d'
      % (all_n, sd_n, 100.0 * sd_n / max(all_n, 1), all_n - sd_n, fail))
print('非SD明细：')
for n, issn, t, pre, sd in sorted(rows, key=lambda x: -x[2]):
    if not sd:
        print('   %-42s %6d  %s' % (n[:42], t, pre))
json.dump([{'journal': n, 'issn': i, 'total': t, 'doi_prefix': p, 'sd': sd}
           for n, i, t, p, sd in rows],
          open('D:/ZSK/geokb/state/_probe_20y_volume.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
