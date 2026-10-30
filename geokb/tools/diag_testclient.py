# -*- coding: utf-8 -*-
"""用 Flask test_client 复现完整请求路径（不占端口、不起后台线程）。只读。"""
import os, statistics, sys, time

sys.path.insert(0, r"D:/ZSK/geokb/server")
os.chdir(r"D:/ZSK/geokb")
import app as appmod

c = appmod.app.test_client()


def t(url, n=6):
    xs = []
    ln = 0
    for _ in range(n):
        t0 = time.perf_counter()
        r = c.get(url)
        b = r.data
        xs.append((time.perf_counter() - t0) * 1000)
        ln = len(b)
    xs.sort()
    print("  %-52s p50 %7.1f  max %7.1f ms  %6.1f KB  [%d]"
          % (url[:52], statistics.median(xs), xs[-1], ln / 1024, r.status_code))


print("== test_client（同进程、完整 Flask 路径）==")
t("/api/facets")
t("/api/search?q=%E7%9F%BD%E5%8D%A1%E5%B2%A9&limit=1&fields=id")
t("/api/search?q=%E7%9F%BD%E5%8D%A1%E5%B2%A9&limit=20")
t("/api/entries?limit=20")
t("/api/expand?q=%E7%9F%BD%E5%8D%A1%E5%B2%A9")
