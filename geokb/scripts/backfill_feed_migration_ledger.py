# -*- coding: utf-8 -*-
"""回溯登记：migrate 成功但漏写台账的论文补写 papers_index.jsonl。

背景（2026-10-18）：migrate_feed_to_papers.py 此前只写 content/papers/ 条目，
不写台账 → journals 页对已迁移文章仍显示「在线抓取」，而 PDF 其实一直在 NAS。
migrate 脚本已修（step ④ 加 append_index）；本脚本回溯补救存量。

判定逻辑（按 paper 条目的 sources 实际指向）：
  A. source 指向 by_journal/<刊>/... 且文件在 → 登该路径（正常归档形态）
  B. source 指向 _feed_migration/<digest>.pdf 且文件在 → 登该路径
  C. 台账已有同 repo_path 或同标题 → 跳过（幂等）
只读探测 + --write 落盘两段式。
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, r'D:/ZSK/geokb/scripts')
sys.path.insert(0, r'D:/PostNews')
sys.path.insert(0, r'D:/PostNews/.workbuddy')

import journal_feed
from daily_archive_papers import append_index, load_index_keys, _norm_title

PAPERS_DIR = Path('D:/ZSK/geokb/content/papers')
NAS_ROOT = Path('S:/GeoKB')

ledger = journal_feed.load_ledger()
index_keys = load_index_keys()
# ⚠️ 台账里 repo_path 混用正/反斜杠（NAS 路径 S:\GeoKB\... 与脚本生成的
# S:/GeoKB/...），比对前必须统一规范化，否则同一条文件会被判成「未登记」。
led_paths = {(v.get('repo_path') or '').replace('\\', '/').lower()
             for v in ledger.values()}


def full_path(rel):
    rel = rel.replace('\\', '/').lstrip('./')
    if re.match(r'^[A-Za-z]:', rel):
        return Path(rel)
    return NAS_ROOT / rel


plan = []
for md in sorted(PAPERS_DIR.glob('paper-*.md')):
    raw = md.read_text(encoding='utf-8', errors='ignore')
    m = re.match(r'^---\n(.*?)\n---', raw, re.S)
    if not m:
        continue
    fm = {}
    for line in m.group(1).splitlines():
        if ':' in line and not line.startswith(' '):
            k, _, v = line.partition(':')
            fm[k.strip()] = v.strip().strip('"\'')
    title = fm.get('title', '')
    eid = fm.get('id', '')
    journal = fm.get('journal', '')

    # 收集所有带 path 的 source（按出现顺序，本地PDF优先）
    paths = re.findall(r'"path":\s*"([^"]+)"', raw)
    urls = re.findall(r'"url":\s*"(https?://[^"]+)"', raw)
    if not paths:
        continue
    # 只关心与 _feed_migration 有关或可能漏登的；先看主 source
    chosen = None
    for rel in paths:
        p = full_path(rel)
        if p.exists():
            chosen = p
            break
    if chosen is None:
        continue
    # 只补 _feed_migration 盲区：其余 paper 的 source 多指向翻译版
    # （.zh.mono/.zh.dual，原文已在台账）或窗口外的老语料（页面无影响），
    # 登了反而是噪声。
    if '_feed_migration' not in str(chosen).replace('\\', '/'):
        continue
    key = _norm_title(title)
    if (str(chosen).replace('\\', '/').lower() in led_paths
            or journal_feed.lookup_ledger(ledger, key) is not None
            or key in index_keys):
        continue
    plan.append({'eid': eid, 'title': title, 'journal': journal,
                 'repo_path': str(chosen), 'size': chosen.stat().st_size,
                 'url': urls[0] if urls else ''})

print('paper 条目总数: %d · 需补登台账: %d' % (
    len(list(PAPERS_DIR.glob('paper-*.md'))), len(plan)))
for e in plan[:10]:
    print('  %s | %s | %d B | %s' % (e['eid'], e['title'][:36],
                                     e['size'], e['repo_path'][-52:]))

if '--write' not in sys.argv:
    print('\n干跑（加 --write 落盘）')
    raise SystemExit(0)

n = 0
for e in plan:
    if append_index({
            'title': e['title'], 'url': e['url'], 'journal': e['journal'],
            'date': '', 'pdf_url': '', 'repo_path': e['repo_path'],
            'size': e['size'], 'source': 'feed-migration',
            'eid': e['eid'], 'indexed_at': datetime.now().isoformat()}):
        n += 1
        print('  ✓ %s | %s' % (e['eid'], e['title'][:40]))
print('\n补登 %d 条' % n)
