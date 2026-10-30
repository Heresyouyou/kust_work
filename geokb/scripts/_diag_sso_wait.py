# -*- coding: utf-8 -*-
"""复现 sso_keeper 的等待逻辑，逐步打印 URL/标题，定位为何不跳 CAS"""
import sys, time
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
sys.path.insert(0, str(BASE / 'scripts'))
from browser_verify_fetch import get_sync_playwright, LAUNCH_ARGS  # noqa
from sso_keeper import SSO_ENTRY, PROFILE  # noqa

sync_playwright = get_sync_playwright('auto')
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), channel='msedge', headless=True,
        args=LAUNCH_ARGS, chromium_sandbox=True, no_viewport=True,
        locale='zh-CN', timezone_id='Asia/Shanghai')
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    print('pages:', len(ctx.pages), 'start url:', page.url)
    try:
        page.goto(SSO_ENTRY, wait_until='domcontentloaded', timeout=60000)
    except Exception as e:
        print('goto exc:', type(e).__name__, e)
    for i in range(16):
        try:
            u = page.url or ''
            t = page.title() or ''
        except Exception as e:
            u, t = 'ERR', type(e).__name__
        print('%2ds url=%s | title=%s' % (i * 5, u[:110], t[:60]))
        if 'cas.kmust.edu.cn' in u:
            print('>>> 到达 CAS')
            break
        time.sleep(5)
    ctx.close()
