# -*- coding: utf-8 -*-
"""诊断：从 SD 的 institution/login 入口进，看落到哪一步"""
import sys, time
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
sys.path.insert(0, str(BASE / 'scripts'))
from browser_verify_fetch import get_sync_playwright, LAUNCH_ARGS  # noqa
from sso_keeper import PROFILE, CAS_HOST, log  # noqa

SD_ENTRY = ('https://www.sciencedirect.com/user/institution/login'
            '?targetURL=%2Fscience%2Farticle%2Fpii%2FS0169136823004328')

sync_playwright = get_sync_playwright('auto')
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), channel='msedge', headless=True,
        args=LAUNCH_ARGS, chromium_sandbox=True, no_viewport=True,
        locale='zh-CN', timezone_id='Asia/Shanghai')
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(SD_ENTRY, wait_until='domcontentloaded', timeout=60000)
    last = None
    for i in range(30):
        page.wait_for_timeout(3000)
        u = page.url or ''
        try:
            t = (page.title() or '')[:50]
        except Exception:
            t = '?'
        if u != last:
            log('%2ds %s | %s' % ((i + 1) * 3, u[:120], t))
            last = u
        if 'cas.kmust.edu.cn' in u:
            log('>>> 需要 CAS 认证')
            break
        if 'sciencedirect.com/science/article' in u:
            log('>>> 已回到文章页')
            break
    txt = page.evaluate("() => document.body ? document.body.innerText.slice(0,600) : ''")
    log('正文：' + txt.replace('\n', ' / ')[:300])
    ctx.close()
