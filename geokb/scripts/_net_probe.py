# -*- coding: utf-8 -*-
"""网络出口体检：当前公网 IP、网卡、可用代理端口。只读，不改任何连接。"""
import json
import os
import socket
import sys
from pathlib import Path

import psutil

print('=== 环境代理变量 ===')
for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
          'http_proxy', 'https_proxy', 'no_proxy'):
    v = os.environ.get(k)
    if v:
        print('  %-12s %s' % (k, v))
print('  (以上若为空则未设置)')

print('\n=== 网卡地址 ===')
for name, addrs in psutil.net_if_addrs().items():
    rows = []
    for a in addrs:
        if a.family == socket.AF_INET:
            rows.append(a.address)
    if rows:
        st = psutil.net_if_stats().get(name)
        print('  %-38s %-16s up=%s' % (name[:38], ','.join(rows),
                                       getattr(st, 'isup', '?')))

print('\n=== 疑似代理端口监听 ===')
try:
    conns = psutil.net_connections('tcp')
except Exception as e:  # noqa: BLE001
    conns = []
    print('  枚举失败', str(e)[:60])
seen = set()
COMMON = {7890, 7891, 7892, 1080, 1081, 8080, 8081, 8888, 8118, 10808, 10809, 20171, 33210}
for c in conns:
    if c.status == psutil.CONN_LISTEN and c.laddr:
        port = c.laddr.port
        if port in COMMON:
            key = (c.laddr.ip, port)
            if key in seen:
                continue
            seen.add(key)
            try:
                nm = psutil.Process(c.pid).name() if c.pid else '?'
            except Exception:
                nm = '?'
            print('  %-16s %-6d pid=%-6s %s' % (c.laddr.ip, port, c.pid, nm))
if not seen:
    print('  (常见代理端口无监听)')

print('\n=== 当前公网出口 IP ===')
for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy',
          'ALL_PROXY', 'all_proxy'):
    os.environ.pop(k, None)
os.environ['NO_PROXY'] = '*'
try:
    from curl_cffi import requests as creq
    for url in ('https://api.ipify.org?format=json', 'https://ipinfo.io/json'):
        try:
            r = creq.get(url, impersonate='chrome', timeout=25, trust_env=False)
            print('  %-32s %s' % (url.split('/')[2], r.text[:200]))
            break
        except Exception as e:  # noqa: BLE001
            print('  %-32s 失败 %s' % (url.split('/')[2], str(e)[:60]))
except Exception as e:  # noqa: BLE001
    print('  curl_cffi 不可用', str(e)[:60])
