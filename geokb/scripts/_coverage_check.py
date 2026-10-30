# -*- coding: utf-8 -*-
"""覆盖率检查：论文仓库 PDF vs 已入库条目，找出未入库/重复入库的 PDF。"""
import glob
import json
import os
import re
from collections import Counter

import yaml

from paths import by_journal  # noqa: E402

REPO = by_journal()
CONTENT = r"D:/ZSK/geokb/content/papers"

pdfs = set()
for p in glob.glob(os.path.join(REPO, "**", "*.pdf"), recursive=True):
    pdfs.add(os.path.relpath(p, REPO).replace("\\", "/"))

used = Counter()
unknown = []
for md in glob.glob(os.path.join(CONTENT, "*.md")):
    raw = open(md, encoding="utf-8").read()
    m = re.match(r"^---\n(.*?)\n---\n", raw, re.S)
    if not m:
        continue
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception:
        continue
    srcs = fm.get("sources") or []
    hit = None
    for s in srcs:
        if isinstance(s, dict) and s.get("path"):
            hit = str(s["path"]).replace("papers_repo/by_journal/", "")
            hit = hit.replace("\\", "/")
            break
        if isinstance(s, str) and "by_journal" in s:
            hit = s.split("by_journal/", 1)[1].replace("\\", "/")
            break
    if hit:
        used[hit] += 1
        if hit not in pdfs:
            unknown.append((os.path.basename(md), hit))
    else:
        unknown.append((os.path.basename(md), "<无 path>"))

missing = sorted(pdfs - set(used))
dup = {k: v for k, v in used.items() if v > 1}

print("仓库 PDF（by_journal）: %d" % len(pdfs))
print("已入库条目引用 PDF  : %d 个唯一 / %d 次" % (len(used), sum(used.values())))
print("未入库 PDF          : %d" % len(missing))
for x in missing[:20]:
    print("   -", x)
print("重复引用 PDF        : %d" % len(dup))
for k, v in list(dup.items())[:10]:
    print("   -", k, "x%d" % v)
print("条目引用了不存在的 PDF: %d" % len(unknown))
for a, b in unknown[:10]:
    print("   -", a, "->", b)
