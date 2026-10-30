# -*- coding: utf-8 -*-
"""全库引用源提取与分类：产出审计底表 state/source_audit.json + 分类统计。"""
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "index", "geokb.db")

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT id, module, title, sources FROM entries").fetchall()
conn.close()

def classify(src):
    """返回 (kind, value)。kind: ext_url / local_fake / internal_path / empty / other"""
    if not isinstance(src, dict):
        return "other", str(src)[:80]
    url = (src.get("url") or "").strip()
    path = (src.get("path") or "").strip()
    if url.startswith(("http://", "https://")):
        return "ext_url", url
    if url.startswith("local://"):
        return "local_fake", url
    if url:
        return "other_url", url
    if path:
        if path.lower().endswith(".pdf"):
            return "internal_pdf", path
        return "internal_path", path
    return "empty", ""

audit = []
kind_counter = Counter()
module_kind = defaultdict(Counter)
url_counter = Counter()
no_source_entries = []

for r in rows:
    try:
        srcs = json.loads(r["sources"] or "[]")
    except Exception:
        srcs = []
    if not isinstance(srcs, list):
        srcs = [srcs]
    if not srcs:
        no_source_entries.append({"id": r["id"], "module": r["module"], "title": r["title"]})
    for s in srcs:
        kind, val = classify(s)
        kind_counter[kind] += 1
        module_kind[r["module"]][kind] += 1
        audit.append({"id": r["id"], "module": r["module"], "kind": kind,
                      "value": val, "name": (s.get("name") or "")[:120] if isinstance(s, dict) else ""})
        if kind == "ext_url":
            url_counter[val] += 1

out = {
    "total_entries": len(rows),
    "total_sources": len(audit),
    "kind_counter": dict(kind_counter),
    "unique_ext_urls": len(url_counter),
    "module_kind": {m: dict(c) for m, c in module_kind.items()},
    "no_source_entries": no_source_entries,
    "sources": audit,
    "ext_url_counts": dict(url_counter),
}
with open(os.path.join(BASE, "state", "source_audit.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)

print("条目总数:", len(rows))
print("来源条目总数:", len(audit))
print("\n按类型:")
for k, v in kind_counter.most_common():
    print(f"  {k:15s} {v}")
print("\n唯一外部 URL:", len(url_counter))
print("无任何来源的条目:", len(no_source_entries))
print("\n按模块 x 类型:")
for m in sorted(module_kind):
    print(f"  {m:18s} {dict(module_kind[m])}")
