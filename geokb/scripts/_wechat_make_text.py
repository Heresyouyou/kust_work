# -*- coding: utf-8 -*-
"""为微信 2026-10 批次 31 篇补生成 state/text/<sha1>.txt（kp_extract 的唯一输入）。

复用 pdf_corpus_ingest.extract_text 的清理规则，保证与存量文本同构。
来源优先取 pdf_state.jsonl 登记的 NAS 路径，回退到微信原目录。
"""
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEXT_DIR = os.path.join(BASE, "state", "text")
STATE = os.path.join(BASE, "state", "pdf_state.jsonl")
EXTRACT = os.path.join(BASE, "state", "wechat_202610_meta", "extract.json")

import pymupdf  # noqa: E402


def clean(text):
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def main():
    recs = json.load(open(EXTRACT, encoding="utf-8"))
    # 31 篇（跳过 index 14 的重复副本）
    want = {}
    for i, r in enumerate(recs):
        if i == 14:
            continue
        want[r["sha1"]] = r["file"]

    # pdf_state 登记路径
    path_of = {}
    for line in open(STATE, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("status") == "done" and rec.get("hash") in want:
            path_of[rec["hash"]] = rec["path"]

    os.makedirs(TEXT_DIR, exist_ok=True)
    ok = skip = fail = 0
    for sha, fname in want.items():
        dst = os.path.join(TEXT_DIR, sha + ".txt")
        if os.path.exists(dst) and os.path.getsize(dst) > 1000:
            skip += 1
            continue
        src = path_of.get(sha)
        if not src or not os.path.exists(src):
            # 回退：微信原目录
            src = os.path.join(
                r"D:\WeChat\xwechat_files\wxid_5a7fxnscvh4h12_8ac3\msg\file\2026-10",
                fname)
        if not os.path.exists(src):
            print("MISS %s %s" % (sha[:12], fname[:60]))
            fail += 1
            continue
        try:
            doc = pymupdf.open(src)
            text = "\n".join(p.get_text("text") for p in doc)
            doc.close()
            text = clean(text)
            if len(text) < 500:
                print("THIN %s chars=%d %s" % (sha[:12], len(text), fname[:50]))
                fail += 1
                continue
            with open(dst, "w", encoding="utf-8") as f:
                f.write(text)
            ok += 1
        except Exception as e:
            print("ERR %s %s: %s" % (sha[:12], fname[:40], str(e)[:80]))
            fail += 1

    print("新增 %d，已存在 %d，失败 %d，合计目标 %d" % (ok, skip, fail, len(want)))


if __name__ == "__main__":
    main()
