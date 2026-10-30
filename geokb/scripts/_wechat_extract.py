# -*- coding: utf-8 -*-
"""提取微信 2026-10 批次 PDF 的首页元数据，供逐篇人工分析入库。

产出 state/wechat_202610_meta/extract.json：
  [{file, sha1, pages, head_text(首页前2500字符), meta(pymupdf metadata)}]
"""
import hashlib
import json
import re
import sys
from pathlib import Path

import pymupdf

SRC = Path(r"D:\WeChat\xwechat_files\wxid_5a7fxnscvh4h12_8ac3\msg\file\2026-10")
OUT = Path(__file__).resolve().parent.parent / "state" / "wechat_202610_meta"
OUT.mkdir(parents=True, exist_ok=True)


def sha1_of(path, chunk=1 << 20):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


pdfs = sorted(SRC.glob("*.pdf"))
print(f"发现 {len(pdfs)} 篇 PDF")
recs = []
for p in pdfs:
    try:
        doc = pymupdf.open(str(p))
        head = doc[0].get_text("text")[:2500]
        meta = dict(doc.metadata or {})
        n = doc.page_count
        doc.close()
    except Exception as e:
        head, meta, n = "", {"error": str(e)}, 0
    head = re.sub(r"[ \t]+", " ", head)
    recs.append({
        "file": p.name,
        "sha1": sha1_of(p),
        "pages": n,
        "size": p.stat().st_size,
        "head_text": head,
        "meta": {k: v for k, v in meta.items() if v},
    })
    print(f"  ok {p.name[:60]} pages={n}")

(OUT / "extract.json").write_text(
    json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\n已写出 {OUT / 'extract.json'}")
