# -*- coding: utf-8 -*-
"""用正确 PDF 路径（含 .1.pdf）验证 curl_cffi 直取；并诊断 OUP 页面性质。"""
import json
import re
import sys
import time

from curl_cffi import requests as cr

GSW_PDF = ('https://pubs.geoscienceworld.org/gsa/geology/article-pdf/54/9/867/'
           '8148554/g54872.1.pdf')
GSW_ART = ('https://pubs.geoscienceworld.org/gsa/geology/article/54/9/867/731446/'
           'Independent-evolution-of-Gondwana-terranes-along')
OUP = 'https://doi.org/10.1093/petrology/egag082'


def sniff(b):
    if b[:4] == b'%PDF':
        return 'PDF'
    low = b[:6000].decode('utf-8', 'ignore').lower()
    for m in ('just a moment', 'cf-chl', 'checking your browser', 'please wait', '请稍候'):
        if m in low:
            return '挑战/中转页(%s)' % m
    if 'citation_title' in low:
        return '文章页'
    return '其他'


def main():
    rows = []

    print('=== 1) gsw 完整 PDF 直链')
    for tag, kw in (('直取', {}),):
        try:
            r = cr.get(GSW_PDF, impersonate='chrome', timeout=120,
                       allow_redirects=True,
                       headers={'Referer': GSW_ART})
            k = sniff(r.content)
            print('  %s  %s %8d B  %s  ct=%s' % (tag, r.status_code, len(r.content), k,
                                                 r.headers.get('content-type')))
            rows.append({'route': 'gsw_pdf_' + tag, 'code': r.status_code,
                         'size': len(r.content), 'kind': k})
            if k == 'PDF':
                open('D:/ZSK/geokb/logs/_gsw_sample.pdf', 'wb').write(r.content)
                print('  已存样本 logs/_gsw_sample.pdf')
        except Exception as e:
            print('  %s EXC %s: %s' % (tag, type(e).__name__, str(e)[:120]))

    print('\n=== 2) gsw 先文章页再 PDF（同 session）')
    s = cr.Session(impersonate='chrome')
    s.get(GSW_ART, timeout=60)
    time.sleep(2)
    r = s.get(GSW_PDF, timeout=120, headers={'Referer': GSW_ART})
    k = sniff(r.content)
    print('  session %s %8d B %s' % (r.status_code, len(r.content), k))
    rows.append({'route': 'gsw_pdf_session', 'code': r.status_code,
                 'size': len(r.content), 'kind': k})

    print('\n=== 3) OUP 页面性质')
    r = cr.get(OUP, impersonate='chrome', timeout=90, allow_redirects=True)
    html = r.text
    print('  code', r.status_code, 'len', len(html), '| final', str(r.url)[:120])
    print('  性质:', sniff(r.content))
    print('  <title>:', (re.search(r'<title>(.*?)</title>', html, re.S) or ['', ''])[1][:100])
    for name in ('citation_pdf_url', 'citation_title', 'citation_doi'):
        m = re.search(r'<meta[^>]+name="%s"[^>]+content="([^"]*)"' % name, html)
        print('  %-18s %s' % (name, (m.group(1)[:110] if m else '—')))
    pdfs = set(re.findall(r'https?://[^\s"\']+?\.pdf[^\s"\']*', html)) 
    for p in list(pdfs)[:5]:
        print('  pdf线索:', p[:130])
    rows.append({'route': 'oup_page', 'code': r.status_code, 'size': len(html),
                 'kind': sniff(r.content)})

    json.dump(rows, open('D:/ZSK/geokb/logs/antibot_pdf_diag2.json', 'w',
                         encoding='utf-8'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    sys.exit(main())
