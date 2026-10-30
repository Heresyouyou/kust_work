# -*- coding: utf-8 -*-
"""探测：真实 Edge + 真实 User Data 能否开 CDP 调试端口。

Edge 136+ 起因安全考虑禁止「默认 User Data 目录 + --remote-debugging-port」。
本脚本实测本机是否受此限制 —— 这决定了「挂到你自己那个 Edge」这条路是否可行。
不动任何 cookie，只启动、探测、然后关闭。
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

EDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
PORT = 9333


def probe():
    if not Path(EDGE).exists():
        print('找不到 Edge:', EDGE)
        return
    print('启动真实 Edge（默认 User Data，带调试端口 %d）…' % PORT)
    p = subprocess.Popen(
        [EDGE, '--remote-debugging-port=%d' % PORT, '--no-first-run',
         '--no-default-browser-check', 'about:blank'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ok = False
    for i in range(15):
        time.sleep(1)
        try:
            with urllib.request.urlopen(
                    'http://127.0.0.1:%d/json/version' % PORT, timeout=2) as r:
                info = json.loads(r.read().decode('utf-8', 'ignore'))
            print('  ✓ 调试端口可用（%.0fs）' % (i + 1))
            print('    Browser :', info.get('Browser'))
            print('    WS      :', (info.get('webSocketDebuggerUrl') or '')[:80])
            ok = True
            break
        except Exception as e:  # noqa: BLE001
            if i == 0:
                print('  [..] 等待端口：%s' % type(e).__name__)
    if not ok:
        print('  ✗ 调试端口未开放 —— Edge 拒绝了默认 profile 的远程调试')
        # 进程可能已自行退出，也可能只是没开端口
        print('  进程是否存活：', p.poll() is None)
    else:
        try:
            with urllib.request.urlopen(
                    'http://127.0.0.1:%d/json/list' % PORT, timeout=3) as r:
                tabs = json.loads(r.read().decode('utf-8', 'ignore'))
            print('  当前标签数：', len(tabs))
        except Exception:  # noqa: BLE001
            pass
    print('关闭探测进程…')
    try:
        p.terminate()
        p.wait(timeout=8)
    except Exception:  # noqa: BLE001
        pass
    return ok


if __name__ == '__main__':
    sys.exit(0 if probe() else 2)
