#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""列出当前所有可见窗口标题（用于确认 Edge 是否真的打开了目标页）。
纯 ctypes，不依赖 pywin32。"""
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
EnumWindows = user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                     wintypes.LPARAM)
IsWindowVisible = user32.IsWindowVisible
GetWindowTextW = user32.GetWindowTextW
GetWindowTextLengthW = user32.GetWindowTextLengthW
GetWindowThreadProcessId = user32.GetWindowThreadProcessId

rows = []


def _cb(hwnd, _):
    if not IsWindowVisible(hwnd):
        return True
    n = GetWindowTextLengthW(hwnd)
    if n <= 0:
        return True
    buf = ctypes.create_unicode_buffer(n + 1)
    GetWindowTextW(hwnd, buf, n + 1)
    t = buf.value.strip()
    if not t:
        return True
    pid = wintypes.DWORD()
    GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    rows.append((pid.value, t))
    return True


EnumWindows(EnumWindowsProc(_cb), 0)

import psutil

print('可见窗口 %d 个：' % len(rows))
for pid, t in rows:
    try:
        name = psutil.Process(pid).name()
    except Exception:
        name = '?'
    low = t.lower()
    mark = ''
    if 'sciencedirect' in low or 'science' in low:
        mark = '  <<< 目标页?'
    if 'edge' in low or name.lower().startswith('msedge'):
        print(f'[{pid}] {name:<14} {t[:80]}{mark}')
    elif mark:
        print(f'[{pid}] {name:<14} {t[:80]}{mark}')
