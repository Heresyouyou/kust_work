# -*- coding: utf-8 -*-
"""验证 curl_cffi 路线能否真的取到 PDF（不只是正文页）。

流程：curl_cffi 取文章页 → 提取 citation_pdf_url / .full.pdf → 下载 → 校验 %PDF
"""
import json
import re
import sys
import time

from curl_cffi import requests as cr

TARGETS = [
    ('gsw', 'https://pubs.geoscienceworld.org/gsa/geology/article/54/9/867/731446/'
            'Independent-evolution-of-Gondwana-terranes-along'),
    ('gsw', 'https://pubs.geoscienceworld.org/gsa/geology/article/54/9/873/731461/'
            'A-unique-Mesozoic-greenalite-deposit-as-a-window'),
    ('oup', 'https://doi.org/10.1093/petrology/egag082'),
    ('oup', 'https://doi.org/10.1093/petrology/egag081'),
]

META_RES = [
    re.compile(r'<meta[^>]+name="citation_pdf_url"[^>]+content="([^"]+)"', re.I),
    re.compile(r'<meta[^>]+content="([^"]+)"[^>]+name="citation_pdf_url"', re.I),
]
LINK_RE = re.compile(r'href="([^"]*\.full(-text)?\.pdf[^"]*)"', re.I)


def find_pdf_url(html, base):
    for r in META_RES:
        m = r.search(html)
        if m:
            return m.group(1).replace('&amp;', '&')
    m = LINK_RE.search(html)
    if m:
        u = m.group(1).replace('&amp;', '&')
        if u.startswith('/'):
            from urllib.parse import urljoin
            u = urljoin(base, u)
        return u
    return ''


def main():
    out = []
    for src, url in TARGETS:
        rec = {'src': src, 'url': url}
        t0 = time.time()
        try:
            r = cr.get(url, impersonate='chrome', timeout=40, allow_redirects=True)
            rec['page_code'] = r.status_code
            html = r.text
            pdf_url = find_pdf_url(html, str(r.url))
            rec['pdf_url'] = pdf_url
            if not pdf_url:
                rec['status'] = 'no_pdf_meta'
                print('%-4s 无 PDF 入口  %s' % (src, url[:70]))
                out.append(rec)
                continue
            time.sleep(1.5)
            r2 = cr.get(pdf_url, impersonate='chrome', timeout=90, allow_redirects=True,
                        headers={'Referer': str(r.url)})
            body = r2.content
            rec['pdf_code'] = r2.status_code
            rec['bytes'] = len(body)
            rec['is_pdf'] = body[:4] == b'%PDF'
            rec['status'] = 'ok' if rec['is_pdf'] else 'not_pdf'
            print('%-4s %s %8d B is_pdf=%s %4.1fs  %s' % (
                src, r2.status_code, len(body), rec['is_pdf'], time.time() - t0,
                pdf_url[:80]))
        except Exception as e:
            rec['status'] = 'exc'
            rec['note'] = '%s: %s' % (type(e).__name__, str(e)[:100])
            print('%-4s EXC %s' % (src, rec['note']))
        out.append(rec)
    json.dump(out, open('D:/ZSK/geokb/logs/antibot_pdf_test.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    ok = sum(1 for r in out if r.get('status') == 'ok')
    print('\n成功 %d / %d' % (ok, len(out)))


if __name__ == '__main__':
    sys.exit(main())
