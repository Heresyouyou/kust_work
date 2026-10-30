# -*- coding: utf-8 -*-
"""看 5556 进程的 CPU 是否被某个线程吃掉（导致所有请求被拖慢）。只读。"""
import time
import psutil

p = psutil.Process(7112)
print("cpu_percent(1s采样) =", p.cpu_percent(interval=1.0))
print("num_threads =", p.num_threads())

def snap():
    d = {}
    for t in p.threads():
        d[t.id] = t.user + t.system
    return d

a = snap()
time.sleep(3.0)
b = snap()
print("\n3 秒内各线程 CPU 时间增量（Top 10，秒）:")
for tid, v in sorted(((k, b.get(k, 0) - a.get(k, 0)) for k in b), key=lambda x: -x[1])[:10]:
    print("  tid %-8s +%.3f s" % (tid, v))

print("\n进程 IO:", p.io_counters())
print("RSS = %.0f MB" % (p.memory_info().rss / 2**20))
print("\n创建时间:", time.strftime("%H:%M:%S", time.localtime(p.create_time())))
