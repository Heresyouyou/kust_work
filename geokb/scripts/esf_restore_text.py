#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""B 方案落地：把 573 篇乱码 PDF 的正文还原成可读文本。

依赖 `state/esf_glyph_dict.npz`（字形位图指纹字典，由 esf_glyph_dict.py --build 生成）。

流程
----
1. 逐页取 (字符, 字体编号)；同一文档内同一 (字体编号, 编码) 只算一次字形指纹
2. 指纹精确命中 → 直接取字；未命中 → 位图 IoU 近似匹配（≥0.90 且与次优拉开差距才算）
3. 输出 `state/esf_text/<eid>.txt`，首行写覆盖率统计
4. 原 PDF 一律不改

用法：
  python scripts/esf_restore_text.py --limit 5        # 试 5 篇
  python scripts/esf_restore_text.py --all            # 全量
  python scripts/esf_restore_text.py --verify         # 用标题做准确率评估
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import esf_glyph_dict as G   # noqa: E402

BASE = Path(__file__).resolve().parent.parent
AUDIT = BASE / "state" / "esf_font_audit.json"
OUTDIR = BASE / "state" / "esf_text"

_cache: dict = {}


def load_dict():
    if "idx" in _cache:
        return _cache["idx"], _cache["bm"], _cache["map"]
    idx, bm, table = G.load_dict()
    z = np.load(G.DICT, allow_pickle=False)
    flat = bm.reshape(len(bm), -1).astype(np.int16)
    # ── 粗筛特征 ──
    # 近似匹配若对 13135 个字形全算 IoU，是 11.5 ms/字；一篇 18 页约 2 万字、
    # 一两千个不同 (字体,编码)，直接 200 秒起步（实测超时）。
    # 先用「填充率 + 宽高比 + 墨点数」筛出 ~150 个候选，再算 IoU，快 10 倍。
    ink = flat > 0
    n_ink = ink.sum(1).astype(np.float32)
    # ⚠️ 行列投影必须用 3D 的 bm，不能对 reshape 后的 flat 做 any(2)（会越界）。
    ink3 = bm > 0
    rows = ink3.any(2)                       # (n, N) 每一行有没有墨
    cols = ink3.any(1)                       # (n, N) 每一列有没有墨
    h = np.maximum(1, rows.sum(1)).astype(np.float32)
    w = np.maximum(1, cols.sum(1)).astype(np.float32)
    _cache.update({
        "idx": idx, "bm": bm, "map": table,
        "hashes": [str(x) for x in z["hashes"]],
        "flat": flat,
        "sums": n_ink,
        "w": w, "h": h,
        "ratio": w / h,
        "fill": n_ink / np.maximum(1.0, w * h),
    })
    return idx, bm, table


def _feat(bm: np.ndarray):
    ink = bm > 0
    n = float(ink.sum())
    rows = ink.any(1)
    cols = ink.any(0)
    h = float(max(1, rows.sum()))
    w = float(max(1, cols.sum()))
    return n, w, h, w / h, n / max(1.0, w * h)


def lookup(bitmap: np.ndarray) -> tuple[str, float, float]:
    """返回 (字符, 置信度, 次优差距)。精确命中给 1.0。"""
    idx, bm_, table = load_dict()
    h = G.bh(bitmap)
    if h in idx:
        return table[h], 1.0, 1.0
    n, w, hh, ratio, fill = _feat(bitmap)
    # 粗筛：宽高比 + 填充率 + 墨点数（三者都对不上就不可能同形）
    d = (np.abs(_cache["ratio"] - ratio) * 2.0
         + np.abs(_cache["fill"] - fill) * 3.0
         + np.abs(_cache["sums"] - n) / 200.0)
    k = min(150, len(d))
    cand = np.argpartition(d, k - 1)[:k]
    flat = bitmap.reshape(-1).astype(np.int16)
    sub = _cache["flat"][cand]
    inter = sub @ flat
    union = _cache["sums"][cand] + n - inter
    iou = inter / np.maximum(1, union)
    order = np.argsort(-iou)[:2]
    best = int(cand[order[0]])
    conf = float(iou[order[0]])
    margin = float(iou[order[0]] - (iou[order[1]] if len(order) > 1 else 0.0))
    return table.get(_cache["hashes"][best], ""), conf, margin


def prepare_lookup():
    idx, bm, table = load_dict()
    z = np.load(G.DICT, allow_pickle=False)
    _cache["hashes"] = [str(x) for x in z["hashes"]]


def restore_pdf(pdf: Path) -> tuple[str, dict]:
    """还原一篇 PDF。返回 (文本, 统计)。

    性能要点（都是实测教训）
    ------------------------
    - **按页解析字体，且只解析该页实际用到的**：每页字体会重新子集化（名字不同），
      全解析 18 个/页 × 15 页 = 270 次，要 30–80 秒；只解析 span 里出现的 5–10 个，
      降到 6 秒级。
    - **字形结果按 (字体编号, 编码) 缓存**：编码跨页固定，同一字形不必反复栅格化。
    """
    doc = pymupdf.open(pdf)
    stats = {"chars": 0, "exact": 0, "approx": 0, "miss": 0, "fonts": 0}
    try:
        font_cache: dict[tuple[str, str], str] = {}
        glyphsets: dict[tuple[int, str], dict] = {}   # (页号, FZ编号) → {glyph: CharString}
        lines_out = []

        # ⚠️ span 里的 `font` 是**简称**（`FzBookMaker0DlFont005368`，无子集前缀），
        # `get_fonts()` 给的是**全名**（`WGYAZT+FzBookMaker0DlFont00536873798`）。
        # 用全名当键永远查不到 —— 必须统一收敛到 `FZ<编号>`。
        def fkey_of(name: str) -> str:
            m = re.search(r"FzBookMaker(\d+)", name or "")
            return "FZ%s" % m.group(1) if m else ""

        for pno in range(doc.page_count):
            page = doc[pno]
            blocks = page.get_text("dict").get("blocks", [])
            need = set()
            for b in blocks:
                if b.get("type") != 0:
                    continue
                for line in b.get("lines", []):
                    for sp in line.get("spans", []):
                        k = fkey_of(str(sp.get("font", "")))
                        if k and (pno, k) not in glyphsets:
                            need.add(k)
            if need:
                xref_of = {}
                for f in page.get_fonts(full=True):
                    k = fkey_of(str(f[3]))
                    if k in need:
                        xref_of[k] = f[0]
                for k in need:
                    xr = xref_of.get(k)
                    if xr is None:
                        continue
                    buf = G.font_bytes(doc, xr)
                    if len(buf) < 500:
                        continue
                    try:
                        from fontTools.cffLib import CFFFontSet
                        cff = CFFFontSet()
                        cff.decompile(io.BytesIO(buf), None)
                        td = cff[cff.fontNames[0]]
                        # ⚠️ 不要在这里把 CharStrings 全部展开成 dict：
                        # `{n: td.CharStrings[n] for n in keys()}` 会强制解码每一个字形，
                        # 单个 30–70 KB 的 CFF 要 0.5–2 秒；15 页 × 8 字体 = 一两百次，
                        # 一篇就上百秒（实测 3 篇直接超时）。只存对象 + 名字集合，
                        # 真正 draw 时才按需解码那一个。
                        cs = td.CharStrings
                        glyphsets[(pno, k)] = (cs, set(cs.keys()))
                        stats["fonts"] += 1
                    except Exception:
                        continue

            for b in blocks:
                if b.get("type") != 0:
                    continue
                for line in b.get("lines", []):
                    buf = []
                    for sp in line.get("spans", []):
                        nm = str(sp.get("font", ""))
                        fk = fkey_of(nm)
                        entry = glyphsets.get((pno, fk)) if fk else None
                        gs, gnames = entry if entry else (None, set())
                        for ch in (sp.get("text") or ""):
                            if ch.isspace():
                                buf.append(ch)
                                continue
                            stats["chars"] += 1
                            # ⚠️ 缓存键必须是 (页号, 字体编号, 编码)，**不能跨页**。
                            # 实测第 1 页 FzBookMaker0 的 0x26 是「地」、第 2/3 页也是；
                            # 但 FzBookMaker8 在第 1 页装的是数字（0x21→"2"），
                            # 在别的页装的是汉字。按 (fk, ch) 共享缓存会把数字串到正文里。
                            key = (pno, fk, ch)
                            got = font_cache.get(key)
                            if got is None:
                                if not gs:
                                    # 本页没有该字体对象 → 不缓存，留给别的页去解
                                    stats["miss"] += 1
                                    buf.append(ch)
                                    continue
                                gname = "G%02X" % ord(ch)
                                if gname not in gnames:
                                    gname = "G%02x" % ord(ch)
                                if gname not in gnames:
                                    stats["miss"] += 1
                                    buf.append(ch)
                                    continue
                                bm = G.glyph_bitmap(gs, gname)
                                if bm is None:
                                    stats["miss"] += 1
                                    buf.append(ch)
                                    continue
                                ch2, conf, margin = lookup(bm)
                                # ⚠️ 只有高置信才写缓存。低置信/未命中若也缓存成原乱码，
                                # 后面有该字形的页就再也不会重试了（实测会把整篇拖成乱码）。
                                # 阈值依据实测分布：正确匹配 IoU 集中在 0.96–1.00，
                                # 误配在 0.50 上下，中间地带极空。
                                # 早前卡 0.995 只有 hash 完全一致才过，一篇 4 万字只解出 354 个；
                                # 放到 0.92 仍偏严。取 0.94 起步，配 0.03 的次优差距防「形近字」。
                                if ch2 and conf >= 0.995:
                                    stats["exact"] += 1
                                    got = ch2
                                    font_cache[key] = got
                                elif ch2 and conf >= 0.94 and margin >= 0.03:
                                    stats["approx"] += 1
                                    got = ch2
                                    font_cache[key] = got
                                else:
                                    stats["miss"] += 1
                                    buf.append(ch)
                                    continue
                            buf.append(got)
                    t = "".join(buf).rstrip()
                    if t:
                        lines_out.append(t)
        txt = "\n".join(lines_out)
    finally:
        doc.close()
    return txt, stats


def verify(n: int = 30) -> None:
    """用台账标题做 ground truth：还原标题块，算字符准确率。"""
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    bad = [x for x in audit["items"] if x["kind"] == "A_bm_only"]
    prepare_lookup()
    tot = hit = 0
    docs = 0
    per = []
    for x in bad[:n]:
        pdf = Path(x["path"])
        want = re.sub(r"\s+", "", str(x.get("title") or ""))
        if len(want) < 8:
            continue
        try:
            doc = pymupdf.open(pdf)
        except Exception:
            continue
        try:
            page = doc[0]
            lines = []
            for b in page.get_text("dict").get("blocks", []):
                if b.get("type") != 0:
                    continue
                for line in b.get("lines", []):
                    spans = [s for s in line.get("spans", []) if (s.get("text") or "").strip()]
                    if spans:
                        lines.append((max(s.get("size", 0) for s in spans),
                                      line.get("bbox", (0, 0, 0, 0))[1], spans))
        finally:
            doc.close()
        if not lines:
            continue
        mx = max(l[0] for l in lines)
        cand = sorted([l for l in lines if l[0] >= mx - 1.0], key=lambda x: x[1])
        # 用整页逻辑还原这几行
        chars = []
        for _sz, _y, spans in cand:
            for sp in spans:
                for ch in (sp.get("text") or ""):
                    if not ch.isspace():
                        chars.append((str(sp.get("font")), ch))
        if not chars:
            continue
        txt, _ = restore_pdf(pdf)
        # 在还原文本里找标题块（用第一页靠前的行）
        head = "".join(txt.split("\n")[:12])
        ok = sum(1 for a, b in zip(head, want) if a == b)
        tot += len(want)
        hit += ok
        docs += 1
        per.append((ok / max(1, len(want)), want, head))
        if docs >= n:
            break
    print("\n标题级验证：%d 篇｜字符准确率 %.1f%%"
          % (docs, hit * 100.0 / max(1, tot)))
    for acc, want, head in per[:5]:
        print("  [%.0f%%] 期望 %s" % (acc * 100, want[:30]))
        print("         还原 %s" % head[:30])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--eids", default="", help="逗号分隔，只处理这些")
    a = ap.parse_args()

    if a.verify:
        verify()
        return 0

    prepare_lookup()
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    bad = [x for x in audit["items"] if x["kind"] == "A_bm_only"]
    if a.eids:
        want = set(a.eids.split(","))
        bad = [x for x in bad if x.get("eid") in want]
    if a.limit:
        bad = bad[:a.limit]
    OUTDIR.mkdir(parents=True, exist_ok=True)

    print("待还原 %d 篇" % len(bad))
    t0 = time.time()
    agg = collections.Counter()
    for i, x in enumerate(bad, 1):
        pdf = Path(x["path"])
        eid = x.get("eid") or pdf.stem[:20]
        try:
            txt, st = restore_pdf(pdf)
        except Exception as e:
            print("  [%d] %s 失败 %s" % (i, eid, type(e).__name__))
            continue
        n = max(1, st["chars"])
        cov = (st["exact"] + st["approx"]) * 100.0 / n
        out = OUTDIR / ("%s.txt" % eid)
        out.write_text("### %s\n### 覆盖率 %.1f%%（精确 %d / 近似 %d / 未命中 %d / 共 %d）\n\n%s"
                       % (x.get("title") or "", cov, st["exact"], st["approx"],
                          st["miss"], st["chars"], txt), encoding="utf-8")
        for k, v in st.items():
            agg[k] += v
        if i % 20 == 0 or i <= 3:
            print("  %d/%d｜%s｜覆盖 %.0f%%｜%.0fs"
                  % (i, len(bad), eid, cov, time.time() - t0), flush=True)

    n = max(1, agg["chars"])
    print("\n合计：字符 %d｜精确 %d（%.1f%%）｜近似 %d（%.1f%%）｜未命中 %d｜%.0fs"
          % (n, agg["exact"], agg["exact"] * 100.0 / n,
             agg["approx"], agg["approx"] * 100.0 / n, agg["miss"], time.time() - t0))
    print("产物目录：%s" % OUTDIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
