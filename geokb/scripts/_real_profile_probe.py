# -*- coding: utf-8 -*-
"""只读体检：真实 Edge/Chrome profile 里有没有 ScienceDirect / Elsevier 的登录态。

不启动浏览器、不访问网络，只读 Cookies 数据库的 host_key（不碰加密值）。
目的：在花 5 分钟开窗口被试之前，先确认「借用真实 profile」这条路有没有登录态可用。
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOTS = {
    'edge': r'%LOCALAPPDATA%\Microsoft\Edge\User Data',
    'chrome': r'%LOCALAPPDATA%\Google\Chrome\User Data',
}
CAND = ['Default/Network/Cookies', 'Default/Cookies']

KEYS = ('sciencedirect', 'elsevier', 'sd-cust', 'sciverse', 'kust',
        'ezproxy', 'webvpn', 'cnki', 'wanfang')


def probe(kind):
    root = Path(os.path.expandvars(ROOTS[kind]))
    if not root.is_dir():
        print('[skip] 没找到 %s: %s' % (kind, root))
        return
    print('=== %s ===' % kind)
    print('  root:', root)
    # 有多少个 profile 目录（Default / Profile 1 / ...）
    profs = [p for p in root.iterdir()
             if p.is_dir() and (p.name == 'Default' or p.name.startswith('Profile'))]
    print('  profiles:', [p.name for p in profs])
    for prof in profs:
        for rel in CAND:
            src = prof / rel.split('/', 1)[1] if rel.startswith('Default/') else prof / rel
            if not src.exists():
                continue
            tmp = Path(tempfile.gettempdir()) / ('ck_%s_%s.db' % (kind, prof.name))
            mode = 'copy'
            try:
                shutil.copy2(src, tmp)
            except Exception:  # noqa: BLE001
                # 浏览器在跑时文件被独占锁住 → 改用 immutable 只读直连原文件，
                # SQLite 不会因此加锁或写入，对正在运行的浏览器无影响。
                tmp = src
                mode = 'immutable'
            try:
                if mode == 'immutable':
                    con = sqlite3.connect(tmp.as_uri() + '?immutable=1', uri=True)
                else:
                    con = sqlite3.connect(str(tmp))
                rows = con.execute(
                    'select host_key, name, length(encrypted_value), is_secure, '
                    'expires_utc from cookies').fetchall()
                con.close()
            except Exception as e:  # noqa: BLE001
                print('   [err] 读 %s: %s' % (src, str(e)[:70]))
                continue
            print('   %-22s %-28s cookies=%d' % (prof.name, rel, len(rows)))
            hits = {}
            for host, name, elen, sec, exp in rows:
                h = (host or '').lower()
                if any(k in h for k in KEYS):
                    hits.setdefault(host, []).append((name, elen))
            if not hits:
                print('      → 无 sciencedirect/elsevier/机构相关 cookie')
            for host, items in sorted(hits.items()):
                names = ', '.join('%s(%dB)' % (n, l) for n, l in items[:8])
                print('      ✔ %-42s %s' % (host[:42], names))
            try:
                tmp.unlink()
            except Exception:  # noqa: BLE001
                pass


if __name__ == '__main__':
    for k in (sys.argv[1:] or ['edge', 'chrome']):
        probe(k)
