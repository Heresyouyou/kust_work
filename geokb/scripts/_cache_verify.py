# -*- coding: utf-8 -*-
"""验证前缀缓存是否被复用（只读，不落盘）。

做法：取一篇论文，模拟真实管线的连续两次调用
      ① 摘要任务（pdf_corpus 的模板）
      ② 知识点任务（kp_extract 的模板）
      两者共用 text_prep.SYSTEM_PROMPT + text_prep.prepare() 输出。
看第二次的 prompt_cache_hit_tokens 是否≈文本长度。
"""
import importlib
import os
import sys

BASE = r"D:/ZSK/geokb"
sys.path.insert(0, os.path.join(BASE, "scripts"))

import kp_extract  # noqa: E402
import pdf_corpus_ingest as pci  # noqa: E402
import text_prep  # noqa: E402
from deepseek_client import ds_chat, last_usage, load_api_key  # noqa: E402

TEXT = os.path.join(BASE, "state", "text")


def pick():
    """挑一篇中等长度、够长的中文论文（缓存才有意义）。"""
    best = None
    for f in os.listdir(TEXT):
        if not f.endswith(".txt"):
            continue
        p = os.path.join(TEXT, f)
        sz = os.path.getsize(p)
        if 60000 < sz < 110000:
            raw = open(p, encoding="utf-8", errors="ignore").read()
            if pci.detect_lang(raw) == "zh":
                return f, raw
        if best is None:
            best = (f, open(p, encoding="utf-8", errors="ignore").read())
    return best


def main():
    key = load_api_key()
    fn, raw = pick()
    body = text_prep.prepare(raw, 5000)
    print("样本: %s" % fn[:40])
    print("清洗前后: 原 %d 字符 / 送 %d 字符 / 估 %d token"
          % (len(raw), len(body), text_prep.est_tokens(body)))
    sysmsg = {"role": "system", "content": text_prep.SYSTEM_PROMPT}

    print("\n── 第 1 次：摘要任务 ──")
    msg1 = [sysmsg, {"role": "user",
                     "content": pci.USER_PROMPT_TMPL.format(text=body)}]
    c1, e1 = ds_chat(msg1, key, max_tokens=2400, thinking=False)
    u1 = last_usage()
    print("  err:", e1)
    print("  in %s | hit %s | miss %s | out %s" % (
        u1.get("prompt_tokens"), u1.get("prompt_cache_hit_tokens"),
        u1.get("prompt_cache_miss_tokens"), u1.get("completion_tokens")))

    print("\n── 第 2 次：知识点任务（应命中前缀）──")
    msg2 = [sysmsg, {"role": "user",
                     "content": kp_extract.PROMPT.format(text=body)}]
    c2, e2 = ds_chat(msg2, key, max_tokens=4000, thinking=False)
    u2 = last_usage()
    print("  err:", e2)
    print("  in %s | hit %s | miss %s | out %s" % (
        u2.get("prompt_tokens"), u2.get("prompt_cache_hit_tokens"),
        u2.get("prompt_cache_miss_tokens"), u2.get("completion_tokens")))

    hit = u2.get("prompt_cache_hit_tokens") or 0
    tot = u2.get("prompt_tokens") or 1
    print("\n  第 2 次缓存命中率: %.1f%%" % (100 * hit / tot))
    print("  第 1 次命中率: %.1f%%" % (
        100 * (u1.get("prompt_cache_hit_tokens") or 0) / (u1.get("prompt_tokens") or 1)))
    if hit > tot * 0.5:
        print("  ✅ 缓存复用生效")
    else:
        print("  ❌ 未命中 —— 检查两条 messages 前缀是否逐字节一致")


if __name__ == "__main__":
    main()
