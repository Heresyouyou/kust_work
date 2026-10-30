# -*- coding: utf-8 -*-
"""把 GeoKB 守护进程切换到新代码（带 EXTRA_PROCS 托管 history_queue_loop）。

步骤：停旧守护 -> 确认任务实例释放 -> 通过 Task Scheduler COM 按需 Run -> 校验。
失败时自动兜底：本地 Popen 一个守护（会话内有效），并明确报错让用户手动恢复。
"""
import os
import subprocess
import time

import psutil
import win32com.client

BASE = r"D:\ZSK\geokb"
PY = os.path.join(BASE, "venv", "Scripts", "python.exe")
PYW = os.path.join(BASE, "venv", "Scripts", "pythonw.exe")
DAEMON = os.path.join(BASE, "scripts", "geokb_daemon.py")
LOCK = os.path.join(BASE, "state", "daemon.lock")
PIDFILE = os.path.join(BASE, "state", "history_queue_loop.pid")
LOG = os.path.join(BASE, "logs", "daemon.log")


def tail(n=12):
    try:
        with open(LOG, encoding="utf-8", errors="ignore") as f:
            return [l.rstrip() for l in f.readlines()[-n:]]
    except Exception:
        return []


def daemons():
    out = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cl = " ".join(p.info["cmdline"] or [])
        except Exception:
            continue
        if "geokb_daemon.py" in cl:
            out.append(p.info["pid"])
    return out


print("=== STEP 0  当前状态 ===")
print("daemon pids:", daemons())
print("loop pidfile:", os.path.exists(PIDFILE))

print("\n=== STEP 1  停止旧守护 ===")
for pid in daemons():
    try:
        psutil.Process(pid).terminate()
        print("  terminated", pid)
    except Exception as e:
        print("  terminate fail", pid, e)
time.sleep(3)
left = daemons()
if left:
    for pid in left:
        try:
            psutil.Process(pid).kill()
            print("  killed", pid)
        except Exception as e:
            print("  kill fail", pid, e)
    time.sleep(2)
print("daemon pids after stop:", daemons())
try:
    if os.path.exists(LOCK):
        os.remove(LOCK)
        print("  stale daemon.lock removed")
except Exception as e:
    print("  lock remove fail:", e)

print("\n=== STEP 2  通过计划任务拉起新守护 ===")
ok = False
try:
    svc = win32com.client.Dispatch("Schedule.Service")
    svc.Connect()
    task = svc.GetFolder("\\").GetTask("GeoKBDaemon")
    for attempt in range(1, 7):
        state = task.State
        print("  attempt %d: task state=%s" % (attempt, state))
        if state != 4:                      # 4 = Running
            try:
                task.Run("")
                print("  Run() ok")
                ok = True
                break
            except Exception as e:
                print("  Run() failed:", e)
        time.sleep(3)
except Exception as e:
    print("  COM setup failed:", e)

if not ok:
    print("\n[FALLBACK] COM 拉起失败，本地兜底启动守护（会话内有效）")
    subprocess.Popen([PYW, DAEMON, "run"], cwd=BASE,
                     creationflags=0x08000000, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True)

print("\n=== STEP 3  等待 30s 后校验 ===")
time.sleep(30)
pids = daemons()
print("daemon pids:", pids)
for pid in pids:
    try:
        p = psutil.Process(pid)
        pp = p.parent()
        print("  pid=%s parent=%s(%s)" % (pid, pp.name() if pp else "?", pp.pid if pp else "?"))
    except Exception:
        pass
print("loop pidfile exists:", os.path.exists(PIDFILE),
      open(PIDFILE, encoding="utf-8").read().strip() if os.path.exists(PIDFILE) else "")
print("--- daemon.log ---")
for l in tail(15):
    print(" ", l)
