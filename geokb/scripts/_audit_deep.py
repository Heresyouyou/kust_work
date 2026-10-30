# -*- coding: utf-8 -*-
"""深度质检：10 篇抽样原文比对 + 按期刊字段完整率 + 标签长尾分析。"""
import glob
import json
import os
import random
import re
import collections

import yaml

CONTENT = r"D:/ZSK/geokb/content/papers"
TEXT_DIR = r"D:/ZSK/geokb/state/text"


def parse(f):
    t = open(f, encoding="utf-8").read()
    m = re.match(r"---\n(.*?)\n---\n(.*)", t, re.S)
    return yaml.safe_load(m.group(1)), m.group(2)


def section(body, name):
    m = re.search(r"## " + name + r"\n\n(.*?)(?=\n\n## |\Z)", body, re.S)
    return m.group(1).strip() if m else ""


files = sorted(glob.glob(os.path.join(CONTENT, "*.md")))
print(f"总条目: {len(files)}")

# ── 按期刊统计 ──
by_journal = collections.defaultdict(lambda: {"n": 0, "kc_short": 0, "no_area": 0, "no_method": 0, "tags_avg": 0.0})
tag_c = collections.Counter()
year_c = collections.Counter()
for f in files:
    fm, body = parse(f)
    j = fm.get("journal") or "未知"
    st = by_journal[j]
    st["n"] += 1
    kc = section(body, "关键内容")
    if len(kc) < 80:
        st["kc_short"] += 1
    if "未能提取" in section(body, "研究区域"):
        st["no_area"] += 1
    if "未能提取" in section(body, "核心方法"):
        st["no_method"] += 1
    st["tags_avg"] += len(fm.get("tags", []))
    for t in fm.get("tags", []):
        tag_c[t] += 1
    year_c[fm.get("year") or "未知"] += 1

print("\n=== 按期刊（完整率）===")
print(f"{'期刊':<12}{'篇数':>5}{'关键内容短':>10}{'缺研究区':>9}{'缺方法':>8}{'均标签':>8}")
for j, st in sorted(by_journal.items(), key=lambda x: -x[1]["n"]):
    print(f"{j:<12}{st['n']:>5}{st['kc_short']:>10}{st['no_area']:>9}{st['no_method']:>8}{st['tags_avg']/st['n']:>8.1f}")

print("\n=== 年份分布 ===")
for y, c in sorted(year_c.items()):
    print(f"  {y}: {c}")

print("\n=== 标签统计 ===")
print(f"唯一标签数: {len(tag_c)}")
print(f"只出现1次的长尾标签: {sum(1 for t,c in tag_c.items() if c==1)}")
print(f"出现≥10次的核心标签: {sum(1 for t,c in tag_c.items() if c>=10)}")
print("Top30:", [f"{t}({c})" for t, c in tag_c.most_common(30)])

# ── 10 篇抽样原文比对 ──
print("\n=== 10 篇抽样原文比对 ===")
random.seed(20261014)
sample = random.sample(files, 10)
ok = 0
for f in sample:
    fm, body = parse(f)
    digest = os.path.basename(f)[len("paper-"):-3]
    txts = glob.glob(os.path.join(TEXT_DIR, digest + "*.txt"))
    kc = section(body, "关键内容")
    area = section(body, "研究区域")
    verdict = "?"
    if txts:
        src = open(txts[0], encoding="utf-8", errors="ignore").read().lower()
        # 粗校验：研究区中的关键地名（取第一个2字以上词）是否出现在原文
        cand = re.findall(r"[A-Za-z][A-Za-z\-]{4,}|[一-鿿]{2,}", area)
        hit = any(c.lower() in src for c in cand[:3] if len(c) >= 2) if cand else False
        # 标题英文关键词命中
        ten = (fm.get("title") or "")
        verdict = "一致" if hit else "研究区未在原文命中(需人工看)"
    print(f"--- {os.path.basename(f)} [{verdict}]")
    print(f"    标题: {(fm.get('title') or '')[:70]}")
    print(f"    区域: {area[:60]}")
    if verdict == "一致":
        ok += 1
print(f"\n抽样结论: {ok}/10 研究区在原文中命中，其余需人工复核")
