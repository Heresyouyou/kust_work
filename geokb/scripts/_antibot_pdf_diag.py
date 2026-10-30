# -*- coding: utf-8 -*-
"""诊断：curl_cffi 取 PDF 直链为何回落（看返回体特征 + Session 保 cookie）。"""
import json
import re
import sys
import time

from curl_cffi import requests as cr

GSW = ('https://pubs.geoscienceworld.org/gsa/geology/article/54/9/867/731446/'
       'Independent-evolution-of-Gondwana-terranes-along')
PDF = ('https://pubs.geoscienceworld.org/gsa/geology/article-pdf/54/9/867/8148554/'
       'g54872.pdf')

CHAL = ('just a moment', 'cf-chl', 'checking your browser', 'attention required')


def sniff(body):
    head = body[:600]
    low = body[:4000].decode('utf-8', 'ignore').lower()
    if body[:4] == b'%PDF':
        return 'PDF'
    for m in CHAL:
        if m in low:
            return '挑战页'
    if 'citation_title' in low or 'article' in low:
        return '文章页'
    return '其他:' + repr(head[:80])


def main():
    rows = []

    print('--- A) 独立 GET（无 session）')
    r = cr.get(PDF, impersonate='chrome', timeout=60, allow_redirects=True)
    print('   code', r.status_code, 'size', len(r.content), '→', sniff(r.content),
          '| ct', r.headers.get('content-type'))
    rows.append({'route': 'A 无session', 'code': r.status_code,
                 'size': len(r.content), 'kind': sniff(r.content),
                 'ct': r.headers.get('content-type')})

    # B) 同 session：先文章页（拿 cookie），再 PDF
    print('--- B) Session: 文章页 → PDF')
    s = cr.Session(impersonate='chrome')
    r1 = s.get(GSW, timeout=60, allow_redirects=True)
    print('   文章页', r1.status_code, len(r1.content), '→', sniff(r1.content),
          '| cookies:', len(s.cookies))
    time.sleep(2)
    r2 = s.get(PDF, timeout=60, allow_redirects=True,
               headers={'Referer': GSW, 'Accept': 'application/pdf,*/*'})
    print('   PDF  ', r2.status_code, len(r2.content), '→', sniff(r2.content),
          '| ct', r2.headers.get('content-type'))
    rows.append({'route': 'B session', 'code': r2.status_code,
                 'size': len(r2.content), 'kind': sniff(r2.content),
                 'ct': r2.headers.get('content-type')})

    # C) 带完整 Sec-Fetch 头 + Accept application/pdf
    print('--- C) 完整头 + Sec-Fetch-Dest:document')
    h = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,'
                  'image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': GSW,
        'Sec-Fetch-Dest': 'document', 'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-User': '?1',
        'Upgrade-Insecure-Requests': '1',
    }
    r3 = cr.get(PDF, impersonate='chrome', headers=h, timeout=60, allow_redirects=True)
    print('   code', r3.status_code, len(r3.content), '→', sniff(r3.content),
          '| ct', r3.headers.get('content-type'))
    rows.append({'route': 'C 完整头', 'code': r3.status_code,
                 'size': len(r3.content), 'kind': sniff(r3.content),
                 'ct': r3.headers.get('content-type')})

    # D) 换成 ?download=true
    print('--- D) 加 ?download=true')
    r4 = cr.get(PDF + '?download=true', impersonate='chrome', timeout=60,
                allow_redirects=True, headers={'Referer': GSW})
    print('   code', r4.status_code, len(r4.content), '→', sniff(r4.content),
          '| ct', r4.headers.get('content-type'))
    rows.append({'route': 'D download=true', 'code': r4.status_code,
                 'size': len(r4.content), 'kind': sniff(r4.content),
                 'ct': r4.headers.get('content-type')})

    json.dump(rows, open('D:/ZSK/geokb/logs/antibot_pdf_diag.json', 'w',
                         encoding='utf-8'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    sys.exit(main())
