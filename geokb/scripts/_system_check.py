# -*- coding: utf-8 -*-
"""GeoKB 全库严格系统检查 —— 覆盖全部条目，不漏项。

检查维度：
  A 文件结构：frontmatter 可解析 / 必需字段 / id·文件名·module 一致性
  B 内容质量：正文残缺 / 空占位 / 重复正文 / 重复标题 / 异常长度
  C 知识粒度：一条是否等于一个独立知识点（用户明确要求）
  D 索引一致：content vs DB / 悬空索引 / 未索引文件 / facets 同步
"""
import glob
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict

import yaml

BASE = r"D:/ZSK/geokb"
CONTENT = os.path.join(BASE, "content")
DB = os.path.join(BASE, "index", "geokb.db")
FACETS = os.path.join(BASE, "index", "facets.json")

FRONT = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
REQUIRED = ("id", "title", "module", "tags", "summary", "confidence", "status")
TERM = "。！？.!?）)】》」』：:;；…\"'*|"
PLACEHOLDER = ("（未能提取）", "待补充", "null", "None", "N/A")


def check_all():
    rows = []
    for path in sorted(glob.glob(os.path.join(CONTENT, "*", "*.md"))):
        rel = os.path.relpath(path, CONTENT).replace("\\", "/")
        raw = open(path, encoding="utf-8").read()
        m = FRONT.match(raw)
        rec = {"rel": rel, "path": path, "issues": []}
        if not m:
            rec["issues"].append(("FATAL", "无 frontmatter"))
            rows.append(rec)
            continue
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except Exception as e:
            rec["issues"].append(("FATAL", f"YAML 不可解析: {str(e)[:60]}"))
            rows.append(rec)
            continue
        body = m.group(2).strip()
        rec.update({"fm": fm, "body": body,
                    "module": os.path.basename(os.path.dirname(path))})

        # ── A 文件结构 ──
        for k in REQUIRED:
            if fm.get(k) in (None, "", [], {}):
                rec["issues"].append(("MISSING", f"缺字段 {k}"))
        stem = os.path.splitext(os.path.basename(path))[0]
        if fm.get("id") and str(fm["id"]) != stem:
            rec["issues"].append(("WARN", "id≠文件名"))
        if fm.get("module") and str(fm["module"]) != rec["module"]:
            rec["issues"].append(("MISSING", "module≠所在目录"))

        # ── B 内容质量 ──
        s = str(fm.get("summary", "") or "").strip()
        if s and s[-1] not in TERM:
            rec["issues"].append(("TRUNC", "summary 非句读结尾"))
        if len(body) < 30:
            rec["issues"].append(("TRUNC", "正文过短/空"))
        else:
            if body.count("```") % 2 == 1:
                rec["issues"].append(("TRUNC", "代码块未闭合"))
            tail = body.rstrip()
            last_line = tail.split("\n")[-1].strip()
            is_list = bool(re.match(r"^([-*+]|\d+\.)\s", last_line))
            is_table = last_line.startswith("|")
            if not is_list and not is_table and tail[-1] not in TERM:
                rec["issues"].append(("TRUNC", f"正文非句读结尾 …{tail[-22:]}"))
            for ph in PLACEHOLDER:
                if ph in body:
                    rec["issues"].append(("PLACEHOLDER", f"含占位符 {ph}"))
                    break
        rec["body_hash"] = hashlib.md5(body.encode("utf-8")).hexdigest()
        rec["title"] = str(fm.get("title", ""))
        rows.append(rec)
    return rows


def report(rows):
    print("=" * 78)
    print("GeoKB 全库严格系统检查")
    print("=" * 78)
    print(f"扫描条目：{len(rows)}")

    print("\n【A/B 条目级问题】")
    kinds = Counter()
    ex = defaultdict(list)
    for r in rows:
        for sev, msg in r["issues"]:
            key = (sev, msg.split(":")[0].split("…")[0])
            kinds[key] += 1
            if len(ex[key]) < 2:
                ex[key].append((r["rel"][:36], msg))
    if not kinds:
        print("  无问题")
    for (sev, k), c in sorted(kinds.items(), key=lambda x: -x[1]):
        print(f"  [{sev}] {k:<24}{c:>5} 条")
        for rel, msg in ex[(sev, k)][:1]:
            print(f"         · {rel}  {msg[:60]}")

    # 重复
    print("\n【B2 重复内容】")
    bh, th = defaultdict(list), defaultdict(list)
    for r in rows:
        if r.get("body_hash"):
            bh[r["body_hash"]].append(r["rel"])
        if r.get("title"):
            th[r["title"]].append(r["rel"])
    dup_b = {k: v for k, v in bh.items() if len(v) > 1}
    dup_t = {k: v for k, v in th.items() if len(v) > 1}
    print(f"  正文完全相同: {len(dup_b)} 组")
    for k, v in list(dup_b.items())[:4]:
        print(f"         · {v[:3]}")
    print(f"  标题完全相同: {len(dup_t)} 组")
    for k, v in list(dup_t.items())[:4]:
        print(f"         · {k[:34]} -> {v[:2]}")

    # ── C 知识粒度 ──
    print("\n【C 知识粒度：一条 = 一个独立知识点？】")
    by_mod = defaultdict(list)
    for r in rows:
        if r.get("module"):
            by_mod[r["module"]].append(r)
    for mod, rs in sorted(by_mod.items(), key=lambda x: -len(x[1])):
        multi = 0
        for r in rs:
            b = r.get("body", "")
            t = str(r.get("title", ""))
            # 术语表打包特征：标题含「（N 条）」或正文含多个二级/三级条目
            if re.search(r"（\d+\s*条）", t):
                multi += 1
            elif mod == "papers" and len(re.findall(r"^## ", b, re.M)) >= 4:
                multi += 1     # 论文条目含 4+ 个章节，属「论文级」而非「知识点级」
        print(f"  {mod:<20}{len(rs):>5} 条，其中「多知识点打包」≈ {multi} 条"
              f"  ({100*multi/len(rs):.0f}%)")

    # ── D 索引一致 ──
    print("\n【D 索引一致性】")
    conn = sqlite3.connect(DB)
    db_rows = conn.execute("SELECT id, module, source_tier FROM entries").fetchall()
    conn.close()
    db_ids = {i for i, _, _ in db_rows}
    fs_ids = {r["fm"]["id"] for r in rows if r.get("fm") and r["fm"].get("id")}
    print(f"  content {len(fs_ids)} 条 / DB {len(db_ids)} 条")
    miss = fs_ids - db_ids
    stale = db_ids - fs_ids
    print(f"  未索引（文件存在但库无）: {len(miss)}")
    for i in sorted(miss)[:8]:
        print(f"         - {i}")
    print(f"  悬空索引（库有但文件无）: {len(stale)}")
    for i in sorted(stale)[:8]:
        print(f"         - {i}")
    db_mod = Counter(m for _, m, _ in db_rows)
    fs_mod = Counter(r["module"] for r in rows if r.get("module"))
    tm = Counter(t for _, _, t in db_rows)
    print(f"  source_tier 分布: {dict(tm)}")
    diff = [(m, db_mod.get(m, 0), fs_mod.get(m, 0))
            for m in sorted(set(db_mod) | set(fs_mod))
            if db_mod.get(m, 0) != fs_mod.get(m, 0)]
    if diff:
        print("  模块级差异:")
        for m, d, f in diff:
            print(f"         {m:<20} DB={d} content={f}")
    else:
        print("  模块级计数: 全部一致")

    print("\n【E 分面索引】")
    # 注意：build_facet_index.py 设计上只覆盖 papers 模块，
    # 因此基准是 papers 条目数，而非全库条目数（否则会误报）。
    n_papers = len(by_mod.get("papers", []))
    if os.path.exists(FACETS):
        try:
            fj = json.load(open(FACETS, encoding="utf-8"))
            tot = fj.get("total_entries")
            print(f"  facets.json total_entries={tot}；papers 模块={n_papers} 条")
            if tot == n_papers:
                print("  ✓ 一致")
            else:
                print("  ⚠ 不一致，需重跑 build_facet_index.py --rebuild")
        except Exception as e:
            print(f"  解析失败: {e}")
    else:
        print("  facets.json 不存在")


if __name__ == "__main__":
    report(check_all())
