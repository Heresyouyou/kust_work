# -*- coding: utf-8 -*-
"""Phase 3b：glossary_verified.json -> glossary 模块条目（按 group 分组）+ _synonyms.yaml。"""
import json
import os
import re
import collections

SRC = r"D:/PostNews/Translate/corpus/glossary_verified.json"
OUT = r"D:/ZSK/geokb/content/glossary"
TODAY = "2026-10-13"

d = json.load(open(SRC, encoding="utf-8"))
rows = d["rows"]
groups = collections.defaultdict(list)
for r in rows:
    groups[r.get("group") or "misc"].append(r)

def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")

fm_template = """---
id: glo-{gslug}-001
title: 术语表·{group}（{n} 条）
module: glossary
tags: [术语, 中英对照, {group}]
summary: 地矿术语中英对照表（{group} 组），共 {n} 条，全部经权威来源交叉验证（HIGH 置信）。
confidence: A
sources:
  - name: GeoTranslate glossary_verified（Earle CC BY / Huston CC BY / USGS 公共领域交叉验证）
    url: local://PostNews/Translate/corpus/glossary_verified.json
    accessed: {today}
updated: {today}
status: active
---
"""

count = 0
for g, items in sorted(groups.items()):
    items.sort(key=lambda r: r["en"].lower())
    lines = [f"# 术语表·{g}\n", "| English | 中文 | 释义（中文） |", "|---|---|---|"]
    for r in items:
        zh_def = (r.get("def_zh") or r.get("def_en") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {r['en']} | {r['zh']} | {zh_def} |")
    gslug = slugify(g)
    with open(os.path.join(OUT, f"terms-{gslug}.md"), "w", encoding="utf-8") as f:
        f.write(fm_template.format(gslug=gslug, group=g, n=len(items), today=TODAY))
        f.write("\n".join(lines) + "\n")
    count += 1

# _synonyms.yaml：中英双向映射（检索扩展用）
syn = ["# GeoKB 同义词/中英映射（由 glossary_verified 293 条生成，可手工增补）",
       "# 格式：标准词: [别名1, 别名2, ...]，检索时对查询词做双向扩展", ""]
for r in sorted(rows, key=lambda x: x["en"].lower()):
    en, zh = r["en"].strip(), r["zh"].strip()
    syn.append(f'"{en}": ["{zh}"]')
    syn.append(f'"{zh}": ["{en}"]')
with open(os.path.join(OUT, "_synonyms.yaml"), "w", encoding="utf-8") as f:
    f.write("\n".join(syn) + "\n")

print(f"entries written: {count}, synonym pairs: {len(rows)}")
