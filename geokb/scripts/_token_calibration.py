# -*- coding: utf-8 -*-
"""Token 消耗实测校准（一次性，只读外部语料，不写知识库）。

目的：用真实 API 的 usage 字段校准"字符数 → token"系数，并验证
      ① 全文一次性输入的真实 input token
      ② 深度知识点提取的真实 output token
      ③ 多轮复用前言时 DeepSeek 前缀缓存的命中率（决定成本能否降一个量级）
"""
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deepseek_client import DS_TRACKING_ID, load_api_key

ENDPOINT = "https://api.deepseek.com/chat/completions"
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
TEXT_DIR = r"D:/ZSK/geokb/state/text"
OUT = r"D:/ZSK/geokb/logs/token_calibration.json"

SYSTEM_PASS1 = """你是资深矿床学与地球科学知识工程师。你的任务是把一篇英文学术论文
"整理通透"：不仅提炼摘要，还要保留可复用的科学事实、方法参数、数值证据与限定条件。
输出严格 JSON，不得输出解释性文字。所有中文表述使用地学规范术语。
数值必须原样保留（含单位与误差范围），不得臆造。"""

USER_PASS1 = """以下是论文全文。请通读后输出 JSON：
{{
  "title_en": "英文原标题",
  "title_zh": "中文译题",
  "journal": "", "year": "",
  "research_question": "要解决的科学问题",
  "study_area": "研究区（含经纬度/构造位置，如原文有）",
  "geological_setting": "地质背景",
  "methods": [{{"name":"方法/技术","instrument":"仪器型号","params":"关键参数"}}],
  "datasets": "数据类型与样本量",
  "key_results": ["带数值的核心结论，每条含具体数值"],
  "conclusions": "结论与地质意义",
  "limitations": "适用限制/不确定性",
  "applications": ["应用场景"],
  "keywords_zh": ["中文关键词 5-8 个"]
}}

--- 论文全文开始 ---
{text}
--- 论文全文结束 ---"""

# 第二轮：复用上一轮完整前言（含全文），仅换任务 → 应命中前缀缓存
USER_PASS2 = """基于上文这篇论文，抽取本次研究的【独立知识点】。
每个知识点必须满足：① 是一条可独立复用的陈述；② 带原文数值证据；③ 标注适用条件。
输出 JSON：
{{
  "knowledge_points": [
    {{
      "name": "知识点名称（≤20字）",
      "statement": "完整的知识陈述（100-200字）",
      "evidence": "支撑数值/观测证据",
      "method": "获得该认识的测试方法",
      "conditions": "适用条件与限定",
      "region": "适用区域/矿床类型",
      "confidence": "A/B/C",
      "tags": ["标签"]
    }}
  ]
}}
请抽取 4-8 个。"""


def pick_samples():
    rows = []
    for f in glob.glob(os.path.join(TEXT_DIR, "*.txt")):
        t = open(f, encoding="utf-8", errors="ignore").read()
        rows.append((len(t), f))
    rows.sort()
    n = len(rows)
    picks = {}
    for q in (10, 25, 50, 75, 90, 95, 99):
        i = min(n - 1, int(n * q / 100))
        picks[q] = rows[i]
    return picks


def call(messages, key, max_tokens):
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}",
                 "HTTP-Tracking-Id": DS_TRACKING_ID}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    key = load_api_key()
    picks = pick_samples()
    results = []
    print(f"model={MODEL}", flush=True)
    for q, (nchars, path) in sorted(picks.items()):
        text = open(path, encoding="utf-8", errors="ignore").read()
        msgs = [{"role": "system", "content": SYSTEM_PASS1},
                {"role": "user", "content": USER_PASS1.format(text=text)}]
        t0 = time.time()
        r1 = call(msgs, key, 2600)
        u1 = r1.get("usage", {})
        c1 = r1["choices"][0]["message"]["content"]

        msgs2 = msgs + [{"role": "assistant", "content": c1},
                        {"role": "user", "content": USER_PASS2}]
        r2 = call(msgs2, key, 2600)
        u2 = r2.get("usage", {})
        dt = time.time() - t0

        rec = {
            "pct": q, "chars": nchars, "file": os.path.basename(path),
            "p1": u1, "p2": u2, "latency_sec": round(dt, 1),
        }
        results.append(rec)
        print(f"[p{q}] chars={nchars:,} "
              f"P1 in={u1.get('prompt_tokens')} out={u1.get('completion_tokens')} "
              f"(hit={u1.get('prompt_cache_hit_tokens')} miss={u1.get('prompt_cache_miss_tokens')}) | "
              f"P2 in={u2.get('prompt_tokens')} out={u2.get('completion_tokens')} "
              f"(hit={u2.get('prompt_cache_hit_tokens')} miss={u2.get('prompt_cache_miss_tokens')}) "
              f"{dt:.0f}s", flush=True)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(results, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\nsaved ->", OUT)


if __name__ == "__main__":
    main()
