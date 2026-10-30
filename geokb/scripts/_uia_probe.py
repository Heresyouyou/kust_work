# -*- coding: utf-8 -*-
"""探测 Edge 窗口的 UIA 树，定位「View PDF」按钮（只读，不点击）。

用法：
    python scripts/_uia_probe.py                # 自动挑一个 Edge 窗口
    python scripts/_uia_probe.py --hwnd 1234567
    python scripts/_uia_probe.py --all          # 打印所有非空元素（调试）
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time

import psutil
import uiautomation as auto

USER32 = ctypes.WinDLL('user32')
USER32.GetWindowTextW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
USER32.GetWindowTextLengthW.argtypes = (wt.HWND,)
USER32.GetClassNameW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
USER32.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))


def win_text(h):
    n = USER32.GetWindowTextLengthW(h)
    b = ctypes.create_unicode_buffer(n + 1)
    USER32.GetWindowTextW(h, b, n + 1)
    return b.value


def edge_windows():
    out = []

    def cb(h, _):
        if not USER32.IsWindowVisible(h):
            return True
        c = ctypes.create_unicode_buffer(256)
        USER32.GetClassNameW(h, c, 256)
        if c.value != 'Chrome_WidgetWin_1':
            return True
        pid = wt.DWORD()
        USER32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        try:
            if 'msedge' not in psutil.Process(pid.value).name().lower():
                return True
        except Exception:
            return True
        t = win_text(h)
        if t.strip():
            out.append((h, t))
        return True

    USER32.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb), 0)
    return out


def walk(win, max_depth=28, want_pdf=True, limit=40):
    t0 = time.time()
    rows = []
    seen = 0
    stack = [(win, 0)]
    while stack:
        ctl, d = stack.pop()
        if d > max_depth:
            continue
        try:
            kids = ctl.GetChildren()
        except Exception:
            continue
        for k in kids:
            seen += 1
            try:
                name = (k.Name or '').strip()
                ct = k.ControlTypeName
            except Exception:
                continue
            if name and (not want_pdf or 'pdf' in name.lower()
                         or 'download' in name.lower() or '下载' in name):
                r = k.BoundingRectangle
                rows.append((d, ct, name, r.left, r.top, r.right - r.left, r.bottom - r.top))
                if len(rows) >= limit:
                    return rows, seen, time.time() - t0
            stack.append((k, d + 1))
    return rows, seen, time.time() - t0


def main():
    if '--all' in sys.argv:
        auto.SetGlobalSearchTimeout(10)
    hwnd = None
    if '--hwnd' in sys.argv:
        hwnd = int(sys.argv[sys.argv.index('--hwnd') + 1])
    wins = edge_windows()
    if not wins:
        print('没有 Edge 窗口')
        return 2
    if not hwnd and '--match' in sys.argv:
        sub = sys.argv[sys.argv.index('--match') + 1].lower()
        cand = [(h, t) for h, t in wins if sub in t.lower()]
        if not cand:
            print('没有标题包含 %r 的窗口：' % sub)
            for h, t in wins:
                print('   ', h, t[:80])
            return 3
        hwnd, title = cand[0]
    if not hwnd:
        hwnd, title = wins[0]
    else:
        title = win_text(hwnd)
    print('窗口 hwnd=%s %r' % (hwnd, title[:80]))
    ctl = auto.ControlFromHandle(hwnd)
    rows, seen, dt = walk(ctl, want_pdf='--all' not in sys.argv)
    print('遍历 %d 个节点，命中 %d，用时 %.1fs' % (seen, len(rows), dt))
    for d, ct, name, l, t, w, h in rows:
        print('  d%-2d %-22s %-40r @(%d,%d) %dx%d' % (d, ct, name[:38], l, t, w, h))
    return 0


if __name__ == '__main__':
    sys.exit(main())
