import urllib.request, urllib.parse, http.cookiejar, sqlite3, os

# token 从配置文件读取（避免硬编码漂移）
_tok = None
with open(r'D:/ZSK/geokb/config/admin_token.txt', encoding='utf-8') as f:
    for line in f:
        if line.strip().startswith('TOKEN='):
            _tok = line.split('=', 1)[1].strip()
assert _tok, 'admin_token.txt 未配置 TOKEN'

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
base = 'http://127.0.0.1:5554'
opener.open(base+'/admin/login', data=urllib.parse.urlencode({'token':_tok}).encode())

conn = sqlite3.connect(r'D:/ZSK/geokb/index/geokb.db')
eid = conn.execute("SELECT id FROM entries WHERE id LIKE 'test-%' ORDER BY id DESC LIMIT 1").fetchone()[0]
conn.close()
print('target eid:', eid)

req = urllib.request.Request(base+f'/admin/entry/{eid}/delete', method='POST')
opener.open(req)
conn = sqlite3.connect(r'D:/ZSK/geokb/index/geokb.db')
status = conn.execute('SELECT status FROM entries WHERE id=?', (eid,)).fetchone()[0]
print('status after delete:', status)
conn.close()

req2 = urllib.request.Request(base+f'/admin/entry/{eid}/restore', method='POST')
opener.open(req2)
conn = sqlite3.connect(r'D:/ZSK/geokb/index/geokb.db')
status2 = conn.execute('SELECT status FROM entries WHERE id=?', (eid,)).fetchone()[0]
print('status after restore:', status2)
conn.close()
