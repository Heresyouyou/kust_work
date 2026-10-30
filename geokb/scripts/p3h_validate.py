# -*- coding: utf-8 -*-
"""Phase 3h：content/ 全量校验——frontmatter 必填字段、module 合法性、id 唯一性，输出统计。"""
import json
import os
import re
import collections

BASE = r"D:/ZSK/geokb/content"
MODULES = {d for d in os.listdir(BASE) if os.path.isdir(os.path.join(BASE, d))}
REQUIRED = ["id", "title", "module", "tags", "summary", "confidence", "sources", "updated", "status"]
VALID_CONF = {"A", "B", "C", "D"}
VALID_STATUS = {"active", "deprecated", "deleted"}

errors, ids, stats = [], [], collections.defaultdict(int)
n = 0
for mod in sorted(MODULES):
    for fn in sorted(os.listdir(os.path.join(BASE, mod))):
        if not fn.endswith(".md"):
            continue
        n += 1
        path = os.path.join(BASE, mod, fn)
        raw = open(path, encoding="utf-8").read()
        m = re.match(r"^---\n(.*?)\n---\n", raw, re.S)
        if not m:
            errors.append(f"{mod}/{fn}: 无 frontmatter")
            continue
        fm_text = m.group(1)
        keys = re.findall(r"^(\w+):", fm_text, re.M)
        for k in REQUIRED:
            if k not in keys:
                errors.append(f"{mod}/{fn}: 缺字段 {k}")
        idm = re.search(r"^id:\s*(\S+)", fm_text, re.M)
        if idm:
            if idm.group(1) in ids:
                errors.append(f"{mod}/{fn}: id 重复 {idm.group(1)}")
            ids.append(idm.group(1))
        modm = re.search(r"^module:\s*(\S+)", fm_text, re.M)
        if modm and modm.group(1) != mod:
            errors.append(f"{mod}/{fn}: module 字段 {modm.group(1)} 与目录 {mod} 不符")
        confm = re.search(r"^confidence:\s*(\S+)", fm_text, re.M)
        if confm and confm.group(1) not in VALID_CONF:
            errors.append(f"{mod}/{fn}: confidence 非法 {confm.group(1)}")
        statm = re.search(r"^status:\s*(\S+)", fm_text, re.M)
        if statm and statm.group(1) not in VALID_STATUS:
            errors.append(f"{mod}/{fn}: status 非法 {statm.group(1)}")
        stats[mod] += 1

papers = sum(1 for _ in open(r"D:/ZSK/geokb/index/papers_index.jsonl", encoding="utf-8"))
print(f"条目总数: {n}（另 papers_index.jsonl 题录 {papers} 条）")
print("模块分布:")
for mod, c in sorted(stats.items()):
    print(f"  {mod:<18} {c}")
print(f"唯一 id: {len(set(ids))}")
print(f"校验错误: {len(errors)}")
for e in errors[:20]:
    print("  ERR", e)
with open(r"D:/ZSK/geokb/raw_sources/phase3_validation.json", "w", encoding="utf-8") as f:
    json.dump({"entries": n, "papers": papers, "by_module": dict(stats),
               "errors": errors, "validated_at": "2026-10-13"}, f, ensure_ascii=False, indent=2)
