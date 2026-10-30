# -*- coding: utf-8 -*-
"""Phase 3d + 3g：
3d  Earle 教材 22 章 -> 各模块 Markdown 条目（英文原文，CC BY 4.0）
3g  Earle 术语表 668 条 -> glossary 按首字母分组条目（与既有 293 条去重）+ 追加同义词映射
附带：USGS 1693 的 DESCRIPTIVE MODEL 名称清单抽取（供 3e 使用）
"""
import collections
import os
import re

SRC_TXT = r"D:/PostNews/Translate/books_text/Earle_Physical-Geology-2e_BCcampus_CCBY.txt"
SRC_GLO = r"D:/PostNews/Translate/books_text/Earle_Physical-Geology-2e_BCcampus_CCBY.glossary.txt"
GLO_DIR = r"D:/ZSK/geokb/content/glossary"
SYN_F = r"D:/ZSK/geokb/content/glossary/_synonyms.yaml"
USGS_TXT = r"D:/PostNews/Translate/books_text/USGS_Bulletin1693_Mineral-Deposit-Models_full.txt"
TODAY = "2026-10-13"
EARLE_SRC = "Earle, S. (2015). Physical Geology, 2nd ed. BCcampus. CC BY 4.0"

# 章号 -> (模块, 模块前缀, 中文题注)
CH_MAP = {
    1: ("field-methods", "fld", "地质学概论"),
    2: ("mineralogy", "min", "矿物学"),
    3: ("petrology", "pet", "侵入岩"),
    4: ("petrology", "pet", "火山作用"),
    5: ("field-methods", "fld", "风化与土壤"),
    6: ("petrology", "pet", "沉积物与沉积岩"),
    7: ("petrology", "pet", "变质作用与变质岩"),
    8: ("geochronology", "gch", "地质时间测定"),
    9: ("geophysics", "gph", "地球内部"),
    10: ("structural", "str", "板块构造"),
    11: ("geophysics", "gph", "地震"),
    12: ("structural", "str", "地质构造"),
    13: ("field-methods", "fld", "河流与洪水"),
    14: ("field-methods", "fld", "地下水"),
    15: ("field-methods", "fld", "块体运动"),
    16: ("field-methods", "fld", "冰川"),
    17: ("field-methods", "fld", "海岸"),
    18: ("field-methods", "fld", "海洋地质"),
    19: ("field-methods", "fld", "气候变化"),
    20: ("economic-geology", "eco", "地质资源"),
    21: ("field-methods", "fld", "加拿大西部地质史（区域实例）"),
    22: ("geochronology", "gch", "地球与太阳系起源"),
}

text = open(SRC_TXT, encoding="utf-8").read()
lines = text.split("\n")

# 找正文章节起点：行匹配 "Chapter N Title -- Physical Geology"
starts = []
for i, ln in enumerate(lines):
    m = re.match(r"^Chapter (\d+) (.+?) -- Physical Geology$", ln)
    if m and int(m.group(1)) in CH_MAP and m.group(2).strip() != "Summary":
        starts.append((i, int(m.group(1)), m.group(2).strip()))
# 去重：同一章可能出现两次（目录/正文），正文版是最后一次出现
by_ch = {}
for i, n, t in starts:
    by_ch[n] = (i, t)  # 后出现覆盖先出现
starts = sorted((v[0], k, v[1]) for k, v in by_ch.items())

# 正文尾部边界：附录区（Glossary/About the Author/Appendix/Versioning），取最后章节起点之后的首个附录标记
end_markers = [i for i, ln in enumerate(lines)
               if i > starts[-1][0] and re.match(r"^(Glossary|About the Author|Appendix|Versioning History|About the Book) -- Physical Geology", ln)]
hard_end = end_markers[0] if end_markers else len(lines)

def chapter_summary(body_lines, n):
    """取 Chapter N Summary 段落前 200 字作 summary。"""
    txt = "\n".join(body_lines)
    m = re.search(rf"Chapter {n} Summary\s*\n(.*?)(?:\nExercises|\nMedia Attributions|\Z)", txt, re.S)
    if m:
        s = re.sub(r"\s+", " ", m.group(1)).strip()
        return s[:180] + ("…" if len(s) > 180 else "")
    return ""

written = []
for idx, (start, n, title) in enumerate(starts):
    end = starts[idx + 1][0] if idx + 1 < len(starts) else hard_end
    body_lines = lines[start + 2:end]  # 跳过 marker 行与重复标题行
    body = "\n".join(body_lines).strip()
    module, prefix, zh = CH_MAP[n]
    summ = chapter_summary(body_lines, n) or (f"《Physical Geology》第 {n} 章「{title}」全文（英文原文，CC BY 4.0）。")
    slug = f"earle-ch{n:02d}"
    tags = [zh, "英文原文", "教材"]
    fm = f"""---
id: {prefix}-{slug}-001
title: {zh}：{title}（Earle·第{n}章）
module: {module}
tags: [{", ".join(tags)}]
summary: {summ} 正文为英文原文（CC BY 4.0），术语对照见 glossary 模块。
confidence: A
sources:
  - name: {EARLE_SRC}
    url: https://opentextbc.ca/geology/
    accessed: {TODAY}
updated: {TODAY}
status: active
---
"""
    outdir = os.path.join(r"D:/ZSK/geokb/content", module)
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, f"{slug}.md"), "w", encoding="utf-8") as f:
        f.write(fm + "\n# " + title + "（" + zh + "）\n\n" + body + "\n")
    written.append((n, module, title, len(body)))

# ---------- 3g：Earle 术语表 ----------
# 既有同义词键（英文小写）
existing = set()
syn_lines = open(SYN_F, encoding="utf-8").read().split("\n")
for ln in syn_lines:
    m = re.match(r'^"(.+?)":', ln)
    if m:
        existing.add(m.group(1).lower())

glo_lines = open(SRC_GLO, encoding="utf-8").read().split("\n")
pat = re.compile(r"^(.+?)\s\((\d+)\)\s(.+)$")
terms = []
for ln in glo_lines:
    ln = ln.strip()
    if not ln or re.fullmatch(r"[A-Z]", ln):  # 字母分组头
        continue
    m = pat.match(ln)
    if m:
        terms.append({"term": m.group(1).strip(), "ch": m.group(2), "def": m.group(3).strip()})

by_letter = collections.defaultdict(list)
new_syn = []
for t in terms:
    L = t["term"][0].upper()
    if not L.isalpha():
        L = "0-9"
    by_letter[L].append(t)
    if t["term"].lower() not in existing:
        new_syn.append(t["term"])

count_glo = 0
for L in sorted(by_letter, key=lambda x: (x == "0-9", x)):
    items = by_letter[L]
    lslug = L.lower() if L.isalpha() else "misc"
    fm = f"""---
id: glo-earle-{lslug}-001
title: Earle 教材术语表·{L}（{len(items)} 条）
module: glossary
tags: [术语, 中英对照, Earle教材, 英文释义]
summary: Physical Geology（CC BY）教材术语表 {L} 部，共 {len(items)} 条英文定义（括号为首次出现的章节），与已验证术语表互补。
confidence: A
sources:
  - name: {EARLE_SRC}
    url: https://opentextbc.ca/geology/
    accessed: {TODAY}
updated: {TODAY}
status: active
---
"""
    rows = ["| Term | Ch. | Definition |", "|---|---|---|"]
    for t in items:
        d = t["def"].replace("|", "\\|")
        rows.append(f"| {t['term']} | {t['ch']} | {d} |")
    with open(os.path.join(GLO_DIR, f"terms-earle-{lslug}.md"), "w", encoding="utf-8") as f:
        f.write(fm + "\n".join(rows) + "\n")
    count_glo += 1

# 追加新同义词（仅有英文，无中文对照 → 暂不加映射，避免噪音）。
# 说明：中文对照的建立留给 3h 校验后按需人工/半自动补全。

# ---------- 附带：USGS 1693 模型名清单 ----------
models = re.findall(r"DESCRIPTIVE MODEL OF (.+?)\s*\n", open(USGS_TXT, encoding="utf-8").read())
print("chapters written:", len(written))
for n, module, title, size in written:
    print(f"  ch{n:02d} -> {module:<18} {size:>7} chars  {title}")
print("glossary letters:", count_glo, "terms:", len(terms), "new(未与293重叠):", len(new_syn))
print("usgs models found:", len(models))
if models:
    with open(r"D:/ZSK/geokb/raw_sources/usgs1693_model_names.json", "w", encoding="utf-8") as f:
        import json
        json.dump(models, f, ensure_ascii=False, indent=1)
