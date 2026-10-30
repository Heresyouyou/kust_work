# -*- coding: utf-8 -*-
"""用「模拟人工」的方式，在真实 Edge（Default profile）里打开论文并保存 PDF。

为什么必须这么做
----------------
ScienceDirect 的 /pdf、/pdfft 端点被 Cloudflare 内容防护（::CLOUDFLARE_ERROR_1000S_BOX::）
拦死，curl_cffi / requests / headless 全路径 403。而**真人点 View PDF 是能看到的**
（只是加载慢，几分钟）。差别不在 UA，而在：
  · 真实 Edge 的 TLS 指纹；
  · Default profile 里的机构登录态 cookie 与 cf_clearance；
  · 无自动化痕迹（不带 --remote-debugging-*、不复制 profile）。
Edge 136+ 起 --remote-debugging-port 对默认数据目录静默失效，没有合法开关能解除，
所以这里**完全不用 CDP**，改用 Win32 SendInput 模拟键盘操作：

  1) 起一个新 Edge 窗口（真实 msedge.exe + --profile-directory=Default）
  2) 打开文章页，等它渲染（Cloudflare 托管挑战在真实浏览器里会自动过）
  3) Ctrl+L → 输入 /pdfft?download=true → Enter
  4) 等 PDF 加载（可长达数分钟），然后用 Ctrl+S 另存为
  5) 用「文件头是否 %PDF + 页数」做客观校验，失败则加时重试

用法
----
    # 自检：只验证按键能否落到 Edge，不下载
    python scripts/sd_ui_fetch.py --self-test

    # 抓一篇
    python scripts/sd_ui_fetch.py --pii S0169136826004932 ^
        --out D:/ZSK/geokb/state/_sd_ui_test/S0169136826004932.pdf --wait 300

    # 拿现成网址
    python scripts/sd_ui_fetch.py --pdf-url "https://..." --out x.pdf

注意：运行期间不要动键盘鼠标（会把焦点抢走）。
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
BLOCK_FILE = BASE / 'state' / 'sd_web_block.json'
DEFAULT_COOLDOWN_H = 24

MSEDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
ARTICLE = 'https://www.sciencedirect.com/science/article/pii/%s'
PDF = 'https://www.sciencedirect.com/science/article/pii/%s/pdfft?isDTMRedir=true&download=true'

USER32 = ctypes.WinDLL('user32', use_last_error=True)
KERNEL32 = ctypes.WinDLL('kernel32', use_last_error=True)

# ── ctypes 结构 ────────────────────────────────────────────────────────────
ULONG_PTR = wt.WPARAM
LRESULT = ctypes.c_ssize_t

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_EXTENDEDKEY = 0x0001
INPUT_KEYBOARD = 1

VK_CONTROL = 0x11
VK_MENU = 0x12
VK_SHIFT = 0x10
VK_RETURN = 0x0D
VK_TAB = 0x09
VK_ESCAPE = 0x1B
VK_A = 0x41
VK_N = 0x4E
VK_S = 0x53
VK_W = 0x57


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wt.WORD), ('wScan', wt.WORD), ('dwFlags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wt.LONG), ('dy', wt.LONG), ('mouseData', wt.DWORD),
                ('dwFlags', wt.DWORD), ('time', wt.DWORD), ('dwExtraInfo', ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [('uMsg', wt.DWORD), ('wParamL', wt.WORD), ('wParamH', wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [('type', wt.DWORD), ('u', _INPUTUNION)]


class RECT(ctypes.Structure):
    _fields_ = [('left', wt.LONG), ('top', wt.LONG),
                ('right', wt.LONG), ('bottom', wt.LONG)]


WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

USER32.SendInput.argtypes = (wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
USER32.SendInput.restype = wt.UINT
USER32.EnumWindows.argtypes = (WNDENUMPROC, wt.LPARAM)
USER32.EnumWindows.restype = wt.BOOL
USER32.IsWindowVisible.argtypes = (wt.HWND,)
USER32.GetWindowTextLengthW.argtypes = (wt.HWND,)
USER32.GetWindowTextW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
USER32.GetClassNameW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
USER32.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
USER32.GetWindowThreadProcessId.restype = wt.DWORD
USER32.SetForegroundWindow.argtypes = (wt.HWND,)
USER32.ShowWindow.argtypes = (wt.HWND, ctypes.c_int)
USER32.BringWindowToTop.argtypes = (wt.HWND,)
USER32.GetForegroundWindow.restype = wt.HWND
USER32.GetWindowRect.argtypes = (wt.HWND, ctypes.POINTER(RECT))
USER32.GetWindow.argtypes = (wt.HWND, wt.UINT)
USER32.GetWindow.restype = wt.HWND
USER32.PostMessageW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
USER32.GetForegroundWindow.restype = wt.HWND
USER32.IsIconic.argtypes = (wt.HWND,)
USER32.AttachThreadInput.argtypes = (wt.DWORD, wt.DWORD, wt.BOOL)
KERNEL32.GetCurrentThreadId.restype = wt.DWORD


def dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            USER32.SetProcessDPIAware()
        except Exception:
            pass


# ── 日志 ──────────────────────────────────────────────────────────────────
_LOG_FP = None


def log(msg, quiet=False):
    line = '[%s] %s' % (time.strftime('%H:%M:%S'), msg)
    if not quiet:
        print(line, flush=True)
    if _LOG_FP:
        _LOG_FP.write(line + '\n')
        _LOG_FP.flush()


# ── 封禁冷却闸门 ──────────────────────────────────────────────────────────
def block_state():
    """读 state/sd_web_block.json；不存在视为未封禁。"""
    try:
        return json.loads(BLOCK_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def mark_blocked(hours=DEFAULT_COOLDOWN_H, note=''):
    """记录一次 IP 封禁，冷却期内本脚本拒绝运行。

    Elsevier 支持页原文：block 会「automatically be released」，但反复重试会
    重新触发 monitoring（今天已实测 43s→112s→200s→403 的加深过程）。
    因此这里宁可多等，也不让脚本自己试探。
    """
    BLOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    data = {
        'blocked_at': now.isoformat(timespec='seconds'),
        'blocked_until': (now + timedelta(hours=hours)).isoformat(timespec='seconds'),
        'cooldown_hours': hours,
        'reason': 'ScienceDirect 返回 IP blocked（elsevier.support 支持页）',
        'note': note,
        'history': (block_state().get('history') or [])[-9:] + [
            {'at': now.isoformat(timespec='seconds'), 'note': note}],
    }
    BLOCK_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                          encoding='utf-8')
    log('已记录封禁：冷却至 %s' % data['blocked_until'])
    return data


def guard(min_gap=600):
    """冷却期内直接退出，返回 (ok, msg)。"""
    st = block_state()
    until = st.get('blocked_until')
    msg = ''
    if until:
        try:
            dt = datetime.fromisoformat(until)
            left = dt - datetime.now()
            if left.total_seconds() > 0:
                return False, ('SD 出口 IP 处于封禁冷却期，剩 %.1f 小时（至 %s）。\n'
                               '  真要继续请先确认封禁已解除，再加 --override。'
                               % (left.total_seconds() / 3600.0, until))
            msg = '封禁冷却已结束（%s）' % until
        except Exception:
            pass
    # 人工节奏：与上一篇至少隔 min_gap 秒
    last = st.get('last_attempt_at')
    if last:
        try:
            gap = (datetime.now() - datetime.fromisoformat(last)).total_seconds()
            if gap < min_gap:
                return False, ('距上次运行仅 %.0f 秒，少于 %d 秒的人工节奏下限。\n'
                               '  连续操作会被判定为 unusual activity。'
                               % (gap, min_gap))
        except Exception:
            pass
    return True, msg


def touch_attempt(note=''):
    """记录本次运行时间（用于人工节奏闸门）。"""
    st = block_state()
    st['last_attempt_at'] = datetime.now().isoformat(timespec='seconds')
    st['last_attempt_note'] = note
    BLOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    BLOCK_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding='utf-8')


# ── 键盘 ──────────────────────────────────────────────────────────────────
def _kb(vk=0, flags=0, scan=0):
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.u.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return inp


def _send(*events):
    n = len(events)
    arr = (INPUT * n)(*events)
    sent = USER32.SendInput(n, arr, ctypes.sizeof(INPUT))
    if sent != n:
        raise OSError('SendInput 仅送出 %d/%d，err=%s'
                      % (sent, n, ctypes.get_last_error()))
    return sent


def key_down(vk):
    _send(_kb(vk))


def key_up(vk):
    _send(_kb(vk, KEYEVENTF_KEYUP))


def tap(vk, hold=0.02):
    key_down(vk)
    time.sleep(hold)
    key_up(vk)
    time.sleep(0.03)


def chord(mods, vk):
    for m in mods:
        key_down(m)
        time.sleep(0.02)
    time.sleep(0.02)
    key_down(vk)
    time.sleep(0.03)
    key_up(vk)
    time.sleep(0.02)
    for m in reversed(mods):
        key_up(m)
        time.sleep(0.02)
    time.sleep(0.05)


def type_text(text, per_char=0.012):
    """按 Unicode 逐字送，绕开键盘布局（中文路径也不会坏）。"""
    for i, ch in enumerate(text):
        code = ord(ch)
        down = _kb(flags=KEYEVENTF_UNICODE, scan=code)
        up = _kb(flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, scan=code)
        _send(down, up)
        time.sleep(per_char)
        if i and i % 40 == 0:
            time.sleep(0.05)


def set_clipboard(text):
    """备用通道：写剪贴板，再用 Ctrl+V。SendInput 被拦时用。"""
    try:
        subprocess.run(['clip'], input=text.encode('utf-16-le'),
                       check=True, creationflags=0x08000000)
        return True
    except Exception:
        return False


# ── 窗口 ──────────────────────────────────────────────────────────────────
def win_text(hwnd):
    n = USER32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ''
    buf = ctypes.create_unicode_buffer(n + 1)
    USER32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def win_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    USER32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def win_pid(hwnd):
    pid = wt.DWORD()
    USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def edge_windows():
    out = []

    def cb(hwnd, _):
        if not USER32.IsWindowVisible(hwnd):
            return True
        if win_class(hwnd) != 'Chrome_WidgetWin_1':
            return True
        pid = win_pid(hwnd)
        try:
            import psutil
            nm = psutil.Process(pid).name().lower()
        except Exception:
            return True
        if 'msedge' not in nm:
            return True
        out.append(hwnd)
        return True

    USER32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def focus_window(hwnd):
    """抢前台。AttachThreadInput 是绕开 SetForegroundWindow 限制的常规手段。"""
    try:
        USER32.ShowWindow(hwnd, 9)          # SW_RESTORE
    except Exception:
        pass
    fore = USER32.GetForegroundWindow()
    if fore == hwnd:
        return True
    tid_fore = USER32.GetWindowThreadProcessId(fore, None) if fore else 0
    tid_cur = KERNEL32.GetCurrentThreadId()
    attached = False
    if tid_fore and tid_fore != tid_cur:
        attached = bool(USER32.AttachThreadInput(tid_cur, tid_fore, True))
    try:
        # 一次 Alt 轻敲可以解锁前台限制（Windows 的常见 workaround）
        tap(VK_MENU, 0.02)
        USER32.BringWindowToTop(hwnd)
        USER32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            USER32.AttachThreadInput(tid_cur, tid_fore, False)
    time.sleep(0.25)
    return USER32.GetForegroundWindow() == hwnd


def dialogs_of(pid):
    out = []

    def cb(hwnd, _):
        if not USER32.IsWindowVisible(hwnd):
            return True
        if win_class(hwnd) != '#32770':
            return True
        if win_pid(hwnd) != pid:
            return True
        out.append(hwnd)
        return True

    USER32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


# ── 主流程 ────────────────────────────────────────────────────────────────
def launch_edge(url, profile='Default'):
    before = set(edge_windows())
    args = [MSEDGE, '--profile-directory=%s' % profile, '--new-window', url]
    flags = 0x00000008 | 0x00000200          # DETACHED_PROCESS | NEW_PROCESS_GROUP
    p = subprocess.Popen(args, close_fds=True, creationflags=flags)
    log('启动 Edge pid=%s（新窗口）' % p.pid)
    hwnd = None
    deadline = time.time() + 25
    while time.time() < deadline:
        time.sleep(0.7)
        new = [h for h in edge_windows() if h not in before]
        titled = [h for h in new if win_text(h).strip()]
        if titled:
            hwnd = titled[0]
            break
        if new:
            hwnd = new[0]
    if not hwnd:
        # 回退：找标题里带 sciencedirect / ScienceDirect 的窗口
        for h in edge_windows():
            t = win_text(h)
            if 'sciencedirect' in t.lower() or 'ScienceDirect' in t:
                hwnd = h
                break
    if hwnd:
        log('窗口 hwnd=%s 标题=%r' % (hwnd, win_text(hwnd)[:80]))
    else:
        log('⚠️ 没抓到新窗口，后续按键可能落到别的窗口')
    return hwnd, p


BLOCK_HINTS = ('problem providing the content',
               'ip address has been blocked',
               'has been blocked',
               '1000s_box', 'access denied')


def looks_blocked(title):
    low = (title or '').lower()
    return any(k in low for k in BLOCK_HINTS)


def wait_title_change(hwnd, baseline, max_wait, keywords, poll=3.0):
    """轮询窗口标题，等到出现关键字（pdf 文件名 / PII）或标题明显变化。

    返回 (code, elapsed)：code 1=命中关键字 / 0=超时 / -1=检测到封禁页。
    """
    t0 = time.time()
    last = None
    hit = False
    while time.time() - t0 < max_wait:
        if not USER32.IsWindow(hwnd):
            log('⚠️ 窗口已关闭')
            return 0, 0
        title = win_text(hwnd)
        if title != last:
            log('  标题 → %r' % title[:100])
            last = title
            if looks_blocked(title):
                log('⛔ 标题命中封禁特征，立即停止')
                return -1, time.time() - t0
        low = title.lower()
        if any(k and k.lower() in low for k in keywords):
            hit = True
            break
        time.sleep(poll)
    return (1 if hit else 0), time.time() - t0


def downloads_snapshot():
    d = Path(os.environ.get('USERPROFILE', '')) / 'Downloads'
    if not d.exists():
        return d, set()
    return d, {p.name for p in d.glob('*.pdf')}


def wait_new_download(dl_dir, before, max_wait, poll=3.0):
    t0 = time.time()
    while time.time() - t0 < max_wait:
        for p in dl_dir.glob('*.pdf'):
            if p.name not in before and p.stat().st_size > 20000:
                size = p.stat().st_size
                time.sleep(1.5)
                if p.exists() and p.stat().st_size == size:
                    return p
        time.sleep(poll)
    return None


def save_dialog_to(pid, target: Path, attempts=3):
    """等另存为对话框 → 输入完整路径 → 回车。"""
    for i in range(attempts):
        dlg = None
        deadline = time.time() + 12
        while time.time() < deadline:
            cands = dialogs_of(pid)
            if cands:
                dlg = cands[0]
                break
            time.sleep(0.4)
        if not dlg:
            log('  ✗ 第 %d 次：没出现另存为对话框' % (i + 1))
            continue
        log('  ✓ 另存为对话框：%r' % win_text(dlg)[:60])
        time.sleep(0.6)
        focus_window(dlg)
        chord([VK_CONTROL], VK_A)        # 全选文件名
        time.sleep(0.2)
        type_text(str(target))
        time.sleep(0.3)
        tap(VK_RETURN)
        # 等对话框消失
        gone = False
        for _ in range(30):
            time.sleep(0.5)
            if not USER32.IsWindow(dlg):
                gone = True
                break
        if not gone:
            # 可能弹了「确认替换」或按钮未触发，再敲一次回车
            rest = dialogs_of(pid)
            log('  对话框仍在（%d 个），再回车' % len(rest))
            for d in rest:
                focus_window(d)
                tap(VK_RETURN)
                time.sleep(0.8)
            for _ in range(20):
                time.sleep(0.5)
                if not USER32.IsWindow(dlg):
                    gone = True
                    break
        if gone:
            log('  ✓ 对话框已关闭')
            return True
        log('  ✗ 第 %d 次：对话框未关闭' % (i + 1))
    return False


def is_real_pdf(path: Path, min_kb=20):
    try:
        if not path.exists() or path.stat().st_size < min_kb * 1024:
            return False, '文件过小或不存在'
        with open(path, 'rb') as f:
            head = f.read(8)
        if not head.startswith(b'%PDF'):
            return False, '文件头不是 %PDF（可能是 HTML 错误页）'
        try:
            import pymupdf as fitz
        except Exception:
            import fitz
        doc = fitz.open(str(path))
        n = doc.page_count
        txt = ''
        if n:
            txt = (doc[0].get_text() or '')[:160].replace('\n', ' ')
        doc.close()
        if n < 1:
            return False, '页数为 0'
        return True, '页数 %d ｜ 首页：%s' % (n, txt)
    except Exception as e:
        return False, '%s: %s' % (type(e).__name__, e)


def shot(hwnd, path):
    try:
        from PIL import ImageGrab
        r = RECT()
        USER32.GetWindowRect(hwnd, ctypes.byref(r))
        img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True)
        img.save(path)
        log('截图 %s（%dx%d）' % (path, img.size[0], img.size[1]))
    except Exception as e:
        log('截图失败：%s' % e)


def self_test(shot_path=None):
    """只验证按键链路：开窗口 → Ctrl+L → 输入 → 截图 → 关窗。"""
    hwnd, _ = launch_edge('about:blank')
    if not hwnd:
        return 2
    time.sleep(3)
    ok = focus_window(hwnd)
    log('前台化 = %s' % ok)
    if not ok:
        return 3
    chord([VK_CONTROL], VK_A)
    type_text('edge ui sendinput ok')
    time.sleep(0.5)
    if shot_path:
        shot(hwnd, shot_path)
    current = win_text(hwnd)
    log('窗口标题 = %r' % current)
    chord([VK_CONTROL], VK_W)
    time.sleep(1)
    return 0


def main():
    global _LOG_FP
    ap = argparse.ArgumentParser()
    ap.add_argument('--pii')
    ap.add_argument('--article-url')
    ap.add_argument('--pdf-url')
    ap.add_argument('--out', help='目标 PDF 路径')
    ap.add_argument('--profile', default='Default')
    ap.add_argument('--wait', type=int, default=300, help='等 PDF 加载的最长秒数')
    ap.add_argument('--attempts', type=int, default=3, help='另存为重试次数')
    ap.add_argument('--shot', help='结束时截图到该路径')
    ap.add_argument('--self-test', action='store_true')
    ap.add_argument('--keep-open', action='store_true')
    ap.add_argument('--log', help='日志文件')
    ap.add_argument('--mark-blocked', type=float, nargs='?', const=DEFAULT_COOLDOWN_H,
                    metavar='HOURS', help='记录一次 IP 封禁并进入冷却（默认 24 小时）')
    ap.add_argument('--override', action='store_true', help='无视封禁冷却强行运行')
    ap.add_argument('--min-gap', type=int, default=600, help='两次运行的最小间隔秒数')
    args = ap.parse_args()

    dpi_aware()
    if args.log:
        _LOG_FP = open(args.log, 'a', encoding='utf-8')

    if args.mark_blocked is not None:
        mark_blocked(args.mark_blocked)
        return 0

    if args.self_test:
        return self_test(args.shot)

    ok, why = guard(args.min_gap)
    if not ok and not args.override:
        print('拒绝运行：%s' % why, file=sys.stderr)
        return 9
    if why:
        log('闸门：%s' % why)
    touch_attempt(args.pii or args.pdf_url or '')

    if not args.out:
        ap.error('需要 --out')
    target = Path(args.out).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()

    if args.pdf_url:
        article_url = args.article_url or ''
        pdf_url = args.pdf_url
        pii = args.pii or ''
    else:
        if not args.pii:
            ap.error('需要 --pii 或 --pdf-url')
        pii = args.pii
        article_url = args.article_url or (ARTICLE % pii)
        pdf_url = PDF % pii

    log('=' * 66)
    log('文章页 %s' % (article_url or '(直接进 PDF)'))
    log('PDF   %s' % pdf_url)
    log('目标   %s' % target)

    t_start = time.time()

    # ① 起窗口 + 打开文章页
    hwnd, _proc = launch_edge(article_url or pdf_url, args.profile)
    if not hwnd:
        return 2
    pid = win_pid(hwnd)
    time.sleep(2)
    if not focus_window(hwnd):
        log('⚠️ 无法把窗口拉到前台，按键可能无效')

    if article_url:
        log('等文章页渲染（Cloudflare 托管挑战在真实浏览器里会自动过）…')
        # 标题从 URL/加载态变成论文名，就视为渲染完成
        for _ in range(20):
            t = win_text(hwnd).strip()
            if t and 'sciencedirect' not in t.lower() and t != 'about:blank':
                break
            time.sleep(1.5)
        log('  当前标题=%r，再稳 6 秒' % win_text(hwnd)[:90])
        time.sleep(6)

    # ② 导航到 PDF 端点（相当于点 View PDF）
    dl_dir, dl_before = downloads_snapshot()
    log('Ctrl+L → PDF 端点')
    focus_window(hwnd)
    chord([VK_CONTROL], 0x4C)               # Ctrl+L
    time.sleep(0.5)
    ok_txt = True
    try:
        type_text(pdf_url, per_char=0.006)
    except OSError as e:
        log('⚠️ SendInput 失败（%s），改用剪贴板粘贴' % e)
        ok_txt = False
        if set_clipboard(pdf_url):
            chord([VK_CONTROL], 0x56)       # Ctrl+V
    time.sleep(0.4)
    tap(VK_RETURN)
    log('  已提交')

    # ③ 等加载：优先看是否直接落到下载目录，否则等标题变成 pdf
    code, waited = wait_title_change(
        hwnd, win_text(hwnd), args.wait,
        keywords=[pii, '.pdf', 'pdf'], poll=3.0)
    log('等待结束：命中=%s，用时 %.0fs，标题=%r'
        % (code, waited, win_text(hwnd)[:100]))
    if code == -1:
        mark_blocked(note='导航到 PDF 端点后被引导到 IP blocked 说明页')
        if USER32.IsWindow(hwnd):
            USER32.PostMessageW(hwnd, 0x0010, 0, 0)
        return 7
    time.sleep(2)

    downloaded = wait_new_download(dl_dir, dl_before, 5)
    if downloaded:
        log('✓ 浏览器已自动下载：%s' % downloaded)
        shutil.move(str(downloaded), str(target))
        good, detail = is_real_pdf(target)
        log('校验 %s → %s ｜ %s' % (good, target.stat().st_size // 1024, detail))
        if not args.keep_open and USER32.IsWindow(hwnd):
            USER32.PostMessageW(hwnd, 0x0010, 0, 0)     # WM_CLOSE
        return 0 if good else 4

    # ④ Ctrl+S 另存为
    for attempt in range(1, args.attempts + 1):
        log('第 %d/%d 轮 Ctrl+S' % (attempt, args.attempts))
        focus_window(hwnd)
        time.sleep(0.3)
        chord([VK_CONTROL], VK_S)
        if save_dialog_to(pid, target, attempts=1):
            time.sleep(2)
            good, detail = is_real_pdf(target)
            log('校验 %s ｜ %s' % (good, detail))
            if good:
                log('✓ 成功：%s（%d KB）'
                    % (target, target.stat().st_size // 1024))
                if args.shot and USER32.IsWindow(hwnd):
                    shot(hwnd, args.shot)
                if not args.keep_open and USER32.IsWindow(hwnd):
                    USER32.PostMessageW(hwnd, 0x0010, 0, 0)
                log('总用时 %.0f 秒' % (time.time() - t_start))
                return 0
        # 没成功 → 多等一会儿（PDF 可能还在流式渲染）
        extra = 60
        log('未成功，再等 %d 秒让 PDF 继续加载' % extra)
        c2, _ = wait_title_change(hwnd, '', extra, keywords=[pii, '.pdf'], poll=4.0)
        if c2 == -1:
            mark_blocked(note='重试期间命中 IP blocked 说明页')
            if USER32.IsWindow(hwnd):
                USER32.PostMessageW(hwnd, 0x0010, 0, 0)
            return 7
        log('  标题=%r' % win_text(hwnd)[:100])

    if args.shot and USER32.IsWindow(hwnd):
        shot(hwnd, args.shot)
    log('✗ 三轮都失败，窗口保留供人工查看（%r）' % win_text(hwnd)[:100])
    log('总用时 %.0f 秒' % (time.time() - t_start))
    return 5


if __name__ == '__main__':
    sys.exit(main())
