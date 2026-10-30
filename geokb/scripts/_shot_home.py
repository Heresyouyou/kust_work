# -*- coding: utf-8 -*-
"""给 /geokb 首页截图（深/浅两套主题），验收「文献爬取进度」内嵌统计块。

出两类图：
  home_<theme>_<tag>.png        —— 整页（看它与论文分面/最近更新的前后关系）
  home_cw_<theme>_<tag>.png     —— 只截 .cw-panel（看细节排版）

必须 --no-proxy-server：本机 HTTP_PROXY 会把 127.0.0.1 也劫持走。
主题键是 kust.theme.mode（'auto' | 'light' | 'dark'），见 /theme.js。
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = 'http://127.0.0.1:5555/geokb/'
OUT = Path('D:/ZSK/geokb/docs/shots')
OUT.mkdir(parents=True, exist_ok=True)
tag = sys.argv[1] if len(sys.argv) > 1 else 'now'
WIDTH = int(sys.argv[2]) if len(sys.argv) > 2 else 1560

errors = []
with sync_playwright() as p:
    try:
        b = p.chromium.launch(headless=True, channel='msedge', args=['--no-proxy-server'])
    except Exception as e:
        print('msedge channel 失败，回退默认 chromium：%s' % e)
        b = p.chromium.launch(headless=True, args=['--no-proxy-server'])
    for theme in ('dark', 'light'):
        ctx = b.new_context(viewport={'width': WIDTH, 'height': 1100})
        pg = ctx.new_page()
        pg.on('pageerror', lambda e: errors.append('%s: %s' % (theme, e)))
        pg.goto(URL, wait_until='domcontentloaded', timeout=30000)
        pg.evaluate("t => localStorage.setItem('kust.theme.mode', t)", theme)
        pg.reload(wait_until='networkidle', timeout=30000)
        pg.wait_for_timeout(1500)

        f = OUT / ('home_%s_%s.png' % (theme, tag))
        pg.screenshot(path=str(f), full_page=True)
        print('%s 整页  %s' % (theme, f))

        el = pg.query_selector('.cw-panel')
        if el is None:
            print('%s  ⚠ 找不到 .cw-panel' % theme)
        else:
            f2 = OUT / ('home_cw_%s_%s.png' % (theme, tag))
            el.screenshot(path=str(f2))
            box = el.bounding_box()
            print('%s 组件  %s  %.0fx%.0f' % (theme, f2, box['width'], box['height']))
            # 量关键元素，确认柱图/条形确实有高度（百分比高度踩过坑）
            h = pg.evaluate("""() => {
              const q = s => document.querySelector(s);
              const r = e => e ? Math.round(e.getBoundingClientRect().height) : -1;
              const bars = [...document.querySelectorAll('.cw-bwrap i')];
              return {
                bars_h: bars.map(x => Math.round(x.getBoundingClientRect().height)),
                jbar_w: [...document.querySelectorAll('.cw-jbar i')]
                          .map(x => Math.round(x.getBoundingClientRect().width)),
                kpi_h: r(q('.cw-kpi')),
                panel_h: r(q('.cw-panel')),
                pv_w: Math.round((q('.cw-pv-fill')||{getBoundingClientRect:()=>({width:-1})})
                                 .getBoundingClientRect().width),
              };
            }""")
            print('    柱高', h['bars_h'])
            print('    条宽', h['jbar_w'][:4], '… KPI 高', h['kpi_h'])
            print('    面板高', h['panel_h'], ' 进度条填充宽', h['pv_w'])
        ctx.close()
    b.close()

if errors:
    print('⚠ JS 报错：')
    for e in errors:
        print('  ', e)
else:
    print('无 JS 报错')
print('done')
