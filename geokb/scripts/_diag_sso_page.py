# -*- coding: utf-8 -*-
"""诊断 auth.elsevier.com/ShibAuth/institutionLogin 页面到底显示什么"""
import sys, time
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
sys.path.insert(0, str(BASE / 'scripts'))
from browser_verify_fetch import get_sync_playwright, LAUNCH_ARGS  # noqa

PROFILE = BASE / 'state' / 'browser_profiles' / 'sciencedirect_auth'
OUT = BASE / 'state' / 'cas_probe'

URLS = [
    ('E1_idplib', 'https://auth.elsevier.com/ShibAuth/institutionLogin'
     '?entityID=https%3A%2F%2Fidplib.kust.edu.cn%2Fidp%2Fshibboleth'
     '&appReturnURL=https%3A%2F%2Fid.elsevier.com%2Fas%2FTJBSbzxo9Z'
     '%2Fresume%2Fas%2Fauthorization.ping&oneTrust=false'),
    ('E2_direct_idp', 'https://idplib.kust.edu.cn/idp/profile/SAML2/Redirect/SSO'),
    ('E3_cas', 'https://cas.kmust.edu.cn/lyuapServer/login'),
]

pw = get_sync_playwright('auto')().start()
ctx = pw.chromium.launch_persistent_context(
    user_data_dir=str(PROFILE), channel='msedge', headless=True,
    args=LAUNCH_ARGS, no_viewport=True)
page = ctx.pages[0] if ctx.pages else ctx.new_page()
OUT.mkdir(parents=True, exist_ok=True)

for tag, url in URLS:
    print('=' * 20, tag)
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=60000)
    except Exception as e:
        print('  goto:', type(e).__name__)
    page.wait_for_timeout(6000)
    print('  final url:', (page.url or '')[:160])
    try:
        print('  title:', (page.title() or '')[:100])
    except Exception:
        pass
    info = page.evaluate("""() => {
        const txt = document.body ? document.body.innerText.replace(/\\n{2,}/g,'\\n').slice(0, 900) : '';
        const links = [...document.querySelectorAll('a')].slice(0,15)
            .map(a => (a.innerText||'').trim().slice(0,40) + ' -> ' + a.href.slice(0,120));
        const forms = [...document.querySelectorAll('form')].slice(0,5)
            .map(f => (f.action||'') + ' | ' + [...new FormData(f).keys()].join(','));
        const btns = [...document.querySelectorAll('button,input[type=submit]')].slice(0,10)
            .map(b => ((b.innerText||b.value||'')+'').trim().slice(0,30));
        return {txt, links, forms, btns};
    }""")
    print('  --- text ---')
    print('\n'.join('   ' + l for l in info['txt'].splitlines()[:18]))
    for k in ('links', 'forms', 'btns'):
        if info[k]:
            print('  --- %s ---' % k)
            for x in info[k]:
                print('   ', x)
    try:
        page.screenshot(path=str(OUT / ('diag_%s.png' % tag)))
    except Exception:
        pass

ctx.close()
pw.stop()
