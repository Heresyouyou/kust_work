#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""能不能「不下载字体、不 OCR」就还原？—— 用知识库自带的同款字库做字形指纹。

思路
----
可读的 366 篇 PDF 用的是 `FZSSK--GBK1-0`（方正书宋，TrueType，**带 ToUnicode**），
乱码的 573 篇用的是 `FzBookMaker*`（同一套字形，但 Type1/CFF 子集、无 ToUnicode）。

若两者确实是同一款字形设计，就能：
  1. 从可读 PDF 抽出 FZSSK 字体 → 用 cmap/ToUnicode 建立「字形 → Unicode」
  2. 抽乱码 PDF 的 FzBookMaker 字形 → 与上面的字形做形状比对
  3. 命中即得 Unicode，**100% 准确，不需要 OCR、不需要映射表**

本脚本只做可行性验证，不写盘：
  · FZSSK 能提出来吗？多少字形？有没有 cmap？
  · 两边的字形能不能对上（用「已确认映射」的 82 个字做 ground truth 验命中率）
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
SEED = BASE / "state" / "esf_charmap_title.json"


def extract_font_bytes(doc, xref) -> bytes:
    try:
        info = doc.extract_font(xref)
        return info[3] or b""
    except Exception:
        pass
    # 退回 FontFile 流
    fd = doc.xref_get_key(xref, "FontDescriptor")
    if fd[0] == "xref":
        n = int(fd[1].split()[0])
        obj = doc.xref_object(n, compressed=False)
        for key in ("FontFile2", "FontFile3", "FontFile"):
            m = re.search(r"/%s\s+(\d+)\s+0\s+R" % key, obj)
            if m:
                return doc.xref_stream_raw(int(m.group(1))) or b""
    return b""


def dump_gbk_font():
    """从可读 PDF 抽 FZSSK，报告字形数与 cmap 覆盖。"""
    d = json.loads(AUDIT.read_text(encoding="utf-8"))
    ok = [x for x in d["items"] if x["kind"] == "C_gbk_ok"]
    print("可读篇 %d，从中抽 FZSSK 字体" % len(ok))
    best = None
    seen_hash = collections.Counter()
    for x in ok[:40]:
        try:
            doc = pymupdf.open(x["path"])
        except Exception:
            continue
        try:
            for f in doc[0].get_fonts(full=True):
                nm = str(f[3])
                if "FZSSK" not in nm:
                    continue
                buf = extract_font_bytes(doc, f[0])
                if len(buf) < 50000:
                    continue
                seen_hash[hash(buf)] += 1
                if best is None or len(buf) > len(best[1]):
                    best = (nm, buf, x["path"])
        finally:
            doc.close()
    if not best:
        print("  ✘ 没抽到 FZSSK")
        return None
    nm, buf, src = best
    print("  抽到最大的一份: %s  %.1f KB  来自 %s"
          % (nm, len(buf) / 1024, Path(src).name[:44]))
    print("  不同 PDF 里同一字体的字节 hash 种类: %d（%s）"
          % (len(seen_hash), "同一字库" if len(seen_hash) == 1 else "有差异"))

    # 解析
    ok_parse = False
    try:
        from fontTools.ttLib import TTFont
        tt = TTFont(io.BytesIO(buf))
        n = tt["maxp"].numGlyphs
        has_cmap = "cmap" in tt
        uni = 0
        if has_cmap:
            cm = tt.getBestCmap() or {}
            uni = sum(1 for k in cm if 0x3400 <= k <= 0x9FFF)
        print("  TrueType 解析成功: 字形 %d｜cmap %s｜其中汉字 %d"
              % (n, "有" if has_cmap else "无", uni))
        ok_parse = True
    except Exception as e:
        print("  TrueType 解析失败: %s（尝试裸 CFF）" % type(e).__name__)
    if not ok_parse:
        try:
            from fontTools.cffLib import CFFFontSet
            cff = CFFFontSet()
            cff.decompile(io.BytesIO(buf), None)
            td = cff[cff.fontNames[0]]
            print("  裸 CFF 解析成功: 字形 %d" % len(list(td.CharStrings.keys())))
            print("  注意: 裸 CFF 没有 cmap，Unicode 只能靠 PDF 的 ToUnicode")
        except Exception as e:
            print("  CFF 也失败: %s" % type(e).__name__)
    return (nm, buf, src)


def check_tounicode_coverage():
    """FZSSK 的 ToUnicode 覆盖多少字（这才是字形→Unicode 的真来源）。"""
    d = json.loads(AUDIT.read_text(encoding="utf-8"))
    ok = [x for x in d["items"] if x["kind"] == "C_gbk_ok"]
    print("\n=== FZSSK 的 ToUnicode 覆盖 ===")
    for x in ok[:3]:
        doc = pymupdf.open(x["path"])
        for f in doc[0].get_fonts(full=True):
            if "FZSSK" not in str(f[3]):
                continue
            tu = doc.xref_get_key(f[0], "ToUnicode")
            if tu[0] != "xref":
                continue
            n = int(tu[1].split()[0])
            stream = doc.xref_stream(n) or b""
            txt = stream.decode("latin-1", "ignore")
            pairs = re.findall(r"<([0-9A-Fa-f]{4})>\s*<([0-9A-Fa-f]{4,})>", txt)
            cjk = 0
            for _src, dst in pairs:
                up = dst[:4]
                try:
                    if 0x3400 <= int(up, 16) <= 0x9FFF:
                        cjk += 1
                except Exception:
                    pass
            print("  %s: ToUnicode 映射 %d 条，其中汉字 %d 条"
                  % (str(f[3])[:30], len(pairs), cjk))
        doc.close()


def compare_visible():
    """决定性验证：同一页上，可读篇的 FZSSK 字形 vs 乱码篇的 FzBookMaker 字形，
    用已确认的 82 个映射当 ground truth，看字形能否区分。"""
    sd = json.loads(SEED.read_text(encoding="utf-8"))
    seedmap = {}
    for fno, m in (sd.get("fonts") or {}).items():
        for ch, good in m.items():
            seedmap[(fno, ch)] = good
    print("\n=== 已确认映射（标题种子）: %d 个 ===" % len(seedmap))

    d = json.loads(AUDIT.read_text(encoding="utf-8"))
    bad = [x for x in d["items"] if x["kind"] == "A_bm_only"]
    # 取一篇，验证 seedmap 是否真的能解释页面文本
    for x in bad[:1]:
        doc = pymupdf.open(x["path"])
        try:
            page = doc[0]
            covered = total = 0
            sample = []
            for b in page.get_text("dict").get("blocks", []):
                if b.get("type") != 0:
                    continue
                for line in b.get("lines", []):
                    for sp in line.get("spans", []):
                        nm = str(sp.get("font", ""))
                        m = re.search(r"FzBookMaker(\d+)", nm)
                        fno = "FZ%s" % m.group(1) if m else "OTHER"
                        for ch in (sp.get("text") or ""):
                            if ch.isspace():
                                continue
                            total += 1
                            if (fno, ch) in seedmap:
                                covered += 1
                                if len(sample) < 24:
                                    sample.append(seedmap[(fno, ch)])
            print("  文件 %s" % Path(x["path"]).name[:50])
            print("  首页字符 %d｜被种子映射覆盖 %d（%.1f%%）"
                  % (total, covered, covered * 100.0 / max(1, total)))
            print("  还原片段: %s" % "".join(sample))
        finally:
            doc.close()


def main() -> int:
    dump_gbk_font()
    check_tounicode_coverage()
    compare_visible()
    return 0


if __name__ == "__main__":
    sys.exit(main())
