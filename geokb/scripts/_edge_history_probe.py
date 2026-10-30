"""只读 Edge 历史，找统一身份认证 / SSO 的真实 URL（不改动任何文件）。"""
import shutil
import sqlite3
import tempfile
from pathlib import Path

SRC = Path('C:/Users/youyo/AppData/Local/Microsoft/Edge/User Data/Default/History')
print('history exists:', SRC.exists())

tmpdir = tempfile.mkdtemp(prefix='edgehist_')
dst = Path(tmpdir) / 'History'
try:
    shutil.copy2(SRC, dst)
    print('copied ->', dst)
except Exception as e:
    print('copy failed:', type(e).__name__, e)
    dst = SRC

con = sqlite3.connect('file:%s?immutable=1' % Path(dst).as_posix(), uri=True)
q = '''
SELECT url, title, visit_count, last_visit_time
FROM urls
WHERE lower(url) LIKE '%kust%' OR title LIKE '%统一身份%' OR title LIKE '%认证%'
ORDER BY last_visit_time DESC
LIMIT 40
'''
rows = con.execute(q).fetchall()
con.close()
print('rows:', len(rows))
for u, t, vc, _ in rows:
    print('  %-90s | %s | vc=%s' % (u[:90], (t or '')[:40], vc))
