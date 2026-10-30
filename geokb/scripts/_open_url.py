#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""打开 URL 并把浏览器窗口提到前台（零自动化参数）。

两个手段：
  1. ShellExecuteW("open", url) —— 走系统关联，进程归 explorer 托管，
     不会被 agent 会话的作业对象回收；Edge 对**已打开的同一 URL 会复用并激活**
     那个标签，所以重复调用等于「把那个标签切到前台」。
  2. 枚举可见窗口，把 msedge.exe 的主窗口 SetForegroundWindow + ShowWindow。

用法：python _open_url.py <url>
"""
import ctypes
import sys
import time
from ctypes import wintypes

DEFAULT = 'https://www.sciencedirect.com/science/article/pii/S0169136823004328'
url = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT).strip()
if not url.lower().startswith(('http://', 'https://')):
    raise SystemExit('not a url: %r' % url)

shell32 = ctypes.windll.shell32
shell32.ShellExecuteW.restype = ctypes.c_void_p
rc = shell32.ShellExecuteW(None, 'open', url, None, None, 1)
n = int(rc) if rc else 0
print('ShellExecuteW -> %d (%s)' % (n, 'ok' if n > 32 else 'FAILED'))

time.sleep(1.2)

user32 = ctypes.windll.user32
PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
targets = []


def _cb(hwnd, _):
    if not user32.IsWindowVisible(hwnd):
        return True
    L = user32.GetWindowTextLengthW(hwnd)
    if L <= 0:
        return True
    buf = ctypes.create_unicode_buffer(L + 1)
    user32.GetWindowTextW(hwnd, buf, L + 1)
    t = buf.value
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if 'Microsoft Edge' in t or 'Microsoft\u200b Edge' in t:
        targets.append((hwnd, t, pid.value))
    return True


user32.EnumWindows(PROC(_cb), 0)

if not targets:
    print('未找到 Edge 窗口')
    sys.exit(0)

for hwnd, t, pid in targets:
    user32.ShowWindow(hwnd, 9)          # SW_RESTORE
    ok = user32.SetForegroundWindow(hwnd)
    print('前台化 %s -> %s | %s' % (pid, bool(ok), t[:70]))
