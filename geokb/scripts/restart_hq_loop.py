# -*- coding: utf-8 -*-
"""重启历史清单调度器，并尽量不打断正在下载的那一篇。

背景
----
改 `history_queue.py` / `history_queue_loop.py` 后，**运行中的旧进程内存里还是旧代码**
（2026-10-20 踩过：入库闸门改了，跑着的调度器仍照旧入库）。必须换进程。

为什么不能直接 terminate
------------------------
单篇下载要 1.5-9 分钟，硬杀会把这次请求打断（虽然下次会重试）。
调度器每篇之间恒定静默 3 分钟，日志里先写「等 3 分 0 秒后爬下一篇」再 nap()，
这 3 分钟就是安全窗口 —— 等它出现再动手。

流程
----
  ① 找到所有 history_queue_loop.py 进程，取最外层那个（venv 里会多一层 python）
  ② 轮询日志尾，最多等 MAX_WAIT 秒，直到最后一条是「后爬下一篇」/「本篇完成」
  ③ terminate 整棵树（父 + 子）
  ④ 删 pid 文件（防 pid 复用让新实例自杀）
  ⑤ 等 geokb_daemon 的 EXTRA_PROCS 在 10s 巡检内拉起，回报新 pid

用法
----
    python scripts/restart_hq_loop.py            # 等安静窗口再重启
    python scripts/restart_hq_loop.py --now      # 不等，立刻重启（会打断当前篇）
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import psutil

BASE = Path('D:/ZSK/geokb')
PID_FILE = BASE / 'state' / 'history_queue_loop.pid'
LOG = BASE / 'logs' / 'history_queue_loop.log'
SCRIPT = 'history_queue_loop.py'
QUIET_MARKS = ('后爬下一篇', '本篇完成', '本篇未完成', '本篇异常', '待爬清单已空')
MAX_WAIT = 900      # 最多等 15 分钟（单篇最长见过 9 分钟）


def find_roots():
    """返回最外层的调度器进程（父不是调度器的那个）。"""
    procs = []
    me = os.getpid()
    for p in psutil.process_iter(['pid', 'cmdline']):
        if p.info['pid'] == me:
            continue
        try:
            joined = ' '.join(p.info['cmdline'] or [])
        except Exception:
            continue
        if SCRIPT in joined and 'restart_hq_loop' not in joined:
            procs.append(p)
    roots = []
    for p in procs:
        try:
            par = p.parent()
            if par is not None and SCRIPT in ' '.join(par.cmdline() or []):
                continue          # 父也是调度器 → 不是最外层
        except Exception:
            pass
        roots.append(p)
    return roots


def tail_is_quiet():
    """日志最后一条是否落在「一篇已结束、正在节流」的安静窗口。"""
    if not LOG.exists():
        return True, '(无日志)'
    try:
        with open(LOG, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 4096))
            lines = [x for x in f.read().decode('utf-8', 'ignore').splitlines() if x.strip()]
    except Exception as e:
        return True, '(读日志失败 %s)' % e
    if not lines:
        return True, '(空日志)'
    last = lines[-1]
    return any(m in last for m in QUIET_MARKS), last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--now', action='store_true', help='不等安静窗口，立刻重启')
    args = ap.parse_args()

    roots = find_roots()
    if not roots:
        print('没有在跑的调度器实例')
    for p in roots:
        try:
            print('目标 pid=%d  ct=%s  cmd=%s'
                  % (p.pid, time.strftime('%m-%d %H:%M:%S', time.localtime(p.create_time())),
                     ' '.join(p.cmdline())[:110]))
        except Exception as e:
            print('目标 pid=%d（信息读取失败 %s）' % (p.pid, e))

    if roots and not args.now:
        print('等安静窗口（每篇之间有 3 分钟节流）…')
        t0 = time.time()
        while time.time() - t0 < MAX_WAIT:
            ok, line = tail_is_quiet()
            if ok:
                print('  已进入安静窗口（等了 %ds）：%s' % (time.time() - t0, line[:110]))
                break
            time.sleep(10)
        else:
            print('  ⚠️ %ds 内没等到，照样重启（当前篇会重试）' % MAX_WAIT)

    old_pids = []
    for p in roots:
        try:
            family = [p] + p.children(recursive=True)
        except Exception:
            family = [p]
        for q in family:
            try:
                old_pids.append(q.pid)
                q.terminate()
            except Exception as e:
                print('  terminate pid=%d 失败：%s' % (q.pid, e))
    # 给 12s 优雅退出，然后强杀残留
    deadline = time.time() + 12
    while time.time() < deadline:
        alive = []
        for pid in old_pids:
            try:
                if psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                    alive.append(pid)
            except Exception:
                pass        # 已退出 / 权限变化 → 视为不在
        if not alive:
            break
        time.sleep(1)
    for pid in old_pids:
        try:
            if psutil.pid_exists(pid):
                psutil.Process(pid).kill()
                print('  强杀残留 pid=%d' % pid)
        except Exception:
            pass
    print('已终止：%s' % old_pids)

    try:
        PID_FILE.unlink()
        print('pid 文件已删除')
    except FileNotFoundError:
        print('pid 文件本就不存在')

    print('等守护进程拉起（最多 90s）…')
    for i in range(30):
        time.sleep(3)
        if PID_FILE.exists():
            try:
                new = int(PID_FILE.read_text(encoding='utf-8').strip())
            except Exception:
                continue
            if new not in old_pids and psutil.pid_exists(new):
                print('新实例 pid=%d（%.0fs 后）' % (new, (i + 1) * 3))
                return 0
    print('⚠️ 90s 内未拉起，需手动跑 tools/start_history_queue.bat')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
