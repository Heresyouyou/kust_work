# -*- coding: utf-8 -*-
"""验证用户疑问：是不是「单个字段内容过长」就会触发截断？

对照组设计（全部 max_tokens=16000，确保不是 token 超限）：
  A: 1 条，要求每条 300 字   → 总需求小
  B: 1 条，要求每条 1200 字  → 单字段极长，但总需求仍小
  C: 8 条，要求每条 300 字   → 总需求大
  D: 1 条，要求每条 2500 字  → 单字段超长，逼近单条极限

判定：
  · 若 B/D 能写满而 C 被压缩 → 触发条件是「总输出量」，不是「单字段长度」
  · 若 B/D 也被压缩         → 触发条件是「单字段长度」
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
OUT = r"D:/ZSK/geokb/logs/field_length_probe.json"
MAX_TOKENS = 16000

SYS = ("你是地学知识工程师。必须严格满足用户对字数的要求，不得自行缩短。")

TMPL = """从以下论文抽取 {n} 个知识点，输出 JSON：
{{
  "knowledge_points": [
    {{{{
      "name": "≤20字",
      "statement": "【必须写满 {words} 字，不得缩写】完整自足的知识陈述，含机制、量化关系与地质意义",
      "evidence": "原始数值 + 行号 [Lnnn]"
    }}}}
  ]
}}
必须恰好 {n} 个，每个 statement 都写满 {words} 字。

--- 正文 ---
{text}
--- 结束 ---"""


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

    cases = [("A 1条×300字", 1, 300), ("B 1条×1200字", 1, 1200),
             ("C 8条×300字", 8, 300), ("D 1条×2500字", 1, 2500)]
    print(f"{'场景':<16}{'要求总字数':>10}{'实际总字':>9}{'完成度':>8}"
          f"{'finish':>8}{'out':>7}{'预算%':>7}")
    print("─" * 70)
    res = []
    for label, n, words in cases:
        msgs = [{"role": "system", "content": SYS},
                {"role": "user", "content": TMPL.format(n=n, words=words, text=text)}]
        r = call(msgs, key)
        u = r["usage"]
        fin = r["choices"][0].get("finish_reason")
        c = r["choices"][0]["message"]["content"]
        from deepseek_client import extract_json
        kps = (extract_json(c) or {}).get("knowledge_points", [])
        lens = [len(k.get("statement", "")) for k in kps if k.get("statement")]
        got_total = sum(lens)
        need_total = n * words
        rate = got_total / need_total if need_total else 0
        res.append({"case": label, "need": need_total, "got": got_total,
                    "rate": round(rate, 2), "finish": fin,
                    "out": u["completion_tokens"]})
        print(f"{label:<16}{need_total:>10}{got_total:>9}{100*rate:>7.0f}%"
              f"{fin:>8}{u['completion_tokens']:>7}"
              f"{100*u['completion_tokens']/MAX_TOKENS:>6.0f}%", flush=True)
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nsaved ->", OUT)
    print("\n判定：比较 B/D（单字段长）与 C（总需求大）的完成度，即可定位触发条件。")


if __name__ == "__main__":
    main()
