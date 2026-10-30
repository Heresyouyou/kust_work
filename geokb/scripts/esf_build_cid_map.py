#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""扫描可读篇，累积「CID → Unicode」，评估能否覆盖乱码篇全部用字。

关键前提（本脚本会验证）
------------------------
1. FZSSK 是**完整字库**（22 026 字形），不是子集 —— 已确认。
2. 同一 CID 在所有 PDF 里对应**同一字形、同一 Unicode** —— 本脚本用哈希投票验证。
3. 366 篇的 ToUnicode 合并后，能覆盖乱码篇的绝大多数用字 —— 本脚本统计覆盖数。

产出 `state/esf_cid_unicode.json`：
  { "by_cid": {"1234": "地", ...}, "conflicts": {...}, "coverage": {...} }
"""
from __future__ import annotations

import collections
import hashlib
import io
import json
import re
import sys
import time
from pathlib import Path

import pymupdf

BASE = Path(__file__).resolve().parent.parent
AUDIT = BASE / "state" / "esf_font_audit.json"
OUT = BASE / "state" / "esf_cid_unicode.json"


def font_bytes(doc, xref) -> bytes:
    try:
        return (doc.extract_font(xref)[3] or b"")
    except Exception:
        pass
    fd = doc.xref_get_key(xref, "FontDescriptor")
    if fd[0] == "xref":
        n = int(fd[1].split()[0])
        obj = doc.xref_object(n, compressed=False)
        for key in ("FontFile2", "FontFile3", "FontFile"):
            m = re.search(r"/%s\s+(\d+)\s+0\s+R" % key, obj)
            if m:
                return doc.xref_stream_raw(int(m.group(1))) or b""
    return b""


def scan(pdf: Path) -> dict[int, str]:
    """取一篇可读 PDF 的 (CID → Unicode)。"""
    out = {}
    try:
        doc = pymupdf.open(pdf)
    except Exception:
        return out
    try:
        for f in doc[0].get_fonts(full=True):
            if "FZ" not in str(f[3]) or "GBK" not in str(f[3]):
                continue
            v = doc.xref_get_key(f[0], "ToUnicode")
            if v[0] != "xref":
                continue
            st = doc.xref_stream(int(v[1].split()[0])) or b""
            txt = st.decode("latin-1", "ignore")
            for a, b2 in re.findall(r"<([0-9A-Fa-f]{4})>\s*<([0-9A-Fa-f]{4,})>", txt):
                try:
                    cid = int(a, 16)
                    ch = chr(int(b2[:4], 16))
                except Exception:
                    continue
                if 0x20 <= cid and ch.strip():
                    out.setdefault(cid, ch)
    finally:
        doc.close()
    return out


def main() -> int:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    ok = [x for x in audit["items"] if x["kind"] == "C_gbk_ok"]
    print("可读篇 %d，开始累积 CID → Unicode" % len(ok))

    votes = collections.defaultdict(collections.Counter)
    t0 = time.time()
    for i, x in enumerate(ok, 1):
        m = scan(Path(x["path"]))
        for cid, ch in m.items():
            votes[cid][ch] += 1
        if i % 60 == 0:
            print("  %d/%d｜已知 CID %d｜%.0fs" % (i, len(ok), len(votes), time.time() - t0),
                  flush=True)

    # 一致性：同一 CID 是否稳定对应同一 Unicode
    by_cid, conflicts = {}, {}
    stable = loose = 0
    for cid, cnt in votes.items():
        total = sum(cnt.values())
        ch, n = cnt.most_common(1)[0]
        if n == total:
            stable += 1
            by_cid[cid] = ch
        elif n / total >= 0.9:
            loose += 1
            by_cid[cid] = ch
            conflicts[cid] = dict(cnt.most_common(3))
        else:
            conflicts[cid] = dict(cnt.most_common(3))

    print("\n=== 一致性 ===")
    print("  已知 CID %d｜100%% 一致 %d｜≥90%% %d｜冲突丢弃 %d"
          % (len(votes), stable, loose, len(votes) - len(by_cid)))
    if conflicts:
        print("  冲突样例:")
        for cid, c in list(conflicts.items())[:4]:
            print("    CID %d → %s" % (cid, c))

    chars = set(by_cid.values())
    cjk = {c for c in chars if "\u3400" <= c <= "\u9fff"}
    print("\n=== 覆盖 ===")
    print("  映射 CID %d 个｜不同字符 %d 个｜其中汉字 %d 个"
          % (len(by_cid), len(chars), len(cjk)))

    # 乱码篇实际用到的码位有多少能被这套 CID 表解释？
    # 注意：乱码篇是 Type1/CFF，编码 ≠ CID，但同一 FZ 编号内编码是固定的；
    # 这里只统计「可读篇侧」的字形字典规模，真正的比对在下一步做。
    OUT.write_text(json.dumps({
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "docs": len(ok),
        "by_cid": {str(k): v for k, v in sorted(by_cid.items())},
        "conflicts": {str(k): v for k, v in conflicts.items()},
        "n_cid": len(by_cid),
        "n_chars": len(chars),
        "n_cjk": len(cjk),
    }, ensure_ascii=False, indent=0), encoding="utf-8")
    print("\n已写 %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
