# -*- coding: utf-8 -*-
"""打印 gsw 文章页里所有 PDF 相关线索，确定正确的 PDF 路径形态。"""
import re
import sys

from curl_cffi import requests as cr

GSW = ('https://pubs.geoscienceworld.org/gsa/geology/article/54/9/867/731446/'
       'Independent-evolution-of-Gondwana-terranes-along')


def main():
    r = cr.get(GSW, impersonate='chrome', timeout=60, allow_redirects=True)
    html = r.text
    print('code', r.status_code, 'len', len(html), '| final url:', str(r.url))
    print('\n--- citation_* meta ---')
    for m in re.finditer(r'<meta[^>]*name="(citation_[a-z_]+)"[^>]*content="([^"]*)"', html):
        print('  %-28s %s' % (m.group(1), m.group(2)[:120]))
    print('\n--- 所有含 pdf 的 href ---')
    seen = set()
    for m in re.finditer(r'href="([^"]*pdf[^"]*)"', html, re.I):
        u = m.group(1)
        if u in seen:
            continue
        seen.add(u)
        print('  ', u[:160])
    print('\n--- 含 article-pdf 的任意字符串 ---')
    for m in re.finditer(r'[\x27"]([^\x27"]*article-pdf[^\x27"]*)[\x27"]', html):
        print('  ', m.group(1)[:160])


if __name__ == '__main__':
    sys.exit(main())
