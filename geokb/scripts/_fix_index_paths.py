# -*- coding: utf-8 -*-
"""把索引文件里的 PDF 绝对路径从本地旧位置改写到 NAS 新位置。

注意：本机 Bash 会把反斜杠改写，故路径常量一律在 .py 文件里定义，不写进命令行。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import pdf_root  # noqa: E402

OLD_LOCAL = "D:" + os.sep + "PostNews" + os.sep + "papers_repo"
OLD_LOCAL_FWD = "D:/PostNews/papers_repo"
NEW = pdf_root()

targets = [
    ("state/pdf_state.jsonl", ("path",)),
    (os.path.join(NEW, "index.jsonl"), ("repo_path", "date_path", "source_path")),
]

for rel, keys in targets:
    p = rel if os.path.isabs(rel) else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), rel)
    if not os.path.exists(p):
        print("跳过（不存在）:", p)
        continue
    rows = [json.loads(l) for l in open(p, encoding="utf-8", errors="ignore") if l.strip()]
    n = 0
    for r in rows:
        for k in keys:
            v = r.get(k)
            if not isinstance(v, str):
                continue
            new = v.replace(OLD_LOCAL, NEW).replace(OLD_LOCAL_FWD, NEW)
            if new != v:
                r[k] = new
                n += 1
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    print("%-24s 更新 %d 处 / %d 条记录 -> %s" % (os.path.basename(p), n, len(rows), NEW))
