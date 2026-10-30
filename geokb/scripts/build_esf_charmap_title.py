#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用「台账正确标题」反推方正书版乱码的字符映射（零 OCR，最可靠）。

为什么这条路最稳
----------------
- 地学前缘台账里的**中文标题是期刊站点题录，正确无误**（前几轮标题审计已核过）。
- PDF 首页标题是页面最大字号块，位置唯一。
- 标题字数固定，乱码序列与正确标题**一一对应**，用 difflib 对齐即可。
- 标题覆盖的都是学术高频字（地质、矿床、构造、岩石…），命中率高。

对齐成功后得到 `乱码char → 汉字`，再喂给 build_esf_charmap.py 的 OCR 结果互相补。
"""
from __future__ import annotations

import collections
import difflib
import json
import random
import re
import sys
import time
from pathlib import Path

import pymupdf

BASE = Path(__file__).resolve().parent.parent
LEDGER = BASE / "state" / "papers_index.jsonl"
OUT = BASE / "state" / "esf_charmap_title.json"


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


def title_block(page) -> tuple[list[tuple[str, str]], float]:
    """页面最大字号的行，按阅读顺序拼成标题块。

    返回 ([(字符, 字体编号)], 字号)。
    ⚠️ 必须带回字体：方正书版每篇会把字库拆成 FzBookMaker0…13 十几个子集，
    **每个子集是独立的编码空间**，同一个 `&` 在 0 号字体是「地」、在 3 号字体
    可能是别的字。不分字体投票会把一致性从 ~95% 拉到 ~78%（实测）。
    """
    lines = []
    for b in page.get_text("dict").get("blocks", []):
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            spans = [s for s in line.get("spans", []) if (s.get("text") or "").strip()]
            if not spans:
                continue
            sz = max(s.get("size", 0) for s in spans)
            lines.append((sz, line.get("bbox", (0, 0, 0, 0))[1], spans))
    if not lines:
        return [], 0.0
    mx = max(l[0] for l in lines)
    cand = [l for l in lines if l[0] >= mx - 1.0]
    cand.sort(key=lambda x: x[1])

    out = []
    for _sz, _y, spans in cand:
        for s in spans:
            fno = font_no(s.get("font", ""))
            for ch in (s.get("text") or ""):
                out.append((ch, fno))
    return out, mx


def font_no(name: str) -> str:
    """`JLNBZQ+FzBookMaker0DlFont00536879635` → `FzBookMaker0`。

    前缀 6 字母是子集随机 ID，尾部数字是导出时间戳，都不稳定，必须剥掉。
    """
    m = re.search(r"FzBookMaker(\d+)", name or "")
    if m:
        return "FZ%s" % m.group(1)
    # 非方正字体（Type0/CID 那些带 ToUnicode 的）单独归类
    return "OTHER"


def norm_title(t: str) -> str:
    """标题归一：去掉引号、括号注释、多余空白，只保留可比对的正文。"""
    t = (t or "").strip()
    t = t.replace(" ", "").replace("\u3000", "")
    for ch in "“”\"'「」『』":
        t = t.replace(ch, "")
    return t


def main() -> int:
    ap = __import__("argparse").ArgumentParser()
    ap.add_argument("--max-docs", type=int, default=0)
    ap.add_argument("--min-ratio", type=float, default=0.72)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()

    rows = esf()
    random.seed(a.seed)
    random.shuffle(rows)
    if a.max_docs:
        rows = rows[:a.max_docs]

    votes = collections.defaultdict(collections.Counter)
    font_of = {}
    used = 0
    bad_align = 0
    t0 = time.time()

    for r in rows:
        p = Path(str(r.get("repo_path")))
        want = norm_title(str(r.get("title") or ""))
        if len(want) < 8:
            continue
        try:
            doc = pymupdf.open(p)
        except Exception:
            continue
        try:
            page = doc[0]
            got_pairs, sz = title_block(page)
        finally:
            doc.close()

        # 去掉空白字符（保留字体编号），再拼成串
        got_pairs = [(c, f) for c, f in got_pairs if not c.isspace()]
        got = "".join(c for c, _ in got_pairs)
        if not got or len(got) < 8:
            continue
        # 只处理乱码块：CJK 很少
        cjk = sum(1 for c in got if "\u3400" <= c <= "\u9fff")
        if cjk > len(got) * 0.3:
            continue     # 这篇是可读的，不需要映射

        # ⚠️ 不能用相似度过滤：标题整体乱码时，乱码串与正确标题**没有任何共同字符**，
        # difflib 的 ratio 恒为 0（实测 20 字标题 ratio=0.0）。真正的判据是
        # **字数相等**——方正书版每个汉字一个 glyph，长度必然一一对应。
        if len(got) == len(want):
            pairs = list(zip(got_pairs, want))
        else:
            # 长度不等时退回 difflib，只采纳等长的 replace 块
            sm = difflib.SequenceMatcher(None, got, want)
            pairs = []
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag in ("replace", "equal") and (i2 - i1) == (j2 - j1):
                    for k in range(i2 - i1):
                        pairs.append((got_pairs[i1 + k], want[j1 + k]))
            if not pairs:
                bad_align += 1
                continue

        for (bad, fno), good in pairs:
            if bad == good:
                continue          # 数字/字母本来就对
            if "\u3400" <= bad <= "\u9fff":
                continue          # 真中文，不用映射
            votes[(fno, bad)][good] += 1
        used += 1

    print("扫描 %d 篇｜对齐成功 %d｜对齐失败 %d｜用时 %.0fs"
          % (len(rows), used, bad_align, time.time() - t0))

    # 按字体分组收敛：每个字体一张独立映射表
    by_font: dict[str, dict[str, str]] = {}
    detail = {}
    keep = drop = 0
    for (fno, bad), cnt in votes.items():
        total = sum(cnt.values())
        good, n = cnt.most_common(1)[0]
        key = "%s|%s" % (fno, bad)
        conf = n / total
        if n >= 2 and conf >= 0.85:
            by_font.setdefault(fno, {})[bad] = good
            detail[key] = {"ch": good, "n": n, "total": total, "conf": round(conf, 2)}
            keep += 1
        else:
            drop += 1

    chars = sum(len(v) for v in by_font.values())
    print("候选 %d｜采纳 %d（按字体分组，≥2 票且一致性 ≥85%%）｜丢弃 %d"
          % (len(votes), keep, drop))
    print("字体分组：" + "，".join("%s=%d" % (k, len(v)) for k, v in sorted(by_font.items())))

    # 展示部分映射
    items = sorted(detail.items(), key=lambda kv: -kv[1]["n"])[:20]
    print("\n高频映射样例：")
    for k, d in items:
        print("   %-16s → %r  (%d/%d, %.0f%%)" % (k, d["ch"], d["n"], d["total"],
                                                  d["conf"] * 100))

    OUT.write_text(json.dumps({"built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                               "docs": used, "fonts": by_font, "detail": detail},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n已写 %s（%d 个字符，%d 个字体）" % (OUT, chars, len(by_font)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
