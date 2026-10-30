# -*- coding: utf-8 -*-
"""一次性清理：移除 state 中的 fallback 记录并删除对应条目文件，供重跑。"""
import json
import os

STATE = r"D:/ZSK/geokb/state/pdf_state.jsonl"
CONTENT = r"D:/ZSK/geokb/content/papers"

keep, drop = [], []
for line in open(STATE, encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    r = json.loads(line)
    if r.get("status") == "fallback":
        drop.append(r)
    else:
        keep.append(line)

with open(STATE, "w", encoding="utf-8") as f:
    for ln in keep:
        f.write(ln + "\n")

removed = 0
for r in drop:
    eid = r.get("entry")
    if eid:
        p = os.path.join(CONTENT, f"{eid}.md")
        if os.path.exists(p):
            os.remove(p)
            removed += 1
print(f"state: kept={len(keep)} dropped={len(drop)}; removed {removed} fallback md files")
