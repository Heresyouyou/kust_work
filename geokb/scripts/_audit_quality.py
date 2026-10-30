# -*- coding: utf-8 -*-
"""一次性质检：审计 content/papers 已入库条目质量（垃圾检测 + 抽样原文比对）。"""
import glob
import json
import os
import random
import re
import statistics
import sys
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
print(f"=== entries: {len(files)} ===")

issues = []
kc_lens, titles, tag_c = [], [], collections.Counter()
for f in files:
    fm, body = parse(f)
    base = os.path.basename(f)
    kc = section(body, "关键内容")
    methods = section(body, "核心方法")
    area = section(body, "研究区域")
    kc_lens.append(len(kc))
    titles.append(fm.get("title", ""))
    for t in fm.get("tags", []):
        tag_c[t] += 1
    if len(kc) < 40:
        issues.append((base, "关键内容过短", kc[:60]))
    if "未能提取" in kc:
        issues.append((base, "关键内容缺失", ""))
    if "未能提取" in methods and "未能提取" in area:
        issues.append((base, "方法+区域双缺失", ""))
    if kc.lstrip().startswith("{") or "key_content" in kc:
        issues.append((base, "JSON残留", kc[:60]))
    bad = sum(1 for ch in kc if ord(ch) == 0xFFFD or 0xE000 <= ord(ch) <= 0xF8FF)
    if bad > 2:
        issues.append((base, "乱码字符", repr(kc[:60])))
    if not fm.get("title") or len(fm["title"]) < 6:
        issues.append((base, "标题异常", fm.get("title", "")))
    if len(fm.get("tags", [])) < 2:
        issues.append((base, "标签过少", str(fm.get("tags"))))

dup = [t for t, c in collections.Counter(titles).items() if c > 1]
print(f"key_content 长度: mean={statistics.mean(kc_lens):.0f} min={min(kc_lens)} max={max(kc_lens)}")
print(f"重复标题: {len(dup)} {dup[:3]}")
print(f"问题条目: {len(issues)} / {len(files)}")
for it in issues[:15]:
    print("  ", it)
print("=== Top15 标签 ===")
for t, c in tag_c.most_common(15):
    print(f"  {c:3d}  {t}")

# 抽样 3 篇与原文比对
print("\n=== 抽样原文比对（3篇）===")
random.seed(42)
for f in random.sample(files, min(3, len(files))):
    fm, body = parse(f)
    digest = os.path.basename(f)[len("paper-"):-3]
    txts = glob.glob(os.path.join(TEXT_DIR, digest + "*.txt"))
    print("-" * 70)
    print("标题:", fm.get("title"))
    print("关键内容:", section(body, "关键内容")[:200])
    print("研究区域:", section(body, "研究区域")[:80])
    if txts:
        src = open(txts[0], encoding="utf-8", errors="ignore").read()
        print("原文前300字:", re.sub(r"\s+", " ", src[:300]))
