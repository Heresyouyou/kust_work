# -*- coding: utf-8 -*-
"""验证 api.elsevier.com 无密钥可达性：能不能拿到全文 XML"""
import sys
import time
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
OUT = BASE / 'state' / '_sd_probe'
OUT.mkdir(parents=True, exist_ok=True)

from curl_cffi import requests as creq  # noqa

PII = sys.argv[1] if len(sys.argv) > 1 else 'S0169136826004932'
URLS = [
    ('xml', 'https://api.elsevier.com/content/article/pii/%s?httpAccept=text/xml' % PII),
    ('json', 'https://api.elsevier.com/content/article/pii/%s?httpAccept=application/json' % PII),
    ('abstract', 'https://api.elsevier.com/content/abstract/pii/%s?httpAccept=json' % PII),
]

S = creq.Session(impersonate='chrome', trust_env=False)

for tag, u in URLS:
    try:
        r = S.get(u, timeout=45,
                  headers={'Accept': 'application/json, text/xml;q=0.9, */*;q=0.8'})
        body = r.content
        print('%-9s %s %7d bytes' % (tag, r.status_code, len(body)))
        txt = body.decode('utf-8', 'ignore')
        print('   head:', txt[:260].replace('\n', ' '))
        if r.status_code == 200:
            f = OUT / ('api_%s_%s.txt' % (PII, tag))
            f.write_text(txt, encoding='utf-8')
            print('   已存', f.name, '| 含正文段落:', txt.count('<ce:para'),
                  '| 含 coredata:', 'coredata' in txt)
    except Exception as e:
        print('%-9s ERR %s %s' % (tag, type(e).__name__, str(e)[:60]))
    time.sleep(2)
