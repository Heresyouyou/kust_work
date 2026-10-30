# -*- coding: utf-8 -*-
"""验证「按源站节流 + 403 退避」能否把 gsw 的 403 变回 200。

对照组：_gsw_channel_compare.py 以 1.2s 固定间隔连发 → 4/6 篇 403。
本组：走 antibot_session.wait_turn()（gsw 20s 分桶间隔）+ 403 指数退避重试。
"""
import json
import re
import sys
import time

sys.path.insert(0, 'D:/ZSK/geokb/scripts')
from curl_cffi import requests as cr
import antibot_session as ab

LEDGER = 'D:/PostNews/journal_ledger.json'
N = 4


def urls():
    d = json.load(open(LEDGER, encoding='utf-8'))
    out, seen = [], set()
    for j, arts in (d.get('journals') or {}).items():
        for a in arts or []:
            u = a.get('url') or ''
            if 'geoscienceworld.org' not in u or u in seen:
                continue
            seen.add(u)
            out.append(((a.get('title') or '')[:44], u))
    return out[:N]


def probe(u):
    r = cr.get(u, impersonate='chrome', timeout=40, allow_redirects=True)
    html = r.text
    has = bool(re.search(r'name=["\']citation_pdf_url["\']', html, re.I))
    chal = any(k in html[:5000].lower() for k in
               ('just a moment', 'cf-chl', 'checking your browser'))
    return r.status_code, has, chal


def main():
    print('引擎:', ab.engine_name(), '| gsw 间隔:', ab.gap_for('pubs.geoscienceworld.org'), 's\n')
    ok = 0
    rows = []
    for i, (t, u) in enumerate(urls(), 1):
        waited = ab.wait_turn(u)
        t0 = time.time()
        code, has, chal = probe(u)
        note = ''
        if code == 403:
            w = ab.backoff(1)
            print('   [退避 %.0fs 后重试]' % w)
            time.sleep(w)
            ab.wait_turn(u, gap=0)
            code, has, chal = probe(u)
            note = 'after_backoff'
        good = (code == 200)
        ok += 1 if good else 0
        print('[%d/%d] 等待%4.0fs → %s pdf=%s chal=%s %.1fs %s | %s'
              % (i, N, waited, code, has, chal, time.time() - t0, note, t[:40]))
        rows.append({'url': u[:100], 'code': code, 'has_pdf': has,
                     'challenge': chal, 'waited': waited, 'note': note})
        time.sleep(1)
    json.dump(rows, open('D:/ZSK/geokb/logs/gsw_paced_result.json', 'w',
                         encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n正文页拿到 %d/%d（对照组 1.2s 间隔时仅 2/6）' % (ok, N))


if __name__ == '__main__':
    sys.exit(main())
