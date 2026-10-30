# -*- coding: utf-8 -*-
"""用 profile 里已有的 cookie 直接访问文章页并尝试取 PDF"""
import sys
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
sys.path.insert(0, str(BASE / 'scripts'))
from browser_verify_fetch import get_sync_playwright, LAUNCH_ARGS  # noqa
from sso_keeper import PROFILE, log, TEST_ARTICLE  # noqa

OUT = BASE / 'state' / 'inbox'
OUT.mkdir(parents=True, exist_ok=True)

sync_playwright = get_sync_playwright('auto')
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), channel='msedge', headless=False,
        args=LAUNCH_ARGS, chromium_sandbox=True, no_viewport=True,
        locale='zh-CN', timezone_id='Asia/Shanghai')
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(TEST_ARTICLE, wait_until='domcontentloaded', timeout=60000)
    page.wait_for_timeout(4000)
    print('url:', page.url[:140])
    print('title:', (page.title() or '')[:80])
    info = page.evaluate("""() => {
        const b = document.body ? document.body.innerText : '';
        const meta = document.querySelector('meta[name="citation_pdf_url"]');
        const links = [...document.querySelectorAll('a')]
            .map(a => a.href).filter(h => h.includes('pdfft') || h.includes('pdf'));
        return {
            access: b.includes('Access to subscribed content') || b.includes('Access through your'),
            blocked: b.includes('problem providing the content'),
            head: b.slice(0, 200).replace(/\\n+/g, ' / '),
            meta: meta ? meta.content : null,
            links: [...new Set(links)].slice(0, 6),
            hasSignIn: b.includes('Sign in'),
        };
    }""")
    for k, v in info.items():
        print('%-9s %s' % (k + ':', str(v)[:200]))

    # 试 pdfft 下载
    url = ('https://www.sciencedirect.com/science/article/pii/'
           'S0169136823004328/pdfft?isDTMRedir=true&download=true')
    r = ctx.request.get(url, timeout=90000, fail_on_status_code=False)
    print('pdfft status:', r.status, '| ctype:', r.headers.get('content-type'),
          '| len:', r.headers.get('content-length'))
    body = r.body()
    print('body head:', body[:16])
    if body[:4] == b'%PDF':
        f = OUT / 'sd_test_S0169136823004328.pdf'
        f.write_bytes(body)
        print('SAVED', f, len(body))
    else:
        print('not pdf, text:', body[:300].decode('utf-8', 'ignore').replace('\n', ' ')[:250])
    ctx.close()
