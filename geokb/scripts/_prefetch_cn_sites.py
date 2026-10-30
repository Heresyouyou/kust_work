# -*- coding: utf-8 -*-
"""预抓中文两刊官网题录到 manifest_raw 缓存（与主流程共用同一 cache key）。

只写缓存，不写任何清单/暂存区，避免与正在跑的 20 年枚举冲突。
矿床地质（kcdz）本地故障、大地构造与成矿学（dgyk）冻结中，都不抓。
"""
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

os.environ['NO_PROXY'] = '*'
os.environ['no_proxy'] = '*'

BASE = Path('D:/ZSK/geokb')
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, 'D:/PostNews')

import history_manifest as hm                      # noqa: E402

RAW = BASE / 'state' / 'manifest_raw'
until = date.today()
frm = until - timedelta(days=365 * 20)
years = list(range(frm.year, until.year + 1))
log = lambda m: print(m, flush=True)

for name, fn in (('地学前缘', hm.fetch_esf), ('地质学报', hm.fetch_dzxb)):
    key = 'v3_20y_site_%s_%s_%s' % (re.sub(r'\W+', '', name)[:16],
                                     frm.strftime('%Y%m%d'), until.strftime('%Y%m%d'))
    p = RAW / (key + '.json')
    if p.exists():
        try:
            old = json.loads(p.read_text(encoding='utf-8'))
            log('  [已有] %-8s %d 条（%s）' % (name, len(old), p.name))
            continue
        except Exception:
            pass
    t0 = time.time()
    try:
        items = fn(years)
    except Exception as e:
        log('  [失败] %-8s %s: %s' % (name, type(e).__name__, str(e)[:90]))
        continue
    RAW.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(items, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, p)
    log('  [抓取] %-8s %4d 条  %.0fs  → %s' % (name, len(items), time.time() - t0, p.name))
