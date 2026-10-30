# -*- coding: utf-8 -*-
"""全库系统性体检：字段完整性 / 正文残缺 / 内容与索引一致性 / 模块调度。

覆盖 content/ 下全部模块（不只 papers）。
"""
import glob
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict

BASE = r"D:/ZSK/geokb"
CONTENT = os.path.join(BASE, "content")
DB = os.path.join(BASE, "index", "geokb.db")
FACETS = os.path.join(BASE, "index", "facets.json")
import yaml  # noqa

FRONT = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
REQUIRED = ("id", "title", "module", "tags", "summary")
TERMINATORS = "。！？.!?】)》」：:;；\"'"
LIST_OPEN = re.compile(r"(?m)^\s*[-*+]\s+\S")
CODE_FENCE = "```"


def scan_content():
    rows = []
    for path in sorted(glob.glob(os.path.join(CONTENT, "*", "*.md"))):
        raw = open(path, encoding="utf-8").read()
        m = FRONT.match(raw)
        rel = os.path.relpath(path, CONTENT).replace("\\", "/")
        if not m:
            rows.append({"rel": rel, "issue": [("FATAL", "无 frontmatter")]})
            continue
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except Exception as e:
            rows.append({"rel": rel, "issue": [("FATAL", f"YAML 解析失败: {str(e)[:40]}")]})
            continue
        body = m.group(2)
        issues = []

        # ① frontmatter 必需字段
        for k in REQUIRED:
            v = fm.get(k)
            if v in (None, "", [], {}):
                issues.append(("MISSING", f"缺字段 {k}"))
        # ② id 与文件名一致性
        eid = str(fm.get("id", ""))
        stem = os.path.splitext(os.path.basename(path))[0]
        if eid and eid != stem:
            issues.append(("WARN", f"id 与文件名不一致: {eid} vs {stem}"))
        # ③ module 与目录一致性
        mod_dir = os.path.basename(os.path.dirname(path))
        mod_fm = str(fm.get("module", ""))
        if mod_fm and mod_fm != mod_dir:
            issues.append(("MISSING", f"module 与目录不符: FM={mod_fm} DIR={mod_dir}"))
        # ④ summary 残缺（句读结尾）
        s = str(fm.get("summary", "") or "").strip()
        if s and s[-1] not in TERMINATORS:
            issues.append(("TRUNC", f"summary 非句读结尾 ...{s[-24:]}"))
        # ⑤ body 残缺
        b = body.strip()
        if len(b) < 20:
            issues.append(("TRUNC", "正文过短/为空"))
        else:
            if b.count(CODE_FENCE) % 2 == 1:
                issues.append(("TRUNC", "代码块围栏未闭合"))
            if b[-1] not in TERMINATORS and not LIST_OPEN.match(b.split("\n")[-1] or ""):
                tail = b[-30:].replace("\n", " ")
                issues.append(("TRUNC", f"正文非句读结尾 ...{tail}"))
            if "（未能提取）" in b:
                pass  # 允许，单独统计
        rows.append({"rel": rel, "module": mod_dir, "id": eid,
                     "summary": s, "body": b, "issue": issues, "fm": fm})
    return rows


def main():
    rows = scan_content()
    print("=" * 76)
    print("GeoKB 全库系统性体检")
    print("=" * 76)
    by_mod = Counter(r.get("module", "?") for r in rows)
    print(f"content 条目总数：{len(rows)}")
    for m, c in by_mod.most_common():
        print(f"  {m:<22}{c:>5}")

    # ── ① 残缺统计 ──
    print("\n【一、残缺与字段问题】")
    kinds = Counter()
    examples = defaultdict(list)
    for r in rows:
        for sev, msg in r.get("issue", []):
            kinds[(sev, msg.split(":")[0])] += 1
            if len(examples[(sev, msg.split(":")[0])]) < 3:
                examples[(sev, msg.split(":")[0])].append((r["rel"][:34], msg))
    if not kinds:
        print("  ✓ 无问题")
    for (sev, k), c in sorted(kinds.items(), key=lambda x: -x[1]):
        print(f"  [{sev}] {k:<28}{c:>5} 条")
        for rel, msg in examples[(sev, k)][:2]:
            print(f"        · {rel}  {msg[:66]}")

    # ── ② 索引一致性 ──
    print("\n【二、内容 vs 索引一致性】")
    conn = sqlite3.connect(DB)
    db_rows = conn.execute("SELECT id, module FROM entries").fetchall()
    conn.close()
    db_ids = {i for i, _ in db_rows}
    fs_ids = {r["id"] for r in rows if r.get("id")}
    db_mod = Counter(m for _, m in db_rows)
    fs_mod = Counter(r["module"] for r in rows if r.get("module"))

    missing_idx = fs_ids - db_ids     # 有文件无索引
    stale_idx = db_ids - fs_ids       # 有索引无文件
    print(f"  content 条目 {len(fs_ids)} / DB 条目 {len(db_ids)}")
    print(f"  未被索引（新文件未重建）: {len(missing_idx)}")
    for i in sorted(missing_idx)[:12]:
        print(f"        - {i}")
    print(f"  悬空索引（文件已删）  : {len(stale_idx)}")
    for i in sorted(stale_idx)[:12]:
        print(f"        - {i}")

    print("\n  模块级计数差异：")
    allm = sorted(set(db_mod) | set(fs_mod))
    for m in allm:
        d, f = db_mod.get(m, 0), fs_mod.get(m, 0)
        flag = "" if d == f else "  ← 不一致"
        print(f"    {m:<22} DB={d:<5} content={f:<5}{flag}")

    # ── ③ 分面索引 ──
    print("\n【三、分面索引 facets.json】")
    if os.path.exists(FACETS):
        try:
            fj = json.load(open(FACETS, encoding="utf-8"))
            keys = fj.keys() if isinstance(fj, dict) else []
            print(f"  存在，顶层键：{list(keys)[:8]}")
        except Exception as e:
            print(f"  解析失败：{e}")
    else:
        print("  不存在（需 build_facet_index.py 重跑）")

    # ── ④ 端口/服务侧模块注册 ──
    print("\n【四、模块调度一致性（ taxa / 程序引用）】")
    try:
        sys.path.insert(0, os.path.join(BASE, "server"))
        import ingest
        known = set(ingest.SOURCE_TIER_BY_MODULE)
        actual = set(fs_mod)
        unknown = actual - known
        if unknown:
            print(f"  ⚠ 以下模块未在 source_tier 映射中（降级为按 confidence 推断）:")
            for m in sorted(unknown):
                print(f"      - {m}  ({fs_mod[m]} 条)")
        else:
            print("  ✓ 全部模块已有来源分级映射")
        # 打印推断结果
        tier = Counter()
        for r in rows:
            if r.get("fm"):
                tier[ingest.infer_source_tier(
                    r["fm"].get("module", ""),
                    r["fm"].get("confidence", "C"))] += 1
        print(f"  推断分级分布：{dict(tier)}")
    except Exception as e:
        print(f"  检查失败：{e}")


if __name__ == "__main__":
    main()
