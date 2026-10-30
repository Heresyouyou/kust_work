#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用「字形位图指纹」建字典：不依赖 CID、不依赖编码、不 OCR。

为什么换成位图指纹
------------------
实测 `FZSSK` 的 CID 每篇重新编号（10213 个 CID 里 8049 个跨文档冲突），
所以 CID 不能当键。但**字形本身是同一款方正书宋**（已验证：`学` fill 0.183 vs 0.182，
`质` 0.233 vs 0.232）——同一字形栅格化后位图几乎相同，可以当指纹。

流程
----
1. 展平贝塞尔 → 多边形；多轮廓用奇偶规则填充（正确处理「口/日/田」的孔洞）
2. 缩放到 40×40 二值图，算哈希 + 保留位图
3. 可读篇：FZSSK 字形 + ToUnicode → 建立「指纹 → Unicode」
4. 乱码篇：FzBookMaker 字形 → 算指纹 → 查表

用法：
  python scripts/esf_glyph_dict.py --build            # 建字典
  python scripts/esf_glyph_dict.py --eval             # 用标题映射评估准确率
"""
from __future__ import annotations

import collections
import hashlib
import io
import json
import math
import re
import sys
import time
from pathlib import Path

import numpy as np
import pymupdf

BASE = Path(__file__).resolve().parent.parent
AUDIT = BASE / "state" / "esf_font_audit.json"
SEED = BASE / "state" / "esf_charmap_title.json"
DICT = BASE / "state" / "esf_glyph_dict.npz"
META = BASE / "state" / "esf_glyph_dict.json"
N = 40        # 栅格边长


# ── 路径展平 ──────────────────────────────────────────────────────────
def flatten(recording, steps=6):
    """fontTools RecordingPen 的录制结果 → 多边形列表（点坐标）。"""
    polys, cur = [], []
    x = y = 0.0
    start = (0.0, 0.0)
    for op, args in recording:
        if op == "moveTo":
            if len(cur) >= 3:
                polys.append(cur)
            x, y = args[0]
            start = (x, y)
            cur = [(x, y)]
        elif op == "lineTo":
            x, y = args[0]
            cur.append((x, y))
        elif op == "qCurveTo":
            # TrueType 二次贝塞尔；末点可能是 None（闭合）
            pts = list(args)
            if pts[-1] is None:
                pts = pts[:-1] + [start]
            for i in range(len(pts) - 1):
                x1, y1 = pts[i]
                x2, y2 = pts[i + 1]
                for s in range(1, steps + 1):
                    t = s / steps
                    mt = 1 - t
                    cur.append((mt * mt * x + 2 * mt * t * x1 + t * t * x2,
                                mt * mt * y + 2 * mt * t * y1 + t * t * y2))
                x, y = x2, y2
        elif op == "curveTo":
            pts = list(args)
            if pts[-1] is None:
                pts = pts[:-1] + [start]
            for i in range(0, len(pts) - 1, 3):
                if i + 2 >= len(pts):
                    break
                x1, y1 = pts[i]
                x2, y2 = pts[i + 1]
                x3, y3 = pts[i + 2]
                for s in range(1, steps + 1):
                    t = s / steps
                    mt = 1 - t
                    cur.append((mt**3 * x + 3 * mt * mt * t * x1 + 3 * mt * t * t * x2 + t**3 * x3,
                                mt**3 * y + 3 * mt * mt * t * y1 + 3 * mt * t * t * y2 + t**3 * y3))
                x, y = x3, y3
        elif op == "closePath":
            if len(cur) >= 3:
                polys.append(cur)
            cur = []
            x, y = start
    if len(cur) >= 3:
        polys.append(cur)
    return polys


def rasterize(polys) -> np.ndarray | None:
    """多边形列表 → N×N 二值图。

    ⚠️ 用 PIL 的 C 实现，不要用「逐像素射线法」：40×40 网格 × 每字约 200 条边，
    36 万字要跑几十亿次浮点，实测直接超时。PIL 画多边形 + 逐轮廓 XOR 合并，
    奇偶规则天然处理「口/日/田」的孔洞，速度快两个数量级。
    """
    if not polys:
        return None
    from PIL import Image, ImageChops, ImageDraw

    xs = [p[0] for poly in polys for p in poly]
    ys = [p[1] for poly in polys for p in poly]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return None
    side = max(w, h)
    ox = x0 - (side - w) / 2
    oy = y0 - (side - h) / 2
    k = (N - 1) / side

    acc = Image.new("1", (N, N), 0)
    for poly in polys:
        if len(poly) < 3:
            continue
        pts = [((p[0] - ox) * k, (N - 1 - (p[1] - oy) * k)) for p in poly]
        mask = Image.new("1", (N, N), 0)
        ImageDraw.Draw(mask).polygon(pts, fill=1)
        acc = ImageChops.logical_xor(acc, mask)
    arr = np.array(acc, dtype=np.uint8)
    return arr if arr.sum() >= 8 else None


def glyph_bitmap(glyphset, gname) -> np.ndarray | None:
    from fontTools.pens.recordingPen import RecordingPen
    try:
        rp = RecordingPen()
        glyphset[gname].draw(rp)
        return rasterize(flatten(rp.value))
    except Exception:
        return None


def bh(bm: np.ndarray) -> str:
    return hashlib.md5(np.packbits(bm).tobytes()).hexdigest()[:16]


# ── 取字体 ────────────────────────────────────────────────────────────
def font_bytes(doc, xref) -> bytes:
    try:
        return (doc.extract_font(xref)[3] or b"")
    except Exception:
        pass
    fd = doc.xref_get_key(xref, "FontDescriptor")
    if fd[0] == "xref":
        n = int(fd[1].split()[0])
        obj = doc.xref_object(n, compressed=False)
        for key in ("FontFile2", "FontFile3", "FontFile"):
            m = re.search(r"/%s\s+(\d+)\s+0\s+R" % key, obj)
            if m:
                return doc.xref_stream_raw(int(m.group(1))) or b""
    return b""


def read_tounicode(doc, xref) -> dict[int, str]:
    v = doc.xref_get_key(xref, "ToUnicode")
    if v[0] != "xref":
        return {}
    st = doc.xref_stream(int(v[1].split()[0])) or b""
    txt = st.decode("latin-1", "ignore")
    out = {}
    for a, b in re.findall(r"<([0-9A-Fa-f]{4})>\s*<([0-9A-Fa-f]{4,})>", txt):
        try:
            out[int(a, 16)] = chr(int(b[:4], 16))
        except Exception:
            pass
    return out


def build() -> None:
    from fontTools.ttLib import TTFont

    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    ok = [x for x in audit["items"] if x["kind"] == "C_gbk_ok"]
    print("可读篇 %d，建「字形位图 → Unicode」字典" % len(ok))

    table: dict[str, str] = {}          # 指纹 → 字符
    votes: dict[str, collections.Counter] = {}
    bms: dict[str, np.ndarray] = {}
    t0 = time.time()
    done = 0
    for i, x in enumerate(ok, 1):
        try:
            doc = pymupdf.open(x["path"])
        except Exception:
            continue
        try:
            for f in doc[0].get_fonts(full=True):
                nm = str(f[3])
                if "FZ" not in nm or "GBK" not in nm:
                    continue
                tu = read_tounicode(doc, f[0])
                if not tu:
                    continue
                buf = font_bytes(doc, f[0])
                if len(buf) < 20000:
                    continue
                try:
                    tt = TTFont(io.BytesIO(buf))
                except Exception:
                    continue
                gs = tt.getGlyphSet()
                order = tt.getGlyphOrder()
                for cid, ch in tu.items():
                    if not (0x3400 <= ord(ch) <= 0x9fff):
                        continue
                    if cid >= len(order):
                        continue
                    bm = glyph_bitmap(gs, order[cid])
                    if bm is None or bm.sum() < 8:
                        continue
                    h = bh(bm)
                    votes.setdefault(h, collections.Counter())[ch] += 1
                    bms.setdefault(h, bm)
                    done += 1
        finally:
            doc.close()
        if i % 60 == 0:
            print("  %d/%d｜指纹 %d｜%.0fs" % (i, len(ok), len(votes), time.time() - t0),
                  flush=True)

    # 收敛：同一指纹对应同一字
    conflict = 0
    for h, cnt in votes.items():
        ch, n = cnt.most_common(1)[0]
        if n / sum(cnt.values()) >= 0.90:
            table[h] = ch
        else:
            conflict += 1

    print("\n字形样本 %d｜不同指纹 %d｜采纳 %d｜冲突丢弃 %d"
          % (done, len(votes), len(table), conflict))
    cjk = {v for v in table.values()}
    print("覆盖汉字 %d 个" % len(cjk))

    hs = list(table.keys())
    arr = np.stack([bms[h] for h in hs]).astype(np.uint8)
    np.savez_compressed(DICT, hashes=np.array(hs), bitmaps=arr)
    META.write_text(json.dumps({
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "docs": len(ok), "samples": done, "n_hash": len(hs),
        "chars": sorted(cjk),
        "map": table,
        "conflict": conflict,
    }, ensure_ascii=False), encoding="utf-8")
    print("已写 %s / %s" % (DICT, META))


def load_dict():
    z = np.load(DICT, allow_pickle=False)
    hashes = [str(h) for h in z["hashes"]]
    bm = z["bitmaps"]
    meta = json.loads(META.read_text(encoding="utf-8"))
    idx = {h: i for i, h in enumerate(hashes)}
    return idx, bm, meta["map"]


def eval_seed() -> None:
    """用标题映射（100% 准确的 82 个）评估字形字典的查中率。"""
    from fontTools.cffLib import CFFFontSet

    idx, bm, table = load_dict()
    print("字典：%d 个指纹 / %d 个汉字" % (len(idx), len(table)))

    seed = json.loads(SEED.read_text(encoding="utf-8"))
    truth = {}
    for fno, m in (seed.get("fonts") or {}).items():
        for ch, good in m.items():
            truth[(fno, ch)] = good
    print("ground truth（标题映射）: %d 个" % len(truth))

    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    bad = [x for x in audit["items"] if x["kind"] == "A_bm_only"]

    hit = miss = wrong = 0
    details = []
    for x in bad[:6]:
        try:
            doc = pymupdf.open(x["path"])
        except Exception:
            continue
        try:
            for f in doc[0].get_fonts(full=True):
                nm = str(f[3])
                m = re.search(r"FzBookMaker(\d+)", nm)
                if not m:
                    continue
                fno = "FZ%s" % m.group(1)
                buf = font_bytes(doc, f[0])
                if len(buf) < 2000:
                    continue
                try:
                    cff = CFFFontSet()
                    cff.decompile(io.BytesIO(buf), None)
                    td = cff[cff.fontNames[0]]
                except Exception:
                    continue
                cs = td.CharStrings
                gsn = {n: cs[n] for n in cs.keys()}
                for (tf, ch), good in truth.items():
                    if tf != fno:
                        continue
                    gname = "G%02X" % ord(ch)
                    if gname not in gsn:
                        gname = "G%02x" % ord(ch)
                    if gname not in gsn:
                        continue
                    bitmap = glyph_bitmap(gsn, gname)
                    if bitmap is None:
                        continue
                    h = bh(bitmap)
                    if h in table:
                        got = table[h]
                        if got == good:
                            hit += 1
                        else:
                            wrong += 1
                            details.append((ch, good, got, "指纹命中但字不同"))
                    else:
                        # 近似匹配：位图交集
                        flat = bitmap.reshape(1, N * N).astype(np.int16)
                        allb = bm.reshape(len(bm), N * N).astype(np.int16)
                        inter = (flat * allb).sum(1)
                        union = flat.sum() + allb.sum(1) - inter
                        iou = inter / np.maximum(1, union)
                        k = int(iou.argmax())
                        if iou[k] >= 0.90:
                            got = table[str(np.load(DICT)["hashes"][k])]
                            if got == good:
                                hit += 1
                            else:
                                wrong += 1
                                details.append((ch, good, got, "近似命中 IoU=%.2f" % iou[k]))
                        else:
                            miss += 1
                            details.append((ch, good, None, "未命中 IoU=%.2f" % iou[k]))
        finally:
            doc.close()

    tot = hit + miss + wrong
    print("\n=== 评估 ===")
    print("  测试 %d｜正确 %d（%.1f%%）｜错 %d｜未命中 %d"
          % (tot, hit, hit * 100.0 / max(1, tot), wrong, miss))
    for d in details[:12]:
        print("  %r 期望 %r 得到 %r —— %s" % d)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--eval", action="store_true")
    a = ap.parse_args()
    if a.build:
        build()
    if a.eval:
        eval_seed()
    if not a.build and not a.eval:
        build()
        eval_seed()
    return 0


if __name__ == "__main__":
    sys.exit(main())
