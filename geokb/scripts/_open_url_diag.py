#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""诊断：默认浏览器是什么、Edge 在哪、startfile 是否真的把 URL 递出去了。"""
import os
import subprocess
import sys
import winreg

print('=== 默认浏览器 (http/https 关联) ===')
for proto in ('http', 'https'):
    try:
        k = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations'
            '\\' + proto + r'\UserChoice')
        print(f'{proto}: ProgId = {winreg.QueryValueEx(k, "ProgId")[0]}')
        winreg.CloseKey(k)
    except Exception as e:
        print(f'{proto}: <{e}>')

print('\n=== ProgId → 命令 ===')
for proto in ('http', 'https'):
    try:
        k = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations'
            '\\' + proto + r'\UserChoice')
        pid = winreg.QueryValueEx(k, 'ProgId')[0]
        winreg.CloseKey(k)
        ck = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,
                            pid + r'\shell\open\command')
        print(f'{pid}: {winreg.QueryValueEx(ck, "")[0]}')
        winreg.CloseKey(ck)
    except Exception as e:
        print(f'{proto}: <{e}>')

print('\n=== Edge 可执行文件 ===')
cands = [
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
]
for c in cands:
    print(f'{c} -> {os.path.exists(c)}')

print('\n=== 试 explorer.exe 打开 ===')
url = sys.argv[1] if len(sys.argv) > 1 else 'https://www.sciencedirect.com'
try:
    p = subprocess.Popen(['explorer.exe', url])
    print('explorer pid:', p.pid, 'rc(poll):', p.poll())
except Exception as e:
    print('explorer failed:', type(e).__name__, e)
