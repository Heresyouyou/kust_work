# -*- coding: utf-8 -*-
"""在真实 Edge（Default profile）里「点按钮」抓 ScienceDirect 的 PDF。

与 sd_ui_fetch.py 的区别
------------------------
sd_ui_fetch 走「地址栏输 /pdfft」，缺 Referer 与页面下发的一次性 md5 令牌，
会被判异常（实测换来 IP 封禁）。
本脚本改为**真的点页面上的 View PDF**：用 UIA 在无障碍树里定位元素，
先试 InvokePattern，不支持就用鼠标点它的中心坐标 —— 浏览器会自己带上
Referer 和令牌，这条请求和真人点击在服务端看起来完全一样。

用法：
    # 只探测，不点击（安全）
    python scripts/sd_ui_click.py --pii S0024493726004172 --probe

    # 真跑
    python scripts/sd_ui_click.py --pii S0024493726004172 ^
        --out D:/ZSK/geokb/state/_sd_ui_test/S0024493726004172.pdf
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import psutil
import uiautomation as auto

BASE = Path(__file__).resolve().parent.parent
BLOCK_FILE = BASE / 'state' / 'sd_web_block.json'
MSEDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
ARTICLE = 'https://www.sciencedirect.com/science/article/pii/%s?via%%3Dihub'

USER32 = ctypes.WinDLL('user32', use_last_error=True)
USER32.GetWindowTextW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
USER32.GetWindowTextLengthW.argtypes = (wt.HWND,)
USER32.GetClassNameW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
USER32.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
USER32.GetWindowRect.argtypes = (wt.HWND, ctypes.POINTER(wt.RECT))
USER32.SetForegroundWindow.argtypes = (wt.HWND,)
USER32.ShowWindow.argtypes = (wt.HWND, ctypes.c_int)

# ── 鼠标（真点击，带真实坐标） ────────────────────────────────────────────
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wt.LONG), ('dy', wt.LONG), ('mouseData', wt.DWORD),
                ('dwFlags', wt.DWORD), ('time', wt.DWORD),
                ('dwExtraInfo', wt.WPARAM)]


class _U(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [('type', wt.DWORD), ('u', _U)]


USER32.SendInput.argtypes = (wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int)

_LOG = None


def log(msg):
    line = '[%s] %s' % (time.strftime('%H:%M:%S'), msg)
    print(line, flush=True)
    if _LOG:
        _LOG.write(line + '\n')
        _LOG.flush()


def screen_size():
    return (USER32.GetSystemMetrics(0), USER32.GetSystemMetrics(1))


def click_at(x, y):
    sw, sh = screen_size()
    ax = int(x * 65535 / max(1, sw - 1))
    ay = int(y * 65535 / max(1, sh - 1))
    def send(flag, dx=0, dy=0):
        inp = INPUT(type=0)          # INPUT_MOUSE
        inp.u.mi = MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=flag,
                              time=0, dwExtraInfo=0)
        USER32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, ax, ay)
    time.sleep(0.25)
    send(MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE, ax, ay)
    time.sleep(0.08)
    send(MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE, ax, ay)


# ── 窗口 ──────────────────────────────────────────────────────────────────
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
        out.append(h)
        return True

    USER32.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb), 0)
    return out


def launch(url, profile='Default'):
    before = set(edge_windows())
    args = [MSEDGE, '--profile-directory=%s' % profile, '--new-window', url]
    subprocess.Popen(args, close_fds=True, creationflags=0x00000008 | 0x00000200)
    hwnd, t0 = None, time.time()
    while time.time() - t0 < 30:
        time.sleep(0.6)
        for h in edge_windows():
            if h in before:
                continue
            if win_text(h).strip():
                hwnd = h
                break
        if hwnd:
            break
    return hwnd


def walk(root, max_depth=30, limit_nodes=20000):
    """广度优先遍历（Chromium 树较深，BFS 先拿到顶部的按钮）。"""
    q = [(root, 0)]
    n = 0
    while q and n < limit_nodes:
        ctl, d = q.pop(0)
        if d > max_depth:
            continue
        try:
            kids = ctl.GetChildren()
        except Exception:
            continue
        for k in kids:
            n += 1
            yield k, d + 1
            q.append((k, d + 1))


def find(root, pred, max_depth=30):
    for k, d in walk(root, max_depth):
        try:
            if pred(k):
                return k
        except Exception:
            continue
    return None


def rect_of(ctl):
    r = ctl.BoundingRectangle
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def activate(ctl, label):
    """先 Invoke，不行再鼠标点中心。"""
    try:
        pat = ctl.GetInvokePattern()
        if pat:
            pat.Invoke()
            log('  Invoke 成功：%s' % label)
            return True
    except Exception:
        pass
    try:
        l, t, w, h = rect_of(ctl)
        if w <= 0 or h <= 0:
            log('  ✗ 元素无尺寸，无法点：%s' % label)
            return False
        x, y = l + w // 2, t + h // 2
        log('  鼠标点击 (%d,%d)：%s' % (x, y, label))
        click_at(x, y)
        return True
    except Exception as e:
        log('  ✗ 点击失败 %s：%s' % (label, e))
        return False


# ── 闸门 ──────────────────────────────────────────────────────────────────
def block_state():
    try:
        return json.loads(BLOCK_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def mark_blocked(hours=24, note=''):
    now = datetime.now()
    st = block_state()
    st.update({
        'blocked_at': now.isoformat(timespec='seconds'),
        'blocked_until': (now + timedelta(hours=hours)).isoformat(timespec='seconds'),
        'reason': 'ScienceDirect 返回 IP blocked',
        'note': note,
        'history': (st.get('history') or [])[-9:] + [
            {'at': now.isoformat(timespec='seconds'), 'note': note}],
    })
    BLOCK_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding='utf-8')


def guard(min_gap):
    st = block_state()
    until = st.get('blocked_until')
    if until:
        try:
            left = datetime.fromisoformat(until) - datetime.now()
            if left.total_seconds() > 0:
                return False, '冷却期内，剩 %.1f 小时（至 %s）' % (
                    left.total_seconds() / 3600.0, until)
        except Exception:
            pass
    last = st.get('last_attempt_at')
    if last:
        try:
            gap = (datetime.now() - datetime.fromisoformat(last)).total_seconds()
            if gap < min_gap:
                return False, '距上次仅 %.0f 秒（下限 %d）' % (gap, min_gap)
        except Exception:
            pass
    return True, ''


def touch():
    st = block_state()
    st['last_attempt_at'] = datetime.now().isoformat(timespec='seconds')
    BLOCK_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding='utf-8')


# ── 主流程 ────────────────────────────────────────────────────────────────
def dismiss_overlays(win):
    """关掉 SD 的调查问卷、Edge 的翻译条 —— 它们会挡住 View PDF。"""
    hit = False
    for label in ('也许以后', '稍后再说', 'No thanks', 'Maybe later'):
        ctl = find(win, lambda k: (k.Name or '').strip() == label, max_depth=26)
        if ctl:
            log('关闭浮层：%s' % label)
            activate(ctl, label)
            time.sleep(0.8)
            hit = True
            break
    auto.SendKeys('{Esc}', waitTime=0.2)
    time.sleep(0.4)
    return hit


def find_view_pdf(win):
    """在树里找 View PDF 控件，返回 [(ctl, name, l, t, w, h)]。

    注意：正文参考文献里每一条也有一个 'View PDF' 链接（y 可到几万像素），
    必须靠屏幕坐标把它们和页头那个按钮区分开。
    """
    out = []
    sh = screen_size()[1]
    sw = screen_size()[0]
    for k, d in walk(win, max_depth=30):
        try:
            name = (k.Name or '').strip()
            if not name or 'view pdf' not in name.lower():
                continue
            l, t, w, h = rect_of(k)
        except Exception:
            continue
        in_view = (0 <= t <= sh) and (0 <= l <= sw) and w > 0 and h > 0
        out.append((k, name, l, t, w, h, in_view))
    # 视口内的排前面，再按 y 升序（页头按钮在最上）
    out.sort(key=lambda x: (0 if x[6] else 1, x[3]))
    return out


def main():
    global _LOG
    ap = argparse.ArgumentParser()
    ap.add_argument('--pii')
    ap.add_argument('--url')
    ap.add_argument('--out')
    ap.add_argument('--probe', action='store_true')
    ap.add_argument('--wait', type=int, default=300, help='等 PDF 渲染的最长秒数')
    ap.add_argument('--log')
    ap.add_argument('--min-gap', type=int, default=600)
    ap.add_argument('--override', action='store_true')
    ap.add_argument('--keep-open', action='store_true')
    ap.add_argument('--hwnd', type=int,
                    help='接管一个已打开的 PDF 窗口，只做「另存」，不再启动/点击')
    args = ap.parse_args()

    if args.log:
        _LOG = open(args.log, 'a', encoding='utf-8')
    auto.SetGlobalSearchTimeout(10)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    if not args.probe:
        ok, why = guard(args.min_gap)
        if not ok and not args.override:
            print('拒绝运行：%s' % why, file=sys.stderr)
            return 9

    url = args.url or (ARTICLE % args.pii)

    # 接管模式：人工已经把 PDF 打开（甚至通过了验证），这里只负责另存
    if args.hwnd:
        target = Path(args.out).resolve() if args.out else None
        if not target:
            print('需要 --out', file=sys.stderr)
            return 2
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        log('接管 hwnd=%s 标题=%r' % (args.hwnd, win_text(args.hwnd)[:80]))
        return wait_and_save(args.hwnd, target, args)

    log('打开 %s' % url)
    hwnd = launch(url)
    if not hwnd:
        log('✗ 没抓到新窗口')
        return 2
    log('窗口 hwnd=%s' % hwnd)
    USER32.ShowWindow(hwnd, 3)               # 最大化
    time.sleep(1.2)

    # 等标题变成论文名
    t0 = time.time()
    while time.time() - t0 < 45:
        t = win_text(hwnd)
        if t and 'sciencedirect.com' not in t.lower() and '无标题' not in t \
                and 'about:blank' not in t:
            break
        time.sleep(1.5)
    log('标题 = %r' % win_text(hwnd)[:100])
    if any(k in win_text(hwnd).lower() for k in ('blocked', 'problem providing')):
        mark_blocked(note='文章页直接命中封禁')
        return 7
    time.sleep(3)

    win = auto.ControlFromHandle(hwnd)
    dismiss_overlays(win)
    time.sleep(1.0)

    cands = find_view_pdf(win)
    log('找到 %d 个 View PDF 控件' % len(cands))
    for ctl, name, l, t, w, h, in_view in cands[:6]:
        log('  %-32r @(%d,%d) %dx%d%s'
            % (name[:30], l, t, w, h, ' [视口内]' if in_view else ''))

    if args.probe:
        log('探测模式，不点击')
        return 0

    target = Path(args.out).resolve() if args.out else None
    if not target:
        print('需要 --out', file=sys.stderr)
        return 2
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()

    pick = next((c for c in cands if c[6]), None)
    if not pick:
        log('✗ 视口内没有可点的 View PDF')
        return 4
    ctl, name, l, t, w, h, _ = pick
    touch()
    log('点击：%r @(%d,%d)' % (name[:40], l, t))
    activate(ctl, name)
    return wait_and_save(hwnd, target, args)


def all_msedge_native_dialogs():
    """找「另存为」原生对话框。

    ⚠️ 两个坑（2026-10-21 实测）：
      1. 保存对话框的 hwnd 属于 **另一个 msedge 进程**，用页面窗口的 pid 找不到；
      2. Edge 的默认下载目录被配成 `E:\\`，而 E 盘不存在 → Ctrl+S / 工具栏保存图标
         全部**静默失败**，既不弹框也不报错，看起来像"没反应"。
    因此这里按「任意 msedge 进程 + 标题含另存为」来找。
    """
    out = []

    def cb(h, _):
        if not USER32.IsWindowVisible(h):
            return True
        c = ctypes.create_unicode_buffer(256)
        USER32.GetClassNameW(h, c, 256)
        if c.value != '#32770':
            return True
        pid = wt.DWORD()
        USER32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        try:
            if 'msedge' not in psutil.Process(pid.value).name().lower():
                return True
        except Exception:
            return True
        t = win_text(h)
        if '另存为' in t or 'Save As' in t:
            out.append((h, t))
        return True

    USER32.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb), 0)
    return out


def fill_native_dialog(target, timeout=60):
    """把完整路径塞进另存为对话框并回车。"""
    import sd_ui_fetch as F
    t0 = time.time()
    while time.time() - t0 < timeout:
        dl = all_msedge_native_dialogs()
        if dl:
            h = dl[0][0]
            F.focus_window(h)
            time.sleep(0.6)
            F.chord([F.VK_CONTROL], F.VK_A)
            time.sleep(0.2)
            F.type_text(str(target))
            time.sleep(0.35)
            F.tap(F.VK_RETURN)
            for _ in range(30):
                time.sleep(0.6)
                if not USER32.IsWindow(h):
                    return True
            return False
        time.sleep(0.8)
    return False


def save_via_print(hwnd, target):
    """打印 → 另存为 PDF → 保存。这是唯一能绕开「E:\\ 不存在」的保存路径。

    为什么不 Ctrl+S：Edge 对 PDF 的 Ctrl+S 走的是浏览器下载通道，目标目录无效
    时会静默失败；而打印预览的「保存」永远弹原生对话框，路径可控。
    """
    import sd_ui_fetch as F
    F.focus_window(hwnd)
    time.sleep(0.4)
    F.tap(F.VK_ESCAPE)               # 先关掉可能残留的查找栏
    time.sleep(0.3)
    F.chord([F.VK_CONTROL], 0x50)    # Ctrl+P
    log('  已发 Ctrl+P，等打印预览')
    time.sleep(3.5)

    win = auto.ControlFromHandle(hwnd)
    btn = find(win, lambda k: (k.ControlTypeName == 'ButtonControl'
                               and (k.Name or '').strip() == '保存'), max_depth=30)
    if not btn:
        log('  ✗ 打印预览里没有「保存」按钮')
        return False
    l, t, w, h = rect_of(btn)
    log('  打印预览就绪，点「保存」@(%d,%d)' % (l, t))
    activate(btn, '保存')
    time.sleep(2.5)
    if not fill_native_dialog(target):
        log('  ✗ 另存为对话框未完成')
        return False
    time.sleep(2)
    good, detail = F.is_real_pdf(target)
    log('  校验 %s ｜ %s' % (good, detail))
    return good


def wait_and_save(hwnd, target, args):
    """点完 View PDF 后：等 PDF 渲染 → 打印另存为 PDF → 校验页数。"""
    import sd_ui_fetch as F

    wait = getattr(args, 'wait', 300)
    t0 = time.time()
    last = None
    blocked = False
    while time.time() - t0 < wait:
        t = win_text(hwnd)
        if t != last:
            log('  标题 → %r' % t[:100])
            last = t
            low = t.lower()
            if 'security verification' in low or 'request verification' in low:
                log('⏸ 出现 ScienceDirect 人机验证页（需人工点一次，不接受自动破解）')
                blocked = True
            if 'has been blocked' in low or 'problem providing' in low:
                mark_blocked(note='点击 View PDF 后命中封禁')
                return 7
        if 'pdf.sciencedirectassets.com' in t.lower() or t.lower().endswith('.pdf'):
            log('  PDF 已加载')
            break
        time.sleep(3)

    if blocked and time.time() - t0 >= wait:
        log('✗ 验证页未通过（%.0fs 超时）' % wait)
        return 6

    time.sleep(2)
    for attempt in range(1, 3):
        log('第 %d 轮打印另存为 PDF' % attempt)
        if save_via_print(hwnd, target):
            log('✓ 成功：%s（%d KB）' % (target, target.stat().st_size // 1024))
            return 0
        time.sleep(3)
    return 5


if __name__ == '__main__':
    sys.exit(main() or 0)
