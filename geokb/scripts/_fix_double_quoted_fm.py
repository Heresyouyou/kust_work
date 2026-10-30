# -*- coding: utf-8 -*-
"""修复「双重 YAML 编码」的历史条目（一次性）。

现象：`title: "\\"边家大院…\\""` —— 字符串本来没有引号，却被写成了两层。
成因：`build_kp_md` / `build_entry_md` 先 `_yml()` 一次，`dump_fm()` 又编码一次。
代码已修（2026-10-23），本脚本回填已有的 582 个文件。

判据（只动确认为双编码的字段）：
  YAML 值按 JSON 解码后，结果**首尾都是引号** → 去掉外层一层，再重新编码。
  正常标题不会整体被引号包住，故不会误伤。

用法：
    python scripts/_fix_double_quoted_fm.py --dry-run
    python scripts/_fix_double_quoted_fm.py
"""
import argparse
import glob
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYS = ("title", "summary", "kp_why", "remarks")


def fix_line(line):
    m = re.match(r"^(%s):\s*(.+)$" % "|".join(KEYS), line)
    if not m:
        return None
    key, raw = m.group(1), m.group(2)
    v = None
    try:
        v = json.loads(raw)
    except Exception:  # noqa: BLE001
        # build_facet_index 用 yaml.safe_dump 重写过 frontmatter，
        # 值为「含双引号的字符串」时会写成单引号形式 '"…"'，json 解不了
        try:
            import yaml
            v = yaml.safe_load(raw)
        except Exception:  # noqa: BLE001
            return None
    if not (isinstance(v, str) and len(v) > 1 and v[0] == '"' and v[-1] == '"'):
        return None
    return "%s: %s" % (key, json.dumps(v[1:-1], ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(BASE, "content", "papers", "*.md"))
                   + glob.glob(os.path.join(BASE, "content", "kpoints", "*.md")))
    changed, touched = 0, []
    for f in files:
        raw = open(f, encoding="utf-8", errors="ignore").read()
        # 只处理 frontmatter 区
        if not raw.startswith("---"):
            continue
        end = raw.find("\n---", 3)
        if end < 0:
            continue
        head, body = raw[:end], raw[end:]
        lines = head.split("\n")
        new, hit = [], 0
        for ln in lines:
            r = fix_line(ln)
            if r:
                new.append(r)
                hit += 1
            else:
                new.append(ln)
        if not hit:
            continue
        changed += hit
        touched.append((f, hit))
        if not args.dry_run:
            open(f, "w", encoding="utf-8").write("\n".join(new) + body)
        if args.limit and len(touched) >= args.limit:
            break

    print("%s：%d 个文件、%d 个字段需要修复" % (
        "预览" if args.dry_run else "已修复", len(touched), changed))
    for f, n in touched[:8]:
        print("  %s  (%d)" % (os.path.basename(f), n))
    if not args.dry_run:
        with open(os.path.join(BASE, "state", "kp_fix_quotes_files.json"), "w",
                  encoding="utf-8") as fh:
            json.dump([os.path.basename(f)[3:15] if "/kpoints/" in f.replace("\\", "/")
                       else os.path.basename(f)[6:18] for f, _ in touched], fh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
