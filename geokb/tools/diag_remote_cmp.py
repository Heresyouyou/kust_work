# -*- coding: utf-8 -*-
"""远程对比：定位 live 服务里多出来的 ~400ms 花在哪。只读 GET。"""
import json, statistics, time, urllib.request
import psutil

OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))

p = psutil.Process(7112)
print("live 5556 进程启动时间:", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.create_time())))
print("app.py 修改时间     :", time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime(__import__("os").path.getmtime(r"D:/ZSK/geokb/server/app.py"))))
print("retriever.py 修改时间:", time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime(__import__("os").path.getmtime(r"D:/ZSK/geokb/server/retriever.py"))))
print("线程数:", p.num_threads(), " 句柄:", p.num_handles())


def ms(url, n=7):
    xs = []
    for _ in range(n):
        t0 = time.perf_counter()
        r = OP.open(url, timeout=90)
        b = r.read()
        r.close()
        xs.append(((time.perf_counter() - t0) * 1000, len(b)))
    xs.sort()
    return statistics.median([x[0] for x in xs]), max(x[0] for x in xs), statistics.median([x[1] for x in xs])


B = "http://127.0.0.1:5556"
cases = [
    ("facets（基线）", B + "/api/facets"),
    ("search q 矽卡岩 limit=1 fields=id",
     B + "/api/search?q=%E7%9F%BD%E5%8D%A1%E5%B2%A9&limit=1&fields=id"),
    ("search q 矽卡岩 limit=20 fields=id",
     B + "/api/search?q=%E7%9F%BD%E5%8D%A1%E5%B2%A9&limit=20&fields=id"),
    ("search q 矽卡岩 limit=20 fuzzy=0",
     B + "/api/search?q=%E7%9F%BD%E5%8D%A1%E5%B2%A9&limit=20&fuzzy=0"),
    ("search q 矽卡岩 limit=20 expand=0",
     B + "/api/search?q=%E7%9F%BD%E5%8D%A1%E5%B2%A9&limit=20&expand=0"),
    ("search 罕见词 limit=20", B + "/api/search?q=zzzznotexist&limit=20"),
    ("entries?limit=20（列表页）", B + "/api/entries?limit=20"),
    ("entry 详情", None),
]
print("\n== 远程时延（中位数 / 最大 / 响应体）==")
for label, u in cases:
    if u is None:
        continue
    med, mx, ln = ms(u)
    print("  %-40s %7.1f / %7.1f ms  %6.1f KB" % (label, med, mx, ln / 1024))

# 详情页
r = json.loads(OP.open(B + "/api/entries?limit=1&fields=id,path", timeout=90).read())
eid = r["data"]["hits"][0]["id"]
med, mx, ln = ms(B + "/api/entry/" + eid)
print("  %-40s %7.1f / %7.1f ms  %6.1f KB  (%s)" % ("entry 详情(含 body)", med, mx, ln / 1024, eid))
med, mx, ln = ms(B + "/api/entry/%s/fulltext" % eid)
print("  %-40s %7.1f / %7.1f ms  %6.1f KB" % ("entry fulltext", med, mx, ln / 1024))
