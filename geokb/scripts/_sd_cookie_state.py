# -*- coding: utf-8 -*-
"""只读：检查受控 profile 里 Elsevier/SD 凭证 cookie 是否还在（不联网）"""
import sys
from datetime import datetime
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
sys.path.insert(0, str(BASE / 'scripts'))
from browser_verify_fetch import get_sync_playwright, LAUNCH_ARGS  # noqa

PROFILE = BASE / 'state' / 'browser_profiles' / 'sciencedirect_auth'
KEY = ('EUID', 'SD_REMOTEACCESS', 'cf_clearance', 'cra-ch-clearance',
       'JSESSIONID', 'sd_session_id', 'at_hash')

sync_playwright = get_sync_playwright('auto')
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), channel='msedge', headless=True,
        args=LAUNCH_ARGS, chromium_sandbox=True)
    cookies = ctx.cookies()
    ctx.close()

now = datetime.now().timestamp()
rows = [c for c in cookies
        if 'elsevier' in c.get('domain', '') or 'sciencedirect' in c.get('domain', '')]
print('SD/Elsevier cookie 总数:', len(rows))
for c in sorted(rows, key=lambda x: x['name']):
    if c['name'] in KEY or c['name'].startswith('SD_'):
        exp = c.get('expires', -1)
        when = '会话级(关浏览器即失效)' if exp in (-1, 0) else \
            datetime.fromtimestamp(exp).strftime('%Y-%m-%d %H:%M')
        left = '' if exp in (-1, 0) else ('  [已过期]' if exp < now else '')
        print('  %-18s %s%s' % (c['name'], when, left))
missing = [k for k in ('EUID', 'SD_REMOTEACCESS') if k not in {c['name'] for c in rows}]
print('关键凭证缺失:', missing or '无')
