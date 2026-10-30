# -*- coding: utf-8 -*-
"""thinking 开关对知识点抽取质量的影响（A/B，只读不落盘）。

方法：取 5 篇已入库、已有 kp 条目的论文，
      ① 读现有 kp 文件（历史 minimal 产物）作为基线；
      ② 用 thinking=disabled 重跑同一提示词、同一段文本；
      ③ 对比条数/长度/数值含率/证据合规率/标签重合度。
不写 content 目录，只输出对比表，零副作用。
"""
import glob
import json
import os
import re
import sys
import statistics

BASE = r"D:/ZSK/geokb"
sys.path.insert(0, os.path.join(BASE, "scripts"))

from deepseek_client import ds_chat, last_usage, load_api_key  # noqa: E402
from kp_extract import PROMPT, paper_meta  # noqa: E402

TEXT = os.path.join(BASE, "state", "text")
KP = os.path.join(BASE, "content", "kpoints")
NUM = re.compile(r"\d")
EV = re.compile(r"L\d+")


def load_existing(sha12):
    """读现有 kp 文件，返回 [{title, body, ev, tags}]。"""
    out = []
    for f in sorted(glob.glob(os.path.join(KP, "kp-%s-*.md" % sha12))):
        raw = open(f, encoding="utf-8").read()
        m = re.match(r"^---\n(.*?)\n---\n?(.*)$", raw, re.S)
        if not m:
            continue
        try:
            import yaml
            fm = yaml.safe_load(m.group(1)) or {}
        except Exception:
            continue
        body = m.group(2)
        mm = re.search(r"## 知识点\s*\n\s*\n(.*?)(?:\n\*\*证据|\Z)", body, re.S)
        out.append({
            "title": str(fm.get("title", "")),
            "body": (mm.group(1).strip() if mm else ""),
            "ev": str(fm.get("evidence", "")),
            "tags": [str(t) for t in (fm.get("tags") or [])],
        })
    return out


def run_variant(sha, key, thinking):
    """同一段文本、同一提示词，跑指定 thinking 配置，返回模型原始输出统计。

    2026-10-19 缓存改造后：文本走 text_prep.prepare（与摘要任务同源），
    提示词为「全文在前、指令在后」的新结构。
    """
    import text_prep
    from kp_extract import SYSTEM_PROMPT
    raw = open(os.path.join(TEXT, sha + ".txt"), encoding="utf-8", errors="ignore").read()
    body = text_prep.prepare(raw, 5000)
    out, err = ds_chat(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": PROMPT.format(text=body)}],
        key, max_tokens=4000, thinking=thinking, timeout=180)
    u = last_usage()
    try:
        kps = (json.loads(out) or {}).get("kps", []) or []
    except Exception:
        m = re.search(r"\{.*\}", out or "", re.S)
        try:
            kps = (json.loads(m.group(0)) or {}).get("kps", []) or [] if m else []
        except Exception:
            kps = []
    norm = [{"title": str(k.get("title", "")), "body": str(k.get("body", "")),
             "ev": str(k.get("evidence", "")),
             "tags": [str(t) for t in (k.get("tags") or [])]} for k in kps[:6]]
    return norm, err, u


def stats(kps):
    if not kps:
        return dict(n=0, chars=0, num_ratio=0.0, ev_ok=0.0, tags=0.0, empty=0)
    bodies = [k["body"] for k in kps]
    return dict(
        n=len(kps),
        chars=round(statistics.mean(len(b) for b in bodies)),
        num_ratio=round(sum(1 for b in bodies if NUM.search(b)) / len(kps), 2),
        ev_ok=round(sum(1 for k in kps if k["ev"] and EV.search(k["ev"])) / len(kps), 2),
        tags=round(statistics.mean(len(k["tags"]) for k in kps), 1),
        empty=sum(1 for b in bodies if not b.strip()),
    )


def pick(n=5):
    """挑 5 篇：已有 >=5 条知识点、正文长度中等（避开极端）。"""
    cand = []
    for f in glob.glob(os.path.join(TEXT, "*.txt")):
        sha = os.path.splitext(os.path.basename(f))[0]
        cur = load_existing(sha[:12])
        if len(cur) < 5:
            continue
        sz = os.path.getsize(f)
        if not (30000 < sz < 120000):
            continue
        cand.append((sz, sha, cur))
    cand.sort()
    step = max(1, len(cand) // n)
    return [cand[i] for i in range(0, len(cand), step)][:n]


def run_disabled(sha, key):
    raw = open(os.path.join(TEXT, sha + ".txt"), encoding="utf-8", errors="ignore").read()
    lines = [l for l in raw.split("\n") if l.strip()]
    body = "\n".join("L%d:%s" % (i + 1, l) for i, l in enumerate(lines))[:12000]
    out, err = ds_chat([{"role": "user", "content": PROMPT + body}],
                       key, max_tokens=4000, thinking=False, timeout=180)
    u = last_usage()
    try:
        kps = (json.loads(out) or {}).get("kps", []) or []
    except Exception:
        m = re.search(r"\{.*\}", out or "", re.S)
        try:
            kps = (json.loads(m.group(0)) or {}).get("kps", []) or [] if m else []
        except Exception:
            kps = []
    norm = [{"title": str(k.get("title", "")), "body": str(k.get("body", "")),
             "ev": str(k.get("evidence", "")),
             "tags": [str(t) for t in (k.get("tags") or [])]} for k in kps[:6]]
    return norm, err, u


def main():
    key = load_api_key()
    sample = pick(5)
    print("样本 %d 篇（同文本、同提示词，仅 thinking 不同）\n" % len(sample))
    hdr = ("%-14s %-10s %5s %6s %6s %6s %5s %5s %8s %8s" %
           ("sha", "版本", "条数", "均字", "含数值", "证据OK", "标签", "空文", "in", "out"))
    print(hdr)
    print("-" * len(hdr))
    acc = {"minimal": dict(n=0, chars=0, num=0, tags=0, out=0), "disabled": dict(n=0, chars=0, num=0, tags=0, out=0)}
    rows = []
    for sz, sha, cur in sample:
        res = {}
        for tag, th in (("minimal", True), ("disabled", False)):
            kps, err, u = run_variant(sha, key, th)
            s = stats(kps)
            res[tag] = s
            a = acc[tag]
            a["n"] += s["n"]; a["chars"] += s["chars"]; a["num"] += s["num_ratio"]
            a["tags"] += s["tags"]; a["out"] += (u or {}).get("completion_tokens") or 0
            print("%-14s %-10s %5d %6d %6.2f %6.2f %5.1f %5d %8s %8s" % (
                sha[:12], tag, s["n"], s["chars"], s["num_ratio"], s["ev_ok"], s["tags"],
                s["empty"], (u or {}).get("prompt_tokens") or "-",
                (u or {}).get("completion_tokens") or "-"))
            if err:
                print("      !! %s" % str(err)[:100])
        rows.append({"sha": sha[:12], **res})
        print()
    n = len(sample)
    print("=== 汇总（%d 篇均值）" % n)
    for tag in ("minimal", "disabled"):
        a = acc[tag]
        print("  %-9s 条数 %.1f | 均字 %.0f | 含数值率 %.2f | 模型标签 %.1f | 输出 %d tok"
              % (tag, a["n"] / n, a["chars"] / n, a["num"] / n, a["tags"] / n, a["out"]))
    j = acc["disabled"]["out"] / acc["minimal"]["out"] if acc["minimal"]["out"] else 0
    print("  → disabled 输出为 minimal 的 %.0f%%，节省 %.0f%%" % (j * 100, (1 - j) * 100))
    json.dump(rows, open(os.path.join(BASE, "logs", "thinking_ab.json"), "w",
                         encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
