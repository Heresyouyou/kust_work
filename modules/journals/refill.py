# -*- coding: utf-8 -*-
"""「补缺申请」——journals 页顶「今日更新」卡片右上角的按钮（2026-10-25）

点一下 = 立刻跑一次投放箱入库（等价于计划任务 12:01 / 18:01 那一轮
`scripts/inbox_ingest.py --run`）。约束（用户口径）：

  ① **冷却 60 秒，按后端计** —— 与开了几个前端无关，状态落
     `state/inbox_refill.json`，多标签/多设备共享同一份；
  ② 只有 **inbox 里真有 PDF**、且 **投放箱入库没在跑**，点击才有效
     （口径：只认 `inbox_ingest.py`；夜间 Agent 入库 / 历史补爬 / 单篇上传
       一律不算，不得拦住按钮 —— 用户 2026-10-25 明确）；
  ③ 有效 → 前端显示「补缺中，x/y 篇」；
  ④ inbox 为空 → 回「暂无更新」。

为什么起独立进程：inbox_ingest 是长任务（~36s/篇，10 篇≈6 分钟）。
放线程里会占住 Flask worker，而且它自己会写日志/状态文件、抢
`pdf_ingest.lock`，按进程跑跟定时任务完全一致。这里用 CREATE_NO_WINDOW
拉起，请求立即返回，前端靠轮询 `state/sd_inbox_status.json` 的 progress
显示 x/y（inbox_ingest 每处理完一篇就刷一次）。

为什么进程存活判断不用 os.kill(pid, 0)：Windows 上那是 TerminateProcess，
会真的把目标进程杀掉。这里走 OpenProcess + GetExitCodeProcess。
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

from flask import jsonify

GEOKB = Path("D:/ZSK/geokb")
PYEXE = GEOKB / "venv" / "Scripts" / "python.exe"
SCRIPT = GEOKB / "scripts" / "inbox_ingest.py"
BUSY_CHECK = GEOKB / "scripts" / "ingest_busy_check.py"
INBOX_DIRS = (GEOKB / "inbox" / "papers", GEOKB / "state" / "inbox")
STATE = GEOKB / "state" / "inbox_refill.json"
RUN_STATUS = GEOKB / "state" / "sd_inbox_status.json"
RUN_LOG = GEOKB / "state" / "inbox_refill_run.log"
# 论文导读模块的队列库：它也在跑 GeoKB 入库，两边共用 pdf_ingest.lock
PAPERS_DB = Path("D:/PostNews/Translate/modules/papers/data/papers.db")

COOLDOWN = 60          # 秒，后端计时
MIN_AGE = 45           # 与 inbox_ingest.MIN_AGE_SEC 一致：太新的文件可能还在写

# 门户进程本身没有控制台（服务方式启动），凡是 subprocess.run 一个 console 程序
# 都会弹一个黑窗 —— 必须显式 CREATE_NO_WINDOW（2026-10-26 实测：一篇论文跳 20+ 个）。
_NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

_lock = threading.Lock()   # 同进程内串行化，避免连点两次起两个进程
_busy_cache = {"ts": 0.0, "data": None}


# ── 基础工具 ─────────────────────────────────────────────────────────────
def _read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def _write_json(p: Path, doc: dict):
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass


def _normts(v) -> str:
    """把 '2026-10-25T12:58:01' 与 '2026-10-25 12:58:01' 归一，才能按字符串比先后。"""
    return str(v or "").replace("T", " ")[:19]


def _alive(pid) -> bool:
    """Windows 安全探活：OpenProcess + GetExitCodeProcess（STILL_ACTIVE=259）。"""
    try:
        pid = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = k32.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259
        finally:
            k32.CloseHandle(handle)
    except Exception:
        return False


def _papers_ingest() -> int:
    """论文导读模块还有几篇在入库（pending / running）。

    为什么补缺要看它：两边都要跑 GeoKB 的 pdf_corpus_ingest，共用
    `state/pdf_ingest.lock`。论文模块正在入库时点补缺，新的 inbox_ingest 会在锁上
    一直等到 25 分钟超时，前端只看到「补缺中 0/N」像卡死。所以先问一句。
    只读另一模块的 sqlite（ro），任何异常都当 0 —— 不能因为读不到就把按钮锁死。
    """
    if not PAPERS_DB.exists():
        return 0
    try:
        con = sqlite3.connect("file:%s?mode=ro" % PAPERS_DB.as_posix(),
                              uri=True, timeout=1.5)
        try:
            n = con.execute("SELECT COUNT(*) FROM jobs WHERE step='ingest' "
                            "AND status IN ('pending','running')").fetchone()[0]
        finally:
            con.close()
        return int(n or 0)
    except Exception:
        return 0


def _scan_inbox():
    """返回 (PDF 总数, 已够「够老」可入库数)。只扫投放目录一层，与 ingest 一致。"""
    now = time.time()
    total = ready = 0
    for d in INBOX_DIRS:
        if not d.is_dir():
            continue
        try:
            items = list(d.glob("*.pdf"))
        except OSError:
            continue
        for p in items:
            total += 1
            try:
                if now - p.stat().st_mtime >= MIN_AGE:
                    ready += 1
            except OSError:
                pass
    return total, ready


def _busy_check(own_pid: int = 0):
    """**投放箱入库**是否正在跑（只看 inbox_ingest.py，别的入库不算）。

    口径见 `scripts/ingest_busy_check.py`：夜间 Agent 入库、历史补爬、
    单篇上传都不拦按钮 —— 它们与投放箱入库各有锁、且 inbox_ingest 自己会
    排队等 `pdf_ingest.lock`。所以这里**不看 `pdf_ingest.lock`**。

    ⚠️ 2026-10-26：**默认不再起子进程**。原因是血泪史 ——
    门户进程没有控制台，`subprocess.run(python.exe …)` 每调一次就弹一个黑窗。
    状态接口跑的时候每 2–3 秒一次，一篇论文就跳了二十多个。
    现在改读 `inbox_ingest` 自己写进 `sd_inbox_status.json` 的 `pid`（它每处理
    一篇都会刷一次心跳），用 OpenProcess 判活，**零窗口、零子进程**。
    只有遇到「旧版脚本写的状态没有 pid 字段」才退回子进程枚举（带 CREATE_NO_WINDOW）。
    """
    global _busy_cache
    if _busy_cache["data"] and time.time() - _busy_cache["ts"] < 3:
        return _busy_cache["data"]

    prog = _progress()
    if not prog.get("running"):
        data = {"busy": False, "hits": []}
        _busy_cache = {"ts": time.time(), "data": data}
        return data

    pid = progress_pid()
    if pid:
        busy = pid != int(own_pid or 0) and _alive(pid)
        data = {"busy": busy,
                "hits": ([{"pid": pid, "what": "inbox_ingest.py"}] if busy else [])}
        _busy_cache = {"ts": time.time(), "data": data}
        return data

    # 旧版兼容：状态文件没有 pid → 只在心跳还新鲜时枚举一次进程
    if not _fresh(prog.get("ts"), 180) or not BUSY_CHECK.exists() or not PYEXE.exists():
        data = {"busy": False, "hits": []}
    else:
        try:
            r = subprocess.run([str(PYEXE), str(BUSY_CHECK)], capture_output=True,
                               text=True, encoding="utf-8", errors="ignore", timeout=20,
                               cwd=str(GEOKB), creationflags=_NO_WIN)
            data = json.loads((r.stdout or "").strip().splitlines()[-1])
        except Exception as e:
            data = {"busy": False, "hits": [],
                    "error": "%s: %s" % (type(e).__name__, e)}
    _busy_cache = {"ts": time.time(), "data": data}
    return data


def _progress():
    """inbox_ingest 写的进度（每篇刷新一次）。"""
    doc = _read_json(RUN_STATUS, {})
    prog = doc.get("progress") or {}
    try:
        done = int(prog.get("done") or 0)
        total = int(prog.get("total") or 0)
    except (TypeError, ValueError):
        done = total = 0
    try:
        pid = int(doc.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    return {"done": done, "total": total,
            "running": bool(doc.get("running")),
            "pid": pid,
            "ts": str(doc.get("ts") or ""),
            "counts": doc.get("counts") or {},
            "elapsed_s": doc.get("elapsed_s") or 0}


def progress_pid() -> int:
    """正在跑的那轮 inbox_ingest 的 pid（它自己写进状态文件的心跳）。"""
    return int(_progress().get("pid") or 0)


def _fresh(ts, seconds: int) -> bool:
    """状态心跳是否还在 seconds 秒内（ts 是本地时间字符串 'YYYY-MM-DD HH:MM:SS'）。"""
    s = _normts(ts)
    if not s:
        return False
    try:
        return 0 <= (time.time() - time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))) < seconds
    except Exception:
        return False


def _state():
    st = _read_json(STATE, {})
    try:
        st["pid"] = int(st.get("pid") or 0)
    except (TypeError, ValueError):
        st["pid"] = 0
    return st


def _status_payload():
    st = _state()
    running = _alive(st.get("pid"))
    total_inbox, ready = _scan_inbox()
    prog = _progress()

    last_start = float(st.get("last_start") or 0)
    left = max(0, int(COOLDOWN - (time.time() - last_start) + 0.999)) if last_start else 0

    done = 0
    total = int(st.get("total") or 0)
    started_s = _normts(st.get("started"))
    fresh = bool(started_s) and _normts(prog.get("ts")) >= started_s
    if running:
        # 只认本次运行之后的进度（避免读到上一轮残留）
        if prog["running"] and fresh:
            done = prog["done"]
            total = prog["total"] or total
        elif fresh and prog.get("total"):
            # 收尾窗口：inbox_ingest 已写完最终计数（running=false），但包装进程
            # 还在做标题回填/索引收尾，_alive 仍为 True。此时若按 done=0 输出，
            # 前端会显示「补缺中，0/8篇」，看起来像进度倒退（用户 2026-10-29 报障）。
            # 已跑完就保持满格，直到进程真正退出。
            done = total = int(prog.get("total") or total)
    else:
        done = total = 0

    if running:
        state = "running"
    elif left > 0:
        state = "cooldown"
    elif ready == 0:
        state = "empty"
    else:
        state = "ready"

    busy = _busy_check(st.get("pid") or 0)
    # 论文模块正在入库 → 现在点补缺只会卡在 GeoKB 的锁上，按钮不该显示「可点」
    papers = _papers_ingest()
    # 只在「本可以点」的时候才把状态改成 busy：刚跑完的那 10s 里进程枚举可能还
    # 读到收尾中的自己，若直接覆盖会把「冷却倒计时」打成「进行中」，看着像卡住。
    if busy.get("busy") and not running and state == "ready":
        state = "busy"
    if papers and not running and state in ("ready", "empty"):
        state = "busy"

    return {
        "ok": True,
        "state": state,
        "running": running,
        "done": done,
        "total": total,
        "inbox": total_inbox,
        "ready": ready,
        "cooldown": left,
        "busy": bool(busy.get("busy") or papers),
        "busy_hits": (busy.get("hits") or [])[:3],
        "papers_ingest": papers,
        # 刚跑完那一轮的计数（前端闪「补缺完成 N 篇」）；跨轮残留不认
        "last_result": (prog.get("counts") or {}) if (not running and fresh) else {},
        "last": {k: st.get(k) for k in ("started", "finished", "result") if st.get(k)},
    }


# ── 路由 ─────────────────────────────────────────────────────────────────
def register(bp):
    @bp.route("/api/journals/refill/status", methods=["GET"])
    def api_refill_status():
        return jsonify(_status_payload())

    @bp.route("/api/journals/refill", methods=["POST"])
    def api_refill():
        with _lock:
            st = _state()
            running = _alive(st.get("pid"))
            if running:
                return jsonify({"ok": False, "reason": "busy", "why": "refill",
                                "running": True, "hint": "已在补缺中"})

            last_start = float(st.get("last_start") or 0)
            left = COOLDOWN - (time.time() - last_start)
            if last_start and left > 0:
                return jsonify({"ok": False, "reason": "cooldown",
                                "retry_after": int(left) + 1,
                                "hint": "%d 秒后可再按" % (int(left) + 1)})

            busy = _busy_check(st.get("pid") or 0)
            if busy.get("busy"):
                return jsonify({"ok": False, "reason": "busy", "hint": "投放箱入库进行中",
                                "busy_hits": (busy.get("hits") or [])[:3]})

            # 论文模块正在入库：现在起 inbox_ingest 只会在 pdf_ingest.lock 上干等
            # （放在 inbox 扫描之前，跟按钮上「等入库 N」的文案保持一致：
            #  否则 inbox 恰好空着时会先回「暂无更新」，跟按钮说的不是一回事）
            papers = _papers_ingest()
            if papers:
                return jsonify({"ok": False, "reason": "busy", "papers_ingest": papers,
                                "hint": "论文模块正在入库 %d 篇，等它跑完再补缺" % papers})

            total_inbox, ready = _scan_inbox()
            if ready <= 0:
                return jsonify({"ok": False, "reason": "empty", "inbox": total_inbox,
                                "hint": ("暂无更新" if total_inbox == 0
                                         else "文件刚落地，稍等 1 分钟")})

            if not PYEXE.exists() or not SCRIPT.exists():
                return jsonify({"ok": False, "reason": "env",
                                "hint": "找不到 GeoKB 的 python 或脚本"})

            now = time.time()
            started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
            # 先把 last_start 落盘，再起进程 —— 两个并发请求只会有一个走到这里
            _write_json(STATE, {"pid": 0, "started": started, "last_start": now,
                                "total": ready, "result": ""})

            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"
            flags = 0
            if os.name == "nt":
                # ⚠️ 用 CREATE_NO_WINDOW 而不是 DETACHED_PROCESS：后者让子进程没有
                # 控制台，它再 spawn（pdf_corpus_ingest / kp_extract / 标题回填）
                # 时每个都会给自己新开一个黑窗。CREATE_NO_WINDOW 给的是**隐藏**控制台，
                # 后代都继承它 → 全程零弹窗。
                flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                         | subprocess.CREATE_NEW_PROCESS_GROUP)
            try:
                RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
                with open(RUN_LOG, "ab") as logf:
                    proc = subprocess.Popen(
                        [str(PYEXE), str(SCRIPT), "--run"],
                        cwd=str(GEOKB), env=env, stdin=subprocess.DEVNULL,
                        stdout=logf, stderr=subprocess.STDOUT,
                        creationflags=flags, close_fds=True)
            except Exception as e:
                _write_json(STATE, {"pid": 0, "started": started, "last_start": 0,
                                    "total": 0, "result": "spawn_failed: %s" % e})
                return jsonify({"ok": False, "reason": "spawn",
                                "hint": "启动失败：%s" % e})

            _write_json(STATE, {"pid": proc.pid, "started": started, "last_start": now,
                                "total": ready, "result": ""})
            _busy_cache["ts"] = 0   # 让状态接口立刻重新判断
            return jsonify({"ok": True, "running": True, "pid": proc.pid,
                            "done": 0, "total": ready, "cooldown": COOLDOWN})

    return bp
