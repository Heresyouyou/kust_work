# -*- coding: utf-8 -*-
"""定位：含数值率下降是「文本量不足」还是「prompt 结构变化」导致。

对同一篇论文用 4 种配置各跑一次，只看输出质量指标（只读，不落盘）：
  A 旧结构：指令在前 + [:12000] 字符          （历史基线配置，复现用）
  B 新结构 + budget 5000
  C 新结构 + budget 8000
  D 新结构 + budget 12000
"""
import json
import os
import re
import statistics
import sys

BASE = r"D:/ZSK/geokb"
sys.path.insert(0, os.path.join(BASE, "scripts"))

import kp_extract  # noqa: E402
import text_prep  # noqa: E402
from deepseek_client import ds_chat, last_usage, load_api_key  # noqa: E402

TEXT = os.path.join(BASE, "state", "text")
NUM = re.compile(r"\d")
EV = re.compile(r"L\d+")

OLD_PROMPT = """你是地矿学科知识抽取器。阅读下面这篇论文全文（每行带行号），抽取其中**独立、可检索的知识点**。

要求：
- 每个知识点是一条独立结论/方法/指标/找矿标志，能单独回答一个问题。
- 最多 6 个，宁缺毋滥；不要复述摘要，不要写"本文研究了……"这类套话。
- 每条 120-260 字，中文，含关键数值与地质对象。
- 每条必须给出证据行号（如 L120-L135）。

输出严格 JSON：{"kps":[{"title":"","body":"","evidence":"L..-L..","tags":["",""]}]}

论文全文：
"""


def old_body(raw):
    lines = [l for l in raw.split("\n") if l.strip()]
    return "\n".join("L%d:%s" % (i + 1, l) for i, l in enumerate(lines))[:12000]


def score(out):
    try:
        kps = (json.loads(out) or {}).get("kps", []) or []
    except Exception:
        m = re.search(r"\{.*\}", out or "", re.S)
        kps = (json.loads(m.group(0)) or {}).get("kps", []) or [] if m else []
    kps = kps[:6]
    if not kps:
        return dict(n=0, chars=0, num=0.0, ev=0.0)
    b = [str(k.get("body", "")) for k in kps]
    return dict(n=len(kps), chars=round(statistics.mean(len(x) for x in b)),
                num=round(sum(1 for x in b if NUM.search(x)) / len(kps), 2),
                ev=round(sum(1 for k in kps if EV.search(str(k.get("evidence", "")))) / len(kps), 2))


def pick(n=2):
    cand = []
    for f in os.listdir(TEXT):
        if not f.endswith(".txt"):
            continue
        p = os.path.join(TEXT, f)
        sz = os.path.getsize(p)
        if 70000 < sz < 130000:
            cand.append((sz, os.path.splitext(f)[0]))
    cand.sort()
    step = max(1, len(cand) // n)
    return [cand[i] for i in range(0, len(cand), step)][:n]


def main():
    key = load_api_key()
    sysm = {"role": "system", "content": text_prep.SYSTEM_PROMPT}
    print("%-14s %-18s %5s %6s %6s %6s %8s %8s" %
          ("sha", "配置", "条数", "均字", "含数值", "证据OK", "送tok", "out"))
    print("-" * 78)
    tot = {}
    for sz, sha in pick(2):
        raw = open(os.path.join(TEXT, sha + ".txt"), encoding="utf-8", errors="ignore").read()
        variants = [("A 旧/12k字符", OLD_PROMPT, old_body(raw))]
        for bg in (5000, 8000, 10000, 12000):
            body = text_prep.prepare(raw, bg)
            variants.append(("B/C/D 新 bg=%d" % bg,
                             kp_extract.PROMPT.format(text=body), body))
        for name, tmpl, body in variants:
            if name.startswith("A"):
                msgs = [{"role": "user", "content": tmpl + body}]
            else:
                msgs = [sysm, {"role": "user", "content": tmpl}]
            out, err = ds_chat(msgs, key, max_tokens=4000, thinking=False)
            u = last_usage()
            s = score(out)
            print("%-14s %-18s %5d %6d %6.2f %6.2f %8d %8s" % (
                sha[:12], name, s["n"], s["chars"], s["num"], s["ev"],
                text_prep.est_tokens(body), u.get("completion_tokens")))
            if err:
                print("      !! %s" % str(err)[:80])
            tot.setdefault(name, []).append(s)
        print()
    print("=== 按配置汇总")
    for name, lst in tot.items():
        print("  %-18s 条数 %.1f | 均字 %.0f | 含数值率 %.2f" % (
            name, statistics.mean(x["n"] for x in lst),
            statistics.mean(x["chars"] for x in lst),
            statistics.mean(x["num"] for x in lst)))
    _ = load_api_key  # noqa


if __name__ == "__main__":
    main()
