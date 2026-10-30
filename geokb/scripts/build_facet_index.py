# -*- coding: utf-8 -*-
"""
分面索引构建：按 config/taxonomy.yaml 把 papers 条目归一到五分面
（deposit_type / process / method / commodity / region）。

做的事：
1. 每条目从 tags + title + key_content 匹配受控词表 → facets 写入 frontmatter；
2. tags 规范化：规范词置顶，保留具体词，总量 ≤12；
3. 产出 index/facets.json（分面 → 规范词 → 条目数/条目ID 列表）；
4. 产出 logs/off_domain_candidates.json（疑似非地学/非矿床条目，仅报告不处理）；
5. --rebuild 时触发索引重建。

用法：
    python build_facet_index.py [--rebuild]
"""
import argparse
import glob
import json
import os
import re
import subprocess
_NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
import sys
import collections

import yaml

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT = os.path.join(BASE, "content", "papers")
TAXO = os.path.join(BASE, "config", "taxonomy.yaml")
FACETS_OUT = os.path.join(BASE, "index", "facets.json")
OFFDOMAIN_OUT = os.path.join(BASE, "logs", "off_domain_candidates.json")

OFFDOMAIN_PAT = re.compile(
    r"视网膜|retinal|ophthal|眼科|临床|clinical|patient|病人|cancer|癌|"
    r"糖尿病|diabetes|心血管|cardiovascular|COVID|流行病|epidemiolog", re.I)


def load_taxonomy():
    with open(TAXO, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    table = []  # (facet, canonical, [synonyms...])
    for facet, block in doc["facets"].items():
        for canonical, syns in block["canonical"]:
            table.append((facet, canonical, [canonical] + [s for s in syns if s]))
    return table


def parse_entry(path):
    t = open(path, encoding="utf-8").read()
    m = re.match(r"---\n(.*?)\n---\n(.*)", t, re.S)
    return yaml.safe_load(m.group(1)), m.group(2), t


def section(body, name):
    m = re.search(r"## " + name + r"\n\n(.*?)(?=\n\n## |\Z)", body, re.S)
    return m.group(1).strip() if m else ""


def _commodity_hit(sym, hay_raw):
    """元素符号命中：词边界 + 排除同位素/年代学记号（U-Pb、Lu-Hf、Sn-W 等）。"""
    if any("一" <= ch <= "鿿" for ch in sym):
        return sym in hay_raw  # 中文名直接子串
    for m in re.finditer(r"(?<![A-Za-z0-9])" + re.escape(sym) + r"(?![A-Za-z0-9])", hay_raw):
        prev = hay_raw[m.start() - 1] if m.start() > 0 else ""
        nxt = hay_raw[m.end()] if m.end() < len(hay_raw) else ""
        if prev in "-/–—":
            continue
        if nxt in "-/–—" and m.end() + 1 < len(hay_raw) and hay_raw[m.end() + 1].isalpha():
            continue
        return True
    return False


def _key_content(body):
    """关键内容：兼容中文『关键内容』与英文『Key Content』两种小节标题。"""
    parts = [section(body, "关键内容"), section(body, "Key Content")]
    return "\n".join(p for p in parts if p)


def assign_facets(table, fm, body):
    hay_raw = " ".join([str(fm.get("title", "")), " ".join(fm.get("tags", [])),
                        _key_content(body)])
    hay = hay_raw.lower()
    facets = {k: [] for k in ("deposit_type", "process", "method", "commodity", "region")}
    for facet, canonical, syns in table:
        for s in syns:
            hit = (_commodity_hit(s, hay_raw) if facet == "commodity"
                   else s.lower() in hay)
            if hit:
                facets[facet].append(canonical)
                break
    # 定年方法命中的元素对不算矿种（U-Pb 定年论文 ≠ 铀矿/铅矿论文）
    DATING_ELEMENTS = {"锆石U-Pb": {"U", "Pb"}, "Re-Os": set(), "Ar-Ar": set(), "Lu-Hf": set()}
    for method, elems in DATING_ELEMENTS.items():
        if method in facets["method"]:
            facets["commodity"] = [c for c in facets["commodity"] if c not in elems]
    return facets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()

    table = load_taxonomy()
    files = sorted(glob.glob(os.path.join(CONTENT, "*.md")))
    facet_index = collections.defaultdict(lambda: collections.defaultdict(list))
    off_domain = []
    changed = 0

    for path in files:
        fm, body, raw = parse_entry(path)
        facets = assign_facets(table, fm, body)
        for facet, terms in facets.items():
            for t in terms:
                facet_index[facet][t].append(fm["id"])

        # 规范化 tags：规范词置顶 + 保留原具体词
        canon = [t for terms in facets.values() for t in terms]
        old_tags = [t for t in fm.get("tags", []) if t not in canon]
        fm["tags"] = (canon + old_tags)[:12]
        fm["facets"] = {k: v for k, v in facets.items() if v}

        # 重写 frontmatter（正文不动）
        front = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)
        new_raw = f"---\n{front}---\n{body}"
        if new_raw != raw:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_raw)
            changed += 1

        # 疑似域外条目：标题含医学/生物关键词 且 无矿床类型命中
        if not facets["deposit_type"] and OFFDOMAIN_PAT.search(str(fm.get("title", ""))):
            off_domain.append({"id": fm["id"], "title": fm.get("title"),
                               "journal": fm.get("journal"), "year": fm.get("year")})

    os.makedirs(os.path.dirname(FACETS_OUT), exist_ok=True)
    summary = {
        facet: sorted(((t, len(ids)) for t, ids in terms.items()), key=lambda x: -x[1])
        for facet, terms in facet_index.items()
    }
    with open(FACETS_OUT, "w", encoding="utf-8") as f:
        json.dump({
            "total_entries": len(files),
            "facets": {k: [{"term": t, "count": c, "ids": facet_index[k][t]}
                         for t, c in v] for k, v in summary.items()},
        }, f, ensure_ascii=False, indent=1)

    os.makedirs(os.path.dirname(OFFDOMAIN_OUT), exist_ok=True)
    with open(OFFDOMAIN_OUT, "w", encoding="utf-8") as f:
        json.dump(off_domain, f, ensure_ascii=False, indent=1)

    print(f"条目 {len(files)}，frontmatter 更新 {changed}")
    for facet, arr in summary.items():
        top = ", ".join(f"{t}({c})" for t, c in arr[:8])
        print(f"  {facet}: {len(arr)} 个规范词 | Top: {top}")
    print(f"疑似域外条目: {len(off_domain)} → {OFFDOMAIN_OUT}")

    if args.rebuild:
        print("重建 GeoKB 索引...", flush=True)
        subprocess.run([sys.executable, os.path.join(BASE, "scripts", "rebuild_index.py")],
                       creationflags=_NO_WIN,
                       timeout=1800)
        print("索引重建完成")


if __name__ == "__main__":
    main()
