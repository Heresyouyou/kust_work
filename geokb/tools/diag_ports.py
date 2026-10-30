# -*- coding: utf-8 -*-
"""列出关键端口的监听进程，判断公网入口前面有没有 nginx/caddy。只读。"""
import psutil

WANT = {5087, 5080, 5443, 5555, 5554, 5556, 80, 443}
for c in psutil.net_connections(kind="inet"):
    if c.status != psutil.CONN_LISTEN or not c.laddr:
        continue
    port = c.laddr.port
    if port not in WANT:
        continue
    try:
        p = psutil.Process(c.pid) if c.pid else None
        name = p.name() if p else "?"
        cl = " ".join(p.cmdline())[:110] if p else ""
    except Exception:
        name, cl = "?", "?"
    print("  :%-5d  %-14s pid=%-7s %s" % (port, name, c.pid, cl))
