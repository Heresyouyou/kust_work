#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Orchestration-side dispatch ledger for GeoKB ingest (parent agent only).

Why this exists
---------------
2026-10-26 night run: the parent agent re-dispatched the same 6 shas over and
over while "waiting" for receipts. Every re-dispatch burned another sub-agent
quota unit and three sub-agents died on HTTP 429 at the same time.

`open_batch.json` records which shas the batch *contains*, and `pending` is
recomputed only when `commit` runs. During the 15-25 min window between
dispatch and write, a pending sha looks "not started" -- so the parent
dispatched it again. This ledger closes that window: a sha is marked
`inflight` the moment it is handed out, and cleared only when its receipt
comes back.

This file only touches orchestration state. It never writes papers, kpoints,
the index, or the ledger of ingested papers.

Usage
-----
  python scripts/ingest_dispatch.py plan [--limit N] [--json]
  python scripts/ingest_dispatch.py mark <sha...> [--agent NAME]
  python scripts/ingest_dispatch.py ack  <sha...>
  python scripts/ingest_dispatch.py status [--json]
  python scripts/ingest_dispatch.py reset

Exit codes: 0 ok / 2 = no open batch, run prep first.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BATCH = ROOT / "state" / "agent_ingest_batches" / "open_batch.json"
LEDGER = ROOT / "state" / "agent_ingest_dispatch.json"
PROGRESS = ROOT / "state" / "night_ingest_progress.md"

DEFAULT_STALE_MIN = 45
MAX_LIMIT = 8


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _sha12(s: str) -> str:
    s = (s or "").strip().lower()
    return s[:12] if len(s) >= 12 else s


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_ledger() -> dict:
    data = _read_json(LEDGER, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("items", {})
    if not isinstance(data["items"], dict):
        data["items"] = {}
    return data


def save_ledger(data: dict) -> None:
    data["updated_at"] = _now().isoformat()
    _atomic_write(LEDGER, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def load_batch() -> dict | None:
    data = _read_json(BATCH, None)
    if not isinstance(data, dict) or not data.get("shas"):
        return None
    return data


def batch_pending(batch: dict) -> list[str]:
    """Shas still owed a paper, straight from disk-derived batch facts."""
    shas = [_sha12(s) for s in batch.get("shas") or []]
    outcome = batch.get("outcome") or {}
    pending = [_sha12(s) for s in batch.get("pending") or []]
    if pending:
        return pending
    return [s for s in shas if outcome.get(s) != "done"]


def inflight_state(ledger: dict, stale_min: int) -> tuple[list[str], list[dict]]:
    alive: list[str] = []
    stale: list[dict] = []
    cutoff = _now() - timedelta(minutes=stale_min)
    for sha, meta in (ledger.get("items") or {}).items():
        if not isinstance(meta, dict) or meta.get("state") != "inflight":
            continue
        ts = meta.get("dispatched_at") or ""
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            dt = None
        if dt is None or dt < cutoff:
            age = None if dt is None else int((_now() - dt).total_seconds() // 60)
            stale.append({"sha12": sha, "age_min": age, "agent": meta.get("agent")})
        else:
            alive.append(sha)
    return alive, stale


def cmd_status(args) -> int:
    batch = load_batch()
    ledger = load_ledger()
    alive, stale = inflight_state(ledger, args.stale_min)
    pending = batch_pending(batch) if batch else []
    dispatchable = [s for s in pending if s not in alive]

    payload = {
        "generated_at": _now().isoformat(),
        "ledger": str(LEDGER.relative_to(ROOT)),
        "batch": None
        if not batch
        else {
            "batch_id": batch.get("batch_id"),
            "status": batch.get("status"),
            "total": len(batch.get("shas") or []),
            "pending": pending,
        },
        "inflight": alive,
        "stale": stale,
        "dispatchable": dispatchable,
        "stale_min": args.stale_min,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if batch else 2

    if not batch:
        print("NO_BATCH | 没有在飞批次 → 先跑 prep --limit 8 --bucket P0")
        return 2

    print(
        f"批次 {batch.get('batch_id')} | {batch.get('status')} | "
        f"{len(batch.get('shas') or [])} 篇 | pending {len(pending)}"
    )
    print(f"在飞 {len(alive)}: {' '.join(alive) if alive else '-'}")
    if stale:
        print(
            "超时 {n} ({m}min): {lst}".format(
                n=len(stale),
                m=args.stale_min,
                lst=" ".join(f"{x['sha12']}({x['age_min']}min)" for x in stale),
            )
        )
    print(f"可派 {len(dispatchable)}: {' '.join(dispatchable) if dispatchable else '-'}")
    return 0


def cmd_plan(args) -> int:
    batch = load_batch()
    if not batch:
        print("NO_BATCH | 没有在飞批次 → prep --limit 8 --bucket P0")
        return 2

    ledger = load_ledger()
    alive, stale = inflight_state(ledger, args.stale_min)
    pending = batch_pending(batch)
    dispatchable = [s for s in pending if s not in alive][: args.limit]

    if args.json:
        print(
            json.dumps(
                {
                    "batch_id": batch.get("batch_id"),
                    "status": batch.get("status"),
                    "pending": pending,
                    "inflight": alive,
                    "stale": stale,
                    "dispatch": dispatchable,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if stale:
        print(
            "WARN 超时未回执（可重派一次）: "
            + " ".join(f"{x['sha12']}({x['age_min']}min)" for x in stale)
        )
    if not dispatchable:
        if pending and alive:
            print(f"WAIT | 无可派，{len(alive)} 篇在飞，等回执（本回合结束，不要播报）")
        elif not pending:
            print("BATCH_DONE | 本批已全部写入 → 跑 ingest_resume.py 复核后 prep 下一批")
        else:
            print("WAIT | 无可派")
        return 0

    print("DISPATCH " + " ".join(dispatchable))
    print(f"# 上一条就是本回合要派的 sha（{len(dispatchable)} 个）；派完立刻结束回合，不写状态播报")
    return 0


def cmd_mark(args) -> int:
    ledger = load_ledger()
    ts = _now().isoformat()
    added = []
    for raw in args.shas:
        sha = _sha12(raw)
        if not sha:
            continue
        prev = ledger["items"].get(sha) or {}
        ledger["items"][sha] = {
            "state": "inflight",
            "dispatched_at": ts,
            "agent": args.agent or prev.get("agent") or "unknown",
            "attempts": int(prev.get("attempts") or 0) + 1,
        }
        added.append(sha)
    save_ledger(ledger)
    print(f"mark {len(added)} 篇在飞: {' '.join(added)}")
    return 0


def cmd_ack(args) -> int:
    ledger = load_ledger()
    done = []
    for raw in args.shas:
        sha = _sha12(raw)
        meta = ledger["items"].get(sha)
        if meta is None:
            ledger["items"][sha] = {"state": "acked", "acked_at": _now().isoformat()}
        else:
            meta["state"] = "acked"
            meta["acked_at"] = _now().isoformat()
        done.append(sha)
    save_ledger(ledger)
    print(f"ack {len(done)} 篇: {' '.join(done)}")
    return 0


def cmd_reset(args) -> int:
    archived = 0
    if LEDGER.exists():
        hist = ROOT / "state" / "_cold" / "dispatch"
        hist.mkdir(parents=True, exist_ok=True)
        target = hist / f"dispatch-{_now().strftime('%Y%m%d-%H%M%S')}.json"
        try:
            os.replace(LEDGER, target)
            archived = 1
        except OSError:
            LEDGER.unlink(missing_ok=True)
    print(f"reset ok（归档 {archived} 份）")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    p = argparse.ArgumentParser(description="GeoKB ingest dispatch ledger (parent only)")
    p.add_argument("--stale-min", type=int, default=DEFAULT_STALE_MIN,
                   help=f"inflight 超过该分钟数视为超时（默认 {DEFAULT_STALE_MIN}）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("plan", help="输出本回合该派的 sha（pending − 在飞）")
    sp.add_argument("--limit", type=int, default=MAX_LIMIT)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_plan)

    ss = sub.add_parser("status", help="在飞 / 超时 / 可派")
    ss.add_argument("--json", action="store_true")
    ss.set_defaults(func=cmd_status)

    sm = sub.add_parser("mark", help="派发时登记")
    sm.add_argument("shas", nargs="+")
    sm.add_argument("--agent", default="")
    sm.set_defaults(func=cmd_mark)

    sa = sub.add_parser("ack", help="收回执时销账")
    sa.add_argument("shas", nargs="+")
    sa.set_defaults(func=cmd_ack)

    sr = sub.add_parser("reset", help="轮次结束时清空（自动归档）")
    sr.set_defaults(func=cmd_reset)

    args = p.parse_args(argv)
    if getattr(args, "limit", 0) > MAX_LIMIT:
        args.limit = MAX_LIMIT
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
