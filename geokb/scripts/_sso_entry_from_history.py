# -*- coding: utf-8 -*-
"""从 Edge 历史里捞真实 SSO 入口（institutionLogin 完整 URL）与 idplib 链"""
import shutil, sqlite3, tempfile
from pathlib import Path

SRC = Path('C:/Users/youyo/AppData/Local/Microsoft/Edge/User Data/Default/History')
d = Path(tempfile.mkdtemp()) / 'H'
shutil.copy2(SRC, d)
con = sqlite3.connect('file:%s?immutable=1' % d.as_posix(), uri=True)
q = """SELECT url, title, visit_count FROM urls
       WHERE url LIKE '%institutionLogin%' OR url LIKE '%idplib%'
              OR url LIKE '%lyuapServer%' OR url LIKE '%ShibAuth%'
       ORDER BY last_visit_time DESC LIMIT 20"""
for u, t, c in con.execute(q):
    print('CNT=%d  %s' % (c, u))
    print('     title:', (t or '')[:100])
con.close()
