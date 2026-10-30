# -*- coding: utf-8 -*-
"""补测：不限制输出长度时，两轮的自然 output token 是多少（决定输出成本）。"""
import glob
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deepseek_client import load_api_key
from _token_calibration import SYSTEM_PASS1, USER_PASS1, USER_PASS2, ENDPOINT, MODEL

TEXT_DIR = r"D:/ZSK/geokb/state/text"
OUT = r"D:/ZSK/geokb/logs/token_calibration_open.json"


def call(messages, key, max_tokens):
    payload = {"model": MODEL, "messages": messages, "temperature": 0.2,
               "max_tokens": max_tokens, "stream": False,
               "response_format": {"type": "json_object"}}
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"}, method="POST")
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    key = load_api_key()
    rows = []
    for f in glob.glob(os.path.join(TEXT_DIR, "*.txt")):
        rows.append((len(open(f, encoding="utf-8", errors="ignore").read()), f))
    rows.sort()
    n = len(rows)
    res = []
    for q in (10, 50, 90):
        _, path = rows[int(n * q / 100)]
        text = open(path, encoding="utf-8", errors="ignore").read()
        msgs = [{"role": "system", "content": SYSTEM_PASS1},
                {"role": "user", "content": USER_PASS1.format(text=text)}]
        r1 = call(msgs, key, 8000)
        u1 = r1["usage"]
        c1 = r1["choices"][0]["message"]["content"]
        n_kp = "?"
        try:
            import re
            m = re.search(r'"knowledge_points"', "")
        except Exception:
            pass
        msgs2 = msgs + [{"role": "assistant", "content": c1},
                        {"role": "user", "content": USER_PASS2}]
        r2 = call(msgs2, key, 8000)
        u2 = r2["usage"]
        c2 = r2["choices"][0]["message"]["content"]
        try:
            obj = json.loads(c2)
            n_kp = len(obj.get("knowledge_points", []))
        except Exception:
            pass
        rec = {"pct": q, "chars": len(text), "file": os.path.basename(path),
               "u1": u1, "u2": u2, "kp_count": n_kp}
        res.append(rec)
        print(f"[p{q}] chars={len(text):,} P1: in={u1['prompt_tokens']} out={u1['completion_tokens']} "
              f"finish={r1['choices'][0].get('finish_reason')} | "
              f"P2: in={u2['prompt_tokens']} out={u2['completion_tokens']} "
              f"hit={u2.get('prompt_cache_hit_tokens')} kp={n_kp} "
              f"finish={r2['choices'][0].get('finish_reason')}", flush=True)
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("saved ->", OUT)


if __name__ == "__main__":
    main()
