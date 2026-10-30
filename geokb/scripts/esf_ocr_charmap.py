#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""B 方案：用 OCR 扩容「方正书版乱码 → Unicode」映射表。

设计要点（都是前面踩过的坑）
------------------------------
1. **按行对齐，不按块**。OCR 会把一行拆成几块、或把几行并成一块，按块匹配会大量丢配。
   以文本层的 line 为基准，找 y 方向重叠最大的 OCR 块拼接，再逐字对齐。
2. **必须按字体分组**。`FzBookMakerN` → `FZN`（剥掉子集随机前缀与导出时间戳）。
   不分字体时 `&→地` 一致性从 100% 掉到 78%，因为每个 FZ 编号是独立编码空间。
3. **标题映射是种子**。台账标题与 PDF 标题块严格 1:1，那 82 个映射 100% 准确，
   直接灌进来，OCR 结果只做补充与交叉验证。
4. **只采纳等长段**。字数相等才逐字对应；不等时用 difflib，只收 replace/equal 且等长的块，
   宁可少采也不采错。

用法：
  python scripts/esf_ocr_charmap.py --pages 250            # 建表
  python scripts/esf_ocr_charmap.py --verify               # 用已有表验证还原率
"""
from __future__ import annotations

import argparse
import collections
import difflib
import json
import random
import re
import sys
import time
from pathlib import Path

import numpy as np
import pymupdf

BASE = Path(__file__).resolve().parent.parent
LEDGER = BASE / "state" / "papers_index.jsonl"
SEEDFILE = BASE / "state" / "esf_charmap_title.json"
OUTFILE = BASE / "state" / "esf_charmap_full.json"
DPI = 200

CJK = lambda s: sum(1 for ch in s if "\u3400" <= ch <= "\u9fff")


def font_no(name: str) -> str:
    """`JLNBZQ+FzBookMaker0DlFont00536879635` → `FZ0`。"""
    m = re.search(r"FzBookMaker(\d+)", name or "")
    return "FZ%s" % m.group(1) if m else "OTHER"


def esf_rows() -> list[dict]:
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
        if p and Path(p).is_file():
            out.append(r)
    return out


def is_garbled(text: str) -> bool:
    """文本层是不是乱码：CJK 占比低且长度够。"""
    t = (text or "").strip()
    if len(t) < 30:
        return False
    return CJK(t) < len(t) * 0.10


# ── 文本层：按行取字符与字体 ──────────────────────────────────────────
def page_lines(page) -> list[dict]:
    out = []
    for b in page.get_text("dict").get("blocks", []):
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            chars = []
            for sp in line.get("spans", []):
                fno = font_no(sp.get("font", ""))
                for ch in (sp.get("text") or ""):
                    if ch.isspace():
                        continue
                    chars.append((ch, fno))
            if chars:
                out.append({"bbox": line.get("bbox", (0, 0, 0, 0)), "chars": chars})
    return out


# ── OCR ──────────────────────────────────────────────────────────────
def ocr_page(page, ocr) -> list[dict]:
    pix = page.get_pixmap(dpi=DPI)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = img[:, :, :3]
    img = np.ascontiguousarray(img[:, :, ::-1])
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
        out.append({"text": txt,
                    "score": score,
                    "bbox": (min(xs) * scale, min(ys) * scale,
                             max(xs) * scale, max(ys) * scale)})
    return out


def y_overlap(a, b) -> float:
    y0 = max(a[1], b[1])
    y1 = min(a[3], b[3])
    return max(0.0, y1 - y0)


def x_overlap(a, b) -> float:
    x0 = max(a[0], b[0])
    x1 = min(a[2], b[2])
    return max(0.0, x1 - x0)


def align_line(chars: list[tuple[str, str]], text: str) -> list[tuple[str, str, str]]:
    """单行对齐：文本层 [(char, font)] vs OCR 文本。返回 [(char, font, 正确字)]。"""
    target = [c for c in text if not c.isspace()]
    if len(target) < 2:
        return []
    src = [c for c, _ in chars]
    if len(src) == len(target):
        return [(chars[i][0], chars[i][1], target[i]) for i in range(len(src))]
    # 长度不等：difflib 只收等长段
    sm = difflib.SequenceMatcher(None, "".join(src), "".join(target))
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "equal") and (i2 - i1) == (j2 - j1):
            for k in range(i2 - i1):
                out.append((chars[i1 + k][0], chars[i1 + k][1], target[j1 + k]))
    return out


def votes_from_page(page, ocr, votes, stats) -> None:
    """一页 → 累积投票。"""
    lines = page_lines(page)
    if len(lines) < 3:
        return
    blocks = [b for b in ocr_page(page, ocr) if b["score"] >= 0.55 and b["text"].strip()]
    if not blocks:
        return
    used = set()
    for ln in lines:
        bb = ln["bbox"]
        # 找 y 重叠占本行高度 50% 以上、且 x 有交叠的 OCR 块
        h = max(1e-6, bb[3] - bb[1])
        cand = []
        for i, bk in enumerate(blocks):
            if i in used:
                continue
            oy = y_overlap(bb, bk["bbox"])
            if oy >= h * 0.5 and x_overlap(bb, bk["bbox"]) > 0:
                cand.append((bk["bbox"][0], i))
        if not cand:
            continue
        cand.sort()
        text = ""
        for _x, i in cand:
            text += blocks[i]["text"]
            used.add(i)
        stats["lines"] += 1
        got = align_line(ln["chars"], text)
        if got:
            stats["aligned"] += 1
        for bad, fno, good in got:
            if bad == good:
                continue
            if "\u3400" <= bad <= "\u9fff":
                continue
            votes[(fno, bad)][good] += 1


def build(npages: int, seed: int, use_seed: bool) -> dict:
    from rapidocr_onnxruntime import RapidOCR

    rows = esf_rows()
    # 只挑乱码期
    bad = []
    for r in rows:
        y = Path(str(r["repo_path"])).name[:4]
        if y.isdigit() and 2007 <= int(y) <= 2023:
            bad.append(r)
    random.seed(seed)
    random.shuffle(bad)

    # 按年份分层：每年至少取几篇，保证排版风格都覆盖到
    by_year = collections.defaultdict(list)
    for r in bad:
        by_year[Path(str(r["repo_path"])).name[:4]].append(r)
    picked = []
    per_year = max(2, npages // max(1, len(by_year)) // 3 + 1)
    for y in sorted(by_year):
        picked += by_year[y][:per_year]
    print("乱码期 %d 篇｜按年分层取 %d 篇（每年 %d）" % (len(bad), len(picked), per_year))

    votes = collections.defaultdict(collections.Counter)
    stats = {"pages": 0, "lines": 0, "aligned": 0}
    ocr = RapidOCR()
    t0 = time.time()

    for r in picked:
        try:
            doc = pymupdf.open(str(r["repo_path"]))
        except Exception:
            continue
        try:
            for pno in range(min(3, doc.page_count)):
                if stats["pages"] >= npages:
                    break
                page = doc[pno]
                if not is_garbled(page.get_text() or ""):
                    continue
                votes_from_page(page, ocr, votes, stats)
                stats["pages"] += 1
                if stats["pages"] % 10 == 0:
                    print("  %d 页｜行 %d｜对齐 %d｜映射 %d｜%.0fs"
                          % (stats["pages"], stats["lines"], stats["aligned"],
                             len(votes), time.time() - t0), flush=True)
        finally:
            doc.close()
        if stats["pages"] >= npages:
            break

    print("\nOCR 完成：%d 页 / %d 行 / 对齐 %d 行（%.1f%%）/ 用时 %.0fs"
          % (stats["pages"], stats["lines"], stats["aligned"],
             stats["aligned"] * 100.0 / max(1, stats["lines"]), time.time() - t0))

    # 收敛：按字体分组，多票 + 高一致性
    by_font: dict[str, dict[str, str]] = {}
    detail = {}
    conflict = 0
    for (fno, ch), cnt in votes.items():
        total = sum(cnt.values())
        good, n = cnt.most_common(1)[0]
        conf = n / total
        if n < 2 or conf < 0.80:
            conflict += 1
            continue
        by_font.setdefault(fno, {})[ch] = good
        detail["%s|%s" % (fno, ch)] = {"ch": good, "n": n, "total": total,
                                       "conf": round(conf, 2), "src": "ocr"}
    ocr_n = sum(len(v) for v in by_font.values())
    print("OCR 映射：%d 个字符（丢弃 %d 个低置信/冲突）" % (ocr_n, conflict))

    # 灌入标题种子（100% 准确，覆盖优先）
    seed_n = 0
    if use_seed and SEEDFILE.exists():
        sd = json.loads(SEEDFILE.read_text(encoding="utf-8"))
        for fno, m in (sd.get("fonts") or {}).items():
            for ch, good in m.items():
                prev = by_font.get(fno, {}).get(ch)
                if prev and prev != good:
                    print("  ⚠️ 种子与 OCR 冲突 %s|%r：OCR=%r 标题=%r（保留标题）"
                          % (fno, ch, prev, good))
                by_font.setdefault(fno, {})[ch] = good
                detail["%s|%s" % (fno, ch)] = dict(detail.get("%s|%s" % (fno, ch), {}),
                                                   ch=good, src="title")
                seed_n += 1
    total_chars = sum(len(v) for v in by_font.values())
    print("合并标题种子 %d 个 → 合计 %d 个字符 / %d 个字体"
          % (seed_n, total_chars, len(by_font)))

    OUTFILE.write_text(json.dumps({
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pages": stats["pages"], "lines": stats["lines"],
        "aligned": stats["aligned"],
        "fonts": by_font, "detail": detail,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写 %s" % OUTFILE)
    return by_font


BACKFILL = {"(": "!", ")": '"', ";": "#", "、": "+", "—": "%"}


def verify() -> None:
    """用标题做 ground truth 验证还原率。"""
    if not OUTFILE.exists():
        print("没有映射表，先跑建表")
        return
    cm = json.loads(OUTFILE.read_text(encoding="utf-8"))["fonts"]
    print("映射表：%d 字符 / %d 字体" % (sum(len(v) for v in cm.values()), len(cm)))

    rows = esf_rows()
    random.seed(9)
    random.shuffle(rows)
    tot = hit = 0
    docs_ok = 0
    docs = 0
    samples = []
    for r in rows:
        p = Path(str(r["repo_path"]))
        want = re.sub(r"\s+", "", str(r.get("title") or ""))
        if len(want) < 8:
            continue
        try:
            doc = pymupdf.open(p)
        except Exception:
            continue
        try:
            page = doc[0]
            # 最大字号行
            lines = []
            for b in page.get_text("dict").get("blocks", []):
                if b.get("type") != 0:
                    continue
                for line in b.get("lines", []):
                    spans = [s for s in line.get("spans", []) if (s.get("text") or "").strip()]
                    if spans:
                        lines.append((max(s.get("size", 0) for s in spans),
                                      line.get("bbox", (0, 0, 0, 0))[1], spans))
            if not lines:
                continue
            mx = max(l[0] for l in lines)
            cand = sorted([l for l in lines if l[0] >= mx - 1.0], key=lambda x: x[1])
            chars, out = [], ""
            for _sz, _y, spans in cand:
                for s in spans:
                    fno = font_no(s.get("font", ""))
                    for ch in (s.get("text") or ""):
                        if ch.isspace():
                            continue
                        chars.append(ch)
                        out += cm.get(fno, {}).get(ch, ch)
        finally:
            doc.close()
        if not chars or not is_garbled("".join(chars) * 2):
            continue
        # 只比较可映射的部分
        docs += 1
        ok = 0
        for i, (a, b2) in enumerate(zip(out, want)):
            if a == b2:
                ok += 1
        acc = ok / max(1, len(want))
        tot += len(want)
        hit += ok
        if acc >= 0.90:
            docs_ok += 1
        if len(samples) < 6:
            samples.append((acc, want, out))
        if docs >= 400:
            break

    print("\n按标题验证（ground truth = 台账标题）：")
    print("  抽样 %d 篇｜字符级准确率 %.1f%%｜≥90%% 还原的篇数 %d（%.0f%%）"
          % (docs, hit * 100.0 / max(1, tot), docs_ok, docs_ok * 100.0 / max(1, docs)))
    print("\n样例：")
    for acc, want, out in samples:
        print("  [%.0f%%] 期望 %s" % (acc * 100, want[:34]))
        print("         还原 %s" % out[:34])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=250)
    ap.add_argument("--seed", type=int, default=4)
    ap.add_argument("--no-seed", action="store_true")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    if a.verify:
        verify()
        return 0
    build(a.pages, a.seed, not a.no_seed)
    verify()
    return 0


if __name__ == "__main__":
    sys.exit(main())
