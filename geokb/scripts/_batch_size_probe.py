# -*- coding: utf-8 -*-
"""验证用户观察："要 DeepSeek 输出太多，它就只给个大概意思"。

方法：同一篇论文、同一模板，只改变"要求抽取 N 个知识点"，
      测量单知识点的 statement 长度与完整度。
若 N 增大 → statement 变短/敷衍，则证实存在输出预算自适应压缩。
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
OUT = r"D:/ZSK/geokb/logs/batch_n_probe.json"

SYS_T = ("你是矿床学与地球科学知识工程师。输出严格 JSON。"
         "statement 必须是完整、自足、含具体机制的中文陈述，不得敷衍概括。")

TMPL = """从以下论文抽取 {n} 个独立知识点，输出：
{{
  "knowledge_points": [
    {{{{
      "name": "≤20字",
      "statement": "完整自足的知识陈述，须含机制或量化关系，100-200字",
      "evidence": "原始数值/观测 + 行号 [Lnnn]",
      "method": "测试方法",
      "tags": [""]
    }}}}
  ]
}}
共 {n} 个，缺一不可。

--- 正文 ---
{text}
--- 结束 ---"""


def call(msgs, key, n=6000):
    payload = {"model": DS_MODEL, "messages": msgs, "max_tokens": n,
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
    text = "\n".join(f"[L{i}] {ln}" for i, ln in enumerate(cleaned.split("\n"), 1))

    res = []
    print(f"{'要求N':>6}{'实际N':>7}{'总out':>8}{'单点均长':>10}{'最短':>7}{'敷衍率':>9}")
    print("─" * 52)
    for n in (1, 2, 3, 5, 8):
        msgs = [{"role": "system", "content": SYS_T},
                {"role": "user", "content": TMPL.format(n=n, text=text)}]
        r = call(msgs, key)
        u = r["usage"]
        c = r["choices"][0]["message"]["content"]
        kps = []
        try:
            from deepseek_client import extract_json
            kps = (extract_json(c) or {}).get("knowledge_points", [])
        except Exception:
            pass
        lens = [len(k.get("statement", "")) for k in kps if k.get("statement")]
        avg = sum(lens) / len(lens) if lens else 0
        fluff = sum(1 for L in lens if L < 60) / len(lens) if lens else 0
        # 敷衍判据：statement 短于 60 字（低于要求的 100-200）
        res.append({"n": n, "got": len(kps), "out": u["completion_tokens"],
                    "avg": round(avg), "min": min(lens) if lens else 0,
                    "fluff": round(fluff, 2)})
        print(f"{n:>6}{len(kps):>7}{u['completion_tokens']:>8}"
              f"{avg:>10.0f}{(min(lens) if lens else 0):>7}{100*fluff:>8.0f}%",
              flush=True)
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nsaved ->", OUT)
    print("\n结论指向：若 N 增大后「单点均长」下降且「敷衍率」上升，")
    print("          则证实存在输出预算自适应压缩 → 必须分批小量处理。")


if __name__ == "__main__":
    main()
