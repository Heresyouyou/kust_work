# -*- coding: utf-8 -*-
"""模拟 pdf.js 分片加载：连续 20 片 256KB Range 请求，测每片耗时。只读。"""
import statistics, time, urllib.request

OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
EID = "paper-190f21ff7003"   # 20.45 MB
CHUNK = 262144


def run(base, label):
    xs = []
    fail = 0
    for i in range(20):
        a = i * CHUNK
        req = urllib.request.Request(base + "/api/entry/%s/pdf?inline=1" % EID
                                     if base.endswith("5556")
                                     else base + "/geokb/pdf/%s" % EID)
        req.add_header("Range", "bytes=%d-%d" % (a, a + CHUNK - 1))
        t0 = time.perf_counter()
        try:
            r = OP.open(req, timeout=60)
            b = r.read()
            r.close()
            xs.append((time.perf_counter() - t0) * 1000)
        except Exception:
            fail += 1
    if xs:
        print("  %-12s p50 %6.1f  p95 %6.1f  max %6.1f ms  失败 %d"
              % (label, statistics.median(xs), sorted(xs)[int(len(xs) * .95) - 1], max(xs), fail))


print("== 20 片 256KB Range 连续请求（20MB 扫描件）==")
run("http://127.0.0.1:5556", "5556 直出")
run("http://127.0.0.1:5555", "5555 代理")

print("\n== 对照：本地磁盘随机 256KB 读 ==")
import os
p = None
for dp, _dn, fn in os.walk("S:/GeoKB/papers_repo"):
    for f in fn:
        if f.endswith(".pdf"):
            q = os.path.join(dp, f)
            if os.path.getsize(q) > 15_000_000:
                p = q
                break
    if p:
        break
if p:
    xs = []
    with open(p, "rb") as f:
        for i in range(20):
            f.seek(i * CHUNK)
            t0 = time.perf_counter()
            f.read(CHUNK)
            xs.append((time.perf_counter() - t0) * 1000)
    print("  NAS 裸读      p50 %6.2f  max %6.2f ms" % (statistics.median(xs), max(xs)))
