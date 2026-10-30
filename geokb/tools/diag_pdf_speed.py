# -*- coding: utf-8 -*-
"""PDF 传输实测：5556 直出 vs 5555 两跳代理 vs NAS 原始读。只读。"""
import json, os, statistics, time, urllib.request

OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
B6 = "http://127.0.0.1:5556"
B5 = "http://127.0.0.1:5555"


def pick(n=3):
    """挑几个真有 PDF 的论文条目（papers 模块优先，文件更大）。"""
    out = []
    for mod in ("papers", "kpoints"):
        try:
            r = OP.open(B6 + "/api/entries?modules=%s&limit=30&fields=id,module" % mod, timeout=60)
            for h in json.loads(r.read())["data"]["hits"]:
                eid = h["id"]
                try:
                    m = json.loads(OP.open(B6 + "/api/entry/%s/pdf/meta" % eid, timeout=60).read())["data"]
                except Exception:
                    continue
                f = (m.get("files") or [{}])[0]
                if f.get("available") and (f.get("bytes") or 0) > 300_000:
                    out.append((eid, f["bytes"], f["rel_path"]))
                if len(out) >= n:
                    return out
        except Exception as e:
            print("  pick 失败:", e)
    return out


def fetch(url, headers=None, read_all=True):
    req = urllib.request.Request(url)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    t0 = time.perf_counter()
    r = OP.open(req, timeout=180)
    first = r.read(65536)
    t_first = (time.perf_counter() - t0) * 1000
    total = len(first) if read_all else 0
    if read_all:
        while True:
            c = r.read(1 << 18)
            if not c:
                break
            total += len(c)
    t_all = (time.perf_counter() - t0) * 1000
    r.close()
    return t_first, t_all, total, r.headers


picked = pick()
print("== 取样条目 ==")
for eid, sz, rel in picked:
    print("  %-40s %6.2f MB" % (eid, sz / 2**20))

for eid, sz, rel in picked[:3]:
    print("\n-- %s (%.2f MB) --" % (eid, sz / 2**20))

    # 5556 直出
    ttfb, tot, n, h = fetch(B6 + "/api/entry/%s/pdf?inline=1" % eid)
    print("  5556 直出      TTFB %7.1f ms  全量 %7.1f ms  %5.2f MB  %5.1f MB/s  status=%s"
          % (ttfb, tot, n / 2**20, (n / 2**20) / max(tot / 1000, 1e-6), 200))

    # 5555 两跳代理
    try:
        ttfb2, tot2, n2, h2 = fetch(B5 + "/geokb/api/entry/%s/pdf?inline=1" % eid)
        print("  5555 两跳代理  TTFB %7.1f ms  全量 %7.1f ms  %5.2f MB  %5.1f MB/s"
              % (ttfb2, tot2, n2 / 2**20, (n2 / 2**20) / max(tot2 / 1000, 1e-6)))
    except Exception as e:
        print("  5555 代理失败:", e)

    # NAS 原始读对照
    p = "S:/GeoKB/papers_repo/" + rel.replace("papers_repo/", "")
    if os.path.isfile(p):
        t0 = time.perf_counter()
        with open(p, "rb") as f:
            f.read(65536)
        t1 = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        with open(p, "rb") as f:
            sz2 = 0
            while True:
                c = f.read(1 << 20)
                if not c:
                    break
                sz2 += len(c)
        t2 = (time.perf_counter() - t0) * 1000
        print("  NAS 原始读     TTFB %7.1f ms  全量 %7.1f ms  %5.2f MB  %5.1f MB/s"
              % (t1, t2, sz2 / 2**20, (sz2 / 2**20) / max(t2 / 1000, 1e-6)))

    # Range 行为
    for base, tag in ((B6, "5556"), (B5 + "/geokb", "5555")):
        try:
            req = urllib.request.Request(base + "/api/entry/%s/pdf?inline=1" % eid)
            req.add_header("Range", "bytes=0-262143")
            r = OP.open(req, timeout=60)
            b = r.read()
            print("  %s Range 0-256K → HTTP %s  %d 字节  Content-Range=%s"
                  % (tag, r.status, len(b), r.headers.get("Content-Range")))
            r.close()
        except Exception as e:
            print("  %s Range 探测: %s" % (tag, e))
