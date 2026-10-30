# -*- coding: utf-8 -*-
"""第二轮：放开 max_tokens=16000，测真实输出长度。
并新增「精简 schema」变体 E —— 直接问：输出能被压到什么程度？

同时用同一篇跑 2 次基线，量化输出长度的方差（关系到成本估算可信度）。
"""
import glob
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deepseek_client import load_api_key
from _clean_layers_probe import clean_layers
from _token_calibration import SYSTEM_PASS1, USER_PASS1, ENDPOINT, MODEL

TEXT_DIR = r"D:/ZSK/geokb/state/text"
OUT = r"D:/ZSK/geokb/logs/token_saving_v2.json"

# E：精简 schema + 引用式。核心思路：不要求模型"复述"任何原文，
# 只要求它在原子字段上给答案，长文本一律用行号引用。
SYS_E = """你是地学知识抽取引擎。只输出 JSON。
铁律：① 禁止复述或改写原文句子；② 凡涉及原文具体表述、数据、引文，一律用行号引用 [Lnnn]；
③ 解释性判断用中文，越短越好；④ 不确定的留空，不得编造。"""

USER_E = """阅读带行号的论文正文，输出：
{{
  "title_zh": "中文译题",
  "title_en": "",
  "year": "",
  "journal": "",
  "research_question": "一句话",
  "study_area": "地点/构造位置 + 行号",
  "methods": ["方法名（仪器/参数，行号）"],
  "results_refs": ["每条结论一句话结论 + 证据行号，如：岩浆演化受xx控制 [L412][L415]"],
  "conclusions_refs": "结论要点 + 行号，控制在 80 字内",
  "keywords_zh": [""]
}}

--- 正文 ---
{text}
--- 结束 ---"""

USER_REF_FULL = """以下论文正文每行带行号。请通读后输出 JSON：
{{
  "title_en": "", "title_zh": "", "journal": "", "year": "",
  "research_question": "", "study_area": "", "geological_setting": "",
  "methods": [{{"name":"","instrument":"","params":""}}],
  "key_results": ["每个结论一句话，证据用行号 [Lnnn] 引用，禁止抄写原文句子"],
  "conclusions": "", "limitations": "", "applications": [""], "keywords_zh": [""]
}}
严格要求：需要引用原文的地方只能用行号 [Lnnn]，禁止大段抄写原文。

--- 正文 ---
{text}
--- 结束 ---"""


def call(messages, key, max_tokens=16000):
    payload = {"model": MODEL, "messages": messages, "temperature": 0.2,
               "max_tokens": max_tokens, "stream": False,
               "response_format": {"type": "json_object"}}
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))


def numbered(t):
    return "\n".join(f"[L{i}] {ln}" for i, ln in enumerate(t.split("\n"), 1))


def main():
    key = load_api_key()
    rows = sorted((len(open(f, encoding="utf-8", errors="ignore").read()), f)
                  for f in glob.glob(os.path.join(TEXT_DIR, "*.txt")))
    _, path = rows[len(rows) // 2]
    raw = open(path, encoding="utf-8", errors="ignore").read()
    _, cleaned = clean_layers(raw)
    n_clean = numbered(cleaned)

    runs = [
        ("A1 原始+标准", SYSTEM_PASS1, USER_PASS1.format(text=raw)),
        ("A2 原始+标准(重复)", SYSTEM_PASS1, USER_PASS1.format(text=raw)),
        ("D 清洗+行号+引用式", SYSTEM_PASS1, USER_REF_FULL.format(text=n_clean)),
        ("E 清洗+精简schema+引用", SYS_E, USER_E.format(text=n_clean)),
    ]
    res = []
    for name, sysp, user in runs:
        msgs = [{"role": "system", "content": sysp}, {"role": "user", "content": user}]
        r = call(msgs, key)
        u = r["usage"]
        rec = {"variant": name, "chars": len(user), "in": u["prompt_tokens"],
               "out": u["completion_tokens"],
               "finish": r["choices"][0].get("finish_reason")}
        res.append(rec)
        print(f"{name:<24} in={u['prompt_tokens']:>6,} out={u['completion_tokens']:>5,} "
              f"{r['choices'][0].get('finish_reason')}", flush=True)

    json.dump({"file": os.path.basename(path), "raw_chars": len(raw),
               "clean_chars": len(cleaned), "runs": res},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    base = [r for r in res if r["variant"].startswith("A1")][0]
    print(f"\n基线 A1: in={base['in']:,} out={base['out']:,}")
    for r in res[1:]:
        c0 = base["in"] * 1 + base["out"] * 4
        c1 = r["in"] * 1 + r["out"] * 4
        print(f"{r['variant']:<24} 输入 {100*(1-r['in']/base['in']):>5.1f}%  "
              f"输出 {100*(1-r['out']/base['out']):>6.1f}%  "
              f"相对成本 {100*(1-c1/c0):>6.1f}%")


if __name__ == "__main__":
    main()
