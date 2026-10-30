# -*- coding: utf-8 -*-
"""实测：默认数据目录 vs 非默认 --user-data-dir，远程调试端口是否可用（Edge 155）"""
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import psutil

MSEDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
TMP = Path('D:/ZSK/geokb/state/browser_profiles/_cdp_probe')
TMP.mkdir(parents=True, exist_ok=True)
FLAGS = 0x00000008 | 0x00000200


def probe(port, timeout=3.0):
    url = 'http://127.0.0.1:%d/json/version' % port
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception as e:
        return {'__err': type(e).__name__}


def kill_by(needle):
    n = 0
    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if not (p.info['name'] or '').lower().startswith('msedge'):
                continue
            if needle in ' '.join(p.info['cmdline'] or []):
                p.terminate()
                n += 1
        except Exception:
            pass
    return n


print('=== 用例 A：非默认 --user-data-dir ===')
p = subprocess.Popen([MSEDGE, '--remote-debugging-port=9333',
                      '--user-data-dir=%s' % TMP, '--no-first-run',
                      '--no-default-browser-check', 'about:blank'],
                     close_fds=True, creationflags=FLAGS)
print('  launched pid', p.pid)
for i in range(10):
    time.sleep(1)
    info = probe(9333)
    if '__err' not in info:
        print('  端口 9333 通了 ->', info.get('Browser'), '|', info.get('Protocol-Version'))
        break
else:
    print('  端口 9333 未通（最后一次：%s）' % info)
print('  清理该实例进程数：', kill_by(str(TMP).replace('\\', '/')))
kill_by('_cdp_probe')
time.sleep(2)

print()
print('=== 用例 B：默认数据目录（只加 --remote-debugging-port）===')
p2 = subprocess.Popen([MSEDGE, '--remote-debugging-port=9334', 'about:blank'],
                      close_fds=True, creationflags=FLAGS)
print('  launched pid', p2.pid)
time.sleep(6)
info = probe(9334)
print('  端口 9334 ->', info)
print('  该 pid 是否存活：', psutil.pid_exists(p2.pid) and psutil.Process(p2.pid).status())
