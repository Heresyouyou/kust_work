# -*- coding: utf-8 -*-
"""缓存改造后的质量回归：新（全文前置+清洗+共用文本）vs 旧（12000 字符硬截断）。

只读，不落盘。每篇按真实管线顺序跑「摘要 → 知识点」，顺便拿到第二次的缓存命中率。
"""
import glob
import json
import os
import re
import statistics
import sys

BASE = r"D:/ZSK/geokb"
sys.path.insert(0, os.path.join(BASE, "scripts"))

import kp_extract  # noqa: E402
import pdf_corpus_ingest as pci  # noqa: E402
import text_prep  # noqa: E402
from deepseek_client import ds_chat, last_usage, load_api_key  # noqa: E402

TEXT = os.path.join(BASE, "state", "text")
KP = os.path.join(BASE, "content", "kpoints")
NUM = re.compile(r"\d")
EV = re.compile(r"L\d+")


def old_kps(sha12):
    """读旧结构产出的 kp 文件作为基线。"""
    out = []
    for f in sorted(glob.glob(os.path.join(KP, "kp-%s-*.md" % sha12))):
        raw = open(f, encoding="utf-8").read()
        m = re.match(r"^---\n(.*?)\n---\n?(.*)$", raw, re.S)
        if not m:
            continue
        import yaml
        fm = yaml.safe_load(m.group(1)) or {}
        mm = re.search(r"## 知识点\s*\n\s*\n(.*?)(?:\n\*\*证据|\Z)", m.group(2), re.S)
        out.append({"body": (mm.group(1).strip() if mm else ""),
                    "ev": str(fm.get("evidence", "")),
                    "tags": [str(t) for t in (fm.get("tags") or [])]})
    return out


def score(kps):
    if not kps:
        return dict(n=0, chars=0, num=0.0, ev=0.0)
    b = [k["body"] for k in kps]
    return dict(n=len(kps), chars=round(statistics.mean(len(x) for x in b)),
                num=round(sum(1 for x in b if NUM.search(x)) / len(kps), 2),
                ev=round(sum(1 for k in kps if k["ev"] and EV.search(k["ev"])) / len(kps), 2))


def pick(n=3):
    cand = []
    for f in glob.glob(os.path.join(TEXT, "*.txt")):
        sha = os.path.splitext(os.path.basename(f))[0]
        if len(old_kps(sha[:12])) < 5:
            continue
        sz = os.path.getsize(f)
        if 40000 < sz < 130000:
            cand.append((sz, sha))
    cand.sort()
    step = max(1, len(cand) // n)
    return [cand[i] for i in range(0, len(cand), step)][:n]


def main():
    key = load_api_key()
    sysm = {"role": "system", "content": text_prep.SYSTEM_PROMPT}
    print("%-14s %-8s %5s %6s %6s %6s %7s %7s" %
          ("sha", "版本", "条数", "均字", "含数值", "证据OK", "送tok", "缓存%"))
    print("-" * 70)
    agg = {"old": [0, 0, 0], "new": [0, 0, 0]}
    for sz, sha in pick(3):
        raw = open(os.path.join(TEXT, sha + ".txt"), encoding="utf-8", errors="ignore").read()
        body = text_prep.prepare(raw, 5000)
        tk = text_prep.est_tokens(body)

        # ① 摘要（真实管线第一步）
        c1, e1 = ds_chat([sysm, {"role": "user",
                                 "content": pci.USER_PROMPT_TMPL.format(text=body)}],
                         key, max_tokens=2400, thinking=False)
        # ② 知识点（应命中前缀）
        c2, e2 = ds_chat([sysm, {"role": "user",
                                 "content": kp_extract.PROMPT.format(text=body)}],
                         key, max_tokens=4000, thinking=False)
        u2 = last_usage()
        hit = 100 * (u2.get("prompt_cache_hit_tokens") or 0) / (u2.get("prompt_tokens") or 1)

        try:
            kps = (json.loads(c2) or {}).get("kps", []) or []
        except Exception:
            m = re.search(r"\{.*\}", c2 or "", re.S)
            kps = (json.loads(m.group(0)) or {}).get("kps", []) or [] if m else []
        new = [{"body": str(k.get("body", "")), "ev": str(k.get("evidence", "")),
                "tags": [str(t) for t in (k.get("tags") or [])]} for k in kps[:6]]

        a, b = score(old_kps(sha[:12])), score(new)
        for tag, s in (("old", a), ("new", b)):
            print("%-14s %-8s %5d %6d %6.2f %6.2f %7s %7s" % (
                sha[:12], tag, s["n"], s["chars"], s["num"], s["ev"],
                tk if tag == "new" else "-", "%.0f" % hit if tag == "new" else "-"))
            agg[tag][0] += s["n"]; agg[tag][1] += s["chars"]; agg[tag][2] += s["num"]
        if e1 or e2:
            print("      !! %s | %s" % (str(e1)[:60], str(e2)[:60]))
        print()
    n = 3
    print("=== 均值")
    for tag in ("old", "new"):
        x = agg[tag]
        print("  %-4s 条数 %.1f | 均字 %.0f | 含数值率 %.2f" % (tag, x[0] / n, x[1] / n, x[2] / n))


if __name__ == "__main__":
    main()
