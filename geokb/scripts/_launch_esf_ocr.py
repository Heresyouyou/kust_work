#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 esf_ocr_text.py 以分离进程方式拉起，stdout/stderr 落到 logs/esf_ocr_all.log。

为什么要分离：OCR 全量要跑数小时，不能用会话内的前台/后台 shell 承载
（会话结束会被回收）。这里用 DETACHED_PROCESS，父进程退出后子进程继续跑。
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PY = BASE / "venv" / "Scripts" / "python.exe"
LOG = BASE / "logs" / "esf_ocr_all.log"

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def main() -> int:
    if not PY.exists():
        print("缺少解释器: %s" % PY, file=sys.stderr)
        return 2
    LOG.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(PY), str(BASE / "scripts" / "esf_ocr_text.py"), "--all", "--workers", "4"]
    with open(LOG, "w", encoding="utf-8", errors="replace") as fh:
        fh.write("# 启动 %s\n# cmd: %s\n" % (dt.datetime.now().isoformat(timespec="seconds"), " ".join(cmd)))
        fh.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(BASE),
            stdout=fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
            close_fds=True,
        )
    print("pid=%d" % proc.pid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
