#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""建立「方正书版乱码 → Unicode」映射表，并验证覆盖率。

原理
----
地学前缘 2007–2021 的 PDF 用方正书版（FzBookMaker）排版，字体子集化时**没有写
ToUnicode 表**，Encoding 是纯序号 glyph 名（`/G21 /G22 …`）。于是文本层能提取出
字符，但都是 ASCII 33–115 区间的「字形序号」——看起来像乱码。

但**视觉层完全正常**（渲染出来就是正常中文）。所以：

  同一页 → 渲染成图 → OCR 得到 (文字, bbox)
        → 文本层 rawdict 得到 (乱码字符, bbox)
        → 按 bbox 重叠对齐 → 建立 `乱码字符 → 正确字符` 映射

因为编码在各篇之间是固定的（同字→同编码），映射表一次建立、全批通用。

用法：
  python scripts/build_esf_charmap.py --pages 30            # 建表
  python scripts/build_esf_charmap.py --pages 30 --apply    # 建表并验证还原效果
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

import numpy as np
import pymupdf

BASE = Path(__file__).resolve().parent.parent
LEDGER = BASE / "state" / "papers_index.jsonl"
MAPFILE = BASE / "state" / "esf_charmap.json"
DPI = 200


def esf_pdfs(limit_year: str = "") -> list[tuple[str, str]]:
    """返回 [(标题, 路径)]，只要地学前缘。"""
    out = []
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
        if limit_year and y != limit_year:
            continue
        out.append((str(r.get("title") or ""), p))
    return out


def page_chars(page) -> list[dict]:
    """文本层逐字符：字符、字体、bbox（PDF 点）。"""
    out = []
    for b in page.get_text("rawdict").get("blocks", []):
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            for sp in line.get("spans", []):
                font = sp.get("font", "")
                for ch in sp.get("chars", []):
                    c = ch.get("c", "")
                    if not c or c.isspace():
                        continue
                    x0, y0, x1, y1 = ch.get("bbox", (0, 0, 0, 0))
                    out.append({"c": c, "font": font, "bbox": (x0, y0, x1, y1)})
    return out


def ocr_page(page, ocr) -> list[dict]:
    """OCR 一页：返回 [{text, bbox(点)}]。"""
    pix = page.get_pixmap(dpi=DPI)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = img[:, :, :3]
    img = np.ascontiguousarray(img[:, :, ::-1])   # RGB → BGR
    res, _ = ocr(img)
    scale = 72.0 / DPI
    out = []
    for item in (res or []):
        box, txt, score = item[0], item[1], item[2]
        try:
            score = float(score)
        except Exception:
            score = 0.0
        xs = [pt[0] for pt in box]
        ys = [pt[1] for pt in box]
        out.append({"text": txt, "score": score,
                    "bbox": (min(xs) * scale, min(ys) * scale,
                             max(xs) * scale, max(ys) * scale)})
    return out


def overlap(a, b) -> float:
    """两个 bbox 的交叠面积（点²）。"""
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def align_page(chars: list[dict], blocks: list[dict]) -> list[tuple[str, str, str]]:
    """把 OCR 块与文本层字符对齐，返回 [(乱码字符, 正确字符, 字体)]。

    只接受「字符数完全相等」的块 —— 这是最严格的对齐，宁可少采也不采错。
    """
    pairs = []
    for blk in blocks:
        txt = blk["text"]
        if blk["score"] < 0.60:
            continue
        # OCR 文本去掉空白，作为目标序列
        target = [c for c in txt if not c.isspace()]
        if len(target) < 2:
            continue
        # 落在该块 bbox 内的文本层字符
        cand = [c for c in chars if overlap(c["bbox"], blk["bbox"]) > 0]
        if len(cand) != len(target):
            continue
        cand.sort(key=lambda c: (c["bbox"][0]))
        for c, t in zip(cand, target):
            pairs.append((c["c"], t, c["font"]))
    return pairs


def build_map(pages_wanted: int, seed: int) -> dict:
    from rapidocr_onnxruntime import RapidOCR

    pairs = esf_pdfs()
    # 只挑 2007–2021 的（乱码期）
    bad_years = {str(y) for y in range(2007, 2022)}
    picked = []
    for title, p in pairs:
        y = Path(p).name[:4]
        if y in bad_years:
            picked.append((title, p))
    random.seed(seed)
    random.shuffle(picked)
    print("乱码期候选 %d 篇" % len(picked))

    ocr = RapidOCR()
    votes = collections.defaultdict(collections.Counter)   # 乱码char → Counter(正确char)
    font_votes = collections.defaultdict(collections.Counter)
    done, t0 = 0, time.time()
    for title, p in picked:
        if done >= pages_wanted:
            break
        try:
            doc = pymupdf.open(p)
        except Exception:
            continue
        try:
            for pno in range(min(2, doc.page_count)):
                if done >= pages_wanted:
                    break
                page = doc[pno]
                chars = page_chars(page)
                if len(chars) < 20:
                    continue
                blocks = ocr_page(page, ocr)
                got = align_page(chars, blocks)
                for bad, good, font in got:
                    votes[bad][good] += 1
                    font_votes[bad][font] += 1
                done += 1
                if done % 5 == 0:
                    print("  已处理 %d 页（%.0fs，累计映射 %d 个字符）"
                          % (done, time.time() - t0, len(votes)), flush=True)
        finally:
            doc.close()

    # 取众数，并要求绝对多数（≥60%）才算可信
    cmap, detail = {}, {}
    for bad, cnt in votes.items():
        total = sum(cnt.values())
        good, n = cnt.most_common(1)[0]
        if total >= 2 and n / total >= 0.6:
            cmap[bad] = good
            detail[bad] = {"ch": good, "n": n, "total": total,
                           "conf": round(n / total, 2),
                           "font": font_votes[bad].most_common(1)[0][0]}
    print("\n映射表：%d 个不同字符（候选 %d，采纳率 %.0f%%），用时 %.0fs"
          % (len(cmap), len(votes), len(cmap) * 100.0 / max(1, len(votes)), time.time() - t0))

    MAPFILE.parent.mkdir(parents=True, exist_ok=True)
    MAPFILE.write_text(json.dumps({"built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                   "pages": done, "map": cmap, "detail": detail},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写 %s" % MAPFILE)
    return cmap


def apply_to(cmap: dict, n: int = 3) -> None:
    """抽几篇验证：还原后的文本有多少字不认识。"""
    pairs = esf_pdfs()
    random.seed(5)
    random.shuffle(pairs)
    tested = 0
    for title, p in pairs:
        if tested >= n:
            break
        try:
            doc = pymupdf.open(p)
        except Exception:
            continue
        try:
            txt = (doc[0].get_text() or "")
        finally:
            doc.close()
        known = sum(1 for c in txt if c in cmap)
        cjkish = sum(1 for c in txt if not c.isspace())
        out = "".join(cmap.get(c, c) for c in txt)
        print("\n" + "=" * 70)
        print("原题：%s" % title[:60])
        print("命中率：%d/%d = %.0f%%（非空白字符）"
              % (known, cjkish, known * 100.0 / max(1, cjkish)))
        print("还原前：%r" % txt[:90])
        print("还原后：%r" % out[:120])
        tested += 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=20, help="用于建表的页数")
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--apply", action="store_true", help="建表后抽 3 篇验证")
    ap.add_argument("--test-only", action="store_true", help="只用已有映射表验证")
    a = ap.parse_args()

    if a.test_only:
        cmap = json.loads(MAPFILE.read_text(encoding="utf-8"))["map"]
        print("加载已有映射表 %d 个字符" % len(cmap))
    else:
        cmap = build_map(a.pages, a.seed)

    if a.apply or a.test_only:
        apply_to(cmap, 3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
