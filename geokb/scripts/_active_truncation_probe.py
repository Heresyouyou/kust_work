# -*- coding: utf-8 -*-
"""验证「模型主动截断」假说（区别于 token 超限）。

判定标准：
  · token 超限  → finish_reason='length' 且 completion_tokens 顶到 max_tokens
  · 主动截断    → finish_reason='stop'、远未到 max_tokens，但内容明显短于要求

实验：固定「每个知识点必须写满 300 字」，只改变要求数量 N。
      若 N 增大后 finish_reason 仍为 stop，但单条字数显著不足 300，
      则证实模型存在输出预算自适应压缩（即用户观察到的现象）。
"""
import glob
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deepseek_client import load_api_key, DS_MODEL, DS_ENDPOINT, DS_EFFORT
from _clean_layers_probe import clean_layers

TEXT_DIR = r"D:/ZSK/geokb/state/text"
OUT = r"D:/ZSK/geokb/logs/active_truncation_probe.json"

SYS = ("你是地学知识工程师。严格遵守用户对篇幅的要求，"
       "每个知识点的 statement 必须写满指定字数，不得因总数多而压缩任何一条。")

TMPL = """从以下论文抽取 {n} 个知识点，输出 JSON：
{{
  "knowledge_points": [
    {{{{
      "name": "≤20字",
      "statement": "【严格要求：必须写满 300 字，不得缩写】完整自足的知识陈述，含机制、量化关系与地质意义",
      "evidence": "原始数值 + 行号 [Lnnn]"
    }}}}
  ]
}}
必须恰好 {n} 个，每个 statement 都写满 300 字。

--- 正文 ---
{text}
--- 结束 ---"""

MAX_TOKENS = 16000      # 给足预算，排除 token 超限干扰


def call(msgs, key):
    payload = {"model": DS_MODEL, "messages": msgs, "max_tokens": MAX_TOKENS,
               "stream": False, "response_format": {"type": "json_object"},
               "thinking": {"type": "enabled"}, "reasoning_effort": DS_EFFORT}
    req = urllib.request.Request(
        DS_ENDPOINT, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    key = load_api_key()
    files = sorted(glob.glob(os.path.join(TEXT_DIR, "*.txt")),
                   key=lambda f: len(open(f, encoding="utf-8", errors="ignore").read()))
    raw = open(files[len(files) // 2], encoding="utf-8", errors="ignore").read()
    _, cleaned = clean_layers(raw)
    text = "\n".join(f"[L{i}] {l}" for i, l in enumerate(cleaned.split("\n"), 1))

    print(f"max_tokens={MAX_TOKENS}（给足预算）；要求每个知识点写满 300 字")
    print(f"{'要求N':>6}{'得到':>6}{'finish':>9}{'总out':>8}{'预算占用':>9}"
          f"{'单点均长':>10}{'达标率':>9}")
    print("─" * 62)
    res = []
    for n in (1, 2, 4, 8):
        msgs = [{"role": "system", "content": SYS},
                {"role": "user", "content": TMPL.format(n=n, text=text)}]
        r = call(msgs, key)
        u = r["usage"]
        fin = r["choices"][0].get("finish_reason")
        c = r["choices"][0]["message"]["content"]
        from deepseek_client import extract_json
        kps = (extract_json(c) or {}).get("knowledge_points", [])
        lens = [len(k.get("statement", "")) for k in kps if k.get("statement")]
        avg = sum(lens) / len(lens) if lens else 0
        pass_rate = (sum(1 for L in lens if L >= 240) / len(lens)) if lens else 0.0
        res.append({"n": n, "got": len(kps), "finish": fin,
                    "out": u["completion_tokens"], "avg": round(avg, 1),
                    "pass_rate": round(pass_rate, 2)})
        print(f"{n:>6}{len(kps):>6}{fin:>9}{u['completion_tokens']:>8}"
              f"{100*u['completion_tokens']/MAX_TOKENS:>8.0f}%"
              f"{avg:>10.0f}{100*pass_rate:>8.0f}%", flush=True)
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nsaved ->", OUT)
    print("\n判定：若 finish 全为 stop 且预算占用远低于 100%，但单点均长随 N 下降，")
    print("      则证实『主动截断/压缩』（非 token 超限）。")


if __name__ == "__main__":
    main()
