# -*- coding: utf-8 -*-
"""反爬方案对比实测（2026-10-20）

目标：找出对 Cloudflare 托管挑战源站（gsw / oup）最有效的**非浏览器**路线。
只读探测：仅 GET 文章页，判定是否为挑战页 / 正文页 / PDF 直链。
"""
import json
import sys
import time
import warnings

warnings.filterwarnings('ignore')

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0')

TARGETS = {
    'gsw': ('https://pubs.geoscienceworld.org/gsa/geology/article/54/9/867/731446/'
            'Independent-evolution-of-Gondwana-terranes-along'),
    'oup': 'https://doi.org/10.1093/petrology/egag082',
}

CHALLENGE_MARKS = ('just a moment', 'checking your browser', 'cf-chl',
                   'attention required', 'enable javascript and cookies',
                   '请稍候', 'verifying you are human')
ARTICLE_MARKS = ('citation_pdf_url', 'citation_title', 'abstract')


def classify(body, code):
    low = body.lower() if isinstance(body, str) else body.decode('utf-8', 'ignore').lower()
    if code == 403:
        return '403'
    for m in CHALLENGE_MARKS:
        if m in low:
            return '挑战页'
    for m in ARTICLE_MARKS:
        if m in low:
            return '正文页'
    if body[:4] == b'%PDF' if isinstance(body, bytes) else False:
        return 'PDF'
    return '未知(%d)' % code


def try_requests(url):
    import requests
    s = requests.Session()
    s.headers.update({'User-Agent': UA, 'Accept-Language': 'en-US,en;q=0.9'})
    r = s.get(url, timeout=25, allow_redirects=True)
    return r.status_code, classify(r.content, r.status_code), len(r.content)


def try_curl_cffi(url, imp):
    from curl_cffi import requests as cr
    r = cr.get(url, impersonate=imp, timeout=30, allow_redirects=True)
    return r.status_code, classify(r.content, r.status_code), len(r.content)


def try_httpx_h2(url):
    import httpx
    h = {'User-Agent': UA, 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
         'Accept-Language': 'en-US,en;q=0.9', 'Accept-Encoding': 'gzip, deflate, br',
         'Sec-Fetch-Dest': 'document', 'Sec-Fetch-Mode': 'navigate',
         'Sec-Fetch-Site': 'none', 'Sec-Fetch-User': '?1',
         'Upgrade-Insecure-Requests': '1'}
    with httpx.Client(http2=True, headers=h, follow_redirects=True, timeout=30) as c:
        r = c.get(url)
        return r.status_code, classify(r.content, r.status_code), len(r.content)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default='')
    ap.add_argument('--src', default='gsw,oup')
    a = ap.parse_args()
    srcs = [s for s in a.src.split(',') if s]
    rows = []
    for src in srcs:
        url = TARGETS[src]
        print('\n======== %s ========' % src)
        print('url:', url[:110])
        tests = [
            ('requests(现状)', lambda u=url: try_requests(u)),
            ('curl_cffi/chrome', lambda u=url: try_curl_cffi(u, 'chrome')),
            ('curl_cffi/chrome124', lambda u=url: try_curl_cffi(u, 'chrome124')),
            ('curl_cffi/edge101', lambda u=url: try_curl_cffi(u, 'edge101')),
            ('httpx http2', lambda u=url: try_httpx_h2(u)),
        ]
        for name, fn in tests:
            if a.only and a.only not in name:
                continue
            t0 = time.time()
            try:
                code, kind, size = fn()
                note = ''
            except Exception as e:
                code, kind, size, note = 0, 'EXC', 0, '%s: %s' % (type(e).__name__, str(e)[:80])
            dt = time.time() - t0
            print('  %-20s %4s %-10s %8d B %5.1fs %s' % (name, code, kind, size, dt, note))
            rows.append({'src': src, 'route': name, 'code': code, 'kind': kind,
                         'size': size, 'sec': round(dt, 1), 'note': note})
    json.dump(rows, open('D:/ZSK/geokb/logs/antibot_matrix.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('\n已写 logs/antibot_matrix.json')


if __name__ == '__main__':
    sys.exit(main())
