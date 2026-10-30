# -*- coding: utf-8 -*-
"""探测学校 WebVPN 是否可作为 ScienceDirect 的替代出口"""
import re
import sys
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
sys.path.insert(0, str(BASE / 'scripts'))
from curl_cffi import requests as creq  # noqa

S = creq.Session(impersonate='chrome', trust_env=False)
S.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                'AppleWebKit/537.36 (KHTML, like Gecko) '
                                'Chrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0'})

for u in ('https://w.libvpn.kust.edu.cn/',
          'https://w.libvpn.kust.edu.cn/login',
          'https://w.libvpn.kust.edu.cn/http/77726476706e69737468656265737421'):
    try:
        r = S.get(u, timeout=30, allow_redirects=False)
        print('%-62s %s loc=%s' % (u[:60], r.status_code,
                                   (r.headers.get('location') or '')[:80]))
        if r.status_code == 200 and len(r.content) < 40000:
            t = r.content.decode('utf-8', 'ignore')
            title = re.search(r'<title>(.*?)</title>', t, re.S)
            print('    title:', (title.group(1).strip()[:60] if title else '-'))
            for kw in ('login', 'cas', 'service=', 'sslvpn', 'webvpn'):
                if kw in t.lower():
                    print('    hit:', kw, 'x%d' % t.lower().count(kw))
            for m in re.finditer(r'(?:href|src|action)="([^"]{3,120})"', t):
                print('    link:', m.group(1)[:100])
    except Exception as e:
        print('%-62s ERR %s %s' % (u[:60], type(e).__name__, str(e)[:50]))
