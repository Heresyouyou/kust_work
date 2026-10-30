"""抓「看得见的黑窗」——直接枚举可见控制台窗口，而不是猜 conhost。

判据（这才是用户眼睛看到的东西）
--------------------------------
`EnumWindows` 找出所有顶层窗口 → 类是 `ConsoleWindowClass`（传统 conhost）
或 `CASCADIA_HOSTING_WINDOW_CLASS`（Windows Terminal）→ `IsWindowVisible()` 为真
→ 就是桌面上真实可见的黑窗。再反查它的 pid 与父链，就知道是谁。

⚠️ 不能用「出现了 conhost.exe」当判据：`CREATE_NO_WINDOW` 也会给子进程
分配一个**隐藏**控制台并配 conhost，窗口根本不可见（2026-10-27 踩过，误报）。

用法：python state/_watch_window.py 300
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time
from datetime import datetime

import psutil

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.restype = wt.BOOL

ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

CONSOLE_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS",
                   "PseudoConsoleWindow"}

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_window_hits.log")
DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 240.0

seen = set()


def visible_consoles():
    """返回当前所有可见控制台窗口：[(hwnd, class, title, pid, rect)]"""
    out = []

    def cb(hwnd, _lp):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(256)
            n = user32.GetClassNameW(hwnd, buf, 256)
            if n <= 0 or buf.value not in CONSOLE_CLASSES:
                return True
            cls = buf.value
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, 512)
            pid = wt.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            out.append((hwnd, cls, title.value, pid.value,
                        (r.left, r.top, r.right - r.left, r.bottom - r.top)))
        except Exception:
            pass
        return True

    user32.EnumWindows(ENUMPROC(cb), 0)
    return out


def chain(p, depth=5):
    out, cur = [], p
    for _ in range(depth):
        try:
            pp = cur.parent()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            break
        if pp is None:
            break
        try:
            out.append((pp.pid, pp.name(), " ".join(pp.cmdline() or [])[:200]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            out.append((pp.pid, "?", "<denied>"))
            break
        cur = pp
    return out


t0 = time.time()
with open(OUT, "a", encoding="utf-8") as log:
    log.write("# === 窗口采样开始 %s ===\n" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.flush()
    while time.time() - t0 < DUR:
        try:
            wins = visible_consoles()
        except Exception:
            time.sleep(0.2)
            continue
        for hwnd, cls, title, pid, rect in wins:
            if hwnd in seen:
                continue
            seen.add(hwnd)
            rec = {"ts": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                   "hwnd": int(hwnd), "class": cls, "title": title,
                   "pid": pid, "rect": rect}
            try:
                pr = psutil.Process(pid)
                rec["name"] = pr.name()
                rec["cmd"] = " ".join(pr.cmdline() or [])[:320]
                rec["age_s"] = round(time.time() - pr.create_time(), 1)
                rec["parents"] = chain(pr)
            except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                rec["name"] = "?"
                rec["cmd"] = "<gone: %s>" % type(e).__name__
                rec["parents"] = []
            log.write(json.dumps(rec, ensure_ascii=False) + "\n")
            log.flush()
            print("=" * 72)
            print("[黑窗] %s  pid=%s  %s  %dx%d" % (rec["ts"], pid, rec.get("name"),
                                                    rect[2], rect[3]))
            print("  标题  : %s" % (title or "<空>"))
            print("  命令行: %s" % (rec["cmd"] or "<空>"))
            for i, (ppid, pn, pc) in enumerate(rec.get("parents") or []):
                print("  %s父(%s) %s %s" % ("  " * (i + 1), ppid, pn, pc[:150]))
        # 记录窗口消失（用于确认「弹出又关闭」的时长）
        now = {w[0] for w in wins}
        for hwnd in list(seen):
            if hwnd not in now:
                log.write(json.dumps({"ts": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                                      "hwnd": int(hwnd), "event": "closed"},
                                     ensure_ascii=False) + "\n")
                log.flush()
                seen.discard(hwnd)
        time.sleep(0.15)

print("\n# 采样 %.0fs，捕获可见控制台窗口 %d 次" % (DUR, len(seen)))
