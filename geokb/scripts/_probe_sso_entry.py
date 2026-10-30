"""找出能直接落到 CAS 登录页的 SSO 入口 URL（跟随重定向链）。"""
from curl_cffi import requests as creq

CANDS = [
    'https://sdauth.sciencedirect.com/',
    ('https://auth.elsevier.com/ShibAuth/institutionLogin'
     '?entityID=https%3A%2F%2Fsdauth.sciencedirect.com%2F'),
    ('https://auth.elsevier.com/ShibAuth/institutionLogin'
     '?entityID=https://sdauth.sciencedirect.com/'),
]

for u in CANDS:
    s = creq.Session(impersonate='chrome')
    print(f'\n=== {u[:100]}')
    try:
        r = s.get(u, timeout=20, verify=False, allow_redirects=True)
        print(f'  HTTP {r.status_code}  final={r.url[:150]}')
        hist = getattr(r, 'redirect_history', None) or r.history or []
        for i, h in enumerate(hist[:8], 1):
            loc = h.headers.get('location', '')[:110]
            print(f'   {i}. {h.status_code} → {loc}')
        print(f'  len={len(r.text or "")}')
    except Exception as e:
        print(f'  ERR {type(e).__name__}: {e}')
