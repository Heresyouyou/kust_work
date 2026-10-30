# -*- coding: utf-8 -*-
"""思考强度 × token 消耗 实测。

官方：deepseek-flash 思考模式默认开启且 effort=high；reasoning tokens 按输出计费。
本脚本在同一任务上对比 4 档设置，拆分 reasoning_tokens 与 content tokens，
回答"降低思考强度能省多少"。
"""
import glob
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deepseek_client import load_api_key, DS_MODEL, DS_ENDPOINT
from _clean_layers_probe import clean_layers
from _token_saving_probe3 import SYS, USER_F

TEXT_DIR = r"D:/ZSK/geokb/state/text"
OUT = r"D:/ZSK/geokb/logs/thinking_effort_probe.json"

# (标签, 是否思考, reasoning_effort)
CONFIGS = [
    ("① thinking=disabled", False, None),
    ("② enabled + effort=minimal", True, "minimal"),
    ("③ enabled + effort=low", True, "low"),
    ("④ enabled + effort=high(默认)", True, "high"),
]


def call(messages, key, thinking, effort, max_tokens=16000):
    payload = {"model": DS_MODEL, "messages": messages,
               "max_tokens": max_tokens, "stream": False,
               "response_format": {"type": "json_object"},
               "thinking": {"type": "enabled" if thinking else "disabled"}}
    if thinking and effort:
        payload["reasoning_effort"] = effort
    if not thinking:
        payload["temperature"] = 0.2
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
    numbered = "\n".join(f"[L{i}] {ln}" for i, ln in enumerate(cleaned.split("\n"), 1))
    msgs = [{"role": "system", "content": SYS},
            {"role": "user", "content": USER_F.format(text=numbered)}]

    res = []
    print(f"{'设置':<30}{'总out':>8}{'思考':>7}{'正文':>7}{'%思考':>8}  finish")
    print("─" * 72)
    for label, thinking, effort in CONFIGS:
        try:
            r = call(msgs, key, thinking, effort)
        except Exception as e:
            print(f"{label:<30} ERR {type(e).__name__}: {str(e)[:60]}")
            continue
        u = r["usage"]
        det = u.get("completion_tokens_details") or {}
        reasoning = det.get("reasoning_tokens", 0) or 0
        total = u["completion_tokens"]
        body = total - reasoning
        fin = r["choices"][0].get("finish_reason")
        rec = {"config": label, "total": total, "reasoning": reasoning,
               "body": body, "finish": fin,
               "prompt_tokens": u["prompt_tokens"]}
        res.append(rec)
        print(f"{label:<30}{total:>8}{reasoning:>7}{body:>7}"
              f"{100*reasoning/max(1,total):>7.0f}%  {fin}", flush=True)

    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if len(res) >= 2:
        base = [r for r in res if "disabled" in r["config"]][0]
        print("\n相对「完全关闭思考」的成本差（输出 ¥4/M）：")
        for r in res:
            d = (r["total"] - base["total"]) * 4 / 1e6
            print(f"  {r['config']:<30} 差异 {d*1e6:>+8.0f} 元/百万篇 "
                  f"（{100*(r['total']/base['total']-1):>+6.1f}%）")
    print("\nsaved ->", OUT)


if __name__ == "__main__":
    main()
