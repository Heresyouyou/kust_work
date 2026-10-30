# -*- coding: utf-8 -*-
"""验证启动锁的原子性：并发抢锁只能有一个成功（隔离目录，不碰生产状态）。

不跑真实调度器：把 PID_FILE / START_LOCK / OP_LOCK 全指到临时目录，
用 6 个子进程同时调用 acquire_pid()，数成功数。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
TMP = Path(tempfile.mkdtemp(prefix='hqlock_'))
print('临时目录', TMP)

WORKER = r'''
import importlib.util, json, os, sys, time
base, tmp = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location('hql', base + '/scripts/history_queue_loop.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
from pathlib import Path
m.PID_FILE = Path(tmp) / 'q.pid'
m.START_LOCK = Path(tmp) / 'q.startlock'
m.OP_LOCK = Path(tmp) / 'op.lock'
m.log = lambda *a, **k: None          # 静音
time.sleep(float(sys.argv[3]))         # 制造同一瞬间竞争
ok = m.acquire_pid()
if ok:
    time.sleep(3)                      # 模拟运行期：拿到锁的进程不会立刻退出
    print(json.dumps({'pid': os.getpid(), 'ok': True}), flush=True)
else:
    print(json.dumps({'pid': os.getpid(), 'ok': False}), flush=True)
'''

wpath = TMP / 'worker.py'
wpath.write_text(WORKER, encoding='utf-8')

N = 6
procs = []
for i in range(N):
    procs.append(subprocess.Popen(
        [sys.executable, str(wpath), str(BASE), str(TMP), '0.0'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding='utf-8', errors='ignore'))
res = []
for p in procs:
    out, _ = p.communicate(timeout=60)
    for line in (out or '').splitlines():
        line = line.strip()
        if line.startswith('{'):
            try:
                res.append(json.loads(line))
            except ValueError:
                pass

wins = [r for r in res if r['ok']]
print('参与 %d ｜ 成功抢到 %d ｜ 被拒 %d' % (len(res), len(wins), len(res) - len(wins)))
for r in res:
    print('  pid %-7d %s' % (r['pid'], '✅ 拿到锁' if r['ok'] else '⛔ 被拒'))
print()
print('判定：', 'PASS 只有一个实例' if len(wins) == 1 else 'FAIL 出现 %d 个实例' % len(wins))

# 清理
for f in TMP.glob('*'):
    try:
        f.unlink()
    except OSError:
        pass
try:
    TMP.rmdir()
except OSError:
    pass
sys.exit(0 if len(wins) == 1 else 1)
