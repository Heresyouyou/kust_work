# -*- coding: utf-8 -*-
"""分层清洗实验：量化每层预处理的投入产出（离线，不调 API）。

关键认识：token 与字符总数成正比，但与"干净度"非线性——清洗掉碎片/重复行后，
剩余文本的 token 密度会从 3.25 chars/token 提高到接近 4，收益叠加。
因此必须逐层测"清洗后字符"并随后实测真实 token。
"""
import glob
import os
import re
from collections import Counter

TEXT_DIR = r"D:/ZSK/geokb/state/text"

# ── 章节/区块识别 ────────────────────────────────────────────
RE_ABS = re.compile(r"(?im)^\s*a\s*b\s*s\s*t\s*r\s*a\s*c\s*t\s*[:.]?\s*$|^\s*abstract\s*[:.]", re.I)
RE_INTRO = re.compile(r"(?im)^\s*(?:\d+\.?\s*)?introduction\s*$", re.I)
RE_CONCL = re.compile(
    r"(?im)^\s*(?:\d+\.?\s*)?(conclusions?|summary and conclusions?|"
    r"concluding remarks|discussion and conclusions?)\s*$", re.I)
RE_REFS = re.compile(r"(?im)^\s*(references|reference list|bibliography|"
                     r"acknowledg\w*|data availability|conflicts of interest|"
                     r"author contributions|funding|supplementary materials)\s*[:.]?\s*$", re.I)

# 期刊页眉页脚 / Running header 特征
RE_BOILER = re.compile(
    r"(?i)(doi\.org/|https?://creativecommons|©\s*\d{4}|open access|"
    r"licensee|basel, switzerland|mdpi|elsevier|springer|frontiers\s*in|"
    r"received:|accepted:|published:|academic editor:|citation:|copyright:|"
    r"creative commons|terms and conditions|all rights reserved)")


def clean_layers(t):
    """返回各层清洗后的文本长度字典。"""
    out = {"L0_raw": len(t)}
    # L1：剔除参考文献致谢等尾部
    cut = len(t)
    for m in RE_REFS.finditer(t):
        if m.start() > len(t) * 0.45:      # 只认后半部分出现的
            cut = min(cut, m.start())
    t1 = t[:cut]
    out["L1_cut_tail"] = len(t1)

    # L2：剔除 boilerplate 行（页眉页脚/版权/收稿信息）
    lines = t1.split("\n")
    keep, drop = [], 0
    for ln in lines:
        s = ln.strip()
        if len(s) < 3:
            continue
        if RE_BOILER.search(s):
            drop += len(ln)
            continue
        keep.append(s)
    t2 = "\n".join(keep)
    out["L2_drop_boiler"] = len(t2)

    # L3：合并重复行（同一行出现 >=3 次视为噪声）
    cnt = Counter(keep)
    keep3 = [ln for ln in keep if cnt[ln] < 3 or len(ln) > 60]
    t3 = "\n".join(keep3)
    out["L3_drop_dup"] = len(t3)

    # L4：修复断行连字符 + 合并短行（提高 token 密度）
    t4 = re.sub(r"-\n\s*", "", t3)
    t4 = re.sub(r"(?<![.!?])\n(?![A-Z0-9(\[])", " ", t4)
    t4 = re.sub(r"[ \t]{2,}", " ", t4)
    t4 = re.sub(r"\n{2,}", "\n", t4)
    out["L4_join_lines"] = len(t4)
    return out, t4


def head_sections(t):
    """能否定位 abstract / intro / conclusions？返回 (命中集合, 头部文本长度)。"""
    hits = set()
    ma = None
    for m in RE_ABS.finditer(t):
        ma = m
        break
    if ma:
        hits.add("abstract")
    mi = None
    for m in RE_INTRO.finditer(t):
        mi = m
        break
    if mi:
        hits.add("intro")
    mc = None
    ms = list(RE_CONCL.finditer(t))
    if ms:
        mc = ms[-1]
        hits.add("concl")
    # 头部 = abstract .. intro 前 + conclusions 之后到 refs
    head = ""
    if ma:
        end = mi.start() if mi else min(len(t), ma.start() + 6000)
        head += t[ma.start():end]
    if mc:
        head += "\n" + t[mc.start(): min(len(t), mc.start() + 6000)]
    return hits, len(head)


def main():
    files = glob.glob(os.path.join(TEXT_DIR, "*.txt"))
    agg = {}
    sec = Counter()
    head_tot = 0
    for f in files:
        t = open(f, encoding="utf-8", errors="ignore").read()
        o, _ = clean_layers(t)
        for k, v in o.items():
            agg[k] = agg.get(k, 0) + v
        hits, hl = head_sections(t)
        for h in hits:
            sec[h] += 1
        head_tot += hl
    n = len(files)
    base = agg["L0_raw"]
    print(f"{n} 篇语料逐层清洗结果（字符）")
    print("─" * 66)
    print(f"{'层':<22}{'字符':>14}{'占比':>9}{'累计省':>9}")
    print("─" * 66)
    prev = base
    for k in ("L0_raw", "L1_cut_tail", "L2_drop_boiler", "L3_drop_dup", "L4_join_lines"):
        v = agg[k]
        print(f"{k:<22}{v:>14,}{100*v/base:>8.1f}%{100*(base-v)/base:>8.1f}%")
    print("─" * 66)
    print("\n章节定位覆盖率（决定 Cascade 是否可行）")
    print("─" * 66)
    for k in ("abstract", "intro", "concl"):
        print(f"  {k:<10} 定位成功 {sec[k]:>4}/{n}  ({100*sec[k]/n:.1f}%)")
    all3 = sum(1 for f in files if len(head_sections(open(f, encoding='utf-8',
               errors='ignore').read())[0]) == 3)
    print(f"  三者全命中   {all3:>4}/{n}  ({100*all3/n:.1f}%)")
    print(f"  头部均长     {head_tot/n:,.0f} 字符/篇 "
          f"（占全文 {100*head_tot/base:.1f}%）")


if __name__ == "__main__":
    main()
