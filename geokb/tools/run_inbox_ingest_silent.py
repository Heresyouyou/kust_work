"""静默执行投放箱入库（计划任务 GeoKB-InboxIngest 的入口）。

为什么要这层包装
----------------
任务原来是 `python.exe "scripts\\inbox_ingest.py" --run`，LogonType=3（交互会话）。
`python.exe` 是控制台程序，计划任务在交互会话里启动它 → **必然出现一个 cmd
风格的 python 控制台窗口**，每天 12:01 / 18:01 各闪一次（2026-10-27 定位）。

改法：任务 action 改成 `pythonw.exe 本文件`（GUI 子系统，自身无窗口），
再由本文件用 `CREATE_NO_WINDOW` 拉起真正的 `python.exe inbox_ingest.py --run`。
隐藏控制台由整条后代链继承，连它内部的 pdf_corpus_ingest / kp_extract 都不会弹。

⚠️ pythonw 下 `sys.stdout is None`，`print()` 会抛 OSError(22)。
   本文件只用 `log()` 落盘，**不要加 print**。
"""
from __future__ import annotations

import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

BASE = Path(r"D:\ZSK\geokb")
PY = BASE / "venv" / "Scripts" / "python.exe"
SCRIPT = BASE / "scripts" / "inbox_ingest.py"
LOG = BASE / "logs" / "inbox_ingest_task.log"

NO_WIN = 0x08000000 if sys.platform == "win32" else 0


def log(msg: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def main() -> int:
    if not (PY.is_file() and SCRIPT.is_file()):
        log("跳过：解释器或脚本不存在（%s / %s）" % (PY, SCRIPT))
        return 1
    try:
        p = subprocess.run(
            [str(PY), str(SCRIPT), "--run"], cwd=str(BASE),
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
            timeout=7200, creationflags=NO_WIN,
        )
        tail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-4:]
        log("inbox_ingest 完成 rc=%s%s"
            % (p.returncode, ("  | " + " / ".join(tail)) if tail else ""))
        return 0 if p.returncode == 0 else 1
    except subprocess.TimeoutExpired:
        log("inbox_ingest 超时（>7200s）")
        return 1
    except Exception as e:  # noqa: BLE001
        log("inbox_ingest 异常 %s: %s" % (type(e).__name__, e))
        log(traceback.format_exc(limit=3))
        return 1


if __name__ == "__main__":
    sys.exit(main())
