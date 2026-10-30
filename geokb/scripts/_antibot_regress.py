# -*- coding: utf-8 -*-
"""回归：new_session 接入 curl_cffi 后，各源站 fetch_one 的实际状态。

目的
----
1. 确认「不会把原本能下的中文源站搞坏」；
2. 确认 gsw/oup 从笼统的 http_403 变成可判定的具体状态（paywalled / ok）。

用法：GEOKB_BROWSER_VERIFY=0 python _antibot_regress.py
"""
import json
import os
import sys
import time

os.environ.setdefault('GEOKB_BROWSER_VERIFY', '0')   # 先只测 HTTP 层
sys.path.insert(0, 'D:/PostNews')

CASES = [
    # gsw 订阅文（pdf 路径 403 = 权限）
    ('gsw', 'Geology', 'Independent evolution of Gondwana terranes along its northern margin',
     'https://pubs.geoscienceworld.org/gsa/geology/article/54/9/867/731446/'
     'Independent-evolution-of-Gondwana-terranes-along'),
    # oup
    ('oup', 'Journal of Petrology', 'Tracing Deep Melt Infiltration in the Mantle Using Sulphide',
     'https://doi.org/10.1093/petrology/egag082'),
    # elsevier（硬墙，看是否退化为更明确的状态）
    ('elsevier', 'Ore Geology Reviews', 'In-situ dolomite U-Pb dating constraints',
     'https://doi.org/10.1016/j.oregeorev.2026.107592'),
]


def main():
    from journal_pdf_fetch import fetch_pdf, available, sources
    import antibot_session as ab
    print('handler 可用:', available(), '| 源站:', sources())
    print('HTTP 引擎:', json.dumps(ab.describe(), ensure_ascii=False))
    print()
    rows = []
    for tag, j, title, url in CASES:
        t0 = time.time()
        r = fetch_pdf(j, title, url)
        data = r.pop('pdf_bytes', None)
        r['bytes'] = len(data) if data else 0
        r['sec'] = round(time.time() - t0, 1)
        rows.append({'tag': tag, **{k: v for k, v in r.items()
                                    if isinstance(v, (str, int, float, type(None)))}})
        print('%-9s status=%-14s %6d B %5.1fs | %s' % (
            tag, r.get('status'), r.get('bytes'), r['sec'],
            str(r.get('note'))[:90]))
        print('           pdf_url=%s' % str(r.get('pdf_url'))[:100])
    json.dump(rows, open('D:/ZSK/geokb/logs/antibot_regress.json', 'w',
                         encoding='utf-8'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    sys.exit(main())
