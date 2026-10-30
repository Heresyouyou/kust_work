# -*- coding: utf-8 -*-
"""正文完整性校验：对比修复后文件与备份，正文（frontmatter 之后的部分）必须逐字节一致。"""
import glob
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUR = os.path.join(BASE, "content")
BAK = os.path.join(BASE, "state", "backup_content_20261015")


def body_of(text):
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    return text[m.end():] if m else text


def main():
    files = sorted(glob.glob(os.path.join(CUR, "**", "*.md"), recursive=True))
    bad, checked = [], 0
    for p in files:
        rel = os.path.relpath(p, CUR)
        b = os.path.join(BAK, rel)
        if not os.path.exists(b):
            continue
        checked += 1
        cur_b = body_of(open(p, encoding="utf-8").read())
        bak_b = body_of(open(b, encoding="utf-8").read())
        # papers-corpus 与撤稿条目是故意追加了正文说明的，排除
        if rel in ("economic-geology/papers-corpus.md",
                   "economic-geology/feed-521d5b18063d.md"):
            if not cur_b.startswith(bak_b.split("## 来源")[0][:200].strip()[:50]) and "公开访问说明" not in cur_b and "撤稿警示" not in cur_b:
                bad.append((rel, "追加说明缺失"))
            continue
        if cur_b != bak_b:
            bad.append((rel, "正文被改动"))
    print(f"校验 {checked} 个文件，正文不一致 {len(bad)} 个")
    for rel, why in bad[:20]:
        print(" ", rel, why)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
