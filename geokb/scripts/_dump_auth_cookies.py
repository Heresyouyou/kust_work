# -*- coding: utf-8 -*-
"""导出受控 profile 里与 ScienceDirect / 机构认证相关的 cookie 清单（只读副本）。"""
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
ck = BASE / 'state' / 'browser_profiles' / 'sciencedirect_auth' / 'Default' / 'Network' / 'Cookies'
print('Cookies 文件:', ck.exists(), ck.stat().st_size if ck.exists() else '')
if not ck.exists():
    sys.exit(1)

tmp = Path(tempfile.gettempdir()) / 'ck_auth_dump.db'
shutil.copyfile(str(ck), str(tmp))
con = sqlite3.connect(str(tmp))
rows = con.execute('select host_key, name, length(encrypted_value), length(value), '
                   'is_secure, path from cookies order by host_key, name').fetchall()
print('cookie 总数:', len(rows))

interesting = []
for h, n, el, pl, sec, pa in rows:
    hl = (h or '').lower()
    if any(k in hl for k in ('sciencedirect', 'elsevier', 'kust', 'carsi', 'edu.cn')):
        interesting.append((h, n, el, pl, sec))
    elif n.lower() in ('sd_remoteaccess', 'cra-ch-clearance', 'cf_clearance', 'euid'):
        interesting.append((h, n, el, pl, sec))

print('\n相关 cookie: %d 条' % len(interesting))
for h, n, el, pl, sec in interesting:
    print('  %-34s %-24s enc=%4dB plain=%dB secure=%s' % (h, n, el, pl, sec))

print('\n全部域名:')
for d, in con.execute('select distinct host_key from cookies order by host_key'):
    print('  ', d)
