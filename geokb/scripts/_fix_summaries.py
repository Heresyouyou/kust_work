# -*- coding: utf-8 -*-
"""修复历史残缺 summary（零 API 成本）。

原理：key_content 在条目正文里是完整保存的，只是 frontmatter 的 summary
     被旧的 [:120] 硬截断。从正文取回完整文本，用句子边界截断重写 summary。
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdf_corpus_ingest import sentence_safe_summary

CONTENT = r"D:/ZSK/geokb/content/papers"
FRONT = re.compile(r"^---\n(.*?)\n---\n", re.S)
KEY = re.compile(r"## 关键内容\n\n(.*?)\n\n## ", re.S)
SUM_LINE = re.compile(r"^summary: .*$", re.M)


def main():
    files = sorted(glob.glob(os.path.join(CONTENT, "*.md")))
    fixed = skipped = 0
    for f in files:
        raw = open(f, encoding="utf-8").read()
        m = FRONT.match(raw)
        if not m:
            skipped += 1
            continue
        fm_block = m.group(1)
        km = KEY.search(raw)
        key_content = ""
        if km:
            v = km.group(1).strip()
            if v and v != "（未能提取）":
                key_content = v
        if not key_content:
            # 正文缺失则跳过，保持原样
            skipped += 1
            continue
        new_sum = sentence_safe_summary(key_content)
        # YAML 安全地写回
        line = "summary: " + json.dumps(new_sum, ensure_ascii=False)
        new_block = SUM_LINE.sub(lambda _: line, fm_block)
        if new_block == fm_block:
            skipped += 1
            continue
        out = raw[:m.start()] + "---\n" + new_block + "\n---\n" + raw[m.end():]
        open(f, "w", encoding="utf-8").write(out)
        fixed += 1
    print(f"修复 {fixed} 条，跳过 {skipped} 条（无正文/无 summary 行）")


if __name__ == "__main__":
    main()
