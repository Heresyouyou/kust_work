# -*- coding: utf-8 -*-
"""验证「浏览器过一次挑战 → 导出 cf_clearance → curl_cffi 复用」是否成立。

若成立：人机验证的成本从「每篇一次浏览器」降到「每站一次浏览器」，
之后所有 PDF 都能用纯 HTTP 快速取，且不触发二次挑战。
"""
import json
import sys
import time

sys.path.insert(0, 'D:/ZSK/geokb/scripts')

GSW_ART = ('https://pubs.geoscienceworld.org/gsa/geology/article/54/9/867/731446/'
           'Independent-evolution-of-Gondwana-terranes-along')
GSW_PDF = ('https://pubs.geoscienceworld.org/gsa/geology/article-pdf/54/9/867/'
           '8148554/g54872.1.pdf')


def main():
    from patchright.sync_api import sync_playwright
    prof = 'D:/ZSK/geokb/state/browser_profiles/gsw'
    started = time.time()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            prof, channel='msedge', headless=True,
            args=['--disable-blink-features=AutomationControlled'],
            viewport={'width': 1440, 'height': 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(GSW_ART, wait_until='domcontentloaded', timeout=60000)
        # 等挑战消散
        for i in range(30):
            title = page.title()
            if not any(k in title.lower() for k in ('just a moment', '请稍候', 'checking')):
                break
            time.sleep(2)
        print('文章页 title:', page.title()[:80], '| %.0fs' % (time.time() - started))
        cookies = ctx.cookies()
        names = [c['name'] for c in cookies]
        print('cookies:', names)
        cf = [c for c in cookies if c['name'] in ('cf_clearance', '__cf_bm')]
        print('CF cookie 数:', len(cf))
        ua = page.evaluate('navigator.userAgent')
        print('UA:', ua)

        # 直接用浏览器内的 fetch 拿 PDF（同上下文，天然带 cookie 与指纹）
        print('\n--- 浏览器内 fetch PDF ---')
        try:
            res = page.evaluate("""async (u) => {
              const r = await fetch(u, {credentials:'include', headers:{'Accept':'application/pdf,*/*'}});
              const b = await r.arrayBuffer();
              const head = new Uint8Array(b.slice(0,4));
              return {status:r.status, ct:r.headers.get('content-type'),
                      len:b.byteLength, head:String.fromCharCode(...head)};
            }""", GSW_PDF)
            print('  ', res)
        except Exception as e:
            print('  fetch 失败:', type(e).__name__, str(e)[:150])

        json.dump({'cookies': cookies, 'ua': ua},
                  open('D:/ZSK/geokb/logs/_bv_cookies.json', 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        print('\n已导出 logs/_bv_cookies.json')
        ctx.close()


if __name__ == '__main__':
    sys.exit(main())
