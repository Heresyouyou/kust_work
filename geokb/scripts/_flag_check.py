# -*- coding: utf-8 -*-
"""验证启动参数里是否还残留会触发「不支持的命令行标志」提示条的标志。

做法：用与生产完全相同的 LAUNCH_ARGS 起一个受控 profile 浏览器，
读它自己的真实 cmdline，逐条比对 Chrome 的 badflags 名单，并确认
navigator.webdriver 为 False。
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'scripts'))

import psutil  # noqa: E402
import browser_verify_fetch as bv  # noqa: E402

BAD = ['--no-sandbox', '--disable-blink-features=AutomationControlled',
       '--enable-automation', '--disable-infobars']

print('LAUNCH_ARGS =', bv.LAUNCH_ARGS)
print('其中命中 badflags 的:', [a for a in bv.LAUNCH_ARGS if a in BAD] or '无')

prof = BASE / 'state' / 'browser_profiles' / '_flagcheck'
with bv.get_sync_playwright('patchright')() as pw:
    # 结论（2026-10-20 实测）：**不能**用 ignore_default_args 剔除
    # `--disable-blink-features=AutomationControlled`。
    #   保留该标志 → 提示条存在，但 navigator.webdriver = False
    #   剔除该标志 → 提示条消失，但 navigator.webdriver = True  ← 更糟
    # 提示条只是本地 UI，Cloudflare 读不到；webdriver=true 是 JS 硬判据。
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(prof), channel='msedge', headless=False,
        no_viewport=True, chromium_sandbox=True, accept_downloads=True,
        locale='zh-CN', timezone_id='Asia/Shanghai', args=bv.LAUNCH_ARGS)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto('about:blank')
        wd = page.evaluate('navigator.webdriver')
        ua = page.evaluate('navigator.userAgent')
        print('\nnavigator.webdriver =', wd)
        print('navigator.userAgent =', ua)
        # 找主进程（带 --user-data-dir 且无 --type=）
        found = []
        for p in psutil.process_iter(['pid', 'cmdline']):
            cl = p.info.get('cmdline') or []
            s = ' '.join(cl)
            if '_flagcheck' in s and not any(a.startswith('--type=') for a in cl):
                found.append((p.info['pid'], cl))
        print('\n主进程数:', len(found))
        for pid, cl in found[:1]:
            hits = [a for a in cl if a in BAD or 'blink-features' in a]
            print('  pid=%d' % pid)
            print('  命中可疑标志:', hits or '无')
            print('  完整 cmdline:', ' '.join(cl)[:600])
    finally:
        try:
            ctx.close()
        except Exception:
            pass
