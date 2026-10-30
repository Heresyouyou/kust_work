# -*- coding: utf-8 -*-
"""终局测试：真实指纹浏览器直接请求 /pdf 端点；同时补测 Elsevier API 与 OA 副本"""
import json
import sys
import time
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
sys.path.insert(0, str(BASE / 'scripts'))
from browser_verify_fetch import get_sync_playwright, LAUNCH_ARGS  # noqa
from sso_keeper import PROFILE  # noqa

from curl_cffi import requests as creq  # noqa

PII = 'S0169136823004328'
MD5 = '5cd500c3b793d89d703076b6aca618ec'
PDF_URL = ('https://www.sciencedirect.com/science/article/pii/%s/pdf'
           '?md5=%s&pid=1-s2.0-%s-main.pdf' % (PII, MD5, PII))
INBOX = BASE / 'state' / 'inbox'
INBOX.mkdir(parents=True, exist_ok=True)


def log(m):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), m), flush=True)


print('=== [S5] 真实指纹浏览器直接打开 /pdf ===')
sync_playwright = get_sync_playwright('auto')
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), channel='msedge', headless=False,
        args=LAUNCH_ARGS, chromium_sandbox=True, no_viewport=True,
        accept_downloads=True, locale='zh-CN', timezone_id='Asia/Shanghai')
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    got = {}

    page.on('download', lambda d: got.setdefault('d', d))

    try:
        resp = page.goto(PDF_URL, wait_until='commit', timeout=90000)
        log('导航响应: %s' % (resp.status if resp else '无'))
    except Exception as e:
        log('goto 异常: %s' % type(e).__name__)

    for i in range(20):
        page.wait_for_timeout(2000)
        if 'd' in got:
            break
        try:
            u = page.url
            t = (page.title() or '')[:40]
        except Exception:
            u, t = '?', '?'
        if 'pdf' not in u.lower() and 'sciencedirectassets' not in u.lower():
            txt = page.evaluate("() => document.body ? document.body.innerText.slice(0,160) : ''")
            log('%2ds url=%s | %s | %s' % ((i + 1) * 2, u[:70], t, txt.replace('\n', ' ')[:90]))
            if 'robot' in txt.lower() or 'captcha' in txt.lower():
                log('  → 需要人工过验证，等待点击（最多 180s）')
                for _ in range(60):
                    page.wait_for_timeout(3000)
                    if 'd' in got:
                        break
                    try:
                        if 'pdf' in page.url.lower():
                            break
                    except Exception:
                        pass
                break

    if 'd' in got:
        d = got['d']
        path = INBOX / ('dl_%s.pdf' % PII)
        d.save_as(str(path))
        log('✅ 下载成功 → %s（%.1f MB）' % (path, path.stat().st_size / 1048576))
    else:
        log('未触发下载')
        # 若落到 blob/pdf 预览，尝试从 viewer 里取真实地址
        try:
            log('  当前 URL: %s' % page.url[:130])
            ck = [c['name'] for c in ctx.cookies()
                  if 'sciencedirectassets' in c.get('domain', '')]
            log('  assets cookie: %s' % ck)
        except Exception:
            pass
    ctx.close()

print()
print('=== [S3] Elsevier API（无密钥）===')
for u, acc in (('https://api.elsevier.com/content/article/pii/%s' % PII, 'json'),
               ('https://api.elsevier.com/content/article/pii/%s?httpAccept=application/pdf' % PII, 'pdf')):
    try:
        r = creq.get(u, impersonate='chrome', timeout=40)
        print('  %s %-74s %s' % (r.status_code, u[-60:], r.text[:70].replace('\n', ' ')))
    except Exception as e:
        print('  异常', type(e).__name__, str(e)[:60])

print()
print('=== [S4] OA 副本 ===')
try:
    r = creq.get('https://api.openalex.org/works',
                 params={'filter': 'title.search:Evandale porphyry Cu-Mo', 'per-page': 3},
                 impersonate='chrome', timeout=40)
    for w in r.json().get('results', [])[:3]:
        print('  %-42s oa=%s %s' % (w.get('doi'), w.get('open_access', {}).get('is_oa'),
                                    (w.get('title') or '')[:50]))
except Exception as e:
    print('  OpenAlex 异常', type(e).__name__, str(e)[:80])
