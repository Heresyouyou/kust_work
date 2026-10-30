# -*- coding: utf-8 -*-
"""Phase 3f（修正版）：论文库按期刊子目录组织，递归遍历解析文件名 -> papers_index.jsonl + 语料说明条目。只读源目录。"""
import json
import os
import re
import collections

SRC = r"D:/PostNews/pdfs/sjiang_porphyry_CuMo_2026/pdfs"
TOP = r"D:/PostNews/pdfs"
OUT = r"D:/ZSK/geokb/content/economic-geology"
TODAY = "2026-10-13"

JOURNALS = {
    "AGSE": "Acta Geologica Sinica (English Edition)",
    "APPLSCI": "Applied Sciences (MDPI)",
    "EARTH_MDPI": "Earth (MDPI)",
    "FRONTENV": "Frontiers in Environmental Science",
    "FRONTES": "Frontiers in Earth Science",
    "FRONTGEO": "Frontiers in Geosciences",
    "GEOG_MDPI": "Geographies (MDPI)",
    "GEOLOGY": "Geology (GSA)",
    "GEOSCI": "Geosciences (MDPI)",
    "IJES": "International Journal of Earth Sciences",
    "JAES": "Journal of Asian Earth Sciences",
    "JGE": "Journal of Geochemical Exploration",
    "JPETROL": "Journal of Petrology",
    "LITHOS": "Lithos",
    "MINDEP": "Mineralium Deposita",
    "MINERALS": "Minerals (MDPI)",
    "MINPET": "Mineralogy and Petrology",
    "OTHERS": "其他/未归类",
    "PRECAMB": "Precambrian Research",
    "SUSTAIN": "Sustainability (MDPI)",
    "WATER_MDPI": "Water (MDPI)",
}

pat = re.compile(r"^(\d{1,2})_(\d{4})_([A-Za-z]+)_(.+?)\.pdf$")
records, bad = [], []
for dirpath, _dirs, files in os.walk(SRC):
    for fn in sorted(files):
        if not fn.lower().endswith(".pdf"):
            continue
        jdir = os.path.basename(dirpath)
        m = pat.match(fn)
        if not m:
            bad.append(f"{jdir}/{fn}")
            continue
        day, year, jcode, rest = m.groups()
        author = rest.split("_")[0]
        records.append({
            "file": f"{dirpath}/{fn}".replace("\\", "/"),
            "year": int(year),
            "journal_dir": jdir,
            "journal": JOURNALS.get(jdir, jdir),
            "first_author": author,
            "ref": rest,
            "doi": None,
            "title": None,
        })

top_records = []
for fn in sorted(os.listdir(TOP)):
    if fn.lower().endswith(".pdf") and os.path.isfile(os.path.join(TOP, fn)):
        top_records.append({"file": f"{TOP}/{fn}", "note": "顶层精选",
                            "zh_translated": ".zh.mono." in fn})

jc = collections.Counter(r["journal"] for r in records)
yc = collections.Counter(r["year"] for r in records)

os.makedirs(r"D:/ZSK/geokb/index", exist_ok=True)
with open(r"D:/ZSK/geokb/index/papers_index.jsonl", "w", encoding="utf-8") as f:
    for r in records:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

jtable = "\n".join(f"| {j} | {n} |" for j, n in jc.most_common())
ytable = "\n".join(f"| {y} | {n} |" for y, n in sorted(yc.items()))
entry = f"""---
id: eco-papers-corpus-001
title: 斑岩-矽卡岩矿床期刊论文语料库（题录索引）
module: economic-geology
tags: [斑岩铜钼矿, 矽卡岩, 三江, 论文语料, 题录]
summary: 本地 {len(records)} 篇期刊论文（{min(yc)}–{max(yc)} 年）的题录索引与主题说明，支撑矿床学/地球化学前沿问题检索；全文存于本地 PDF 库，按需二次摄取。
confidence: B
sources:
  - name: 本地论文库（sjiang_porphyry_CuMo_2026，21 个期刊子目录）
    url: local://PostNews/pdfs/sjiang_porphyry_CuMo_2026/pdfs
    accessed: {TODAY}
updated: {TODAY}
status: active
---

# 语料概况

- 总量：**{len(records)} 篇**（文件名解析成功），未匹配 {len(bad)} 篇；另有顶层精选 {len(top_records)} 个 PDF（含 {sum(1 for t in top_records if t['zh_translated'])} 篇中文精译版）。
- 主题域：三江及邻区斑岩 Cu-Mo 成矿系统、矽卡岩矿床、萤石/关键金属地球化学、Au/Co/Ag-Zn-Pb 矿床。
- 题录索引文件：`geokb/index/papers_index.jsonl`（每行一条：file/year/journal_dir/journal/first_author/ref；title 与 doi 留空，后续经 CrossRef 批量补全）。
- 全文 PDF 共约 18GB，**不入库原文**；引用时给出文件路径与题录。

## 期刊分布

| 期刊 | 篇数 |
|---|---|
{jtable}

## 年份分布

| 年份 | 篇数 |
|---|---|
{ytable}

## 使用方式（面向 Agent）

1. 先检索本条目判断问题是否属于该语料域；
2. 需要具体文献时，读取 `papers_index.jsonl` 按 journal/year/author 过滤；
3. 需要正文时对单个 PDF 做按需摄取（后续增量流程）。
"""
with open(os.path.join(OUT, "papers-corpus.md"), "w", encoding="utf-8") as f:
    f.write(entry)

print(f"parsed: {len(records)}, unmatched: {len(bad)}")
if bad[:10]:
    print("unmatched samples:", bad[:10])
print("journals:", dict(jc.most_common()))
print("years:", dict(sorted(yc.items())))
