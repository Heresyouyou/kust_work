# -*- coding: utf-8 -*-
"""单次提交诊断：提交后到底发生了什么（只提交一次，不重试）"""
import sys, time
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
sys.path.insert(0, str(BASE / 'scripts'))
from browser_verify_fetch import get_sync_playwright, LAUNCH_ARGS  # noqa
from sso_keeper import (SSO_ENTRY, PROFILE, load_cred, _goto_account_tab,
                        _solve_captcha, CAS_HOST, log)  # noqa

OUT = BASE / 'state' / 'cas_probe'
OUT.mkdir(parents=True, exist_ok=True)
cred = load_cred()
if not cred:
    raise SystemExit(2)
user, pwd = cred

sync_playwright = get_sync_playwright('auto')
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), channel='msedge', headless=True,
        args=LAUNCH_ARGS, chromium_sandbox=True, no_viewport=True,
        locale='zh-CN', timezone_id='Asia/Shanghai')
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(SSO_ENTRY, wait_until='domcontentloaded', timeout=60000)
    for _ in range(40):
        if CAS_HOST in (page.url or ''):
            break
        time.sleep(2)
    log('到 CAS：' + (page.url or '')[:100])
    page.wait_for_timeout(2000)
    _goto_account_tab(page)

    # 打印表单结构（确认字段）
    struct = page.evaluate("""() => {
        const out = [];
        document.querySelectorAll('input').forEach(i => out.push(
            (i.id||i.name||'?') + '|type=' + i.type + '|ph=' + (i.placeholder||'')));
        return out;
    }""")
    log('inputs: ' + str(struct))

    page.fill('#userName', user)
    page.fill('#password', pwd)
    ans, raw = _solve_captcha(page)
    log('captcha raw=%r ans=%s' % (raw, ans))
    page.fill('#captcha', ans)
    page.screenshot(path=str(OUT / 'cas_before_submit.png'))

    page.click('button:has-text("登 录")')
    for i in range(12):
        page.wait_for_timeout(5000)
        u = page.url or ''
        try:
            t = (page.title() or '')[:60]
        except Exception:
            t = '?'
        txt = page.evaluate(
            "() => document.body ? document.body.innerText.replace(/\\n{2,}/g,'\\n').slice(0,400) : ''")
        log('%2ds %s | %s | %s' % ((i + 1) * 5, u[:90], t, txt.replace('\n', ' / ')[:160]))
        if CAS_HOST not in u:
            log('>>> 已离开 CAS')
            break
    try:
        page.screenshot(path=str(OUT / 'cas_after_submit.png'))
    except Exception:
        pass
    ctx.close()
