#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按「字体类型」重新判定地学前缘 940 篇，替代按 CJK 占比的粗判。

判据（比 CJK 占比可靠得多）
--------------------------
- `FzBookMaker*DlFont*` = 方正书版**子集化**字体，Encoding 是纯序号 → **真乱码**
- `FZ*--GBK1-0`（FZSSK/FZKTK/FZFSK/FZHTK/FZWBK…）= 方正**标准 GBK** 字体 → 可正常解
- `SSJ-PK*` = 空挂的 Type0 对象，从不上版面
- 只有前者、没有后者 → 整篇不可读
- 两者都有 → 部分可读（页眉/正文混排）

同时顺手统计：可读篇里能不能提取到完整的 FZSSK 字体，用于字形比对建表。
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
OUT = BASE / "state" / "esf_font_audit.json"


def esf() -> list[dict]:
    out = []
    for ln in LEDGER.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = ln.strip()
        if not s:
            continue
        try:
            r = json.loads(s)
        except Exception:
            continue
        if "地学前缘" in str(r.get("journal") or ""):
            p = str(r.get("repo_path") or "")
            if p and Path(p).is_file():
                out.append(r)
    return out


def classify_fonts(names: set[str]) -> dict:
    bookmaker = [n for n in names if "FzBookMaker" in n]
    gbk = [n for n in names if re.search(r"FZ[A-Z]{3}--GBK", n)]
    ssj = [n for n in names if n.startswith("SSJ-")]
    other = [n for n in names if n not in bookmaker and n not in gbk and n not in ssj]
    return {"bookmaker": bookmaker, "gbk": gbk, "ssj": ssj, "other": other}


def main() -> int:
    rows = esf()
    print("地学前缘有 PDF: %d 篇" % len(rows))
    res = []
    kinds = collections.Counter()
    for i, r in enumerate(rows, 1):
        p = Path(str(r["repo_path"]))
        try:
            doc = pymupdf.open(p)
        except Exception as e:
            res.append({"eid": r.get("eid"), "kind": "broken", "err": str(e)[:50]})
            kinds["broken"] += 1
            continue
        try:
            names = set()
            for pno in range(min(3, doc.page_count)):
                for f in doc[pno].get_fonts(full=True):
                    names.add(str(f[3]))
            # 页面实际用到的字体
            used = set()
            for pno in range(min(2, doc.page_count)):
                for b in doc[pno].get_text("dict").get("blocks", []):
                    if b.get("type") != 0:
                        continue
                    for line in b.get("lines", []):
                        for sp in line.get("spans", []):
                            if (sp.get("text") or "").strip():
                                used.add(str(sp.get("font")))
            c = classify_fonts(names)
            used_bm = [n for n in used if "FzBookMaker" in n]
            used_gbk = [n for n in used if re.search(r"FZ[A-Z]{3}--GBK", n)]
            # 判定
            if used_bm and not used_gbk:
                kind = "A_bm_only"       # 真乱码：版面全是子集字体
            elif used_bm and used_gbk:
                kind = "B_mixed"         # 混排：部分可读
            elif used_gbk:
                kind = "C_gbk_ok"        # 标准 GBK，可读
            else:
                # 既没 BM 也没 GBK，看文本本身
                txt = doc[0].get_text() or ""
                cjk = sum(1 for ch in txt if "\u3400" <= ch <= "\u9fff")
                kind = "D_other_ok" if cjk > 20 else "E_unknown"
            kinds[kind] += 1
            res.append({"eid": r.get("eid"), "year": str(r.get("date") or "")[:4],
                        "kind": kind, "title": str(r.get("title") or "")[:50],
                        "path": str(p), "n_bm": len(c["bookmaker"]),
                        "n_gbk": len(c["gbk"]), "gbk_names": c["gbk"][:3],
                        "used_bm": len(used_bm), "used_gbk": len(used_gbk)})
        finally:
            doc.close()
        if i % 100 == 0:
            print("  %d/%d …" % (i, len(rows)), flush=True)

    print("\n=== 按字体类型的真实分类 ===")
    label = {"A_bm_only": "真乱码（只用子集字体）", "B_mixed": "混排（部分可读）",
             "C_gbk_ok": "标准 GBK 字体，可读", "D_other_ok": "其他字体但可读",
             "E_unknown": "判不出", "broken": "打不开"}
    for k, v in sorted(kinds.items()):
        print("  %-12s %-26s %d" % (k, label.get(k, ""), v))

    # 年份交叉
    print("\n=== 按年份 × 类型 ===")
    y = collections.defaultdict(collections.Counter)
    for r in res:
        y[r.get("year") or "?"][r["kind"]] += 1
    cols = ["A_bm_only", "B_mixed", "C_gbk_ok", "D_other_ok", "E_unknown", "broken"]
    print("  年份  " + "".join("%-12s" % c[:10] for c in cols))
    for yr in sorted(y):
        print("  %-6s" % yr + "".join("%-12d" % y[yr].get(c, 0) for c in cols))

    OUT.write_text(json.dumps({"built_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
                               "summary": dict(kinds), "items": res},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n明细已写 %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
