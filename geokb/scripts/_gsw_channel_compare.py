# -*- coding: utf-8 -*-
"""验证 curl_cffi 通道对 gsw 的实际收益：有多少篇能拿到免费全文入口。

判据：文章页里出现 citation_pdf_url = 该文有可直接下载的 PDF 入口。
对比「requests（旧）」与「curl_cffi（新）」两种通道的命中数。
"""
import json
import re
import sys
import time

from curl_cffi import requests as cr
import requests as rq

LEDGER = 'D:/PostNews/journal_ledger.json'
N = 6


def gsw_urls():
    d = json.load(open(LEDGER, encoding='utf-8'))
    out, seen = [], set()
    for j, arts in (d.get('journals') or {}).items():
        for a in arts or []:
            u = a.get('url') or ''
            if 'geoscienceworld.org' not in u or u in seen:
                continue
            seen.add(u)
            out.append((j, (a.get('title') or '')[:50], u))
    return out[:N]


def has_pdf_meta(html):
    return bool(re.search(r'name=["\']citation_pdf_url["\']', html, re.I))


def main():
    rows = []
    for j, t, u in gsw_urls():
        r = {'journal': j, 'title': t, 'url': u[:100]}
        # 旧通道
        try:
            x = rq.get(u, timeout=25, headers={'User-Agent':
                     'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                     '(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36'})
            r['old_code'] = x.status_code
            r['old_pdf'] = has_pdf_meta(x.text)
        except Exception as e:
            r['old_code'] = 0
            r['old_pdf'] = False
        time.sleep(1.2)
        # 新通道
        try:
            x = cr.get(u, impersonate='chrome', timeout=40)
            r['new_code'] = x.status_code
            r['new_pdf'] = has_pdf_meta(x.text)
        except Exception as e:
            r['new_code'] = 0
            r['new_pdf'] = False
            r['err'] = str(e)[:80]
        rows.append(r)
        print('%-28s 旧 %3s pdf=%-5s | 新 %3s pdf=%-5s | %s' % (
            j[:28], r['old_code'], r['old_pdf'], r['new_code'], r['new_pdf'], t[:34]))
        time.sleep(1.2)
    json.dump(rows, open('D:/ZSK/geokb/logs/gsw_channel_compare.json', 'w',
                         encoding='utf-8'), ensure_ascii=False, indent=1)
    o = sum(1 for r in rows if r['old_pdf'])
    n = sum(1 for r in rows if r['new_pdf'])
    print('\n含免费 PDF 入口：旧通道 %d/%d，新通道 %d/%d' % (o, len(rows), n, len(rows)))


if __name__ == '__main__':
    sys.exit(main())
