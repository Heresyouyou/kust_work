# -*- coding: utf-8 -*-
"""复现「输出最后几个字符丢失」：用生产管线真实 prompt + 真实 max_tokens 跑批，
统计 finish_reason 与 completion_tokens 分布。

假设：max_tokens 上限过低 + 代码从不检查 finish_reason
      → 触及上限时被静默截断，残缺 JSON 被当成完整结果接受。
"""
import glob
import json
import os
import random
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deepseek_client import DS_TRACKING_ID, load_api_key  # noqa
from pdf_corpus_ingest import SYSTEM_PROMPT, USER_PROMPT_TMPL, smart_truncate
from deepseek_client import DS_MODEL, DS_ENDPOINT

TEXT_DIR = r"D:/ZSK/geokb/state/text"
OUT = r"D:/ZSK/geokb/logs/truncation_repro.json"


def call(messages, key, max_tokens):
    payload = {"model": DS_MODEL, "messages": messages, "temperature": 0.2,
               "max_tokens": max_tokens, "stream": False,
               "response_format": {"type": "json_object"}}
    req = urllib.request.Request(
        DS_ENDPOINT, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}",
                 "HTTP-Tracking-Id": DS_TRACKING_ID}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    key = load_api_key()
    files = sorted(glob.glob(os.path.join(TEXT_DIR, "*.txt")))
    # 取体量最大的 6 篇 —— 输出最容易撞上限的那一类
    files.sort(key=lambda f: len(open(f, encoding="utf-8", errors="ignore").read()))
    sample = files[-6:] + random.Random(7).sample(files[:600], 4)

    res = []
    for f in sample:
        text = smart_truncate(open(f, encoding="utf-8", errors="ignore").read())
        msgs = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_PROMPT_TMPL.format(text=text)}]
        r = call(msgs, key, 1200)          # ← 生产管线的真实上限
        u = r["usage"]
        c = r["choices"][0]["message"]["content"]
        fin = r["choices"][0].get("finish_reason")
        tail = repr(c[-30:])
        okjson = c.strip().endswith("}")
        rec = {"file": os.path.basename(f)[:12], "out": u["completion_tokens"],
               "finish": fin, "chars": len(c), "closed": okjson, "tail": tail}
        res.append(rec)
        flag = " ← 截断!" if fin == "length" or not okjson else ""
        print(f"{rec['file']}  out={u['completion_tokens']:>5}  chars={len(c):>5}  "
              f"finish={fin:<8} closed={okjson}{flag}", flush=True)

    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    bad = [r for r in res if r["finish"] == "length" or not r["closed"]]
    print(f"\n截断/未闭合：{len(bad)}/{len(res)}")
    outs = [r["out"] for r in res]
    print(f"completion_tokens: min={min(outs)} max={max(outs)} "
          f"上限1200的占用率最大 {100*max(outs)/1200:.0f}%")
    for b in bad:
        print(f"  {b['file']} tail={b['tail']}")


if __name__ == "__main__":
    main()
