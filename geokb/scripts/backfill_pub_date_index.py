# -*- coding: utf-8 -*-
"""索引回填 pub_date（2026-10-24）。

只做两件事，**不重建、不重新嵌入**：
1. `init_schema()` 给旧库补 `pub_date` 列（ALTER TABLE）。
2. 扫 content/papers 与 content/kpoints 的 md，把 frontmatter 的 pub_date 写回 entries。

用法：python scripts/backfill_pub_date_index.py
"""
import glob
import re
import sys
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "server"))

import ingest  # noqa: E402

FM = re.compile(r"^---\r?\n(.*?)\r?\n---", re.S)


def main():
    conn = ingest.get_db()
    ingest.init_schema(conn)  # 补 pub_date 列
    cols = [r[1] for r in conn.execute("PRAGMA table_info(entries)")]
    print("pub_date 列存在:", "pub_date" in cols)
    n = hit = 0
    for mod in ("papers", "kpoints"):
        for p in glob.glob(str(BASE / "content" / mod / "*.md")):
            raw = open(p, encoding="utf-8", newline="").read()
            m = FM.match(raw)
            if not m:
                continue
            try:
                fm = yaml.safe_load(m.group(1)) or {}
            except Exception:
                continue
            eid = str(fm.get("id") or "")
            if not eid:
                continue
            pub = str(fm.get("pub_date") or "")
            n += 1
            cur = conn.execute("UPDATE entries SET pub_date=? WHERE id=?", (pub, eid))
            if cur.rowcount:
                hit += 1
    conn.commit()
    print("扫描 %d 个条目文件，更新 %d 行" % (n, hit))
    # 抽样
    for eid in ("paper-0708cd922f74", "kp-0708cd922f74-1"):
        r = conn.execute("SELECT id,pub_date,updated FROM entries WHERE id=?", (eid,)).fetchone()
        print(" ", r)
    conn.close()


if __name__ == "__main__":
    main()
