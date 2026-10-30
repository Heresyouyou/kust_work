# -*- coding: utf-8 -*-
"""有窗口取单篇 PDF：等人工过 Turnstile → 文章页就绪 → pdfft 下载 → 落 inbox

用法:
    python scripts/sd_get_pdf.py --pii S0169136823004328 --wait 300
"""
import argparse
import sys
import time
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
sys.path.insert(0, str(BASE / 'scripts'))
from browser_verify_fetch import get_sync_playwright, LAUNCH_ARGS  # noqa

PROFILE = BASE / 'state' / 'browser_profiles' / 'sciencedirect_auth'
INBOX = BASE / 'state' / 'inbox'


def log(m):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), m), flush=True)


def page_state(page):
    return page.evaluate("""() => {
        const b = document.body ? document.body.innerText : '';
        return {
            txt: b.slice(0, 400).replace(/\\n+/g, ' / '),
            robot: b.includes('Are you a robot') || b.includes('captcha challenge'),
            blocked: b.includes('problem providing the content'),
            access: b.includes('Access to subscribed content')
                    || b.includes('Access through your institution'),
            hasPdf: !!document.querySelector('meta[name="citation_pdf_url"]')
                    || !!document.querySelector('a[href*="/pdfft"]'),
            title: document.title || '',
        };
    }""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pii', default='S0169136823004328')
    ap.add_argument('--wait', type=int, default=300)
    args = ap.parse_args()
    url = 'https://www.sciencedirect.com/science/article/pii/' + args.pii

    sync_playwright = get_sync_playwright('auto')
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), channel='msedge', headless=False,
            args=LAUNCH_ARGS, chromium_sandbox=True, no_viewport=True,
            locale='zh-CN', timezone_id='Asia/Shanghai')
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, wait_until='domcontentloaded', timeout=90000)
        log('窗口已打开，等待页面就绪（需要时请手动点一下验证框）')

        ready = False
        deadline = time.time() + args.wait
        last = ''
        while time.time() < deadline:
            page.wait_for_timeout(3000)
            try:
                st = page_state(page)
            except Exception:
                continue
            sig = '%s|%s|%s' % (st['robot'], st['blocked'], st['access'])
            if sig != last:
                last = sig
                log('robot=%s blocked=%s access=%s | %s'
                    % (st['robot'], st['blocked'], st['access'], st['title'][:50]))
            if st['access'] and st['hasPdf']:
                ready = True
                break
            if st['access'] and not st['blocked'] and not st['robot']:
                ready = True
                break
        if not ready:
            log('[fail] 超时未就绪')
            ctx.close()
            return 1

        log('文章页就绪，尝试下载')
        pdf_url = page.evaluate(
            "() => { const m = document.querySelector('meta[name=\"citation_pdf_url\"]');"
            " return m ? m.content : null; }")
        log('citation_pdf_url = %s' % (pdf_url or '无'))

        cands = []
        if pdf_url:
            cands.append(pdf_url)
        cands.append('https://www.sciencedirect.com/science/article/pii/%s/pdfft'
                     '?isDTMRedir=true&download=true' % args.pii)

        INBOX.mkdir(parents=True, exist_ok=True)
        for u in cands:
            log('  GET %s' % u[:90])
            try:
                r = ctx.request.get(u, timeout=120000, fail_on_status_code=False)
            except Exception as e:
                log('    异常 %s' % type(e).__name__)
                continue
            body = r.body()
            log('    status=%s ctype=%s bytes=%d head=%r'
                % (r.status, r.headers.get('content-type'), len(body), body[:5]))
            if body[:4] == b'%PDF':
                out = INBOX / ('sd_%s.pdf' % args.pii)
                out.write_bytes(body)
                log('  ✅ 已保存 %s（%.1f MB）' % (out, len(body) / 1048576))
                ctx.close()
                return 0
        log('[fail] 两个入口都没拿到 PDF')
        ctx.close()
        return 1


if __name__ == '__main__':
    sys.exit(main())
