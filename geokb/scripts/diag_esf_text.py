#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""诊断《地学前缘》PDF 的文本层可用性。

分类口径（互斥，按顺序判定）：
  A broken      pymupdf 打不开 / 0 页            → 文件损坏
  B scan_only   有页面、图片能出来，但提取文本 <40 字 → 纯扫描件，无文本层
  C pua_mapped  能提取文本，但 CJK 字符占可打印字符 <15%，
                且私有区(U+E000-F8FF) >5%          → 字体用了自定义 CMap，映射丢失
  D mojibake    能提取，CJK 正常但出现大量 Ã©/â€ 型乱码 → 编码层错误
  E readable    以上都不是                        → 可读（无论有没有关键词行）
  F empty       页面里既无文本也无图片            → 空壳文件

用法：
  python scripts/diag_esf_text.py --limit 40        # 抽样
  python scripts/diag_esf_text.py --all --report logs/esf_diag.md
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import random
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
LEDGER = BASE / "state" / "papers_index.jsonl"

CJK = lambda s: sum(1 for ch in s if "\u3400" <= ch <= "\u9fff")
PUA = lambda s: sum(1 for ch in s if "\ue000" <= ch <= "\uf8ff")
MOJI = ("Ã©", "Ã¨", "Ã¼", "â€", "Ã¤", "Ã¶", "ï¿½", "Â°")


def load_esf() -> list[dict]:
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
            out.append(r)
    return out


def classify(path: Path) -> dict:
    import pymupdf

    res = {"path": str(path), "size": 0, "pages": 0, "chars": 0,
           "cjk": 0, "pua": 0, "kind": "", "note": "", "head": ""}
    try:
        res["size"] = path.stat().st_size
    except OSError as e:
        res["kind"] = "A_broken"
        res["note"] = "stat 失败 %s" % e
        return res

    if res["size"] == 0:
        res["kind"] = "A_broken"
        res["note"] = "0 字节"
        return res

    try:
        doc = pymupdf.open(str(path))
    except Exception as e:
        res["kind"] = "A_broken"
        res["note"] = "open 失败 %s: %s" % (type(e).__name__, str(e)[:60])
        return res

    try:
        res["pages"] = doc.page_count
        if doc.page_count == 0:
            res["kind"] = "A_broken"
            res["note"] = "0 页"
            return res
        if doc.is_encrypted and not doc.authenticate(""):
            res["kind"] = "A_broken"
            res["note"] = "加密且无法用空密码打开"
            return res

        pages = min(3, doc.page_count)
        txt = "\n".join(doc[i].get_text() or "" for i in range(pages))
        imgs = 0
        for i in range(pages):
            try:
                imgs += len(doc[i].get_images(full=False))
            except Exception:
                pass
        res["chars"] = len(txt.strip())
        res["cjk"] = CJK(txt)
        res["pua"] = PUA(txt)
        res["head"] = txt.strip()[:120].replace("\n", " ")
        res["imgs"] = imgs

        printable = sum(1 for ch in txt if ch.isprintable() and not ch.isspace())
        cjk_ratio = res["cjk"] / printable if printable else 0
        pua_ratio = res["pua"] / printable if printable else 0

        if res["chars"] < 40:
            res["kind"] = "B_scan_only" if imgs else "F_empty"
            res["note"] = "前 3 页文本 %d 字，图片 %d 张" % (res["chars"], imgs)
        elif pua_ratio > 0.05 or cjk_ratio < 0.15:
            res["kind"] = "C_pua_mapped"
            res["note"] = "CJK 占比 %.0f%%，私有区 %.0f%%" % (cjk_ratio * 100, pua_ratio * 100)
        elif any(k in txt for k in MOJI):
            res["kind"] = "D_mojibake"
            res["note"] = "含典型乱码字节"
        else:
            res["kind"] = "E_readable"
            res["note"] = "CJK %.0f%%" % (cjk_ratio * 100)
    finally:
        doc.close()
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="抽样篇数（0=全部）")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--report", default="")
    ap.add_argument("--workers", type=int, default=1)
    a = ap.parse_args()

    rows = load_esf()
    todo = []
    for r in rows:
        p = str(r.get("repo_path") or "")
        if p and os.path.isfile(p):
            todo.append((p, r))
    print("地学前缘台账 %d 条｜PDF 存在 %d 篇" % (len(rows), len(todo)))

    if a.limit and not a.all:
        random.seed(a.seed)
        todo = random.sample(todo, min(a.limit, len(todo)))

    t0 = time.time()
    out = []
    for n, (p, r) in enumerate(todo, 1):
        d = classify(Path(p))
        d["title"] = str(r.get("title") or "")
        d["date"] = r.get("date")
        out.append(d)
        if n % 20 == 0:
            print("  已检查 %d/%d（%.0fs）" % (n, len(todo), time.time() - t0), flush=True)

    c = collections.Counter(d["kind"] for d in out)
    print("\n=== 分类结果（%d 篇，%.0fs）===" % (len(out), time.time() - t0))
    for k, v in sorted(c.items()):
        print("  %-14s %d" % (k, v))

    print("\n=== 各类样例 ===")
    for k in sorted(c):
        print("\n[%s]" % k)
        for d in [x for x in out if x["kind"] == k][:4]:
            print("  %s | %d 页 %d KB | %s" % (os.path.basename(d["path"])[:46],
                                               d["pages"], d["size"] // 1024, d["note"]))
            print("     head: %r" % d["head"][:90])
            print("     title: %s" % d["title"][:60])

    if a.report:
        rep = BASE / a.report if not os.path.isabs(a.report) else Path(a.report)
        rep.parent.mkdir(parents=True, exist_ok=True)
        L = ["# 地学前缘 PDF 文本层诊断", "",
             "台账 %d 条｜抽查 %d 篇｜用时 %.0fs" % (len(rows), len(out), time.time() - t0), "",
             "| 类别 | 篇数 |", "|---|---|"]
        for k, v in sorted(c.items()):
            L.append("| %s | %d |" % (k, v))
        L += ["", "## 明细", "",
              "| # | 类别 | 页 | KB | 说明 | 文件 | 标题 |", "|---|---|---|---|---|---|---|"]
        for n, d in enumerate(out, 1):
            L.append("| %d | %s | %d | %d | %s | %s | %s |" % (
                n, d["kind"], d["pages"], d["size"] // 1024, d["note"],
                os.path.basename(d["path"])[:52].replace("|", "/"),
                d["title"][:50].replace("|", "/")))
        rep.write_text("\n".join(L) + "\n", encoding="utf-8")
        print("\n报告：%s" % rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
