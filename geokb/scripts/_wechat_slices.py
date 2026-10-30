# -*- coding: utf-8 -*-
"""为微信批次每篇 PDF 提取「摘要 + 结论」摘录，供人工逐篇分析。

产出 state/wechat_202610_meta/slices/NN.txt（NN=批次内序号）。
"""
import json
import re
from pathlib import Path

import pymupdf

SRC = Path(r"D:\WeChat\xwechat_files\wxid_5a7fxnscvh4h12_8ac3\msg\file\2026-10")
META = Path(__file__).resolve().parent.parent / "state" / "wechat_202610_meta"
SLICES = META / "slices"
SLICES.mkdir(parents=True, exist_ok=True)

recs = json.loads((META / "extract.json").read_text(encoding="utf-8"))


def clean(t):
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def grab(text, pats, maxlen=2600):
    for p in pats:
        m = re.search(p, text, re.I | re.S)
        if m:
            seg = clean(m.group(1))[:maxlen]
            if len(seg) > 120:
                return seg
    return ""


for i, r in enumerate(recs):
    p = SRC / r["file"]
    doc = pymupdf.open(str(p))
    full = clean("\n".join(pg.get_text("text") for pg in doc))
    doc.close()

    abstract = grab(full, [
        r"(?is)abstract[.:：]?\s*(.+?)(?:\n\s*(?:keywords?|key words|index terms|1\.?\s*introduction)\b)",
        r"(?s)摘\s*要[：:．.]?\s*(.+?)(?:\n\s*关\s*键\s*词)",
    ])
    concl = grab(full, [
        r"(?is)\n\s*(?:\d\.?\s*)?conclusions?(?:\s+and\s+(?:discussion|outlook))?[.:：]?\s*\n(.+?)(?:\n\s*(?:acknowledg|references|appendix)\b|\Z)",
        r"(?s)结\s*论(?:\s*与\s*讨\s*论)?[：:．.]?\s*(.+?)(?:\n\s*(?:致\s*谢|参考文献)\b|\Z)",
    ], maxlen=2200)

    out = f"""# [{i:02d}] {r['file']}
sha1: {r['sha1']}  pages: {r['pages']}

===== 摘要 =====
{abstract or '(未抓到)'}

===== 结论 =====
{concl or '(未抓到)'}
"""
    (SLICES / f"{i:02d}.txt").write_text(out, encoding="utf-8")
    print(f"[{i:02d}] abstract={len(abstract)} concl={len(concl)}  {r['file'][:50]}")

print("done ->", SLICES)
