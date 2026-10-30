#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""GeoKB 入库服务层（CLI 与 MCP 共用的唯一门面）。

设计原则
--------
1. **不复制逻辑**：write / commit / evict 分别委托给
   `agent_ingest.write_paper`、`agent_ingest.commit_shas`、`ctx_evict.do_evict`，
   本模块只负责「加锁 + 权限 + 结构化返回 + 审计」。
2. **能力契约**：`write` 只接受「在飞批次内」或「已 prep（有 wip 笔记）」的 sha。
   子代理因此物理上改不了别人的篇，这不是靠自觉，而是服务端拒绝。
3. **并发安全**：写按 sha 分锁（不同篇可并行），commit / evict / prep 走全局锁
   （它们要碰 sqlite 索引、台账、批次清单）。锁用 OS 文件锁，进程崩溃自动释放。
4. **无删除能力**：本模块不提供任何 delete/unlink 入口。

返回值统一为 dict；`ok=False` 时必带 `error`（中文，可直接回给 agent）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_NO_WIN = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW：子孙全继承隐藏控制台
BASE = Path(__file__).resolve().parent.parent
SCRIPTS = BASE / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import agent_ingest as ai  # noqa: E402

PY = sys.executable
AUDIT = ai.STATE / "api_audit.jsonl"
DIGEST = SCRIPTS / "ingest_digest.py"


# ──────────────────────────────────────────────────────────────────────
# 审计
# ──────────────────────────────────────────────────────────────────────
def _audit(tool: str, payload: dict, result: dict) -> None:
    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "tool": tool,
        "sha": payload.get("sha") or payload.get("shas"),
        "ok": bool(result.get("ok")),
        "error": result.get("error") or "",
        "pid": os.getpid(),
    }
    try:
        with ai.cross_process_lock("audit"):
            AUDIT.parent.mkdir(parents=True, exist_ok=True)
            with open(AUDIT, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass  # 审计失败不得影响主流程


# ──────────────────────────────────────────────────────────────────────
# 批次 / 权限
# ──────────────────────────────────────────────────────────────────────
def open_batch() -> dict:
    if not ai.OPEN_BATCH.exists():
        return {}
    try:
        return json.loads(ai.OPEN_BATCH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _batch_shas() -> set:
    return set(open_batch().get("shas") or [])


def writable(sha: str) -> tuple[bool, str]:
    """能力契约：只有「在飞批次内」或「已 prep（有 wip 笔记）」的篇可写。"""
    if not sha or len(sha) != 12:
        return False, "sha12 格式不对（应为 12 位十六进制）"
    if sha in _batch_shas():
        return True, "in_open_batch"
    if (ai.WIP_DIR / ("%s.md" % sha)).exists():
        return True, "prepped"
    return False, ("该 sha 不在在飞批次、也没有 prep 笔记。"
                   "请先 geokb_prep 取批，或确认 sha 是否正确。")


def _queue() -> list:
    if not ai.QUEUE.exists():
        return []
    out = []
    for line in ai.QUEUE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    return out


# ──────────────────────────────────────────────────────────────────────
# status
# ──────────────────────────────────────────────────────────────────────
def status(fix: bool = False) -> dict:
    import ingest_resume as ir
    ck = ir.diagnose(fix=fix)
    keep = ("env", "disk", "judge", "buckets_recomputed", "queue",
            "batch", "actions", "done", "incomplete", "orphans")
    out = {k: ck.get(k) for k in keep}
    out["ok"] = True
    return out


# ──────────────────────────────────────────────────────────────────────
# next：取队首待办
# ──────────────────────────────────────────────────────────────────────
def next_task() -> dict:
    papers = {p.stem[6:] for p in ai.PAPERS_DIR.glob("paper-*.md")}
    skips = ai.load_skips()
    cand = []
    for x in _queue():
        sha = x.get("sha12") or ""
        if not sha or sha in papers or sha in skips:
            continue
        if x.get("bucket") != "P0":
            continue
        cand.append(x)
    cand.sort(key=lambda x: x.get("ledger_idx", 1 << 30))
    if not cand:
        return {"ok": True, "done": True,
                "message": "P0 待办为 0（以磁盘条目为准）"}
    x = cand[0]
    sha = x["sha12"]
    return {
        "ok": True, "done": False, "sha": sha,
        "title": x.get("title") or "", "journal": x.get("journal") or "",
        "year": x.get("year") or "", "bucket": x.get("bucket"),
        "has_wip": (ai.WIP_DIR / ("%s.md" % sha)).exists(),
        "has_payload": (ai.WIP_DIR / ("payload-%s.json" % sha)).exists(),
        "in_open_batch": sha in _batch_shas(),
        "remaining_p0": len(cand),
    }


# ──────────────────────────────────────────────────────────────────────
# read_text：复用 ingest_digest，不在服务层重写文本抽取
# ──────────────────────────────────────────────────────────────────────
def read_text(sha: str, mode: str = "nav", start: int = 0, end: int = 0) -> dict:
    if not sha or len(sha) != 12:
        return {"ok": False, "error": "sha12 格式不对"}
    cmd = [PY, str(DIGEST)]
    if mode == "full":
        cmd += [sha, "--full"]
    elif mode == "slices":
        if not (start > 0 and end >= start):
            return {"ok": False, "error": "slices 模式需要 start>0 且 end>=start"}
        cmd += [sha, "--slices", "%d-%d" % (start, end)]
    else:
        cmd += [sha]
    try:
        r = subprocess.run(cmd, cwd=str(BASE), capture_output=True,
                           timeout=300, encoding="utf-8", errors="replace",
                           creationflags=_NO_WIN)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "文本抽取超时（>300s）"}
    text = r.stdout or ""
    stripped = text.strip()
    # ingest_digest 用 "!!" 开头表示「拿不到原文」，不能当成正常内容回给 agent
    if stripped.startswith("!!") or (r.returncode != 0 and not stripped):
        msg = stripped.splitlines()[0] if stripped else (r.stderr or "抽取失败")[-800:]
        hint = ""
        if ("wip 笔记" in msg) or ("原文缓存" in msg):
            hint = "该篇尚未 prep：先调 geokb_prep 取批（prep 会生成笔记并缓存原文）。"
        return {"ok": False, "sha": sha, "error": msg + ("；" + hint if hint else "")}
    return {"ok": True, "sha": sha, "mode": mode, "chars": len(text), "text": text}


# ──────────────────────────────────────────────────────────────────────
# prep：取批（会覆盖批次清单，因此在飞批次未收口时拒绝）
# ──────────────────────────────────────────────────────────────────────
def prep(limit: int = 6, bucket: str = "P0", force: bool = False, sha: str = None) -> dict:
    b = open_batch()
    # 以「条目是否存在」判收口，而不是只信 status 字段：
    # 条目被删/回滚后 status 可能仍是 closed，只看 status 会误开新批并覆盖清单。
    missing = [s for s in (b.get("shas") or [])
               if not (ai.PAPERS_DIR / ("paper-%s.md" % s)).exists()]
    if b and missing and not force:
        return {"ok": False,
                "error": ("上一批（%s）还有 %d 篇没写成：%s。先重做或 skip，再取新批；"
                          "确需并行扩批请传 force=true。"
                          % (b.get("batch_id"), len(missing), " ".join(missing))),
                "batch": b}
    with ai.cross_process_lock("prep", timeout=180):
        ns = type("A", (), {"bucket": [bucket], "sha": [sha] if sha else None,
                            "limit": limit})()
        rc = ai.cmd_prep(ns)
    if rc != 0:
        return {"ok": False, "error": "prep 失败（见服务端日志）"}
    b = open_batch()
    return {"ok": True, "batch_id": b.get("batch_id"),
            "shas": b.get("shas") or [], "count": len(b.get("shas") or [])}


# ──────────────────────────────────────────────────────────────────────
# write：按 sha 分锁，不同篇可并行
# ──────────────────────────────────────────────────────────────────────
def write(sha: str, payload: dict) -> dict:
    okperm, why = writable(sha)
    if not okperm:
        return {"ok": False, "sha": sha, "error": why}
    if not isinstance(payload, dict):
        return {"ok": False, "sha": sha, "error": "payload 必须是 JSON 对象"}
    try:
        with ai.cross_process_lock("write-%s" % sha, timeout=120):
            r = ai.write_paper(sha, payload)
    except TimeoutError as e:
        return {"ok": False, "sha": sha, "error": str(e)}
    _audit("write", {"sha": sha}, r)
    r["permission"] = why
    return r


# ──────────────────────────────────────────────────────────────────────
# commit / evict：全局锁
# ──────────────────────────────────────────────────────────────────────
def commit(shas=None, all_written: bool = False) -> dict:
    if not shas and not all_written:
        return {"ok": False, "error": "需要 shas 或 all_written=true"}
    if shas:
        bad = [s for s in shas if not (ai.PAPERS_DIR / ("paper-%s.md" % s)).exists()]
        if bad:
            return {"ok": False,
                    "error": "以下篇还没有条目文件，不能 commit（先 write）：%s"
                             % " ".join(bad)}
    try:
        with ai.cross_process_lock("commit", timeout=600):
            r = ai.commit_shas(shas=shas, all_written=all_written, no_facet=False)
    except TimeoutError as e:
        return {"ok": False, "error": str(e)}
    _audit("commit", {"shas": shas or "all_written"}, r)
    return r


def evict(shas) -> dict:
    if not shas:
        return {"ok": False, "error": "需要 shas"}
    blocked = [s for s in shas if not (ai.PAPERS_DIR / ("paper-%s.md" % s)).exists()]
    if blocked:
        return {"ok": False,
                "error": "以下篇没有条目，拒绝出栈（安全闸门）：%s" % " ".join(blocked)}
    try:
        import ctx_evict as ce
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "导入 ctx_evict 失败: %s" % e}
    done, skipped = [], []
    try:
        with ai.cross_process_lock("evict", timeout=300):
            for s in shas:
                try:
                    moved = ce.do_evict(s, dry=False)
                except Exception as e:  # noqa: BLE001
                    skipped.append({"sha": s, "reason": str(e)})
                    continue
                if moved:
                    done.append({"sha": s, "files_moved": moved})
                else:
                    skipped.append({"sha": s, "reason": "热区已空（之前已出栈）"})
    except TimeoutError as e:
        return {"ok": False, "error": str(e)}
    r = {"ok": True, "evicted": done, "skipped": skipped}
    _audit("evict", {"shas": shas}, r)
    return r


# ──────────────────────────────────────────────────────────────────────
# 只读：批次详情
# ──────────────────────────────────────────────────────────────────────
def batch_info() -> dict:
    b = open_batch()
    if not b:
        return {"ok": True, "batch": None}
    return {"ok": True, "batch": b}
