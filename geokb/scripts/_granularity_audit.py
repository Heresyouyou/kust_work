# -*- coding: utf-8 -*-
"""知识粒度审计：统计 glossary 打包量、papers 可切分知识点潜力、正文长度分布。"""
import glob
import os
import re
from collections import Counter

import yaml

BASE = r"D:/ZSK/geokb"
CONTENT = os.path.join(BASE, "content")


def load(path):
    raw = open(path, encoding="utf-8").read()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    if not m:
        return None, raw
    try:
        return yaml.safe_load(m.group(1)) or {}, m.group(2)
    except Exception:
        return None, m.group(2)


# ---- glossary ----
g_tot = 0
g_files = 0
g_sizes = []
for p in sorted(glob.glob(os.path.join(CONTENT, "glossary", "*.md"))):
    if os.path.basename(p).startswith("_"):
        continue
    fm, body = load(p)
    if not fm:
        continue
    t = str(fm.get("title", ""))
    m = re.search(r"（(\d+)\s*条）", t)
    if m:
        k = int(m.group(1))
        g_sizes.append(k)
        g_tot += k
        g_files += 1
    # 实际术语行数（正文中的 “- **术语**” 或 表格行）
print("[glossary] 打包文件 %d 个，标题声明知识点合计 %d 条" % (g_files, g_tot))
print("[glossary] 单包含量分布:", sorted(Counter(g_sizes).items(), reverse=True)[:12])

# 正文里真实的术语条目数（按行首 “- ” 或 “| ” 计）
real = 0
for p in sorted(glob.glob(os.path.join(CONTENT, "glossary", "*.md"))):
    if os.path.basename(p).startswith("_"):
        continue
    _, body = load(p)
    real += len(re.findall(r"^\s*[-*]\s+\S", body, re.M))
print("[glossary] 正文实际术语行合计:", real)

# ---- papers ----
lens = []
secnames = Counter()
for p in sorted(glob.glob(os.path.join(CONTENT, "papers", "*.md"))):
    _, body = load(p)
    lens.append(len(body))
    for h in re.findall(r"^##\s+(.+)$", body, re.M):
        secnames[h.strip()] += 1
print("\n[papers] 条目数 %d；正文字符 总 %d / 均 %.0f / 中位 %d / max %d"
      % (len(lens), sum(lens), sum(lens) / len(lens), sorted(lens)[len(lens) // 2], max(lens)))
print("[papers] 章节模板统一为:", secnames.most_common(8))

# 可切分知识点潜力：核心方法条数 + 应用场景条数 + 1(关键内容) + 1(结论)
pot = []
for p in sorted(glob.glob(os.path.join(CONTENT, "papers", "*.md"))):
    _, body = load(p)
    secs = re.split(r"^##\s+", body, flags=re.M)[1:]
    d = {}
    for s in secs:
        name = s.split("\n", 1)[0].strip()
        d[name] = s
    n_meth = len(re.findall(r"^\s*[-*]\s+\S", d.get("核心方法", ""), re.M))
    n_app = len(re.findall(r"^\s*[-*]\s+\S", d.get("应用场景", ""), re.M))
    has_data = 1 if re.search(r"\d", d.get("数据指标", "") or "") else 0
    pot.append(1 + min(n_meth, 3) + min(n_app, 2) + has_data)
print("[papers] 每篇可切知识点潜力：均值 %.2f；合计 ≈ %d 条"
      % (sum(pot) / len(pot), sum(pot)))
print("[papers] 潜力分布:", Counter(pot).most_common())
