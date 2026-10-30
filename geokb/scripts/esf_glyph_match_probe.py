#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""决定性验证：乱码篇的 FzBookMaker 字形，与可读篇 FZSSK 字形是否同一套设计。

若同源，就能用「可读篇的 (CID, Unicode) + 字形轮廓」做字典，
对乱码篇的每个字形做形状匹配 —— 不需要 OCR、不需要映射表、准确率可到 ~100%。

验证方式（用已确认的标题映射当 ground truth）：
  已知 `FZ0|& = 地`（293/293 票，100% 一致）
  → 取乱码篇 FZ0 字体里编码 0x26 的字形 A
  → 取可读篇 FZSSK 里「地」的字形 B（通过 ToUnicode 反查 CID）
  → 比 A 与 B 的形状特征（bbox 比例、面积、点数、轮廓周长）
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
AUDIT = BASE / "state" / "esf_font_audit.json"


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


def glyph_feat_from_pen(glyphset, gname):
    """用 fontTools pens 取形状特征。"""
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.areaPen import AreaPen
    from fontTools.pens.recordingPen import RecordingPen
    try:
        bp = BoundsPen(glyphset)
        glyphset[gname].draw(bp)
        if not bp.bounds:
            return None
        ap = AreaPen(glyphset)
        glyphset[gname].draw(ap)
        rp = RecordingPen()
        glyphset[gname].draw(rp)
        npts = sum(len(args) for op, args in rp.value if op in ("curveTo", "qCurveTo", "lineTo"))
        x0, y0, x1, y1 = bp.bounds
        w, h = max(1e-6, x1 - x0), max(1e-6, y1 - y0)
        return {"bbox": (round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)),
                "w": round(w, 1), "h": round(h, 1),
                "ratio": round(w / h, 3),
                "area": round(abs(ap.value), 1),
                "fill": round(abs(ap.value) / (w * h), 3),
                "npts": npts}
    except Exception as e:
        return {"err": "%s: %s" % (type(e).__name__, str(e)[:40])}


def load_cff(path: Path, want: str):
    """从乱码 PDF 取指定 FzBookMaker 编号的 CFF 字体。"""
    from fontTools.cffLib import CFFFontSet
    doc = pymupdf.open(path)
    try:
        for f in doc[0].get_fonts(full=True):
            nm = str(f[3])
            m = re.search(r"FzBookMaker(\d+)", nm)
            if not m or "FZ%s" % m.group(1) != want:
                continue
            buf = font_bytes(doc, f[0])
            if len(buf) < 1000:
                continue
            cff = CFFFontSet()
            cff.decompile(io.BytesIO(buf), None)
            td = cff[cff.fontNames[0]]
            return td, nm
    finally:
        doc.close()
    return None, ""


def load_tt(path: Path, want: str):
    """从可读 PDF 取 FZSSK 字体 + ToUnicode。"""
    from fontTools.ttLib import TTFont
    doc = pymupdf.open(path)
    try:
        for f in doc[0].get_fonts(full=True):
            nm = str(f[3])
            if want not in nm:
                continue
            buf = font_bytes(doc, f[0])
            tu = {}
            v = doc.xref_get_key(f[0], "ToUnicode")
            if v[0] == "xref":
                st = doc.xref_stream(int(v[1].split()[0])) or b""
                txt = st.decode("latin-1", "ignore")
                for a, b2 in re.findall(r"<([0-9A-Fa-f]{4})>\s*<([0-9A-Fa-f]{4,})>", txt):
                    try:
                        tu[int(a, 16)] = chr(int(b2[:4], 16))
                    except Exception:
                        pass
            tt = TTFont(io.BytesIO(buf))
            return tt, tu, nm
    finally:
        doc.close()
    return None, {}, ""


def main() -> int:
    d = json.loads(AUDIT.read_text(encoding="utf-8"))
    bad = [x for x in d["items"] if x["kind"] == "A_bm_only"]
    ok = [x for x in d["items"] if x["kind"] == "C_gbk_ok"]

    # ground truth: FZ0 里编码 0x26('&') = 地，0x27(''') = 学，0x2c(',') = 质
    TRUTH = {"FZ0": {"&": "地", "'": "学", ",": "质", "*": "中"}}
    print("ground truth:", TRUTH)

    # 1) 乱码篇里取 FZ0
    src_bad = None
    for x in bad:
        # 不能拿 `"FZ0" in nm` 判断：真实字体名是 `WGYAZT+FzBookMaker0DlFont00536873798`，
        # 里面根本没有 `FZ0` 字样。load_cff 内部已按编号筛过，这里只看是否拿到。
        td, nm = load_cff(Path(x["path"]), "FZ0")
        if td is not None:
            src_bad = (td, nm, x["path"])
            break
    if not src_bad:
        print("✘ 没找到 FZ0")
        return 1
    td, bmname, badpath = src_bad
    print("\n乱码篇 FZ0: %s（%s）" % (bmname[:40], Path(badpath).name[:40]))
    names = list(td.CharStrings.keys())
    print("  字形数 %d｜前 6: %s" % (len(names), names[:6]))

    # CFF 的 glyphset 需要一个 font 对象
    cff_gs = {n: td.CharStrings[n] for n in names}

    feats_bad = {}
    for ch in TRUTH["FZ0"]:
        code = ord(ch)
        gname = "G%02X" % code
        if gname not in cff_gs:
            # 可能命名规则不同，试 G<hex大写>
            cand = [n for n in names if n.upper() == gname.upper()]
            gname = cand[0] if cand else None
        if not gname:
            print("  ⚠️ 编码 0x%02X（%r）没有对应字形" % (code, ch))
            continue
        f = glyph_feat_from_pen(cff_gs, gname)
        feats_bad[TRUTH["FZ0"][ch]] = f
        print("  %r(0x%02X→%s) = %s → %s" % (ch, code, gname, TRUTH["FZ0"][ch], f))

    # 2) 可读篇取 FZSSK + ToUnicode
    src_ok = None
    for x in ok:
        tt, tu, nm = load_tt(Path(x["path"]), "FZSSK")
        if tt and tu:
            src_ok = (tt, tu, nm, x["path"])
            break
    if not src_ok:
        print("✘ 没找到 FZSSK")
        return 1
    tt, tu, ssname, okpath = src_ok
    print("\n可读篇 FZSSK: %s（%s）" % (ssname[:40], Path(okpath).name[:40]))
    print("  ToUnicode 条目 %d｜ToUnicode 里有 地=%s 学=%s 质=%s 中=%s"
          % (len(tu),
             "有" if "地" in tu.values() else "无",
             "有" if "学" in tu.values() else "无",
             "有" if "质" in tu.values() else "无",
             "有" if "中" in tu.values() else "无"))

    rev = {v: k for k, v in tu.items()}
    gs = tt.getGlyphSet()
    order = tt.getGlyphOrder()
    print("\n=== 形状特征对比（关键：ratio 与 fill 是否接近）===")
    print("  %-4s %-28s %-28s %s" % ("字", "乱码篇 FzBookMaker", "可读篇 FZSSK", "判断"))
    same = 0
    for ch, fb in feats_bad.items():
        if not fb or "err" in fb:
            print("  %-4s %s" % (ch, fb))
            continue
        cid = rev.get(ch)
        if cid is None:
            print("  %-4s 可读篇 ToUnicode 里没有这个字" % ch)
            continue
        gname = order[cid] if cid < len(order) else None
        if not gname:
            print("  %-4s CID %d 超出字形范围" % (ch, cid))
            continue
        fs = glyph_feat_from_pen(gs, gname)
        if not fs or "err" in fs:
            print("  %-4s FZSSK 取字形失败 %s" % (ch, fs))
            continue
        dr = abs(fb["ratio"] - fs["ratio"]) / max(fb["ratio"], fs["ratio"])
        df = abs(fb["fill"] - fs["fill"])
        ok_same = dr < 0.08 and df < 0.08
        if ok_same:
            same += 1
        print("  %-4s ratio=%-6s fill=%-6s npts=%-4s | ratio=%-6s fill=%-6s npts=%-4s | %s"
              % (ch, fb["ratio"], fb["fill"], fb["npts"],
                 fs["ratio"], fs["fill"], fs["npts"],
                 "✅ 同形" if ok_same else "✘ 差异大"))
    print("\n同形 %d / %d" % (same, len(feats_bad)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
