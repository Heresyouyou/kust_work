# -*- coding: utf-8 -*-
"""深挖：把文章页/pdfft 响应存盘并分析，找出真正的 PDF 入口"""
import re
import sys
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
sys.path.insert(0, str(BASE / 'scripts'))
OUT = BASE / 'state' / '_sd_probe'
OUT.mkdir(parents=True, exist_ok=True)

PII = 'S0169136823004328'
ARTICLE = 'https://www.sciencedirect.com/science/article/pii/' + PII
PDFFT = ARTICLE + '/pdfft?isDTMRedir=true&download=true'

from browser_verify_fetch import get_sync_playwright, LAUNCH_ARGS  # noqa
from sso_keeper import PROFILE  # noqa

sync_playwright = get_sync_playwright('auto')
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), channel='msedge', headless=True,
        args=LAUNCH_ARGS, chromium_sandbox=True)
    jar = {c['name']: c['value'] for c in ctx.cookies()
           if 'elsevier' in c.get('domain', '') or 'sciencedirect' in c.get('domain', '')}
    ctx.close()

from curl_cffi import requests as creq  # noqa

s = creq.Session(impersonate='chrome', trust_env=False)
for k, v in jar.items():
    s.cookies.set(k, v, domain='.sciencedirect.com')

HDR = {'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
       'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'}


def analyze(name, r):
    (OUT / (name + '.html')).write_bytes(r.content)
    txt = r.content.decode('utf-8', 'ignore')
    title = re.search(r'<title>(.*?)</title>', txt, re.S)
    print('--- %s ---' % name)
    print('  url   :', str(r.url)[:120])
    print('  status:', r.status_code, '| bytes:', len(r.content),
          '| ctype:', r.headers.get('content-type'))
    print('  title :', (title.group(1).strip()[:90] if title else '-'))
    for kw in ('Are you a robot', 'problem providing the content', 'Get Access',
               'Purchase PDF', 'Download full text', 'Access to subscribed',
               'citation_pdf_url', 'pdfft', 'sciencedirectassets',
               'challenge-platform', 'cf-chl'):
        n = txt.count(kw)
        if n:
            print('  hit   : %-28s x%d' % (kw, n))
    for m in re.finditer(r'(https://[^"\'<>\\ ]*?(?:pdf|assets)[^"\'<>\\ ]{0,120})', txt):
        u = m.group(1)
        if 'sciencedirectassets' in u or u.endswith('.pdf'):
            print('  link  :', u[:150])
    return txt


r1 = s.get(ARTICLE, headers=HDR, timeout=60)
t1 = analyze('article', r1)

r2 = s.get(PDFFT, headers=dict(HDR, Accept='application/pdf,*/*',
                               Referer=ARTICLE), timeout=90)
t2 = analyze('pdfft', r2)
print('  history:', [h.status_code for h in (r2.history or [])])

# 从文章页里挖 meta / 结构化数据
m = re.search(r'<meta[^>]+name="citation_pdf_url"[^>]*content="([^"]+)"', t1)
print('\n citation_pdf_url =', m.group(1) if m else '无')
for pat, label in ((r'"pdfDownload":\{[^}]{0,300}', 'pdfDownload'),
                   (r'data-pdf-url="([^"]+)"', 'data-pdf-url'),
                   (r'/science/article/pii/%s/pdf[^"\'<> ]*' % PII, 'pdfPath')):
    for mm in re.finditer(pat, t1):
        print(' %-12s %s' % (label, mm.group(0)[:150]))
