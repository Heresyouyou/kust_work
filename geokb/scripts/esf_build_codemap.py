#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""建立「字体编号 + 编码 → 字符」表，让还原阶段完全不用再解析字体。

为什么要这张表
--------------
`FzBookMaker{M}` 的编码是**全局固定**的（实测 `FZ0|&→地` 293/293 一致），
但字体文件是**逐页子集化**的（同一文档第 1/2/3 页的 FzBookMaker0 分别是
30 KB / 68 KB / 42 KB）—— 也就是说形状数据按页不同，编码却相同。

于是可以：先用字形字典把若干页的 (字体编号, 编码) 全部解出来，收敛成一张表；
之后还原只需读编码查表，**不必再解析任何字体**，速度提升两个数量级。

用法：
  python scripts/esf_build_codemap.py --pages 40
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

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import esf_glyph_dict as G          # noqa: E402
import esf_restore_text as R        # noqa: E402

BASE = Path(__file__).resolve().parent.parent
AUDIT = BASE / "state" / "esf_font_audit.json"
OUT = BASE / "state" / "esf_codemap.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=40, help="采样页数")
    ap.add_argument("--seed", type=int, default=13)
    a = ap.parse_args()

    R.prepare_lookup()
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    bad = [x for x in audit["items"] if x["kind"] == "A_bm_only"]

    import random
    random.seed(a.seed)
    random.shuffle(bad)

    votes: dict[tuple[str, int], collections.Counter] = collections.defaultdict(collections.Counter)
    t0 = time.time()
    pages_done = 0
    docs_done = 0

    for x in bad:
        if pages_done >= a.pages:
            break
        try:
            doc = pymupdf.open(x["path"])
        except Exception:
            continue
        docs_done += 1
        try:
            for pno in range(min(3, doc.page_count)):
                if pages_done >= a.pages:
                    break
                page = doc[pno]
                # 该页用到的字体
                for f in page.get_fonts(full=True):
                    nm = str(f[3])
                    m = re.search(r"FzBookMaker(\d+)", nm)
                    if not m:
                        continue
                    fno = "FZ%s" % m.group(1)
                    buf = G.font_bytes(doc, f[0])
                    if len(buf) < 500:
                        continue
                    try:
                        from fontTools.cffLib import CFFFontSet
                        cff = CFFFontSet()
                        cff.decompile(io.BytesIO(buf), None)
                        td = cff[cff.fontNames[0]]
                    except Exception:
                        continue
                    cs = {n: td.CharStrings[n] for n in td.CharStrings.keys()}
                    for gname in cs:
                        mm = re.match(r"G([0-9A-Fa-f]{2})$", gname)
                        if not mm:
                            continue
                        code = int(mm.group(1), 16)
                        bm = G.glyph_bitmap(cs, gname)
                        if bm is None:
                            continue
                        ch, conf, margin = R.lookup(bm)
                        if not ch:
                            continue
                        # 阈值必须卡紧：实测正确匹配 IoU 在 0.98–1.00，错误匹配在 0.5 上下。
                        # 早前用 0.88 放进来一堆误配，同一 (字体,编码) 被投出 3 个不同字，
                        # 1139 个组合里 719 个被判冲突 —— 那是阈值的问题，不是编码不固定。
                        if conf >= 0.995 or (conf >= 0.93 and margin >= 0.06):
                            votes[(fno, code)][ch] += 1
                pages_done += 1
                if pages_done % 5 == 0:
                    print("  %d 页｜已知组合 %d｜%.0fs"
                          % (pages_done, len(votes), time.time() - t0), flush=True)
        finally:
            doc.close()

    cmap, detail = {}, {}
    stable = loose = drop = 0
    for (fno, code), cnt in votes.items():
        total = sum(cnt.values())
        ch, n = cnt.most_common(1)[0]
        key = "%s|%d" % (fno, code)
        if n == total and total >= 2:
            cmap[key] = ch
            detail[key] = dict(ch=ch, n=n, total=total, conf=1.0)
            stable += 1
        elif n / total >= 0.95:
            cmap[key] = ch
            detail[key] = dict(ch=ch, n=n, total=total, conf=round(n / total, 2))
            loose += 1
        else:
            drop += 1

    print("\n=== 结果 ===")
    print("  采样 %d 页 / %d 篇｜组合 %d" % (pages_done, docs_done, len(votes)))
    print("  采纳 %d（100%% 一致 %d｜≥95%% %d）｜丢弃 %d"
          % (len(cmap), stable, loose, drop))
    chars = {v for v in cmap.values()}
    cjk = {c for c in chars if "\u3400" <= c <= "\u9fff"}
    print("  覆盖字符 %d｜其中汉字 %d" % (len(chars), len(cjk)))

    # 按字体统计
    byfont = collections.Counter(k.split("|")[0] for k in cmap)
    print("  按字体: " + "，".join("%s=%d" % kv for kv in sorted(byfont.items())))

    OUT.write_text(json.dumps({
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pages": pages_done, "docs": docs_done,
        "n": len(cmap), "n_chars": len(chars), "n_cjk": len(cjk),
        "map": cmap, "detail": detail,
    }, ensure_ascii=False, indent=0), encoding="utf-8")
    print("\n已写 %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
