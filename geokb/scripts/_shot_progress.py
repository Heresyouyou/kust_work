# -*- coding: utf-8 -*-
"""给 /geokb/progress 截图（深/浅两套主题），用于验收视觉。

要点：
  · 必须 --no-proxy-server：本机 HTTP_PROXY 会把 127.0.0.1 也劫持走；
  · 主题由 /theme.js 读 localStorage 决定，切主题后要 reload 才生效；
  · full_page=False 会漏掉下半屏，用 full_page=True 出长图。
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = 'http://127.0.0.1:5555/geokb/progress'
OUT = Path('D:/ZSK/geokb/docs/shots')
OUT.mkdir(parents=True, exist_ok=True)
tag = sys.argv[1] if len(sys.argv) > 1 else 'now'

with sync_playwright() as p:
    # playwright 自带的 chromium 没下载；本机已装 Edge，直接用 channel 复用，
    # 省掉 ~150MB 下载（--no-proxy-server 同样有效）。
    try:
        b = p.chromium.launch(headless=True, channel='msedge', args=['--no-proxy-server'])
    except Exception as e:
        print('msedge channel 失败，回退默认 chromium：%s' % e)
        b = p.chromium.launch(headless=True, args=['--no-proxy-server'])
    for theme in ('dark', 'light'):
        ctx = b.new_context(viewport={'width': 1560, 'height': 1100})
        pg = ctx.new_page()
        # 先落到同源任意页，写 localStorage 再进目标页
        pg.goto('http://127.0.0.1:5555/geokb/progress', wait_until='domcontentloaded', timeout=30000)
        # 主题键是 kust.theme.mode（'auto' | 'light' | 'dark'），见 /theme.js
        pg.evaluate("t => localStorage.setItem('kust.theme.mode', t)", theme)
        pg.reload(wait_until='networkidle', timeout=30000)
        pg.wait_for_timeout(1200)
        f = OUT / ('progress_%s_%s.png' % (theme, tag))
        pg.screenshot(path=str(f), full_page=True)
        print('%s  %s  (%dx%d)' % (theme, f, pg.viewport_size['width'], pg.viewport_size['height']))
        # 顺手把页面里的 JS 报错也带出来
        ctx.close()
    b.close()
print('done')
