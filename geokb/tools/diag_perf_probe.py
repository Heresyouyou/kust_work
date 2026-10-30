# -*- coding: utf-8 -*-
"""只读性能探针：DB 体积构成 + 在线接口时延。不做任何写操作。"""
import json, os, sqlite3, sys, time, urllib.request

BASE = r"D:/ZSK/geokb"
DB = os.path.join(BASE, "index", "geokb.db")

print("=" * 60)
print("DB 体积构成（只读）")
print("=" * 60)
conn = sqlite3.connect(DB)
pc = conn.execute("PRAGMA page_count").fetchone()[0]
fl = conn.execute("PRAGMA freelist_count").fetchone()[0]
ps = conn.execute("PRAGMA page_size").fetchone()[0]
print("page_size=%d  page_count=%d  freelist=%d (%.1f MB 空洞 / %.1f%%)"
      % (ps, pc, fl, fl * ps / 2**20, 100.0 * fl / max(pc, 1)))
print("文件 %.1f MB" % (os.path.getsize(DB) / 2**20))
print("journal_mode=%s  auto_vacuum=%s" % (
    conn.execute("PRAGMA journal_mode").fetchone()[0],
    conn.execute("PRAGMA auto_vacuum").fetchone()[0]))

try:
    print("\n-- dbstat 各对象占用 Top10 --")
    rows = conn.execute(
        "SELECT name, SUM(pgsize) sz FROM dbstat GROUP BY name ORDER BY sz DESC LIMIT 12"
    ).fetchall()
    for name, sz in rows:
        print("  %-28s %8.1f MB" % (name, sz / 2**20))
except Exception as e:
    print("  dbstat 不可用:", e)

print("\n-- 行数与文本量 --")
for t in ("entries", "fts_entries"):
    try:
        n = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        print("  %-14s %d 行" % (t, n))
    except Exception as e:
        print("  %-14s %s" % (t, e))
try:
    tot = conn.execute("SELECT SUM(LENGTH(body)), AVG(LENGTH(body)), MAX(LENGTH(body)) FROM entries").fetchone()
    print("  body 总量 %.1f MB 均值 %.0f 字符 最大 %d" % (tot[0] / 2**20, tot[1] or 0, tot[2] or 0))
except Exception as e:
    print("  body 统计失败:", e)
try:
    n = conn.execute("SELECT COUNT(*) FROM vec_entries").fetchone()[0]
    print("  vec_entries %d 行 (512-dim float32 理论 %.1f MB)" % (n, n * 512 * 4 / 2**20))
except Exception as e:
    print("  vec_entries:", e)

print("\n-- 索引清单 --")
for r in conn.execute("SELECT name, tbl_name FROM sqlite_master WHERE type='index'"):
    print("  %-28s on %s" % (r[0], r[1]))
conn.close()

# ---------------------------------------------------------------- 在线时延
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def timed(url, method="GET", data=None, timeout=30):
    req = urllib.request.Request(url, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(data).encode()
    t0 = time.perf_counter()
    try:
        r = OPENER.open(req, timeout=timeout)
        body = r.read()
        dt = (time.perf_counter() - t0) * 1000
        return dt, len(body), r.status, body
    except Exception as e:
        return (time.perf_counter() - t0) * 1000, 0, "ERR:%s" % e, b""


print("\n" + "=" * 60)
print("在线接口时延（localhost:5556，绕过代理）")
print("=" * 60)
for url, m, d in [
    ("http://127.0.0.1:5556/health", "GET", None),
    ("http://127.0.0.1:5556/api/search?q=%E7%9F%BD%E5%8D%A1%E5%B2%A9", "GET", None),
    ("http://127.0.0.1:5556/api/search?q=%E7%9F%BD%E5%8D%A1%E5%B2%A9&limit=20", "GET", None),
    ("http://127.0.0.1:5556/api/search", "POST", {"q": "garnet scheelite skarn", "limit": 10}),
    ("http://127.0.0.1:5556/api/entries?limit=20", "GET", None),
    ("http://127.0.0.1:5556/api/facets", "GET", None),
]:
    dt, n, st, _ = timed(url, m, d)
    print("  %-6s %-58s %7.1f ms  %6.1f KB  %s" % (m, url.split("5556")[-1][:58], dt, n / 1024, st))

# 冷 / 热两次，看模型与 SQLite 页缓存影响
print("\n  重复 3 次同一查询（看是否有一次性开销）:")
for i in range(3):
    dt, n, st, _ = timed("http://127.0.0.1:5556/api/search?q=%E7%9F%BD%E5%8D%A1%E5%B2%A9")
    print("    #%d  %.1f ms" % (i + 1, dt))

# ---------------------------------------------------------------- PDF
print("\n" + "=" * 60)
print("PDF 传输时延（本地回环，NAS 源）")
print("=" * 60)
dt, n, st, body = timed("http://127.0.0.1:5556/api/entries?limit=50")
try:
    ids = [h["id"] for h in json.loads(body)["hits"]]
except Exception:
    ids = []
picked = None
for eid in ids:
    dt, n, st, body = timed("http://127.0.0.1:5556/api/entry/%s/pdf/meta" % eid)
    try:
        d = json.loads(body)["data"]
        f = d["files"][0] if d["files"] else None
        if f and f.get("available"):
            picked = (eid, f["bytes"])
            print("  选中 %s  %.2f MB  root=%s" % (eid, (f["bytes"] or 0) / 2**20, d["pdf_root"]))
            break
    except Exception:
        continue

if picked:
    eid, nbytes = picked
    # 首字节时间 + 全量时间
    req = urllib.request.Request("http://127.0.0.1:5556/api/entry/%s/pdf?inline=1" % eid)
    t0 = time.perf_counter()
    r = OPENER.open(req, timeout=120)
    first = r.read(65536)
    t_first = (time.perf_counter() - t0) * 1000
    total = len(first)
    while True:
        chunk = r.read(1 << 18)
        if not chunk:
            break
        total += len(chunk)
    t_all = (time.perf_counter() - t0) * 1000
    print("  TTFB %.0f ms | 全量 %.1f MB %.0f ms → %.1f MB/s"
          % (t_first, total / 2**20, t_all, (total / 2**20) / max(t_all / 1000, 1e-6)))
    # Range 支持探测
    req = urllib.request.Request("http://127.0.0.1:5556/api/entry/%s/pdf" % eid)
    req.add_header("Range", "bytes=0-1023")
    try:
        r = OPENER.open(req, timeout=30)
        print("  Range 支持: HTTP %s  返回 %d 字节  Content-Range=%s"
              % (r.status, len(r.read()), r.headers.get("Content-Range")))
    except Exception as e:
        print("  Range 探测失败:", e)
else:
    print("  未找到可用 PDF 条目")
