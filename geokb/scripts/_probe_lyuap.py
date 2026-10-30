"""探测金智 LyuAP 统一认证登录页（cas.kmust.edu.cn）的表单与验证码。"""
import re

from curl_cffi import requests as creq

URLS = [
    'https://cas.kmust.edu.cn/lyuapServer/login',
    'https://idplib.kust.edu.cn/idp/profile/SAML2/Redirect/SSO',
]

s = creq.Session(impersonate='chrome')
for u in URLS:
    try:
        r = s.get(u, timeout=15, verify=False, allow_redirects=True)
    except Exception as e:
        print(f'{u} → {type(e).__name__}: {e}')
        continue
    html = r.text or ''
    title = re.search(r'<title>(.*?)</title>', html, re.S | re.I)
    print(f'\n=== {u}')
    print(f'  HTTP {r.status_code}  len={len(html)}  final={r.url[:110]}')
    print(f'  title={(title.group(1).strip()[:60] if title else "")!r}')
    if '403' in html[:2000] and '拒绝' in html:
        print('  !! 被 WAF 拒绝（403 页面）')
    names = re.findall(r'<input[^>]*>', html, re.I)
    for tag in names[:14]:
        n = re.search(r'name=["\']([^"\']+)', tag, re.I)
        t = re.search(r'type=["\']([^"\']+)', tag, re.I)
        i = re.search(r'id=["\']([^"\']+)', tag, re.I)
        print(f'    input name={n.group(1) if n else "-":<18} '
              f'type={t.group(1) if t else "-":<10} id={i.group(1) if i else "-"}')
    cap = re.search(r'<img[^>]+src=["\']([^"\']*(?:captcha|code|verify)[^"\']*)',
                    html, re.I)
    print(f'  captcha_img={cap.group(1)[:120] if cap else "无"}')
    if re.search(r'captcha|验证码|checkcode|verifyCode', html, re.I):
        print('  !! 页面含验证码相关字样')
