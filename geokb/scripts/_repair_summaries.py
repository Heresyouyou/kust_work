# -*- coding: utf-8 -*-
"""修复 frontmatter 的 summary —— 零 API 成本。

同时解决两类问题：
  A. summary 被旧的 [:120] 硬截断（97.6% 条目段落残缺）
  B. summary 裸标量含 ": " 导致 YAML 解析失败（227 条从未入库）

关键教训（上一版在此翻车）：summary 在 YAML 里是**折叠标量**，可能跨多行
缩进续行。只替换首行会把续行变成孤儿缩进标量，直接炸掉整个 frontmatter。
因此必须「吞掉所有续行」后整体重写，并用 width 足够大的 safe_dump 避免再折叠。
"""
import glob
import os
import re
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdf_corpus_ingest import sentence_safe_summary

CONTENT = r"D:/ZSK/geokb/content"
KEY_RE = re.compile(r"## 关键内容\n\n(.*?)\n\n## ", re.S)
KEY_RE2 = re.compile(r"## (?:Key Content|关键内容)\n\n(.*?)\n\n## ", re.S)


def split_front(raw):
    """返回 (lines, end_idx) —— end_idx 是结束 --- 的行号；无则 (-1)。"""
    lines = raw.split("\n")
    if not lines or lines[0].strip() != "---":
        return lines, -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines, i
    return lines, -1


def find_summary_block(lines, end):
    """定位 summary 起始行与其所有折叠续行，返回 (start, stop)。"""
    start = None
    for j in range(1, end):
        if lines[j].startswith("summary:"):
            start = j
            break
    if start is None:
        return None, None
    stop = start + 1
    while stop < end:
        ln = lines[stop]
        if ln and ln[0] in " \t":
            s = ln.strip()
            # 形如 "key: value" 的缩进行视为新键，停止吞并
            m = re.match(r"^([A-Za-z_][\w-]*)\s*:", s)
            if m:
                break
            stop += 1
        else:
            break
    return start, stop


def ensure_quoted(lines, end, field):
    """若 field 是裸标量且值中含 ': '（YAML 会误判为嵌套映射），加引号重写。

    与 summary 同样处理折叠续行。返回新的 (lines, end)；未改动返回 (None, None)。
    """
    start = None
    for j in range(1, end):
        if lines[j].startswith(field + ":"):
            start = j
            break
    if start is None:
        return None, None
    rest = lines[start][len(field) + 1:].strip()
    if rest.startswith(('"', "'", "[", "{", "|", ">")):
        return None, None          # 已是引号/结构化标量，无需处理
    # 吞续行
    stop = start + 1
    while stop < end and lines[stop] and lines[stop][0] in " \t":
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:", lines[stop].strip())
        if m:
            break
        stop += 1
    value = rest
    for j in range(start + 1, stop):
        value += " " + lines[j].strip()
    if ": " not in value:
        return None, None          # 无歧义，保持原样
    block = yaml.safe_dump({field: value}, allow_unicode=True,
                           default_flow_style=False, width=10 ** 6).rstrip("\n")
    new_lines = lines[:start] + block.split("\n") + lines[stop:]
    return new_lines, len(new_lines) if False else new_lines.index("---", 1)


def repair_file(path, dry=False):
    raw = open(path, encoding="utf-8").read()
    lines, end = split_front(raw)
    if end < 0:
        return "NO_FRONTMATTER", None
    # ① 先修 title 等裸标量歧义（含 ': ' 会让整个 frontmatter 解析失败）
    changed_title = False
    for fld in ("title",):
        nl, ne = ensure_quoted(lines, end, fld)
        if nl is not None:
            lines, end = nl, ne
            changed_title = True
    start, stop = find_summary_block(lines, end)
    if start is None:
        return "NO_SUMMARY", None

    # ① 现有 summary：优先用解析值，解析失败则从折叠行文本拼接
    front_text = "\n".join(lines[1:end])
    parsed = None
    try:
        parsed = yaml.safe_load(front_text)
    except Exception:
        parsed = None
    if parsed is not None and isinstance(parsed, dict) and "summary" in parsed:
        current = parsed.get("summary") or ""
    else:
        current = lines[start][len("summary:"):].strip()
        for j in range(start + 1, stop):
            current += " " + lines[j].strip()

    # ② papers 有完整「关键内容」正文 -> 重算，修复 [:120] 截断；
    #    其他模块没有该章节 -> 只保留原值，本步仅修 YAML 语法
    body = "\n".join(lines[end + 1:])
    km = KEY_RE.search(body) or KEY_RE2.search(body)
    kc = km.group(1).strip() if km else ""
    if kc and kc != "（未能提取）":
        new_sum = sentence_safe_summary(kc)
        mode = "REBUILT"
    else:
        new_sum = current
        mode = "SYNTAX_ONLY" + ("+TITLE" if changed_title else "")
    # width 足够大 => 单行输出，杜绝再次折叠
    block = yaml.safe_dump({"summary": new_sum}, allow_unicode=True,
                           default_flow_style=False, width=10 ** 6).rstrip("\n")
    new_lines = lines[:start] + block.split("\n") + lines[stop:]
    out = "\n".join(new_lines)

    # 回读校验：必须能被 yaml 解析，且 summary 字段正确
    try:
        fm = yaml.safe_load(re.match(r"^---\n(.*?)\n---\n", out, re.S).group(1))
    except Exception as e:
        return "VERIFY_FAIL", str(e)[:80]
    got = fm.get("summary")
    if got != new_sum:
        return "MISMATCH", f"{repr(got)[:40]} != {repr(new_sum)[:40]}"
    if not dry:
        open(path, "w", encoding="utf-8").write(out)
    return "OK:" + mode, len(new_sum)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(CONTENT, "*", "*.md")))
    if args.limit:
        files = files[:args.limit]
    from collections import Counter
    stat = Counter()
    failures = []
    for f in files:
        code, info = repair_file(f, dry=args.dry)
        stat[code] += 1
        if not code.startswith("OK"):
            failures.append((os.path.relpath(f, CONTENT), code, info))
    print("=" * 70)
    print(f"{'[DRY-RUN] ' if args.dry else ''}处理 {len(files)} 个文件")
    print("=" * 70)
    for k, v in stat.most_common():
        print(f"  {k:<22}{v:>6}")
    for rel, code, info in failures[:10]:
        print(f"  ! {rel[:44]}  {code}  {info}")


if __name__ == "__main__":
    main()
