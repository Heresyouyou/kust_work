# -*- coding: utf-8 -*-
"""
生成「FIFO 待清清单」——按积压先后排序，最早积压的排最前。

用户口径（2026-10-23）：
    不用清完 P0。存量会动态增长，追不完也不需要追完。
    只需列出清单、从**最早积压**的开始，按顺序一篇篇往下清，清掉多少算多少；
    新下载的自动排到队尾，不打断当前顺序。

排序依据 = state/papers_index.jsonl 的**行号**（追加式台账，行号即进入积压的先后）。

用法：
    python agent_fifo_list.py                # 输出到 docs/agent_ingest_FIFO清单.md
    python agent_fifo_list.py --bucket P0     # 只要 P0
    python agent_fifo_list.py --limit 60      # 只列前 60 条（本轮要清的部分）
    python agent_fifo_list.py --stdout       # 只打印不落盘
"""
import argparse
import collections
import json
import os
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE = os.path.join(BASE, "state", "agent_ingest_queue.jsonl")
OUT = os.path.join(BASE, "docs", "agent_ingest_FIFO清单.md")

BUCKET_DESC = {
    "P0": "无条目 —— 需完整撰写（**只有这类才增加论文数**）",
    "P1": "有条目无知识点 —— 补知识点（论文数不变）",
    "P2": "缺分面 —— 补受控词（论文数不变）",
    "P3": "缺标签（论文数不变）",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", action="append", default=None,
                    help="只列指定桶（可重复）；默认 P0/P1/P2")
    ap.add_argument("--limit", type=int, default=0, help="只列前 N 条")
    ap.add_argument("--stdout", action="store_true")
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(QUEUE, encoding="utf-8") if l.strip()]
    want = args.bucket or ["P0", "P1", "P2"]
    todo = [r for r in rows if r.get("bucket") in want]
    # FIFO：台账行号升序 = 最早积压在前
    todo.sort(key=lambda r: r.get("ledger_idx", 1 << 30))
    if args.limit:
        todo = todo[: args.limit]

    out = []
    w = out.append
    w("# GeoKB 待清清单（FIFO · 最早积压优先）")
    w("")
    w("> 生成时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M"))
    w(">")
    w("> **口径（用户 2026-10-23）**：不用清完。存量动态增长，追不完也不需要追完。")
    w("> 只按此清单**从最早积压的开始，按顺序往下清**，清掉多少算多少；")
    w("> 新下载的论文自动排到队尾，不打断当前顺序。")
    w(">")
    w("> 排序依据 = `state/papers_index.jsonl` 行号（`ledger_idx`，追加式台账，行号即积压先后）。")
    w("")
    w("## 概览")
    w("")
    w("| 项 | 值 |")
    w("|---|---|")
    w("| 队列总量 | %d |" % len(rows))
    c = collections.Counter(r.get("bucket") for r in rows)
    for b in ("P0", "P1", "P2", "P3", "P4"):
        if c.get(b):
            w("| %s | %d |" % (b, c[b]))
    w("| 本清单条数 | %d（%s） |" % (len(todo), "+".join(want)))
    w("")
    w("> ⚠️ **只有 P0 会增加论文条目数**；P1/P2 只补知识点与分面，论文数不变。")
    w("> 汇报时必须分开说，绝不能合并计数。")
    w("")

    # 按桶分段输出，段内保持 FIFO
    for b in want:
        sub = [r for r in todo if r.get("bucket") == b]
        if not sub:
            continue
        w("---")
        w("")
        w("## %s（%d 篇）— %s" % (b, len(sub), BUCKET_DESC.get(b, "")))
        w("")
        w("| 序 | 台账行 | sha12 | 期刊 | 年 | 标题 |")
        w("|---|---|---|---|---|---|")
        for i, r in enumerate(sub, 1):
            t = (r.get("title") or "").replace("|", "/").replace("\n", " ").strip()
            if len(t) > 76:
                t = t[:76] + "…"
            w("| %d | %s | `%s` | %s | %s | %s |" % (
                i, r.get("ledger_idx", ""), r["sha12"],
                r.get("journal") or "", r.get("year") or "", t))
        w("")

    w("---")
    w("")
    w("## 执行约定")
    w("")
    w("1. **从头开始，不跳序**：`prep` 已按 `ledger_idx` 升序取，直接 `prep --limit N --bucket P0` 即可。")
    w("2. **每批 5–10 篇**，处理完汇报；状态机幂等，可随时中断续跑。")
    w("3. **知识点 2–8 条不凑数**，按信息密度取最有价值的。")
    w("4. **窗口 23:00–次日 08:00**，窗口内零 DeepSeek 调用。")
    w("5. 每次开工先 `scan` 刷新队列 —— 新下载的会排到队尾，不影响已定顺序。")

    text = "\n".join(out)
    if args.stdout:
        print(text)
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print("written %s (%d chars, %d 条)" % (OUT, len(text), len(todo)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
