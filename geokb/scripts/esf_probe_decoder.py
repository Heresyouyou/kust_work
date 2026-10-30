#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""查「有没有现成解码器 / 能不能从源头解」的四条硬线索。

1. /ActualText 与 /Alt —— PDF 规范允许给文本片段标注「实际文本」（无障碍/复制粘贴用）。
   方正 PDFGenerator 若写了，就能直接还原，根本不需要映射表。
2. SSJ-PK 那 3 个带 ToUnicode 的 Type0 字体到底渲染了什么 —— 若含中文，可直接反推。
3. 字体子集的原始字形 ID 是否还活着（CFF charset / CIDToGIDMap）。
4. 编码空间的上界（LastChar）与实际用到的码位，判断是「按序重编」还是「保留原 GID」。
"""
from __future__ import annotations

import collections
import io
import json
import re
import sys
from pathlib import Path

import pymupdf

BASE = Path(__file__).resolve().parent.parent
LEDGER = BASE / "state" / "papers_index.jsonl"


def pick() -> list[dict]:
    out, seen = [], set()
    for ln in LEDGER.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = ln.strip()
        if not s:
            continue
        try:
            r = json.loads(s)
        except Exception:
            continue
        if "地学前缘" not in str(r.get("journal") or ""):
            continue
        p = str(r.get("repo_path") or "")
        if not p or not Path(p).is_file():
            continue
        y = str(r.get("date") or "")[:4]
        if y in ("2009", "2015", "2019", "2022") and y not in seen:
            seen.add(y)
            out.append(r)
    return out


def probe(p: Path) -> None:
    print("=" * 76)
    print("文件:", p.name[:66])
    doc = pymupdf.open(p)

    # ── 1. ActualText / Alt ──
    print("\n[1] /ActualText 与 /Alt 标注")
    hit = 0
    for pno in range(min(4, doc.page_count)):
        raw = doc.xref_stream(doc[pno].xref) or b""
        txt = raw.decode("latin-1", "ignore")
        for key in ("ActualText", "Alt", "E "):
            n = len(re.findall(r"/%s\b" % key, txt))
            if n:
                print("    第%d页 /%s 出现 %d 次" % (pno + 1, key.strip(), n))
                hit += n
    # 也扫对象的 /StructTreeRoot 与 Annots
    try:
        cat = doc.pdf_catalog()
        st = doc.xref_get_key(cat, "StructTreeRoot")
        print("    StructTreeRoot: %s" % (st,))
        print("    MarkInfo      : %s" % (doc.xref_get_key(cat, "MarkInfo"),))
    except Exception as e:
        print("    结构树读取失败 %s" % e)
    if not hit:
        print("    → 没有任何 ActualText/Alt 标注（这条捷径不存在）")

    # ── 2. SSJ-PK 字体渲染了什么 ──
    print("\n[2] 带 ToUnicode 的 Type0 字体（SSJ-PK*）实际渲染内容")
    used = collections.defaultdict(list)
    for pno in range(min(5, doc.page_count)):
        for b in doc[pno].get_text("dict").get("blocks", []):
            if b.get("type") != 0:
                continue
            for line in b.get("lines", []):
                for sp in line.get("spans", []):
                    t = (sp.get("text") or "").strip()
                    if t:
                        used[str(sp.get("font"))].append(t[:40])
    for fn, samples in sorted(used.items(), key=lambda kv: -len(kv[1])):
        cjk = sum(1 for s in samples for c in s if "\u3400" <= c <= "\u9fff")
        mark = " ★含中文" if cjk else ""
        print("    %-32s %4d spans｜中文 %3d%s" % (fn[:32], len(samples), cjk, mark))
        if cjk:
            for s in samples[:2]:
                print("        例: %r" % s[:34])
    ssj = [fn for fn in used if "SSJ" in fn]
    if not ssj:
        print("    → SSJ-PK 字体在页面上**一次都没被使用**（空挂对象，无利用价值）")
    else:
        print("    → SSJ 字体被使用，见上")

    # ── 3. 字形原始 ID 是否还活着 ──
    print("\n[3] 子集字体的原始字形 ID")
    for f in doc[0].get_fonts(full=True):
        if "FzBookMaker" not in str(f[3]):
            continue
        info = doc.extract_font(f[0])
        buf = info[3] or b""
        print("    %s（%d 字节，%s）" % (str(f[3])[:44], len(buf), info[1]))
        try:
            from fontTools.cffLib import CFFFontSet
            cff = CFFFontSet()
            cff.decompile(io.BytesIO(buf), None)
            td = cff[cff.fontNames[0]]
            names = list(td.CharStrings.keys())
            print("      glyph 名: %s …（共 %d）" % (names[:8], len(names)))
            # charset 里是否带 uniXXXX / cidXXXX 之类
            uni = [n for n in names if re.match(r"(?i)uni[0-9a-f]{4}$", n)]
            cid = [n for n in names if re.match(r"(?i)cid\d+$", n)]
            print("      含 uniXXXX: %d｜含 cidN: %d｜其余: %d"
                  % (len(uni), len(cid), len(names) - len(uni) - len(cid)))
            # TopDict 的 ROS（CID 字体才有）
            try:
                ros = td.rawDict.get("ROS")
                print("      ROS(字符集): %s" % (ros,))
            except Exception:
                pass
        except Exception as e:
            print("      CFF 解析失败: %s" % type(e).__name__)
        break

    # ── 4. 编码空间上界 ──
    print("\n[4] 编码空间")
    mx_first = mx_last = 0
    nfonts = 0
    for pno in range(min(3, doc.page_count)):
        for f in doc[pno].get_fonts(full=True):
            if "FzBookMaker" not in str(f[3]):
                continue
            nfonts += 1
            v = doc.xref_get_key(f[0], "LastChar")
            try:
                mx_last = max(mx_last, int(v[1]))
            except Exception:
                pass
            v2 = doc.xref_get_key(f[0], "FirstChar")
            try:
                mx_first = max(mx_first, int(v2[1]))
            except Exception:
                pass
    print("    前 3 页 FzBookMaker 字体数: %d｜FirstChar 最大 %d｜LastChar 最大 %d"
          % (nfonts, mx_first, mx_last))
    # 实际用到的码位范围
    codes = collections.Counter()
    for pno in range(min(3, doc.page_count)):
        for b in doc[pno].get_text("rawdict").get("blocks", []):
            if b.get("type") != 0:
                continue
            for line in b.get("lines", []):
                for sp in line.get("spans", []):
                    for ch in sp.get("chars", []):
                        c = ch.get("c", "")
                        if c and not c.isspace():
                            codes[ord(c)] += 1
    if codes:
        print("    实际码位: 0x%04X – 0x%04X（%d 个不同值）"
              % (min(codes), max(codes), len(codes)))
        hi = [(hex(k), v) for k, v in codes.items() if k >= 0x80]
        print("    ≥0x80 的码位: %d 个 %s" % (len(hi), hi[:6]))
    doc.close()


def main() -> int:
    for r in pick()[:3]:
        probe(Path(str(r["repo_path"])))
    print()
    print("=" * 76)
    print("结论要点：")
    print("  · 若 [1] 无 ActualText、[2] SSJ 字体未被使用、[3] glyph 名是 G<hex(编码)>，")
    print("    则 PDF 内部**不存在**任何可反推字符的信息，也就没有「现成解码器」。")
    print("  · 剩下的只有「字形形状」这一条物理线索 —— 需要一份同款字库做指纹比对。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
