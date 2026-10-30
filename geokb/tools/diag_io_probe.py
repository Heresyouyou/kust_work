# -*- coding: utf-8 -*-
"""NAS(SMB) 与 HTTP 时延分布。只读。"""
import os, statistics, time, urllib.request

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_ms(url):
    t0 = time.perf_counter()
    r = OPENER.open(url, timeout=60)
    r.read()
    r.close()
    return (time.perf_counter() - t0) * 1000


def dist(label, fn, n=15):
    xs = []
    for _ in range(n):
        try:
            xs.append(fn())
        except Exception as e:
            xs.append(float("nan"))
    xs = [x for x in xs if x == x]
    if not xs:
        print("  %-34s 全部失败" % label)
        return
    xs.sort()
    print("  %-34s min %7.1f  p50 %7.1f  p95 %7.1f  max %7.1f ms"
          % (label, xs[0], statistics.median(xs), xs[int(len(xs) * 0.95) - 1], xs[-1]))


print("== HTTP 时延分布 (n=15) ==")
dist("GET /health", lambda: http_ms("http://127.0.0.1:5556/health"))
dist("GET /api/facets", lambda: http_ms("http://127.0.0.1:5556/api/facets"))
dist("GET /api/search?q=矽卡岩&limit=20",
     lambda: http_ms("http://127.0.0.1:5556/api/search?q=%E7%9F%BD%E5%8D%A1%E5%B2%A9&limit=20"))
dist("GET /api/search?q=矽卡岩 (自适应)",
     lambda: http_ms("http://127.0.0.1:5556/api/search?q=%E7%9F%BD%E5%8D%A1%E5%B2%A9"))
dist("GET /api/entries?limit=20", lambda: http_ms("http://127.0.0.1:5556/api/entries?limit=20"))

print("\n== NAS / 本地磁盘 IO 时延 ==")
root = "S:/GeoKB/papers_repo"
print("  root=%s  isdir=%s" % (root, os.path.isdir(root)))
dist("os.listdir(NAS by_journal 一级)", lambda: len(os.listdir(root + "/by_journal")) and 0, 5)

# 找一个真实期刊目录
jdir = None
try:
    for j in os.listdir(root + "/by_journal"):
        cand = root + "/by_journal/" + j
        if os.path.isdir(cand):
            for y in os.listdir(cand):
                yy = cand + "/" + y
                if os.path.isdir(yy):
                    jdir = yy
                    break
        if jdir:
            break
except Exception as e:
    print("  列举失败:", e)
if jdir:
    print("  取样目录:", jdir)
    dist("os.listdir(NAS 年目录, ~N 文件)", lambda: len(os.listdir(jdir)) and 0, 10)
    pdfs = [f for f in os.listdir(jdir) if f.lower().endswith(".pdf")]
    if pdfs:
        p = os.path.join(jdir, pdfs[0])
        sz = os.path.getsize(p)
        print("  取样 PDF: %s  %.2f MB" % (pdfs[0][:50], sz / 2**20))

        def read_all():
            t0 = time.perf_counter()
            with open(p, "rb") as f:
                while f.read(1 << 20):
                    pass
            return (time.perf_counter() - t0) * 1000
        dist("读整个 PDF (NAS 顺序读)", read_all, 5)
        print("     → %.1f MB/s" % (sz / 2**20 / max(statistics.median([read_all()]) / 1000, 1e-6)))

        def open_first():
            t0 = time.perf_counter()
            with open(p, "rb") as f:
                f.read(65536)
            return (time.perf_counter() - t0) * 1000
        dist("打开+读前 64KB (TTFB 近似)", open_first, 10)

print("\n== 本地磁盘对照（D 盘同尺寸文件）==")
d = "D:/ZSK/geokb/index/geokb.db"
dist("读本地 237MB 文件 8MB", lambda: _local() if False else None, 1) if False else None
t0 = time.perf_counter()
with open(d, "rb") as f:
    f.read(8 << 20)
print("  本地读 8MB: %.1f ms → %.0f MB/s"
      % ((time.perf_counter() - t0) * 1000, 8 / max(time.perf_counter() - t0, 1e-6)))
