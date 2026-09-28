# -*- coding: utf-8 -*-
"""重启 kust.work 的 Flask 进程（改过 .py 之后用）。

为什么不用 taskkill / Stop-Process：受保护进程会 WinError 5。可靠做法只有
ctypes OpenProcess(PROCESS_TERMINATE) → TerminateProcess。

安全约束（宁可拒绝也不误杀）：
  1. 只杀 `.flask_5555.pid` 里记的那个 PID；
  2. 该 PID 必须**确实在监听 5555**（netstat 校验），否则直接放弃 ——
     5555 上还有一个 svchost 端口代理也处于 LISTENING，写错就误杀系统进程；
  3. 默认要求 `/api/portal/health` 的 `active == 0`（没有翻译任务在跑）才算安全窗口，
     要强行重启加 `--force`。

用法：
    python tools/restart_web.py            # 检查安全窗口 → kill → 等守护拉起
    python tools/restart_web.py --force    # 跳过 active 检查
    python tools/restart_web.py --check    # 只看状态，不动手
"""
import argparse
import ctypes
import re
import subprocess
import sys
import time
import urllib.request

ROOT = None
for _cand in (__import__('pathlib').Path(__file__).resolve().parents[1],):
    ROOT = _cand
PID_FILE = ROOT / ".flask_5555.pid"
HEALTH = "http://127.0.0.1:5555/api/portal/health"
PROCESS_TERMINATE = 0x0001


def _opener():
    """本机回环绝不能被 HTTP_PROXY 接管（站点经典坑）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health(timeout=5):
    try:
        with _opener().open(HEALTH, timeout=timeout) as r:
            import json
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as exc:
        return {"_err": repr(exc)}


def listeners_5555():
    """netstat 里 LISTENING 5555 的 PID 集合（含 svchost 端口代理，故必须核对）。"""
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                         errors="replace").stdout
    pids = set()
    for line in out.splitlines():
        if "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(":5555"):
            pids.add(parts[-1])
    return pids


def kill(pid):
    h = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not h:
        raise SystemExit("OpenProcess 失败（pid=%s），err=%s" % (pid, ctypes.get_last_error()))
    try:
        if not ctypes.windll.kernel32.TerminateProcess(h, 0):
            raise SystemExit("TerminateProcess 失败（pid=%s）" % pid)
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def wait_up(timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = health()
        if isinstance(h, dict) and not h.get("_err"):
            return round(time.time() - t0, 1), h
        time.sleep(3)
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="跳过 active==0 的安全窗口检查")
    ap.add_argument("--check", action="store_true", help="只看状态，不重启")
    a = ap.parse_args()

    h = health()
    alive = isinstance(h, dict) and not h.get("_err")
    pid_txt = PID_FILE.read_text(encoding="utf-8").strip() if PID_FILE.exists() else ""
    ls = listeners_5555()
    print("health      : %s" % h)
    print("pid 文件    : %s" % (pid_txt or "(无)"))
    print("5555 监听者 : %s" % ", ".join(sorted(ls)) or "(无)")

    if a.check:
        return 0
    if not alive:
        raise SystemExit("服务当前不可达，拒绝动手（先看 .flask_5555.pid 与日志）")
    if not pid_txt or pid_txt not in ls:
        raise SystemExit("pid 文件里的 %r 不在 5555 监听者中，拒绝 kill（防误杀 svchost）" % pid_txt)
    active = h.get("active")
    if active not in (0, None) and not a.force:
        raise SystemExit("active=%s，有任务在跑，不是安全窗口；确认要重启加 --force" % active)

    print("→ kill pid=%s" % pid_txt)
    kill(int(pid_txt))

    dt, h2 = wait_up()
    if dt is None:
        print("!! 180s 内未恢复：看 logs/flask.out.log 与 PostNewsSupervisor 状态")
        return 1
    new_pid = PID_FILE.read_text(encoding="utf-8").strip() if PID_FILE.exists() else ""
    print("✓ %ss 后已上线：pid=%s health=%s" % (dt, new_pid, h2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
