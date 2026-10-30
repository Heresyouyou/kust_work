"""判断「投放箱入库是否正在跑」——给 journals 页「补缺申请」按钮做前置校验。

被门户（Translate 的 Flask）以子进程调用，输出一行 JSON：
    {"busy": true, "hits": [{"pid": 1234, "what": "inbox_ingest.py", "cmd": "..."}]}

**口径（用户 2026-10-25 明确）**：这里的「在跑入库」**只指投放箱入库**
（`inbox_ingest.py`，即 12:01 / 18:01 那轮，或按钮自己拉起的那轮）。
夜间 Agent 入库（agent_ingest）、历史补爬、单篇上传（user_upload）等
**其他入库方式一概不算**，不得因此拦住按钮 —— 它们各有各的锁与队列，
且投放箱入库会自己等 `pdf_ingest.lock`（`wait_lock` 最多 25 分钟），
不会与它们撞车。

也正因为如此，这里**不看 `pdf_ingest.lock`**：那把锁是「所有 PDF 入库」
共用的，user_upload / pdf_corpus_ingest 都会持有，用它判断会把按钮误锁。

为什么要单独一个脚本：
  · 门户 venv（_buildenv）**没有 psutil**，而 GeoKB venv 有；
  · `os.kill(pid, 0)` 在 Windows 上不是「探活」，而是 **TerminateProcess**，
    会真的把进程杀掉 —— 绝不能拿来做存活判断。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

WATCH = ("inbox_ingest.py",)


def main() -> int:
    hits = []
    me = Path(__file__).name
    try:
        import psutil
        for p in psutil.process_iter(["pid", "cmdline"]):
            cl = " ".join(p.info.get("cmdline") or [])
            if not cl or me in cl:
                continue
            for w in WATCH:
                if w in cl:
                    hits.append({"pid": p.info["pid"], "what": w, "cmd": cl[:180]})
                    break
    except Exception as e:
        # psutil 挂了别把按钮卡死：当作没人跑（点击后 inbox_ingest 自己会排队等锁）
        sys.stderr.write("psutil unavailable: %s\n" % e)

    print(json.dumps({"busy": bool(hits), "hits": hits}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
