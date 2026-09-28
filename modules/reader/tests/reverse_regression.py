#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reverse_regression.py —— 给 scan_undefined 做「反向回归」用：

临时去掉 viewport.js 的 rotateSizes 导入，再跑扫描，应当报缺失；
跑完自动还原原文件，避免污染源码。
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'assets' / 'kpdf' / 'viewport.js'
SCRIPT = ROOT / 'tests' / 'scan_undefined.py'

def run_scanner():
    r = subprocess.run(
        [r'D:\Python 3.12.4\python.exe', str(SCRIPT)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    return r.returncode, r.stdout + r.stderr

def main():
    raw = TARGET.read_text(encoding='utf-8')
    backup = raw
    broken = raw.replace('rotateSizes,', '', 1)
    if broken == raw:
        print('FAIL: 没找到可移除的 rotateSizes 导入（测试前提不成立）')
        return 2
    print(f'临时去掉 {TARGET.name} 里的 rotateSizes 导入（备份 {len(backup)} 字节 → 修改后 {len(broken)} 字节）')
    TARGET.write_text(broken, encoding='utf-8')
    try:
        rc, out = run_scanner()
        ok = 'rotateSizes' in out and rc != 0
        print('--- 扫描输出（关键行）---')
        for line in out.splitlines():
            if 'rotateSizes' in line or line.startswith('扫描'):
                print(line)
        print('判定:', '✅ 捕获到了' if ok else '❌ 漏掉了')
    finally:
        TARGET.write_text(backup, encoding='utf-8')
        rc2, out2 = run_scanner()
        print(f'\n还原后再次扫描：{rc2}（应当为 0）')
        for line in out2.splitlines():
            if line.startswith('扫描'):
                print(line)
    return 0 if ok else 1

if __name__ == '__main__':
    sys.exit(main())