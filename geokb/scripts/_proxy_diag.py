# -*- coding: utf-8 -*-
"""定位 502 CONNECT tunnel failed 的真实来源（2026-10-20）。

已知：curl_cffi Session(impersonate='chrome', trust_env=False) 仍报
`curl: (7) CONNECT tunnel failed, response 502`，说明代理不是从
环境变量进的 libcurl。逐个排除。
"""
import os
import sys

URL = 'https://orbi.uliege.be/bitstream/2268/345893/1/s00126-024-01325-9.pdf'

print('--- 环境变量 ---')
for k in list(os.environ):
    if 'proxy' in k.lower():
        print('  %s=%s' % (k, os.environ[k]))
print('  GEOKB_USE_PROXY=%r' % os.environ.get('GEOKB_USE_PROXY'))

from curl_cffi import requests as creq  # noqa: E402

cases = []

# 1. 原样
try:
    s = creq.Session(impersonate='chrome', trust_env=False)
    r = s.get(URL, timeout=25, allow_redirects=True)
    cases.append(('Session(trust_env=False)', 'HTTP %d %d bytes' % (r.status_code, len(r.content or b''))))
except Exception as e:  # noqa: BLE001
    cases.append(('Session(trust_env=False)', '%s: %s' % (type(e).__name__, str(e)[:90])))

# 2. 显式 proxies 置空
try:
    s = creq.Session(impersonate='chrome', trust_env=False)
    s.proxies = {}
    r = s.get(URL, timeout=25, proxies={'http': None, 'https': None})
    cases.append(('proxies={None}', 'HTTP %d %d bytes' % (r.status_code, len(r.content or b''))))
except Exception as e:  # noqa: BLE001
    cases.append(('proxies={None}', '%s: %s' % (type(e).__name__, str(e)[:90])))

# 3. NO_PROXY='*'
try:
    os.environ['NO_PROXY'] = '*'
    os.environ['no_proxy'] = '*'
    s = creq.Session(impersonate='chrome', trust_env=False)
    r = s.get(URL, timeout=25)
    cases.append(("NO_PROXY='*'", 'HTTP %d %d bytes' % (r.status_code, len(r.content or b''))))
except Exception as e:  # noqa: BLE001
    cases.append(("NO_PROXY='*'", '%s: %s' % (type(e).__name__, str(e)[:90])))

# 4. 不用 impersonate（裸 curl 行为）
try:
    r = creq.get(URL, timeout=25, impersonate='chrome', proxies={'https': ''})
    cases.append(("proxy=''", 'HTTP %d %d bytes' % (r.status_code, len(r.content or b''))))
except Exception as e:  # noqa: BLE001
    cases.append(("proxy=''", '%s: %s' % (type(e).__name__, str(e)[:90])))

for name, res in cases:
    print('%-24s %s' % (name, res))
