# -*- coding: utf-8 -*-
"""深度知识化吞吐实测：用真实论文跑 N2 管线（单轮 + 行号引用），测墙钟耗时与 token，
用于推算全量整理所需时间。样本数由命令行指定，默认 3。
"""
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deepseek_client import ds_chat, load_api_key  # noqa: E402

BASE = r"D:/ZSK/geokb"
TEXTDIR = os.path.join(BASE, "state", "text")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 3

PROMPT = """你是地矿学科知识抽取器。阅读下面这篇论文全文（每行带行号），抽取其中**独立、可检索的知识点**。

要求：
- 每个知识点是一条独立结论/方法/指标/找矿标志，能单独回答一个问题。
- 最多 6 个，宁缺毋滥；不要复述摘要，不要写"本文研究了……"这类套话。
- 每条 120-260 字，中文，含关键数值与地质对象。
- 每条必须给出证据行号（如 L120-L135）。

输出严格 JSON：{"kps":[{"title":"","body":"","evidence":"L..-L..","tags":["",""]}]}

论文全文：
"""

files = sorted(glob.glob(os.path.join(TEXTDIR, "*.txt")))[:N]
key = load_api_key()
rows = []
print("样本数 =", len(files))
for i, f in enumerate(files, 1):
    raw = open(f, encoding="utf-8", errors="ignore").read()
    # 行号化 + 截断（与既有管线一致的预算）
    lines = [l for l in raw.split("\n") if l.strip()]
    body = "\n".join("L%d:%s" % (n + 1, l) for n, l in enumerate(lines))[:12000]
    t0 = time.time()
    try:
        out, meta = ds_chat(
            [{"role": "user", "content": PROMPT + body}],
            key, max_tokens=4000, timeout=180,
        )
        dt = time.time() - t0
        try:
            data = json.loads(out)
            n_kp = len(data.get("kps", []))
        except Exception:
            data = None
            n_kp = -1
        usage = meta.get("usage", {}) if isinstance(meta, dict) else {}
        rec = {"file": os.path.basename(f), "sec": round(dt, 1), "kp": n_kp,
               "in": usage.get("prompt_tokens"), "out": usage.get("completion_tokens"),
               "chars": len(body)}
        rows.append(rec)
        print(json.dumps(rec, ensure_ascii=False))
    except Exception as e:
        rows.append({"file": os.path.basename(f), "error": str(e)[:120]})
        print(rows[-1])

ok = [r for r in rows if "sec" in r]
if ok:
    avg = sum(r["sec"] for r in ok) / len(ok)
    okp = [r for r in ok if r["kp"] > 0]
    avg_kp = sum(r["kp"] for r in okp) / len(okp) if okp else 0
    print("\n" + "=" * 60)
    print("单篇平均耗时  : %.1f s" % avg)
    print("单篇平均知识点: %.1f 个" % avg_kp)
    for conc in (1, 4, 8, 16):
        total_h = avg * 748 / conc / 3600
        print("  %2d 并发 -> 748 篇全量约 %.1f 小时" % (conc, total_h))
    print("=" * 60)
    json.dump(rows, open(os.path.join(BASE, "logs", "timing_probe.json"), "w",
                         encoding="utf-8"), ensure_ascii=False, indent=1)
