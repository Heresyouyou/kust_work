# -*- coding: utf-8 -*-
"""重启监听指定端口的 python 服务，并等守护进程拉起后探活。

用法： python scripts/_restart_port.py 5556 [路径前缀]
"""
import sys
import time
import urllib.request

import psutil

port = int(sys.argv[1])
probe = sys.argv[2] if len(sys.argv) > 2 else '/health'

pid = None
for p in psutil.process_iter(['pid', 'name', 'cmdline']):
    try:
        if p.info.get('name') != 'python.exe':
            continue
        for c in (p.net_connections(kind='inet') or []):
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port:
                pid = p.pid
                print('port=%d pid=%s cmd=%s' % (port, pid, ' '.join(p.info.get('cmdline') or [])[:180]))
                break
        if pid:
            break
    except Exception:
        continue

if not pid:
    print('端口 %d 没有 python 进程在监听' % port)
    raise SystemExit(1)

psutil.Process(pid).terminate()
print('已 terminate，等待守护拉起…')

url = 'http://127.0.0.1:%d%s' % (port, probe)
# 本机 HTTP_PROXY 会把 127.0.0.1 也劫持走（实测 502 Bad Gateway），必须显式置空
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for i in range(40):
    time.sleep(1.5)
    try:
        with opener.open(url, timeout=3) as r:
            body = r.read(200).decode('utf-8', 'ignore')
            print('探活成功（%.1fs）HTTP %s：%s' % ((i + 1) * 1.5, r.status, body[:160]))
            break
    except Exception as e:
        last = e
else:
    print('⚠️ 60s 内未探活：%s' % last)
