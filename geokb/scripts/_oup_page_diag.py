# -*- coding: utf-8 -*-
"""OUP 文章页结构诊断：curl_cffi 200 的页面里 PDF 入口在哪。"""
import json
import re
import sys

from curl_cffi import requests as cr

URL = 'https://doi.org/10.1093/petrology/egag082'


def main():
    r = cr.get(URL, impersonate='chrome', timeout=60, allow_redirects=True,
               headers={'Accept-Language': 'en-US,en;q=0.9'})
    html = r.text
    print('code', r.status_code, 'len', len(html))
    print('final', str(r.url)[:130])
    t = re.search(r'<title>(.*?)</title>', html, re.S)
    print('title:', (t.group(1).strip()[:90] if t else '—'))

    print('\n--- citation_* ---')
    for m in re.finditer(r'<meta[^>]*name="(citation_[a-z_]+)"[^>]*content="([^"]*)"', html):
        print('  %-26s %s' % (m.group(1), m.group(2)[:90]))

    print('\n--- 含 pdf 的 href / 路径 ---')
    seen = set()
    for m in re.finditer(r'(?:href|content)="([^"]*pdf[^"]*)"', html, re.I):
        u = m.group(1)
        if u in seen:
            continue
        seen.add(u)
        print('  ', u[:140])
    for m in re.finditer(r'(/[\w\-/]*?(?:article-pdf|advance-article-pdf)[\w\-/.]*)', html):
        u = m.group(1)
        if u in seen:
            continue
        seen.add(u)
        print('  [path]', u[:140])

    print('\n--- 关键标记 ---')
    for k in ('citation_pdf_url', 'article-pdf', 'advance-article-pdf',
              'Just a moment', '请稍候', 'sign in', 'purchase'):
        print('  %-22s %s' % (k, html.lower().count(k.lower())))
    open('D:/ZSK/geokb/logs/_oup_page.html', 'w', encoding='utf-8').write(html)


if __name__ == '__main__':
    sys.exit(main())
