# -*- coding: utf-8 -*-
"""第三轮：测试「单轮直出知识点」方案 F/F2 —— 跳过中间的"全文通读"步骤，
直接从清洗后的全文产出结构化知识点。

疑问：E 方案输出降到 1,816 是否会牺牲"通透"？
     F 方案把预算投向真正的最终产品（知识点），而不是中间摘要。
"""
import glob
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deepseek_client import load_api_key
from _clean_layers_probe import clean_layers
from _token_calibration import ENDPOINT, MODEL

TEXT_DIR = r"D:/ZSK/geokb/state/text"
OUT = r"D:/ZSK/geokb/logs/token_saving_v3.json"

SYS = """你是矿床学与地球科学知识工程师。从论文中抽取出可独立复用的知识点。
铁律：① statement 必须是完整、自足的科学陈述（其他人读它能直接用，不需要回看原文）；
② evidence 只给原始数值/观测 + 行号引用 [Lnnn]，禁止复述句子；
③ 数值必须逐字保留，含单位与误差；④ 不确定的标 confidence=C，严禁编造。只输出 JSON。"""

USER_F = """读取带行号的论文正文，输出：
{{
  "title_zh": "中文译题", "title_en": "", "journal": "", "year": "",
  "study_area": "",
  "methods": ["方法名（仪器/参数）"],
  "knowledge_points": [
    {{
      "name": "知识点名称（≤20字）",
      "statement": "完整、自足的知识陈述 100-200 字，含机制或量化关系",
      "evidence": "原始数值/观测证据 + [Lnnn] 行号",
      "method": "获得该认识的测试方法",
      "conditions": "适用条件与限定",
      "region": "适用区域/矿床类型",
      "confidence": "A/B/C",
      "tags": [""]
    }}
  ]
}}
抽 5-8 个。evidence 用行号引用，statement 用完整中文写出——这两者的分工不要混淆。

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

    runs = [
        ("F 单轮·行号·知识点", USER_F.format(text=numbered(cleaned))),
        ("F2 单轮·无行号·知识点", USER_F.format(text=cleaned.replace(
            "evidence", "evidence"))),
    ]
    # F2：把 prompt 里的行号要求换成"原文数值"
    u2 = USER_F.replace(" + [Lnnn] 行号", "").replace(
        "evidence 用行号引用，statement 用完整中文写出——这两者的分工不要混淆。",
        "evidence 直接写原始数值。").replace("带行号的", "")
    runs[1] = ("F2 单轮·无行号·知识点", u2.format(text=cleaned))

    res = []
    for name, user in runs:
        msgs = [{"role": "system", "content": SYS}, {"role": "user", "content": user}]
        r = call(msgs, key)
        u = r["usage"]
        c = r["choices"][0]["message"]["content"]
        rec = {"variant": name, "in": u["prompt_tokens"],
               "out": u["completion_tokens"],
               "finish": r["choices"][0].get("finish_reason"), "content": c}
        res.append(rec)
        print(f"{name:<24} in={u['prompt_tokens']:>6,} out={u['completion_tokens']:>5,} "
              f"{r['choices'][0].get('finish_reason')}", flush=True)

    json.dump({"file": os.path.basename(path), "runs": res},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 打印 F 的第一个知识点，检查质量是否"通透"
    try:
        obj = json.loads(res[0]["content"])
        print(f"\n=== F 产出：title={obj.get('title_zh')}")
        kps = obj.get("knowledge_points", [])
        print(f"知识点数：{len(kps)}")
        for i, kp in enumerate(kps[:2], 1):
            print(f"\n[{i}] {kp.get('name')}")
            print(f"  statement: {kp.get('statement')}")
            print(f"  evidence : {kp.get('evidence')}")
            print(f"  method   : {kp.get('method')}  conf={kp.get('confidence')}")
    except Exception as e:
        print("JSON parse fail:", e)


if __name__ == "__main__":
    main()
