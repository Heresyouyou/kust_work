# -*- coding: utf-8 -*-
"""关闭 sd_login_once 打开的受控 profile 浏览器（避免自匹配，排除自身 PID）。"""
import os
import time

import psutil

SELF = os.getpid()
MARK = 'sciencedirect' + '_auth'      # 拆开拼接，避免命令行自匹配
SCRIPT = 'sd_login' + '_once'


def kill(match):
    n = 0
    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
        pid = p.info['pid']
        if pid == SELF:
            continue
        cl = ' '.join(p.info.get('cmdline') or [])
        if match in cl:
            try:
                p.terminate()
                n += 1
                print('  terminate pid=%d' % pid)
            except Exception as e:  # noqa: BLE001
                print('  fail pid=%d %s' % (pid, str(e)[:50]))
    return n


print('kill python:', kill(SCRIPT))
time.sleep(2)
print('kill msedge:', kill(MARK))
time.sleep(4)

left = 0
for p in psutil.process_iter(['pid', 'cmdline']):
    if p.info['pid'] == SELF:
        continue
    if MARK in ' '.join(p.info.get('cmdline') or []):
        left += 1
print('剩余相关进程:', left)
