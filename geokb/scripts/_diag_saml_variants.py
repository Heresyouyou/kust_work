# -*- coding: utf-8 -*-
"""CAS 会话已在 profile 中，测试不同 returnURL 变体能否走完 SAML"""
import sys, time
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
sys.path.insert(0, str(BASE / 'scripts'))
from browser_verify_fetch import get_sync_playwright, LAUNCH_ARGS  # noqa
from sso_keeper import PROFILE, log  # noqa

EID = 'https%3A%2F%2Fidplib.kust.edu.cn%2Fidp%2Fshibboleth'
VARIANTS = [
    ('no_return', 'https://auth.elsevier.com/ShibAuth/institutionLogin?entityID=' + EID),
    ('sd_home', 'https://auth.elsevier.com/ShibAuth/institutionLogin?entityID=' + EID
     + '&appReturnURL=https%3A%2F%2Fwww.sciencedirect.com%2Fscience%2Farticle%2Fpii%2FS0169136823004328'),
]

sync_playwright = get_sync_playwright('auto')
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), channel='msedge', headless=True,
        args=LAUNCH_ARGS, chromium_sandbox=True, no_viewport=True,
        locale='zh-CN', timezone_id='Asia/Shanghai')
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on('response', lambda r: None)
    for tag, url in VARIANTS:
        print('=' * 18, tag)
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=60000)
        except Exception as e:
            print('  goto:', type(e).__name__)
        last, stable = None, 0
        for i in range(30):
            page.wait_for_timeout(3000)
            u = page.url or ''
            if u == last:
                stable += 1
            else:
                stable = 0
                print('  %2ds %s' % ((i + 1) * 3, u[:130]))
                last = u
            if stable >= 2 and i > 2:
                break
        txt = page.evaluate("() => document.body ? document.body.innerText.slice(0,300) : ''")
        print('  text:', txt.replace('\n', ' / ')[:200])
        ck = [c['name'] for c in ctx.cookies()
              if 'elsevier' in c.get('domain', '') or 'sciencedirect' in c.get('domain', '')]
        print('  elsevier cookies:', sorted(set(ck))[:18])
    ctx.close()
