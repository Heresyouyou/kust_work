# -*- coding: utf-8 -*-
"""一次性：把 off_domain_candidates.json 中的域外条目标记 status=deprecated 并重建索引。"""
import json
import os
import re
import subprocess
import sys

BASE = r"D:/ZSK/geokb"
CAND = os.path.join(BASE, "logs", "off_domain_candidates.json")

cands = json.load(open(CAND, encoding="utf-8"))
n = 0
for c in cands:
    p = os.path.join(BASE, "content", "papers", c["id"] + ".md")
    if not os.path.exists(p):
        print("missing:", c["id"])
        continue
    t = open(p, encoding="utf-8").read()
    t2 = re.sub(r"^status:\s*\S+", "status: deprecated", t, count=1, flags=re.M)
    if t2 != t:
        open(p, "w", encoding="utf-8").write(t2)
        n += 1
        print("deprecated:", c["id"], c["title"][:50])
print(f"marked {n}/{len(cands)}")
subprocess.run([sys.executable, os.path.join(BASE, "scripts", "rebuild_index.py")], timeout=1800)
print("rebuild done")
