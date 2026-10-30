# -*- coding: utf-8 -*-
"""离线 token 核算（只读，不调 API）。

目的：量化当前入库链路每一步真正送进模型的 token，以及「清洗/改序/去重」
      三类改动的理论收益。校准系数来自 logs/token_calibration.json 实测
      （英文 3.0 chars/token；中文按 1 字 1 token 估）。
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _clean_layers_probe import clean_layers  # noqa: E402

TEXT_DIR = r"D:/ZSK/geokb/state/text"
CJK = re.compile(r"[\u4e00-\u9fff]")


def est_tok(s):
    """实测校准：CJK≈1 token/字，ASCII≈3 chars/token（不是 4）。"""
    cjk = len(CJK.findall(s))
    return int(cjk + (len(s) - cjk) / 3.0)


def main():
    files = sorted(glob.glob(os.path.join(TEXT_DIR, "*.txt")))
    n = len(files)
    rows = []
    for fp in files:
        raw = open(fp, encoding="utf-8", errors="ignore").read()
        full = est_tok(raw)
        # kp_extract 现状：空行剔除 + 每行加 "L<n>:" 前缀 + 截断 12000 字符
        lines = [l for l in raw.split("\n") if l.strip()]
        numbered = "\n".join("L%d:%s" % (i + 1, l) for i, l in enumerate(lines))
        kp_in = est_tok(numbered[:12000])
        # pdf_corpus_ingest 现状：smart_truncate(5000) 用 CJK+rest/4 估算
        try:
            from pdf_corpus_ingest import smart_truncate
            st = smart_truncate(raw)
            cor_in = est_tok(st)
        except Exception:
            cor_in = 0
        # 清洗后（L4）：同一预算 12000 字符能装多少有效内容
        try:
            _, cleaned = clean_layers(raw)
            cl = est_tok(cleaned)
        except Exception:
            cleaned, cl = raw, full
        rows.append(dict(f=os.path.basename(fp), chars=len(raw), full=full,
                         kp_in=kp_in, cor_in=cor_in, clean=cl, cchars=len(cleaned)))

    def avg(k):
        return sum(r[k] for r in rows) / n

    print("样本 %d 篇" % n)
    print("平均字符 %.0f | 全文 token %.0f" % (avg("chars"), avg("full")))
    print("kp_extract 送 %0.f token（12000 字符截断后）" % avg("kp_in"))
    print("pdf_corpus 送 %.0f token（smart_truncate 5000）" % avg("cor_in"))
    print("清洗后全文 token %.0f（占原文 %.1f%%）" % (avg("clean"), 100 * avg("clean") / avg("full")))
    print("两任务合计 in %.0f token/篇" % (avg("kp_in") + avg("cor_in")))

    # 清洗后的信息密度：同样 token 预算能装多少字符
    dens = avg("cchars") / avg("clean") if avg("clean") else 0
    print("清洗后密度 %.2f chars/token" % dens)


if __name__ == "__main__":
    main()
