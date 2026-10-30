# -*- coding: utf-8 -*-
"""拉取 pdfft 的 403 响应体并定性：是无订阅权限、还是 Cloudflare 拦截。"""
import json
import os
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'scripts'))

# 本机存在沙箱注入的 HTTP_PROXY，curl_cffi 会读它 → 必须清掉
for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
    os.environ.pop(k, None)
os.environ['NO_PROXY'] = '*'

from curl_cffi import requests  # noqa: E402

ck_file = BASE / 'state' / 'browser_state' / 'cookies_sciencedirect.json'
d = json.loads(ck_file.read_text(encoding='utf-8'))
cookies = d.get('cookies') or {}
ua = d.get('ua') or ''

url = sys.argv[1] if len(sys.argv) > 1 else (
    'https://www.sciencedirect.com/science/article/pii/S0169136826004932/pdfft'
    '?md5=4dbc21fef0656cc15c19cf4bcc8a2a8e&pid=1-s2.0-S0169136826004932-main.pdf')
ref = 'https://www.sciencedirect.com/science/article/pii/S0169136826004932'

hdr = {
    'Accept': 'text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Referer': ref,
    'Upgrade-Insecure-Requests': '1',
}
if ua:
    hdr['User-Agent'] = ua

s = requests.Session(impersonate='chrome', trust_env=False)
for k, v in cookies.items():
    s.cookies.set(k, v, domain='.sciencedirect.com')

r = s.get(url, headers=hdr, timeout=90, allow_redirects=True)
body = r.content
print('status', r.status_code, 'len', len(body), 'final', str(r.url)[:140])
print('history', [(h.status_code, str(h.headers.get('location'))[:100]) for h in r.history][:5])
out = BASE / 'logs' / 'sd_403_body.html'
out.write_bytes(body)
print('已存', out)

try:
    txt = body.decode('utf-8', 'ignore')
except Exception:
    txt = ''
m = re.search(r'<title>(.*?)</title>', txt, re.S | re.I)
print('title:', (m.group(1).strip()[:120] if m else '(无)'))
for kw in ('Just a moment', 'Are you a robot', 'captcha', 'Access Denied',
           'purchase', 'Get Access', 'not available', 'subscribe'):
    print('  含 %-16s %s' % (kw, kw.lower() in txt.lower()))
sm = re.search(r'.{0,120}(Access Denied|purchase|subscri|permission).{0,120}', txt, re.I | re.S)
print('片段:', re.sub(r'\s+', ' ', sm.group(0))[:300] if sm else '(无)')
