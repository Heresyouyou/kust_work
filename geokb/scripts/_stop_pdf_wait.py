# -*- coding: utf-8 -*-
"""停掉等待人工的取 PDF 窗口（不再消耗 CF 评分）"""
import time

import psutil

self_pid = psutil.Process().pid
killed = []
for p in psutil.process_iter(['pid', 'name', 'cmdline']):
    try:
        if p.info['pid'] == self_pid:
            continue
        name = (p.info['name'] or '').lower()
        cmd = ' '.join(p.info['cmdline'] or [])
        if name.startswith('python') and 'sd_get_pdf.py' in cmd:
            p.terminate(); killed.append(('py', p.info['pid']))
        elif name.startswith('msedge') and 'sciencedirect_auth' in cmd:
            p.terminate(); killed.append(('edge', p.info['pid']))
    except Exception:
        pass
print('terminated:', killed)
time.sleep(2)
left = [p.info['pid'] for p in psutil.process_iter(['pid', 'name', 'cmdline'])
        if (p.info['name'] or '').lower().startswith('msedge')
        and 'sciencedirect_auth' in ' '.join(p.info['cmdline'] or [])]
print('残留:', left)
