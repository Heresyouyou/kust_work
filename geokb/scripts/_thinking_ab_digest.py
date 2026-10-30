# -*- coding: utf-8 -*-
"""摘要任务（pdf_corpus_ingest）thinking 开关 A/B —— 只读，不写 llm_raw、不写条目。

对比：thinking=minimal（现状） vs disabled。
指标：JSON 解析成功、字段非空率、关键字段平均字数、输出 token。
"""
import json
import os
import re
import statistics
import sys

BASE = r"D:/ZSK/geokb"
sys.path.insert(0, os.path.join(BASE, "scripts"))

from deepseek_client import ds_chat, extract_json, last_usage, load_api_key  # noqa: E402
from pdf_corpus_ingest import (DS_MAX_TOKENS, EN_PROMPT_TMPL, SYSTEM_PROMPT,  # noqa: E402
                              USER_PROMPT_TMPL, detect_lang, smart_truncate)

TEXT = os.path.join(BASE, "state", "text")
# 需要判断"有没有实质内容"的字段（中英两套 schema 取并集）
CORE = ["title_en", "key_content", "key_content_en", "core_methods",
        "core_methods_en", "study_area", "study_area_en", "deposit_types",
        "deposit_types_en", "tags", "tags_en", "commodities"]
LONG = ["key_content", "key_content_en", "abstract_en"]


def run(text, key, thinking):
    lang = detect_lang(text)
    tmpl = EN_PROMPT_TMPL if lang == "en" else USER_PROMPT_TMPL
    content, err = ds_chat(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": tmpl.format(text=smart_truncate(text))}],
        key, max_tokens=DS_MAX_TOKENS, thinking=thinking)
    u = last_usage()
    obj = extract_json(content or "") or {}
    return obj, lang, err, u


def score(obj):
    """字段非空率（只看本次 schema 实际要求的字段）+ 关键长字段字数。"""
    keys = [k for k in CORE if k in obj]
    nonempty = sum(1 for k in keys if obj.get(k) not in (None, "", [], {}))
    longs = [len(str(obj.get(k) or "")) for k in LONG if k in obj]
    return dict(fields=len(keys), rate=round(nonempty / len(keys), 2) if keys else 0,
                longs=round(statistics.mean(longs)) if longs else 0)


def pick(n=3):
    fs = sorted((os.path.getsize(os.path.join(TEXT, f)), f)
                for f in os.listdir(TEXT) if f.endswith(".txt"))
    step = max(1, len(fs) // (n + 2))
    return [fs[i][1] for i in range(1, len(fs), step)][:n]


def main():
    key = load_api_key()
    print("%-14s %-9s %-4s %6s %6s %6s %8s %8s" %
          ("sha", "版本", "语种", "字段数", "非空率", "关键字数", "in", "out"))
    print("-" * 74)
    acc = {}
    for fn in pick(3):
        text = open(os.path.join(TEXT, fn), encoding="utf-8", errors="ignore").read()
        for tag, th in (("minimal", True), ("disabled", False)):
            obj, lang, err, u = run(text, key, th)
            s = score(obj)
            a = acc.setdefault(tag, dict(rate=0, longs=0, out=0, n=0, fail=0))
            a["rate"] += s["rate"]; a["longs"] += s["longs"]; a["n"] += 1
            a["out"] += (u or {}).get("completion_tokens") or 0
            if err or not obj:
                a["fail"] += 1
            print("%-14s %-9s %-4s %6d %6.2f %6d %8s %8s" % (
                fn[:12], tag, lang, s["fields"], s["rate"], s["longs"],
                (u or {}).get("prompt_tokens") or "-", (u or {}).get("completion_tokens") or "-"))
            if err:
                print("      !! %s" % str(err)[:90])
        print()
    print("=== 汇总")
    for tag in ("minimal", "disabled"):
        a = acc[tag]
        print("  %-9s 非空率 %.2f | 关键字段字数 %.0f | 输出/次 %d | 失败 %d"
              % (tag, a["rate"] / a["n"], a["longs"] / a["n"], a["out"] / a["n"], a["fail"]))
    if acc["minimal"]["out"]:
        r = acc["disabled"]["out"] / acc["minimal"]["out"]
        print("  → disabled 输出为 minimal 的 %.0f%%，节省 %.0f%%" % (r * 100, (1 - r) * 100))


if __name__ == "__main__":
    main()
