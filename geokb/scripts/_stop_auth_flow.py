# -*- coding: utf-8 -*-
"""停掉 sso_keeper --login --headed 及其启动的受控 profile Edge（不碰 Default profile）"""
import time
import psutil

self_pid = psutil.Process().pid
killed_py, killed_edge = [], []
for p in psutil.process_iter(['pid', 'name', 'cmdline']):
    try:
        if p.info['pid'] == self_pid:
            continue
        name = (p.info['name'] or '').lower()
        cmd = ' '.join(p.info['cmdline'] or [])
        if name.startswith('python') and 'sso_keeper.py' in cmd:
            p.terminate(); killed_py.append(p.info['pid'])
        elif name.startswith('msedge') and 'sciencedirect_auth' in cmd:
            p.terminate(); killed_edge.append(p.info['pid'])
    except Exception:
        pass
print('terminate python:', killed_py)
print('terminate edge(auth profile):', killed_edge)
time.sleep(3)
# 确认
alive = []
for p in psutil.process_iter(['pid', 'name', 'cmdline']):
    try:
        cmd = ' '.join(p.info['cmdline'] or [])
        if 'sciencedirect_auth' in cmd and (p.info['name'] or '').lower().startswith('msedge'):
            alive.append(p.info['pid'])
    except Exception:
        pass
print('still alive auth-profile edge:', alive)
