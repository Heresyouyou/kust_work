# -*- coding: utf-8 -*-
"""回填论文/知识点的发布日期 pub_date（2026-10-24）。

口径
----
- 论文：pub_date 取台账 state/papers_index.jsonl 的 date 字段（按 repo_path 归一化匹配），
  退路 = 文件名前缀 YYYY-MM-DD_，再退 = frontmatter year。
- 精度：day == '01' 视为「只有年月」→ 归一为 YYYY-MM；其余保留 YYYY-MM-DD；只有年则 YYYY。
- 知识点：pub_date = 父论文（parent）的 pub_date。
- **不改 updated**（入库日期，管理用、不展示）。

用法
----
    python scripts/migrate_pub_date.py            # 干跑，只统计
    python scripts/migrate_pub_date.py --apply    # 实际写入
"""
import glob
import json
import os
import re
import sys
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent.parent
CONTENT = BASE / "content"
LEDGER = BASE / "state" / "papers_index.jsonl"
APPLY = "--apply" in sys.argv

FM_RE = re.compile(r"^---\r?\n(.*?)\r?\n---", re.S)


def norm(p):
    p = str(p or "").replace("\\", "/")
    i = p.find("papers_repo/")
    return p[i:] if i >= 0 else p


def load_ledger():
    by_path, by_eid = {}, {}
    n = 0
    if not LEDGER.exists():
        return by_path, by_eid, n
    for ln in open(LEDGER, encoding="utf-8"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        n += 1
        dt = str(d.get("date") or "").strip()
        if not dt:
            continue
        rp = norm(d.get("repo_path"))
        if rp:
            by_path.setdefault(rp, dt)
        eid = str(d.get("eid") or "").strip()
        if eid:
            by_eid.setdefault(eid, dt)
    return by_path, by_eid, n


def normalize_date(dt, year):
    dt = str(dt or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", dt):
        y, m, d = dt.split("-")
        return "%s-%s" % (y, m) if d == "01" else dt
    if re.match(r"^\d{4}-\d{2}$", dt):
        return dt
    if re.match(r"^\d{4}$", dt):
        return dt
    y = str(year or "").strip()
    return y if re.match(r"^\d{4}$", y) else ""


def set_pub_date(text, pub):
    """在 frontmatter 写入/替换 pub_date 行，保留其余内容与换行符。"""
    m = FM_RE.match(text)
    if not m:
        return text
    fm = m.group(1)
    line = "pub_date: '%s'" % pub
    if re.search(r"^pub_date:.*$", fm, re.M):
        fm2 = re.sub(r"^pub_date:.*$", line, fm, count=1, flags=re.M)
    elif re.search(r"^year:.*$", fm, re.M):
        fm2 = re.sub(r"^(year:.*)$", lambda mm: mm.group(1) + "\n" + line, fm, count=1, flags=re.M)
    elif re.search(r"^updated:.*$", fm, re.M):
        fm2 = re.sub(r"^(updated:.*)$", lambda mm: mm.group(1) + "\n" + line, fm, count=1, flags=re.M)
    else:
        fm2 = fm + "\n" + line
    return text[:m.start(1)] + fm2 + text[m.end(1):]


def main():
    by_path, by_eid, nled = load_ledger()
    print("台账行 %d | path 键 %d | eid 键 %d | apply=%s" % (nled, len(by_path), len(by_eid), APPLY))

    stats = {"ledger": 0, "fname": 0, "year": 0, "none": 0}
    paper_pub = {}
    changed_p = 0
    for p in sorted(glob.glob(str(CONTENT / "papers" / "paper-*.md"))):
        raw = open(p, encoding="utf-8", newline="").read()
        m = FM_RE.match(raw)
        if not m:
            stats["none"] += 1
            continue
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except Exception:
            stats["none"] += 1
            continue
        if not isinstance(fm, dict):
            stats["none"] += 1
            continue
        eid = str(fm.get("id") or "")
        year = str(fm.get("year") or "")
        srcs = fm.get("sources") or []
        src = srcs[0].get("path") if (srcs and isinstance(srcs[0], dict)) else ""
        dt = by_path.get(norm(src)) or by_eid.get(eid) or ""
        kind = "ledger"
        if not dt:
            base = os.path.basename(str(src or p))
            mm = re.match(r"^(\d{4}-\d{2}-\d{2})_", base)
            if mm:
                dt = mm.group(1)
                kind = "fname"
        if not dt:
            stats["year"] += 1
            pub = normalize_date("", year)
        else:
            stats[kind] += 1
            pub = normalize_date(dt, year)
        if not pub:
            stats["none"] += 1
            continue
        paper_pub[eid] = pub
        new = set_pub_date(raw, pub)
        if new != raw:
            changed_p += 1
            if APPLY:
                open(p, "w", encoding="utf-8", newline="").write(new)

    kp_done = kp_skip = kp_changed = 0
    for p in sorted(glob.glob(str(CONTENT / "kpoints" / "kp-*.md"))):
        raw = open(p, encoding="utf-8", newline="").read()
        m = FM_RE.match(raw)
        if not m:
            kp_skip += 1
            continue
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except Exception:
            kp_skip += 1
            continue
        parent = str(fm.get("parent") or "") if isinstance(fm, dict) else ""
        pub = paper_pub.get(parent)
        if not pub:
            kp_skip += 1
            continue
        kp_done += 1
        new = set_pub_date(raw, pub)
        if new != raw:
            kp_changed += 1
            if APPLY:
                open(p, "w", encoding="utf-8", newline="").write(new)

    print("论文：台账 %d / 文件名 %d / 仅年 %d / 无 %d | 改动 %d" % (
        stats["ledger"], stats["fname"], stats["year"], stats["none"], changed_p))
    print("知识点：有父论文 %d / 跳过 %d | 改动 %d" % (kp_done, kp_skip, kp_changed))
    print("样例：", list(paper_pub.items())[:5])


if __name__ == "__main__":
    main()
