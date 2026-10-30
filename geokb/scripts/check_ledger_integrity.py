# -*- coding: utf-8 -*-
"""台账完整性核查：每条有 repo_path 的登记，验证 NAS 文件真实存在且字节数吻合。
只读，不改任何数据。"""
import json
import os
from pathlib import Path

LEDGER = Path('D:/ZSK/geokb/state/papers_index.jsonl')

total = pdf_entries = missing = size_mismatch = ok = 0
bad = []
for line in LEDGER.read_text(encoding='utf-8', errors='replace').splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        m = json.loads(line)
    except Exception:
        continue
    total += 1
    rp = (m.get('repo_path') or '').strip()
    if not rp or 'papers_repo/' not in rp.replace('\\', '/'):
        continue          # 纯题录（metadata_only）不参与核查
    pdf_entries += 1
    p = Path(rp)
    if not p.exists():
        missing += 1
        bad.append(('MISSING', m.get('title', '')[:46], rp[:90]))
        continue
    try:
        actual = p.stat().st_size
    except OSError as e:
        missing += 1
        bad.append(('STAT_ERR', m.get('title', '')[:46], str(e)[:60]))
        continue
    declared = m.get('size') or 0
    if declared and actual != declared:
        size_mismatch += 1
        bad.append(('SIZE', m.get('title', '')[:46],
                    'declared=%d actual=%d' % (declared, actual)))
    else:
        ok += 1

print('台账总行数: %d · 有 PDF 的条目: %d' % (total, pdf_entries))
print('  文件在位且字节吻合: %d' % ok)
print('  文件缺失/不可读:    %d' % missing)
print('  字节数不符:         %d' % size_mismatch)
if bad:
    print('\n问题条目（前 25 条）:')
    for kind, t, d in bad[:25]:
        print('  [%s] %s | %s' % (kind, t, d))
    if len(bad) > 25:
        print('  ...共 %d 条' % len(bad))
