# -*- coding: utf-8 -*-
"""ScienceDirect「Are you a robot?」验证页 DOM 诊断（2026-10-20）。

用户实操证实：ScienceDirect 的 View PDF 可以在本地通过点按验证拿到，
只是加载要几分钟。此前被误判成「任何指纹一律 403、IP 无权限」。

本脚本只做一件事：打开文章页，把验证页的真实结构（title / iframe / 可点元素
/ 正文关键节点）打出来，供 browser_verify_fetch.py 写选择器。
不下载 PDF，不改任何状态。
"""
import argparse
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--doi', default='10.1016/j.oregeorev.2023.105716')
    ap.add_argument('--wait', type=int, default=180, help='最长等待秒数')
    ap.add_argument('--headed', action='store_true')
    ap.add_argument('--out', default='')
    args = ap.parse_args()

    pii = None
    doi = args.doi
    # 先经 doi.org 换成 sciencedirect 文章页（PII 在落地 URL 里）
    url = 'https://doi.org/' + doi

    try:
        from patchright.sync_api import sync_playwright as spw
        engine = 'patchright'
    except ImportError:
        from playwright.sync_api import sync_playwright as spw  # noqa: F401
        engine = 'playwright'
    print('引擎', engine, flush=True)

    with spw() as p:
        b = p.chromium.launch(headless=not args.headed, channel='msedge',
                              args=['--disable-blink-features=AutomationControlled'])
        ctx = b.new_context(viewport={'width': 1440, 'height': 900},
                            user_agent=('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                                        'Chrome/131.0.0.0 Safari/537.36'))
        page = ctx.new_page()
        page.goto(url, timeout=90000, wait_until='domcontentloaded')
        print('落地:', page.url, flush=True)
        m = None
        import re
        mm = re.search(r'/pii/([A-Z0-9]+)', page.url)
        if mm:
            pii = mm.group(1)
            print('PII', pii, flush=True)

        snap = {}
        t0 = time.time()
        last = ''
        while time.time() - t0 < args.wait:
            try:
                title = page.title()
            except Exception:
                title = ''
            if title != last:
                print('  [%3ds] title=%r url=%s' % (time.time() - t0, title, page.url[:90]), flush=True)
                last = title
            low = title.lower()
            # 已到文章页且不是验证页 → 收工（⚠ 验证页 title 就是 "ScienceDirect"，
            # 不能只看标题，必须查 #captcha-box）
            if ('sciencedirect' in page.url) and not any(
                    k in low for k in ('robot', 'captcha', 'moment', 'verify', 'attention')):
                try:
                    has_box = page.query_selector('#captcha-box') is not None
                except Exception:
                    has_box = False
                if not has_box and page.query_selector('h1, .article-title, #screen-reader-main-title'):
                    print('  → 已进入文章页', flush=True)
                    break
            time.sleep(3)

        snap['url'] = page.url
        snap['title'] = page.title() if True else ''
        try:
            snap['frames'] = [{'url': (f.url or '')[:120],
                               'name': (f.name or '')[:40]} for f in page.frames]
        except Exception:
            snap['frames'] = []
        # 候选可点元素
        sels = ['input[type=checkbox]', 'button', 'a[href*="pdf"]', '[role=button]',
                'div[class*=captcha]', 'div[id*=captcha]', 'iframe',
                '#pdfLink', '.pdf-download', 'a[aria-label*=PDF]']
        snap['clickable'] = {}
        for s in sels:
            try:
                els = page.query_selector_all(s)
                if els:
                    snap['clickable'][s] = [
                        {'tag': (e.evaluate('e=>e.tagName') or ''),
                         'text': (e.inner_text() or '')[:60].replace('\n', ' '),
                         'id': e.get_attribute('id') or '',
                         'cls': (e.get_attribute('class') or '')[:60],
                         'href': (e.get_attribute('href') or '')[:100]}
                        for e in els[:6]]
            except Exception:
                pass
        # 页面里所有 iframe 的 src（可能是验证框）
        try:
            snap['iframes'] = [{'src': (page.get_attribute('iframe', 'src') or '')[:120]}]
        except Exception:
            pass
        snap['body_head'] = ''
        try:
            snap['body_head'] = (page.inner_text('body') or '')[:600].replace('\n', ' | ')
        except Exception:
            pass
        snap['pii'] = pii

        # ── 重点：Are you a robot 页的真实结构 ──
        snap['captcha'] = {}
        try:
            box = page.query_selector('#captcha-box')
            if box:
                snap['captcha']['exists'] = True
                snap['captcha']['html'] = (box.inner_html() or '')[:1500]
                snap['captcha']['text'] = (box.inner_text() or '')[:300]
                snap['captcha']['children'] = [
                    {'tag': c.evaluate('e=>e.tagName'),
                     'id': c.get_attribute('id') or '',
                     'cls': (c.get_attribute('class') or '')[:50],
                     'src': (c.get_attribute('src') or '')[:110]}
                    for c in (box.query_selector_all('*') or [])[:14]]
            else:
                snap['captcha']['exists'] = False
        except Exception as e:  # noqa: BLE001
            snap['captcha']['err'] = str(e)[:150]
        # 页面上所有 iframe（含动态插入）
        try:
            snap['all_iframes'] = page.evaluate(
                "() => Array.from(document.querySelectorAll('iframe')).map("
                "f => ({src: (f.src||'').slice(0,120), id: f.id, "
                "cls: (f.className||'').slice(0,60), w: f.offsetWidth, h: f.offsetHeight}))")
        except Exception:
            snap['all_iframes'] = []
        # 可疑的第三方脚本（captcha 提供方）
        try:
            snap['scripts'] = page.evaluate(
                "() => Array.from(document.querySelectorAll('script[src]')).map("
                "s => s.src).filter(u => /captcha|recaptcha|hcaptcha|px|perimeter|"
                "arkose|funcaptcha|turnstile/i.test(u)).slice(0,8)")
        except Exception:
            snap['scripts'] = []

        outp = args.out or str(BASE / 'logs' / 'sd_captcha_probe.json')
        Path(outp).write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding='utf-8')
        print('\n--- 快照 ---')
        print('title:', snap['title'])
        print('frames:', json.dumps(snap['frames'], ensure_ascii=False)[:400])
        print('clickable keys:', list(snap['clickable'].keys()))
        for k, v in snap['clickable'].items():
            print('  %-24s %s' % (k, json.dumps(v, ensure_ascii=False)[:260]))
        print("body_head:", snap["body_head"][:300])
        print("captcha:", json.dumps(snap["captcha"], ensure_ascii=False)[:900])
        print("iframes:", json.dumps(snap["all_iframes"], ensure_ascii=False)[:400])
        print("scripts:", json.dumps(snap["scripts"], ensure_ascii=False)[:400])
        print('→', outp)
        ctx.close()
        b.close()


if __name__ == '__main__':
    main()
