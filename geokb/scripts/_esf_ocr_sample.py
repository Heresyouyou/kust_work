#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""采样 4 分钟：每 30 秒记录一次 state/esf_text 的 .txt 数量，估算速率与 ETA。"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "state" / "esf_text"
TARGET = 573
DUR = 240


def count() -> int:
    try:
        return sum(1 for p in OUT.iterdir() if p.suffix == ".txt")
    except FileNotFoundError:
        return -1


t0 = time.time()
samples = [(0.0, count())]
print("%s start=%d" % (dt.datetime.now().strftime("%H:%M:%S"), samples[0][1]), flush=True)
while time.time() - t0 < DUR:
    time.sleep(30)
    el = time.time() - t0
    c = count()
    samples.append((el, c))
    print("  +%3.0fs  n=%d" % (el, c), flush=True)

first, last = samples[0][0], samples[-1][0]
n0, n1 = samples[0][1], samples[-1][1]
dn = n1 - n0
dtm = (last - first) / 60.0
if dn > 0:
    rate = dn / dtm
    eta = (TARGET - n1) / rate
    print("RATE %.1f 篇/分钟 | +%d 篇 / %.1f 分钟 | 剩余 %d 篇 | ETA %.1f 小时"
          % (rate, dn, dtm, TARGET - n1, eta), flush=True)
else:
    print("RATE 0 | +0 篇 / %.1f 分钟 | 当前 %d 篇" % (dtm, n1), flush=True)
