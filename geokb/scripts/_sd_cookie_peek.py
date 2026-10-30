"""停掉探针会话（释放 _real_edge profile 锁），并只读检查 profile 里的 SD cookie。"""
import sqlite3
import sys
import time
from pathlib import Path

import psutil

BASE = Path('D:/ZSK/geokb')
PROFILE = BASE / 'state' / 'browser_profiles' / '_real_edge'
SELF = psutil.Process().pid

# 1) 停探针
for p in psutil.process_iter(['pid', 'name', 'cmdline']):
    try:
        if p.info['pid'] == SELF:
            continue
        c = ' '.join(p.info['cmdline'] or [])
        if 'sd_flow_probe.py' in c and 'python' in (p.info['name'] or '').lower():
            print('terminate probe', p.info['pid'])
            p.terminate()
    except Exception:
        pass
time.sleep(1.5)

# 2) 关它的 Edge
k = 0
for p in psutil.process_iter(['pid', 'name', 'cmdline']):
    try:
        c = ' '.join(p.info['cmdline'] or [])
        if '_real_edge' in c and (p.info['name'] or '').lower().startswith('msedge'):
            p.terminate()
            k += 1
    except Exception:
        pass
print('edge terminated:', k)
time.sleep(2)

# 3) 只读看 cookie 名单（值是加密的，只看名字与范围）
ck = PROFILE / 'Default' / 'Network' / 'Cookies'
print('\n=== Cookies 文件 ===', ck.exists(), ck.stat().st_size if ck.exists() else '')
if ck.exists():
    try:
        con = sqlite3.connect('file:%s?immutable=1' % ck.as_posix(), uri=True)
        rows = con.execute(
            "SELECT host_key, name, expires_utc, is_secure, length(encrypted_value) "
            "FROM cookies WHERE host_key LIKE '%sciencedirect%' "
            "OR host_key LIKE '%elsevier%' OR host_key LIKE '%kust%' "
            "ORDER BY host_key, name").fetchall()
        con.close()
        print('匹配 cookie %d 条：' % len(rows))
        for h, n, exp, sec, ln in rows:
            print('  %-28s %-26s enc=%dB secure=%s' % (h, n, ln, sec))
    except Exception as e:
        print('读 cookie 失败:', type(e).__name__, e)
