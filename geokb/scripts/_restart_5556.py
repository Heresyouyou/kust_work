# -*- coding: utf-8 -*-
"""重启 GeoKB 5556：只杀「监听进程 + 其 python.exe 垫片父进程」，绝不碰 pythonw 守护。

纪律来源（2026-10-14 血的教训）：venv 的 python.exe 是垫片，会 spawn 同名子进程；
守护进程是 pythonw.exe 且命令行含 daemon，误杀会导致全服务停摆。
"""
import os
import subprocess
import sys
import time

import psutil


def listener_pid(port=5556):
    for c in psutil.net_connections(kind="inet"):
        if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN:
            return c.pid
    return None


def info(pid):
    try:
        p = psutil.Process(pid)
        return p.name(), (p.cmdline() or []), p.ppid()
    except Exception:
        return None, [], None


def main():
    pid = listener_pid()
    if not pid:
        print("no listener on 5556")
        return
    name, cmd, ppid = info(pid)
    print("listener pid=%s name=%s ppid=%s" % (pid, name, ppid))
    print("  cmd:", " ".join(cmd)[:160])

    targets = [pid]
    # 垫片父进程：同名 python.exe 且命令行含 geokb 路径但不含 daemon
    if ppid:
        pn, pc, _ = info(ppid)
        joined = " ".join(pc)
        if pn and pn.lower().startswith("python") and "geokb" in joined.lower() \
                and "daemon" not in joined.lower():
            print("  shim parent pid=%s name=%s cmd=%s" % (ppid, pn, joined[:160]))
            targets.append(ppid)
        else:
            print("  parent not shim (name=%s) -> 不动" % pn)

    for t in targets:
        try:
            psutil.Process(t).terminate()
            print("  terminated", t)
        except Exception as e:
            print("  terminate fail", t, e)

    for _ in range(30):
        time.sleep(1)
        np = listener_pid()
        if np and np not in targets:
            print("5556 back on pid=%s after %ds" % (np, _ + 1))
            return
    print("5556 NOT back within 30s — 检查守护 state/daemon.lock 与 logs/daemon.log")


if __name__ == "__main__":
    main()
