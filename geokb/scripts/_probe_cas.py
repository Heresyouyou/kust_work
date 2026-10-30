"""探测昆工统一身份认证（CAS / SSO）入口与登录页结构。

只做公共页面 GET，不涉及任何账号凭据，也不操作浏览器窗口。
"""
import re
import socket

CANDIDATES = [
    'cas.kust.edu.cn',
    'sso.kust.edu.cn',
    'authserver.kust.edu.cn',
    'uis.kust.edu.cn',
    'id.kust.edu.cn',
    'iap.kust.edu.cn',
    'oauth.kust.edu.cn',
    'passport.kust.edu.cn',
    'login.kust.edu.cn',
    'my.kust.edu.cn',
]

print('=== DNS / TCP ===')
alive = []
for h in CANDIDATES:
    try:
        ip = socket.gethostbyname(h)
    except Exception:
        continue
    try:
        s = socket.create_connection((ip, 443), timeout=4)
        s.close()
        alive.append((h, ip))
        print(f'  {h:<28} {ip:<16} 443 OK')
    except Exception as e:
        print(f'  {h:<28} {ip:<16} 443 {type(e).__name__}')

print('\n=== 登录页结构 ===')
try:
    from curl_cffi import requests as creq
except Exception:
    creq = None
    print('  curl_cffi 不可用')

if creq:
    for h, ip in alive:
        for url in (f'https://{h}/', f'https://{h}/cas/login',
                    f'https://{h}/authserver/login'):
            try:
                r = creq.get(url, impersonate='chrome', timeout=12,
                             allow_redirects=True, verify=False)
            except Exception as e:
                print(f'  {url} → {type(e).__name__}')
                continue
            if r.status_code != 200:
                print(f'  {url} → HTTP {r.status_code}')
                continue
            html = r.text or ''
            title = re.search(r'<title>(.*?)</title>', html, re.S | re.I)
            title = (title.group(1).strip()[:60] if title else '')
            inputs = re.findall(
                r'<input[^>]+name=["\']([^"\']+)["\'][^>]*>', html, re.I)
            has_code = bool(re.search(r'captcha|verifycode|kaptcha|checkcode',
                                      html, re.I))
            print(f'  {url}')
            print(f'    title={title!r} len={len(html)} captcha={has_code}')
            print(f'    inputs={inputs[:12]}')
            if has_code:
                m = re.search(r'<img[^>]+src=["\']([^"\']*(?:captcha|code)[^"\']*)',
                              html, re.I)
                if m:
                    print(f'    captcha_img={m.group(1)[:100]}')
            print(f'    final_url={r.url[:110]}')
