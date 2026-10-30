"""台账规范化：去掉重复空行、统一 CRLF（一次性修复，2026-10-25）。

事故原因：一条临时脚本用 `for ln in open(...)` 迭代（保留行尾 \\n），
再 `"\\n".join(out)` 回写 → 每条记录后多出一个空行（2375 → 4749 行）。
JSONL 解析不受影响（空行被跳过），但行数翻倍、体积虚高。

    python scripts/normalize_ledger.py            # 干跑
    python scripts/normalize_ledger.py --apply
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
LEDGER = BASE / "state" / "papers_index.jsonl"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    raw = LEDGER.read_text(encoding="utf-8", errors="ignore").splitlines()
    keep, bad = [], 0
    for ln in raw:
        s = ln.strip()
        if not s:
            continue
        try:
            json.loads(s)
        except Exception:
            bad += 1
            keep.append(s)       # 保住可疑行，宁可留着也不删数据
            continue
        keep.append(s)

    print("原 %d 行 | 有效记录 %d | 无法解析 %d | apply=%s"
          % (len(raw), len(keep), bad, a.apply))
    if not a.apply:
        return

    bak = LEDGER.with_suffix(".jsonl.bak-normalize")
    if not bak.exists():
        bak.write_text("\n".join(raw) + "\n", encoding="utf-8")
    tmp = LEDGER.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
    tmp.replace(LEDGER)
    print("已写 %d 行（CRLF，备份 %s）" % (len(keep), bak.name))


if __name__ == "__main__":
    main()
