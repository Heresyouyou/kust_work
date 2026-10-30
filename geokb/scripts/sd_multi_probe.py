# -*- coding: utf-8 -*-
"""多方案探测：HTTP 层（curl_cffi 指纹 + 真 cookie）、Elsevier API、OA 副本"""
import json
import sys
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
sys.path.insert(0, str(BASE / 'scripts'))

PII = 'S0169136823004328'
ARTICLE = 'https://www.sciencedirect.com/science/article/pii/' + PII
PDFFT = ARTICLE + '/pdfft?isDTMRedir=true&download=true'

# ── 1) 取 profile 里的 cookie（本地读取，不联网）────────────────────
from browser_verify_fetch import get_sync_playwright, LAUNCH_ARGS  # noqa
from sso_keeper import PROFILE  # noqa

sync_playwright = get_sync_playwright('auto')
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), channel='msedge', headless=True,
        args=LAUNCH_ARGS, chromium_sandbox=True)
    jar = {}
    for c in ctx.cookies():
        d = c.get('domain', '')
        if 'elsevier' in d or 'sciencedirect' in d:
            jar[c['name']] = c['value']
    ctx.close()

print('cookie 数:', len(jar), '| 关键:', [k for k in ('EUID', 'SD_REMOTEACCESS',
                                                     'cf_clearance') if k in jar])

from curl_cffi import requests as creq  # noqa

s = creq.Session(impersonate='chrome', trust_env=False)
for k, v in jar.items():
    s.cookies.set(k, v, domain='.sciencedirect.com')

HDR = {'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
       'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
       'Referer': 'https://www.sciencedirect.com/'}


def show(tag, r):
    body = r.content
    head = body[:5]
    txt = ''
    try:
        txt = body.decode('utf-8', 'ignore')
    except Exception:
        pass
    flags = []
    if 'Are you a robot' in txt:
        flags.append('ROBOT')
    if 'problem providing the content' in txt:
        flags.append('BLOCKED')
    if 'Access to subscribed content' in txt:
        flags.append('SUBSCRIBED')
    if txt.count('citation_pdf_url'):
        flags.append('HAS_PDF_META')
    print('  %-8s status=%s bytes=%d head=%r %s'
          % (tag, r.status_code, len(body), head, ','.join(flags) or '-'))


print('\n[S1] curl_cffi(chrome) + 真 cookie → 文章页')
try:
    r = s.get(ARTICLE, headers=HDR, timeout=60)
    show('article', r)
except Exception as e:
    print('  异常', type(e).__name__, e)

print('\n[S2] 同一会话 → pdfft 直链')
try:
    r = s.get(PDFFT, headers=dict(HDR, Referer=ARTICLE), timeout=90)
    show('pdfft', r)
    if r.content[:4] == b'%PDF':
        out = BASE / 'state' / 'inbox' / ('curl_%s.pdf' % PII)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(r.content)
        print('  ✅ 保存', out)
except Exception as e:
    print('  异常', type(e).__name__, e)

print('\n[S3] Elsevier 官方 API（无密钥，探边界）')
for u in ('https://api.elsevier.com/content/article/pii/%s' % PII,
          'https://api.elsevier.com/content/article/pii/%s?httpAccept=application/pdf' % PII):
    try:
        r = creq.get(u, impersonate='chrome', trust_env=False, timeout=40)
        print('  %-6s %s %s' % (r.status_code, u[-46:], r.text[:80].replace('\n', ' ')))
    except Exception as e:
        print('  异常', type(e).__name__)

print('\n[S4] OA 副本（OpenAlex / Unpaywall，按标题）')
TITLE = ('Middle Devonian Evandale porphyry Cu-Mo (Au) deposit, '
         'southwestern New Brunswick')
try:
    r = creq.get('https://api.openalex.org/works',
                 params={'search': TITLE, 'per-page': 3},
                 impersonate='chrome', trust_env=False, timeout=40)
    for w in r.json().get('results', [])[:3]:
        print('  %s | oa=%s | %s' % (w.get('doi'), w.get('open_access', {}).get('is_oa'),
                                     (w.get('title') or '')[:60]))
        for loc in (w.get('locations') or []):
            if loc.get('is_oa') and (loc.get('pdf_url') or ''):
                print('      pdf:', loc['pdf_url'][:100])
except Exception as e:
    print('  OpenAlex 异常', type(e).__name__)
