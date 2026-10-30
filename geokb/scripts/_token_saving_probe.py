# -*- coding: utf-8 -*-
"""省 token 四变体对照实测。

变体：
  A  L0 原始全文       + 标准 prompt     （基线，已测得 25,175 in / 7,717 out）
  B  L1 剔尾部文献后   + 标准 prompt     → 验证清洗的 token 收益
  C  L4 全清洗+合并行  + 标准 prompt     → 验证 token 密度提升
  D  L4 + 行号 + 引用式输出              → 验证输出能被压到多少
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
OUT = r"D:/ZSK/geokb/logs/token_saving_abcd.json"

USER_REF = """以下论文正文每行带行号。请通读后输出 JSON：
{{
  "title_en": "", "title_zh": "", "journal": "", "year": "",
  "research_question": "",
  "study_area": "",
  "geological_setting": "",
  "methods": [{{"name":"","instrument":"","params":""}}],
  "key_results": ["每个结论一句话，结论本身用中文写出，原文数值证据放在行号引用里，形如 [L412]"],
  "conclusions": "",
  "limitations": "",
  "applications": [""],
  "keywords_zh": [""]
}}
严格要求：
- 除 keywords_zh 外，任何需要引用原文的地方只能用行号 [Lnnn]，禁止大段抄写原文句子。
- "title_en"/"journal"/"year" 为短字段，可直接写出。
- 解释与转述用中文，数值必须精确保留。

--- 正文开始 ---
{text}
--- 正文结束 ---"""


def call(messages, key, max_tokens=8000):
    payload = {"model": MODEL, "messages": messages, "temperature": 0.2,
               "max_tokens": max_tokens, "stream": False,
               "response_format": {"type": "json_object"}}
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"}, method="POST")
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.loads(r.read().decode("utf-8"))


def numbered(text):
    return "\n".join(f"[L{i}] {ln}" for i, ln in enumerate(text.split("\n"), 1))


def main():
    key = load_api_key()
    rows = sorted((len(open(f, encoding="utf-8", errors="ignore").read()), f)
                  for f in glob.glob(os.path.join(TEXT_DIR, "*.txt")))
    _, path = rows[len(rows) // 2]
    raw = open(path, encoding="utf-8", errors="ignore").read()
    layers, cleaned = clean_layers(raw)
    # L1 单独取（只剔尾部）
    import re
    cut = len(raw)
    from _clean_layers_probe import RE_REFS
    for m in RE_REFS.finditer(raw):
        if m.start() > len(raw) * 0.45:
            cut = min(cut, m.start())
    l1 = raw[:cut]

    variants = [
        ("A 原始全文+标准", raw, USER_PASS1.format(text=raw)),
        ("B 剔尾部+标准", l1, USER_PASS1.format(text=l1)),
        ("C 全清洗+标准", cleaned, USER_PASS1.format(text=cleaned)),
        ("D 清洗+行号+引用式", numbered(cleaned), USER_REF.format(text=numbered(cleaned))),
    ]
    res = []
    for name, text, user in variants:
        msgs = [{"role": "system", "content": SYSTEM_PASS1},
                {"role": "user", "content": user}]
        r = call(msgs, key)
        u = r["usage"]
        # 行号自身带来的 token 开销要算进去（属于 D 的成本）
        rec = {"variant": name, "chars": len(text),
               "in": u["prompt_tokens"], "out": u["completion_tokens"],
               "finish": r["choices"][0].get("finish_reason"),
               "chars_per_token": round(len(text) / u["prompt_tokens"], 2)}
        res.append(rec)
        print(f"{name:<20} chars={len(text):>7,}  in={u['prompt_tokens']:>6,}  "
              f"out={u['completion_tokens']:>5,}  c/t={rec['chars_per_token']}  "
              f"{r['choices'][0].get('finish_reason')}", flush=True)

    json.dump({"file": os.path.basename(path), "raw_chars": len(raw),
               "layers": layers, "variants": res},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nsaved ->", OUT)
    base = res[0]
    for r in res[1:]:
        print(f"{r['variant']:<20} 输入省 {100*(1-r['in']/base['in']):>5.1f}%  "
              f"输出省 {100*(1-r['out']/base['out']):>5.1f}%  "
              f"总省 {100*(1-(r['in']*1+r['out']*4)/(base['in']*1+base['out']*4)):>5.1f}%")


if __name__ == "__main__":
    main()
