#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""排查「能不能从源头上拿到这套字库/编码」的所有可能线索。

检查 6 个方向：
  1. PDF 元数据（Producer / Creator）—— 判断排版软件与版本
  2. 字体 BaseFont 的命名规律 —— 子集前缀、字库编号、导出时间戳
  3. glyph 名与编码的关系 —— 确认子集化是否丢弃了原始字形 ID
  4. 文档内是否存在带 ToUnicode 的 Type0/CID 字体，且渲染了中文
  5. FontDescriptor / CIDSystemInfo 里的字集线索（Adobe-GB1 等）
  6. 同一文档不同页的字体编号是否稳定
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

import pymupdf

BASE = Path(__file__).resolve().parent.parent
LEDGER = BASE / "state" / "papers_index.jsonl"


def pick(years=("2009", "2015", "2019")) -> list[dict]:
    rows, seen = [], set()
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
        y = str(r.get("date") or "")[:4]
        if p and Path(p).is_file() and y in years and y not in seen:
            seen.add(y)
            rows.append(r)
    return rows


def main() -> int:
    rows = pick()
    for r in rows[:2]:
        p = Path(str(r["repo_path"]))
        print("=" * 78)
        print("文件:", p.name[:66])
        doc = pymupdf.open(p)

        # ── 1. 元数据 ──
        print("\n[1] PDF 元数据")
        md = doc.metadata or {}
        for k in ("producer", "creator", "format", "title", "author", "subject"):
            if md.get(k):
                print("    %-10s %s" % (k, str(md[k])[:90]))
        try:
            xmp = doc.get_xml_metadata() or ""
            if xmp:
                for m in re.finditer(r"<(pdf:Producer|pdf:Creator|xmp:CreatorTool)>([^<]{0,90})",
                                     xmp):
                    print("    XMP %-12s %s" % (m.group(1), m.group(2)))
        except Exception:
            pass

        # ── 2. 全部字体命名 ──
        print("\n[2] 字体 BaseFont（全集，去重）")
        names = collections.Counter()
        for pno in range(min(4, doc.page_count)):
            for f in doc[pno].get_fonts(full=True):
                names[(f[0], str(f[3]))] += 1
        for (xref, nm), n in sorted(names.items(), key=lambda kv: kv[1], reverse=True):
            print("    xref=%-5s %s" % (xref, nm[:64]))

        # ── 3. glyph 名 vs 编码 ──
        print("\n[3] glyph 名与编码的关系")
        for f in doc[0].get_fonts(full=True):
            if "FzBookMaker" not in str(f[3]):
                continue
            v = doc.xref_get_key(f[0], "Encoding")
            if v[0] == "xref":
                n = int(v[1].split()[0])
                obj = doc.xref_object(n, compressed=False)
                m = re.search(r"/Differences\s*\[(.{0,140})", obj, re.S)
                if m:
                    seg = re.sub(r"\s+", " ", m.group(1))
                    print("    xref=%s  /Differences: %s …" % (f[0], seg))
                    # 验证：编码 N ↔ glyph G<hex(N)> 是否恒等
                    pairs = re.findall(r"(\d+)\s*/G([0-9A-Fa-f]+)", obj)
                    eq = sum(1 for a, b in pairs if int(a) == int(b, 16))
                    print("      编码 N ↔ glyph G<hex(N)>：%d / %d 恒等"
                          % (eq, len(pairs)))
            # FontDescriptor 里的字集线索
            fd = doc.xref_get_key(f[0], "FontDescriptor")
            if fd[0] == "xref":
                fn = int(fd[1].split()[0])
                fobj = doc.xref_object(fn, compressed=False)
                keys = re.findall(r"/([A-Za-z]+)", fobj)
                print("      FontDescriptor 键: %s" % ",".join(sorted(set(keys))))
            break

        # ── 4. 是否所有字体都被实际使用 ──
        print("\n[4] 每个字体实际渲染了什么（全文档扫前 3 页）")
        use = collections.defaultdict(list)
        for pno in range(min(3, doc.page_count)):
            for b in doc[pno].get_text("dict").get("blocks", []):
                if b.get("type") != 0:
                    continue
                for line in b.get("lines", []):
                    for sp in line.get("spans", []):
                        t = (sp.get("text") or "").strip()
                        if t:
                            use[str(sp.get("font"))].append(t[:30])
        for fn, samples in sorted(use.items(), key=lambda kv: -len(kv[1])):
            cjk = sum(1 for s in samples for c in s if "\u3400" <= c <= "\u9fff")
            print("    %-30s %4d spans｜中文 %d｜例 %r"
                  % (fn[:30], len(samples), cjk, samples[0][:22]))

        # ── 5. CIDSystemInfo ──
        print("\n[5] CIDSystemInfo / 字集")
        for f in doc[0].get_fonts(full=True):
            v = doc.xref_get_key(f[0], "CIDSystemInfo")
            if v and v[0] != "null":
                print("    %s → %s" % (str(f[3])[:34], v))
        doc.close()
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
