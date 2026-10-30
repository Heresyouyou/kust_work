#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""esf OCR 守护：内存不足时静默等待，内存恢复后自动续跑，直到 573 篇齐了或超时。

背景（2026-10-29）
------------------
--workers 4 直接起跑时，ONNX Runtime 在「可用内存仅 ~1 GB、swap 6.6 GB」的条件下
无法分配 inference arena：同一进程里第 1 页能跑（但要 77 秒），第 2 页起 0.6 秒即抛
`ONNXRuntime inferece failed.`，连续 4 篇全废。
占用大头是 07:37 启动的 ingest_external_feeds.py（6.0 GB RSS）——那是正常在跑的业务
管线，不能为了 OCR 去杀。因此改为「等它跑完、内存回落后再续跑」。

判据与动作
----------
- 有 esf_ocr_text.py 在跑 → 什么都不做
- 可用内存 < MEM_MIN_GB → 等待（每 60s 复查，每 10min 记一行日志）
- 可用内存达标 → 分离进程拉起 --all --workers 4（脚本自带断点续跑，已完成的跳过）
- 产物数 >= TARGET 或超过 MAX_HOURS → 退出
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PY = BASE / "venv" / "Scripts" / "python.exe"
OUTDIR = BASE / "state" / "esf_text"
LOG = BASE / "logs" / "esf_ocr_all.log"
WLOG = BASE / "logs" / "esf_ocr_watch.log"

TARGET = 573
MEM_MIN_GB = 3.5          # 4 个 worker 峰值约 1.5 GB，留足余量
MAX_HOURS = 16
POLL = 60
DETACHED = 0x00000008 | 0x00000200 | 0x08000000


def n_txt() -> int:
    try:
        return sum(1 for p in OUTDIR.iterdir() if p.suffix == ".txt")
    except FileNotFoundError:
        return 0


def ocr_running() -> bool:
    import psutil
    me = os.getpid()
    for p in psutil.process_iter(["pid", "cmdline"]):
        if p.info["pid"] == me:
            continue
        cl = p.info["cmdline"] or []
        if any(a == "-c" for a in cl):
            continue
        if any(a.endswith("esf_ocr_text.py") for a in cl):
            return True
    return False


def avail_gb() -> float:
    import psutil
    return psutil.virtual_memory().available / 1e9


def log(msg: str) -> None:
    line = "%s %s\n" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    with open(WLOG, "a", encoding="utf-8") as fh:
        fh.write(line)


def launch() -> int:
    cmd = [str(PY), str(BASE / "scripts" / "esf_ocr_text.py"), "--all", "--workers", "4"]
    with open(LOG, "a", encoding="utf-8", errors="replace") as fh:
        fh.write("\n# 守护续跑 %s\n# cmd: %s\n"
                 % (dt.datetime.now().isoformat(timespec="seconds"), " ".join(cmd)))
        fh.flush()
        proc = subprocess.Popen(
            cmd, cwd=str(BASE), stdout=fh, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=DETACHED, close_fds=True)
    return proc.pid


def main() -> int:
    t0 = time.time()
    log("守护启动 pid=%d 已有 %d 篇 阈值 %.1f GB" % (os.getpid(), n_txt(), MEM_MIN_GB))
    last_note = 0.0
    launches = 0
    last_launch_n = None
    last_launch_t = 0.0
    while True:
        el = (time.time() - t0) / 3600
        n = n_txt()
        if n >= TARGET:
            log("完成 %d/%d，守护退出" % (n, TARGET))
            return 0
        if el > MAX_HOURS:
            log("超时 %.1f h，当前 %d/%d，守护退出" % (el, n, TARGET))
            return 0
        if ocr_running():
            time.sleep(POLL)
            continue
        av = avail_gb()
        if av >= MEM_MIN_GB:
            # 防重启风暴：上一轮跑完若一篇没多出来，说明条件仍不合适，退避 10 分钟再试
            if last_launch_n is not None and n <= last_launch_n:
                if time.time() - last_launch_t < 600:
                    time.sleep(POLL)
                    continue
                log("上次拉起无新增产物（%d 篇），退避后再试" % n)
            last_launch_n = n
            last_launch_t = time.time()
            pid = launch()
            launches += 1
            log("内存达标 %.2f GB，已拉起 OCR pid=%d（第 %d 次），当前 %d 篇"
                % (av, pid, launches, n))
            time.sleep(90)          # 等它把模型加载起来，别重复拉起
            continue
        if time.time() - last_note > 600:
            log("等待内存：可用 %.2f GB < %.1f GB，当前 %d/%d 篇" % (av, MEM_MIN_GB, n, TARGET))
            last_note = time.time()
        time.sleep(POLL)


if __name__ == "__main__":
    raise SystemExit(main())
