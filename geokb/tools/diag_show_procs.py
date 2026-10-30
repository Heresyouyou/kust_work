# -*- coding: utf-8 -*-
"""查看两个门户服务的启动方式（是否 dev server / 线程数），只读。"""
import psutil

for p in psutil.process_iter(["pid", "name", "cmdline", "num_threads", "memory_info"]):
    try:
        cl = " ".join(p.info["cmdline"] or [])
    except Exception:
        continue
    if not cl:
        continue
    low = cl.lower()
    if ("5556" in cl or "5555" in cl or "server/app.py" in low.replace("\\", "/")
            or "waitress" in low or "supervisor" in low):
        if p.info["pid"] == psutil.Process().pid:
            continue
        print("PID %-7s thr=%-3s rss=%5.0fMB  %s" % (
            p.info["pid"], p.info["num_threads"],
            p.info["memory_info"].rss / 2**20, cl[:160]))
