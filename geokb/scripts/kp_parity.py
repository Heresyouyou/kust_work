# -*- coding: utf-8 -*-
"""两种入库模式的产出对拍 / 字段一致性审计。

背景：API 链路（kp_extract.py）与 Agent 链路（agent_ingest.py kps）曾各写一套
渲染模板，导致同一篇论文两种模式产出的 frontmatter 字段不同（API 有
kp_score/kp_rule_score/kp_ai_value、summary 截 200；Agent 全缺、summary 全文）。
现已统一到 kp_store。本工具用来**证明并守住**这件事。

用法：
  venv/Scripts/python.exe scripts/kp_parity.py --sha 0e7dff06fae7
      把该篇现有知识点用两种模式各渲染一次，逐行 diff（应只差 extraction 一行）

  venv/Scripts/python.exe scripts/kp_parity.py --audit 200
      抽查现有知识点文件，统计关键字段缺失率（暴露历史遗留的不一致）

退出码：0 一致 / 1 有差异或缺失。
"""
import glob
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))

import kp_store  # noqa: E402

KP_DIR = kp_store.KPOINTS_DIR

# 统一后每条知识点必须有的 frontmatter 键（少一个就说明有路径没走 kp_store）
REQUIRED = ["id", "title", "module", "tags", "summary", "confidence", "status",
            "updated", "sources", "parent", "evidence", "extraction", "journal",
            "year", "kp_type", "kp_reuse", "kp_score", "kp_rule_score",
            "kp_ai_value", "kp_version"]


def _load_kps(sha12):
    """读现有知识点文件 → [(fm, body)]。"""
    out = []
    for f in sorted(glob.glob(str(KP_DIR / ("kp-%s-*.md" % sha12))),
                    key=lambda x: int(re.search(r"-(\d+)\.md$", x).group(1))):
        raw = open(f, encoding="utf-8", errors="ignore").read()
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
        if not m:
            continue
        import yaml
        fm = yaml.safe_load(m.group(1)) or {}
        b = re.search(r"## 知识点\s*\n+([\s\S]*?)\n+\*\*证据", m.group(2))
        out.append((fm, (b.group(1) if b else "").strip()))
    return out


def parity(sha12):
    meta = kp_store.paper_meta(sha12)
    if not meta.get("title"):
        print("[FAIL] 找不到条目 paper-%s.md" % sha12)
        return 1
    rows = _load_kps(sha12)
    if not rows:
        print("[INFO] 该篇没有知识点文件（0 条是合法结论）")
        return 0
    bad = 0
    for i, (fm, body) in enumerate(rows, 1):
        kp = {"title": fm.get("title"), "body": body, "tags": fm.get("tags") or [],
              "evidence": fm.get("evidence") or "", "type": fm.get("kp_type"),
              "reuse": fm.get("kp_reuse"), "why": fm.get("kp_why") or "",
              "confidence": fm.get("confidence"), "score": fm.get("kp_score"),
              "score_rule": fm.get("kp_rule_score"), "ai_value": fm.get("kp_ai_value")}
        norm = kp_store.normalize(kp, meta)
        if not norm:
            print("[FAIL] kp%d 归一失败" % i)
            bad += 1
            continue
        _k1, api_md = kp_store.render(meta, norm, i, "deepseek-kp")
        _k2, agent_md = kp_store.render(meta, norm, i, "agent-kp")
        d = [l for l in api_md.splitlines()
             if l not in agent_md.splitlines() or True]
        diff = [(a, b) for a, b in zip(api_md.splitlines(), agent_md.splitlines())
                if a != b]
        if len(api_md.splitlines()) != len(agent_md.splitlines()):
            print("[FAIL] kp%d 行数不同：API %d / Agent %d"
                  % (i, len(api_md.splitlines()), len(agent_md.splitlines())))
            bad += 1
            continue
        if diff and not (len(diff) == 1 and diff[0][0].startswith("extraction:")):
            print("[FAIL] kp%d 渲染差异 %d 处（应只差 extraction）" % (i, len(diff)))
            for a, b in diff[:4]:
                print("      API: %s\n    Agent: %s" % (a, b))
            bad += 1
        else:
            miss = [k for k in REQUIRED if k not in fm]
            print("  kp%d 一致%s" % (i, ("，但缺字段 " + ",".join(miss)) if miss else ""))
            if miss:
                bad += 1
    print("结论：%s" % ("全部一致" if not bad else "%d 条有问题" % bad))
    return 0 if not bad else 1


def audit(limit=200):
    files = sorted(glob.glob(str(KP_DIR / "kp-*.md")), reverse=True)[:limit]
    if not files:
        print("没有知识点文件")
        return 1
    stat = {k: 0 for k in REQUIRED}
    total = 0
    for f in files:
        raw = open(f, encoding="utf-8", errors="ignore").read()
        m = re.match(r"^---\n(.*?)\n---\n", raw, re.S)
        if not m:
            continue
        total += 1
        for k in REQUIRED:
            if re.search(r"(?m)^%s:" % re.escape(k), m.group(1)):
                stat[k] += 1
    print("抽查 %d 个知识点文件，关键字段覆盖率：" % total)
    for k in REQUIRED:
        pct = 100.0 * stat[k] / max(total, 1)
        flag = "" if pct >= 99 else ("  ← 需回填" if pct < 90 else "  ← 偏低")
        print("  %-16s %5.1f%%%s" % (k, pct, flag))
    low = [k for k in REQUIRED if 100.0 * stat[k] / max(total, 1) < 99]
    print("结论：%s" % ("字段齐全" if not low else "缺字段：" + ", ".join(low)))
    return 0 if not low else 1


def main():
    argv = sys.argv[1:]
    if "--sha" in argv:
        return parity(argv[argv.index("--sha") + 1].strip()[:12])
    if "--audit" in argv:
        i = argv.index("--audit")
        n = int(argv[i + 1]) if i + 1 < len(argv) and argv[i + 1].isdigit() else 200
        return audit(n)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
