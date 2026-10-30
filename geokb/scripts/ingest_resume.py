#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""GeoKB 入库 · 断点诊断与续跑计划（唯一权威入口）。

为什么需要它
------------
入库是「write → commit → 索引 → 台账 → 分面 → 出栈」的多步流水线，
任何一步都可能被打断（模型限额、断电、Ctrl-C、并发轮次）。历史上有四个
状态源各说各话：

  L0 磁盘事实   content/papers/paper-<sha>.md、content/kpoints/、index/geokb.db、state/papers_index.jsonl
  L1 队列       state/agent_ingest_queue.jsonl（scan 由 L0 派生，是缓存）
  L2 事件日志   state/agent_ingest_state.jsonl（append-only，只作审计）
  L3 批次清单   state/agent_ingest_batches/open_batch.json（在飞批次）

实测曾出现：磁盘已有 1701 篇条目，`status` 只认 committed=355，
另有 4 篇「条目已写、索引未建、台账未登」完全不可见。按 status 续跑会
重复处理上千篇；按进度 md 续跑会漏掉真实断点。

本脚本的判定原则
----------------
1. **只有 L0 算数**。committed 的定义是「paper 文件存在 且 解析通过 且
   已进索引库 且 台账有记录」，四项缺一即 incomplete。
2. **L1 只修正不发明**。队列的 entry/bucket 若与磁盘不符，标记 stale 并可
   `--fix` 原地修正（不跑 scan，避免 NAS 掉线时把队列清空）。
3. **L2 不参与判定**，只用于展示事件日志与磁盘的差异，提示历史漂移。
4. **L3 让在飞批次可对账**：prep 落清单，commit 按磁盘事实收口。
5. 输出**唯一动作序列**。任一中断点，agent 只需照 actions 顺序执行，
   不需要自己推理；P0 耗尽时 done=True。

用法
----
    $PY scripts/ingest_resume.py                 # 人读：诊断 + NEXT ACTION
    $PY scripts/ingest_resume.py --json          # 机器读：完整 checkpoint
    $PY scripts/ingest_resume.py --fix           # 修正队列 stale 标记
    $PY scripts/ingest_resume.py --takeover      # 确认无并发，强制接管
    $PY scripts/ingest_resume.py --no-heartbeat  # 只读诊断，不写心跳

退出码：0=正常（有动作或已完成）；3=检测到可能并发的另一轮。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = BASE / "scripts"
sys.path.insert(0, str(SCRIPTS))

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

import agent_ingest as ai  # noqa: E402

PY = "D:/ZSK/geokb/venv/Scripts/python.exe"
CHECKPOINT = ai.STATE / "ingest_checkpoint.json"
HEARTBEAT = ai.STATE / "ingest_heartbeat.json"
BATCH_DIR = ai.STATE / "agent_ingest_batches"
OPEN_BATCH = BATCH_DIR / "open_batch.json"
TEXT_DIR = ai.STATE / "text"
HEARTBEAT_STALE_MIN = 10


# ── 基础 ──────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _pid_alive(pid) -> bool:
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except Exception:  # noqa: BLE001
        try:
            os.kill(int(pid), 0)
            return True
        except Exception:  # noqa: BLE001
            return False


# ── L0：磁盘事实 ──────────────────────────────────────────────────────
def load_papers() -> dict:
    return {p.stem[6:]: p for p in ai.PAPERS_DIR.glob("paper-*.md")}


def load_kp_counts() -> Counter:
    c = Counter()
    for p in ai.KPOINTS_DIR.glob("kp-*.md"):
        parts = p.stem.split("-")
        if len(parts) >= 3:
            c[parts[1]] += 1
    return c


class LedgerIndex:
    """台账索引：eid 精确匹配优先，repo_path 归一化匹配兜底（同 _ledger_has_path）。"""

    def __init__(self):
        self.rows = ai.load_ledger()
        self.eids = set()
        self.norm_paths = set()
        self.rel_paths = set()
        for r in self.rows:
            e = r.get("eid")
            if e:
                self.eids.add(e)
            k = ai.norm(r.get("repo_path"))
            if not k:
                continue
            self.norm_paths.add(k)
            m = re.search(r"papers_repo/(.*)$", k)
            if m:
                self.rel_paths.add(m.group(1))

    def has(self, eid: str, path) -> bool:
        if eid and eid in self.eids:
            return True
        k = ai.norm(path)
        if not k:
            return False
        if k in self.norm_paths:
            return True
        return any(k.endswith(rel) for rel in self.rel_paths)


def parse_ok(p: Path):
    """条目能否被 server/ingest.parse_entry 解析；None = 无法判定（导入失败）。"""
    try:
        sys.path.insert(0, str(BASE / "server"))
        import ingest  # noqa: PLC0415
        return bool(ingest.parse_entry(str(p)))
    except Exception:  # noqa: BLE001
        return None


# ── L1：队列（派生缓存）───────────────────────────────────────────────
def load_queue() -> list:
    if not ai.QUEUE.exists():
        return []
    out = []
    for line in ai.QUEUE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    return out


def classify(sha: str, paper: Path | None, kp_n: int, skips=frozenset()) -> str:
    """与 scan 的 _classify 同口径，但 entry 由磁盘直接判定。

    SKIP 优先：已 skip 的 sha 永远保持 SKIP，不能被重算成 P0/P4。
    否则 --fix 会把「栏目说明页」重新塞回 P0，导致无限回炉。
    """
    if sha in skips:
        return "SKIP"
    if paper is None:
        return "P0"
    flags = ai.quick_fm_flags(paper)  # 兼容 3/4 元组，按下标取值
    tags_ok, facets_ok, kp_marked = flags[0], flags[1], flags[2]
    return ai._classify(True, kp_n, facets_ok, tags_ok, kp_marked)


# ── L3：在飞批次 ──────────────────────────────────────────────────────
def load_open_batch():
    b = _read_json(OPEN_BATCH)
    return b if isinstance(b, dict) and b.get("shas") else None


# ── 并发心跳 ──────────────────────────────────────────────────────────
def check_heartbeat(takeover: bool, strict: bool = False):
    """并发判定。

    设计取舍：**可续跑优先**。诊断脚本是短命进程，PID 必然已退出；
    若按「10 分钟内就阻塞」会在「修复→复核」这种正常两步操作上误报。
    因此默认只在「上次心跳进程仍存活」时判为并发（+ 告警）；
    `--strict` 才按纯时间窗拦截（适合明确要求互斥的场合）。
    """
    prev = _read_json(HEARTBEAT) or {}
    mins = None
    stale = True
    try:
        last = datetime.fromisoformat(str(prev.get("last_seen")))
        mins = (datetime.now() - last).total_seconds() / 60.0
        stale = mins >= HEARTBEAT_STALE_MIN
    except Exception:  # noqa: BLE001
        pass
    alive = _pid_alive(prev.get("pid")) if prev.get("pid") else False
    fresh = (not stale)
    active = (not takeover) and (alive if not strict else fresh)
    return {"previous": prev, "minutes_ago": None if mins is None else round(mins, 1),
            "pid_alive": alive, "fresh": fresh, "strict": strict,
            "active": active, "warn": fresh and not alive}


def write_heartbeat(tag: str) -> None:
    _atomic_write_json(HEARTBEAT, {"pid": os.getpid(), "last_seen": _now(), "tag": tag})


# ── 主诊断 ────────────────────────────────────────────────────────────
def diagnose(fix: bool = False) -> dict:
    papers = load_papers()
    kp = load_kp_counts()
    idx = ai.load_index_ids()
    ledger = LedgerIndex()
    queue = load_queue()
    q_by_sha = {x.get("sha12"): x for x in queue if x.get("sha12")}

    # ---- 逐篇判定（L0）----
    # 判定口径（重要）：
    #   「检索可用」只取决于「已进索引 + 条目可解析」。索引建立的前提是条目解析成功，
    #   所以 indexed 即视为 retrievable，不必逐篇 parse（1701 篇逐个 yaml 解析约 12 分钟）。
    #   台账缺失**不等于**坏档：翻译管线（.zh.mono/.zh.dual）产出的条目从未进采集台账，
    #   但完全可检索 —— 单列为 unledgered（信息项），不进动作序列。
    blocking = []      # 真缺口：未进索引 / 解析失败 → 必须 commit 修复
    unledgered = []    # 可检索但采集台账无记录 → 仅记录，不阻断
    committed = 0
    for sha, p in papers.items():
        eid = "paper-" + sha
        idx_ok = eid in idx
        q = q_by_sha.get(sha) or {}
        led_ok = ledger.has(eid, q.get("path"))
        if idx_ok and led_ok:
            committed += 1
            continue
        if idx_ok and not led_ok:
            unledgered.append({
                "sha12": sha, "eid": eid, "in_queue": sha in q_by_sha,
                "has_payload": (ai.WIP_DIR / ("payload-%s.json" % sha)).exists(),
            })
            continue
        why = ["no_index"]
        pok = parse_ok(p)
        if pok is False:
            why.append("parse_fail")
        blocking.append({
            "sha12": sha, "eid": eid, "why": why, "parse_ok": pok,
            "in_queue": sha in q_by_sha,
            "has_payload": (ai.WIP_DIR / ("payload-%s.json" % sha)).exists(),
        })
    incomplete = blocking

    # ---- 孤儿：有中间产物、无条目（已 skip 的不算，否则会无限重派）----
    skips = ai.load_skips()
    payload_orphan, wip_orphan, skipped_residue = [], [], []
    if ai.WIP_DIR.exists():
        for f in sorted(ai.WIP_DIR.glob("payload-*.json")):
            sha = f.stem[len("payload-"):]
            if sha in papers:
                continue
            if sha in skips:
                skipped_residue.append({"sha12": sha, "have": ["payload"]})
                continue
            payload_orphan.append({"sha12": sha, "have": ["payload"], "in_queue": sha in q_by_sha})
        for f in sorted(ai.WIP_DIR.glob("*.md")):
            sha = f.stem
            if sha in papers:
                continue
            if (ai.WIP_DIR / ("payload-%s.json" % sha)).exists():
                continue  # 已归入 payload_orphan / skipped_residue
            if sha in skips:
                skipped_residue.append({"sha12": sha, "have": ["wip"]})
                continue
            wip_orphan.append({"sha12": sha, "have": ["wip"], "in_queue": sha in q_by_sha})

    # ---- 热区：已入库却仍有可回读副本 ----
    hot = []
    for sha in papers:
        files = []
        if (ai.WIP_DIR / ("%s.md" % sha)).exists():
            files.append("wip")
        if (ai.WIP_DIR / ("payload-%s.json" % sha)).exists():
            files.append("payload")
        if TEXT_DIR.exists() and any(TEXT_DIR.glob("%s*.txt" % sha)):
            files.append("text")
        if files:
            hot.append({"sha12": sha, "files": files})

    # ---- 队列对账（L1）----
    by_bucket = Counter()
    stale = []
    for x in queue:
        sha = x.get("sha12") or ""
        if not sha:
            continue
        newb = classify(sha, papers.get(sha), kp.get(sha, 0), skips)
        by_bucket[newb] += 1
        if newb != x.get("bucket"):
            stale.append({"sha12": sha, "old": x.get("bucket"), "new": newb})
            if fix:
                x["bucket"] = newb
                x["entry"] = papers.get(sha) is not None
                x["kp"] = kp.get(sha, 0)
    if fix and stale:
        tmp = ai.QUEUE.with_name(ai.QUEUE.name + ".tmp")
        tmp.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in queue) + "\n",
                       encoding="utf-8")
        os.replace(str(tmp), str(ai.QUEUE))

    # ---- 待办 P0（FIFO 排序，排除 SKIP）----
    todo = []
    for x in queue:
        sha = x.get("sha12") or ""
        if not sha or sha in papers or sha in skips:
            continue
        if classify(sha, None, 0, skips) != "P0":
            continue
        todo.append(x)
    todo.sort(key=lambda x: x.get("ledger_idx", 1 << 30))

    # ---- 批次对账 ----
    batch = load_open_batch()
    batch_info = None
    if batch:
        done, pending = [], []
        for sha in batch.get("shas") or []:
            p = papers.get(sha)
            if p is None:
                pending.append(sha)
                continue
            eid = "paper-" + sha
            q = q_by_sha.get(sha) or {}
            if (eid in idx) and ledger.has(eid, q.get("path")):
                done.append(sha)
            else:
                pending.append(sha)   # 已写未 commit → 归入 incomplete，不重复派发
        batch_info = {"batch_id": batch.get("batch_id"),
                      "status": "closed" if not pending else "partial",
                      "created_at": batch.get("created_at"),
                      "total": len(batch.get("shas") or []),
                      "done": done, "pending": pending}

    # ---- 事件日志 vs 磁盘（漂移提示，不参与判定）----
    try:
        states = ai.read_states()
        l2 = Counter((states.get(s) or {}).get("stage", "pending") for s in papers)
        l2 = dict(l2)
    except Exception:  # noqa: BLE001
        l2 = {}

    # ---- 动作序列（状态机）----
    actions = []
    seq = 0

    if incomplete:
        seq += 1
        shas = [x["sha12"] for x in incomplete]
        actions.append({
            "seq": seq, "role": "child", "id": "repair_commit",
            "title": "派子代理收口 %d 篇「条目已落盘但未进索引」" % len(shas),
            "why": "write 成功、commit 未执行。不进索引就永远检索不到，是最隐蔽的数据丢失。",
            "commands": ["%s scripts/agent_ingest.py commit %s --no-facet --force"
                         % (PY, " ".join("--sha %s" % s for s in shas)),
                         "%s scripts/build_facet_index.py" % PY],
            "note": "父代理不得执行；派一个收口子代理做 commit + evict，然后回执。",
        })

    if hot:
        seq += 1
        shas = [x["sha12"] for x in hot]
        actions.append({
            "seq": seq, "role": "child", "id": "evict_hot",
            "title": "派子代理出栈 %d 篇（已入库仍在热区）" % len(shas),
            "why": "把 wip 笔记/payload/全文缓存移入 state/_cold，阻断复读；只移不删，可 restore。",
            "commands": ["%s scripts/ctx_evict.py evict %s" % (PY, " ".join(shas))],
            "note": "父代理不得执行；并入收口子代理的任务。",
        })

    # 在飞批次里「连条目都没有」的篇，需要重新派子代理（已 skip 的排除）
    batch_pending = [s for s in (batch_info or {}).get("pending", [])
                     if s not in papers and s not in skips]
    retry = []
    for s in batch_pending:
        retry.append(s)
    for o in payload_orphan + wip_orphan:
        if o["sha12"] not in retry:
            retry.append(o["sha12"])

    if retry:
        seq += 1
        actions.append({
            "seq": seq, "role": "child", "id": "redispatch",
            "title": "派子代理重做 %d 篇半成品：%s" % (len(retry), " ".join(retry)),
            "why": "这些篇有 wip/payload 但没有条目，属于中断残留；G1 要求逐字段重写，不能复用旧 payload。",
            "shas": retry,
            "commands": [],
            "note": "每篇派一个 single 子代理：读全文 → 写 payload → commit(本篇) → evict(本篇) → 回执。父代理不介入。",
        })

    if todo and not retry:
        seq += 1
        actions.append({
            "seq": seq, "role": "parent", "id": "prep_next",
            "title": "取下一批 P0（当前待办 %d 篇）" % len(todo),
            "why": "FIFO 按台账行号，最早积压先清。prep 会硬跳过已入库者并落批次清单。",
            "commands": ["%s scripts/agent_ingest.py prep --limit 6 --bucket P0" % PY],
            "shas": [x["sha12"] for x in todo[:6]],
            "note": "父代理唯一写操作：只取批（不产生条目/索引/台账）。取完按 shas 派子代理。",
        })

    done = not actions
    result = {
        "generated_at": _now(),
        "env": {"queue_exists": ai.QUEUE.exists(),
                "nas_ok": bool(ai._pdf_root() and Path(ai._pdf_root()).exists()),
                "pdf_root": str(ai._pdf_root() or "")},
        "disk": {"papers": len(papers), "kpoints": sum(kp.values()),
                 "index_ids": len(idx),
                 "indexed_papers": sum(1 for s in papers if ("paper-" + s) in idx),
                 "ledger_rows": len(ledger.rows)},
        "judge": {"ingested": len(papers), "committed": committed,
                  "incomplete": len(incomplete),
                  "payload_orphan": len(payload_orphan), "wip_orphan": len(wip_orphan),
                  "unledgered": len(unledgered), "skipped_residue": len(skipped_residue),
                  "todo_p0": len(todo), "hot": len(hot)},
        "buckets_recomputed": dict(by_bucket),
        "queue": {"total": len(queue), "stale": len(stale),
                  "stale_sample": stale[:10], "fixed": bool(fix and stale)},
        "l2_stage_of_papers": l2,
        "incomplete": incomplete,
        "unledgered": unledgered,
        "orphans": payload_orphan + wip_orphan,
        "skipped_residue": skipped_residue,
        "batch": batch_info,
        "hotzone": hot,
        "actions": actions,
        "done": done,
    }
    return result


def render(ck: dict) -> None:
    j, d, q = ck["judge"], ck["disk"], ck["queue"]
    print("=" * 72)
    print("GeoKB 入库 · 断点诊断（唯一权威口径：磁盘事实）")
    print("=" * 72)
    print("磁盘  papers=%(papers)d  kpoints=%(kpoints)d  indexed_papers=%(indexed_papers)d"
          "  ledger=%(ledger_rows)d" % d)
    print("判定  ingested=%(ingested)d  committed=%(committed)d  incomplete=%(incomplete)d"
          "  todo_P0=%(todo_p0)d  hot=%(hot)d" % j)
    print("残留  payload_orphan=%(payload_orphan)d  wip_orphan=%(wip_orphan)d"
          "  ｜已 skip 残留=%(skipped_residue)d（不重派）" % j)
    print("信息  未入采集台账=%(unledgered)d（翻译管线产物，可检索，不阻断）" % j)
    print("队列  total=%d  stale=%d%s" % (q["total"], q["stale"], "（已修正）" if q["fixed"] else ""))
    print("分桶(重算) %s" % ck["buckets_recomputed"])
    if ck.get("l2_stage_of_papers"):
        print("事件日志(仅审计，不作数) %s" % ck["l2_stage_of_papers"])
    if not ck["env"]["nas_ok"]:
        print("⚠️ NAS 不可达（%s）：prep/write 会失败，先恢复挂载再跑。" % ck["env"]["pdf_root"])
    b = ck.get("batch")
    if b:
        print("在飞批次  %s  status=%s  done=%d/%d  pending=%s"
              % (b["batch_id"], b["status"], len(b["done"]), b["total"],
                 " ".join(b["pending"]) or "-"))
    if ck["incomplete"]:
        print("-" * 72)
        print("incomplete 明细：")
        for x in ck["incomplete"][:12]:
            print("  %s  why=%s  parse=%s  在队列=%s  有payload=%s"
                  % (x["sha12"], ",".join(x["why"]), x["parse_ok"],
                     x["in_queue"], x["has_payload"]))
        if len(ck["incomplete"]) > 12:
            print("  ... 另 %d 篇" % (len(ck["incomplete"]) - 12))
    print("-" * 72)
    if ck["done"]:
        print("NEXT  ✅ 已完成：P0 待办为 0，无 incomplete / 孤儿 / 热区残留。")
    else:
        print("NEXT  按下列顺序执行，全部做完后重新运行本脚本复核：")
        for a in ck["actions"]:
            print("  [%d] (%s) %s" % (a["seq"], a["role"], a["title"]))
            for c in a.get("commands") or []:
                print("      $ " + c)
            if a.get("note"):
                print("      · " + a["note"])
            if a.get("why"):
                print("      · " + a["why"])
    print("-" * 72)
    print("机器可读：%s" % CHECKPOINT)
    print("=" * 72)


def main() -> int:
    ap = argparse.ArgumentParser(description="GeoKB 入库断点诊断（唯一权威入口）")
    ap.add_argument("--json", action="store_true", help="只输出机器可读 JSON")
    ap.add_argument("--fix", action="store_true", help="按磁盘事实修正队列 stale 标记")
    ap.add_argument("--takeover", action="store_true", help="确认无并发，强制接管")
    ap.add_argument("--no-heartbeat", action="store_true", help="只读诊断，不写心跳")
    ap.add_argument("--strict", action="store_true",
                    help="按 10 分钟时间窗强制互斥（默认只在持有进程仍存活时拦截）")
    args = ap.parse_args()

    hb = check_heartbeat(args.takeover, strict=args.strict)
    if hb["active"] and not args.json:
        mins = hb["minutes_ago"]
        print("[并发] 上次心跳 %s 分钟前（pid=%s，进程存活=%s）。"
              % ("未知" if mins is None else ("%.1f" % mins),
                 hb["previous"].get("pid"), hb["pid_alive"]), file=sys.stderr)
        print("       可能仍有另一轮在写库。确认无并发后加 --takeover 重跑。", file=sys.stderr)
        return 3
    if hb["warn"] and not args.json:
        print("[提示] 上次心跳 %s 分钟前，但该进程已退出，判定无并发。"
              % ("未知" if hb["minutes_ago"] is None else ("%.1f" % hb["minutes_ago"])),
              file=sys.stderr)

    ck = diagnose(fix=args.fix)
    ck["heartbeat"] = {"previous_minutes_ago": hb["minutes_ago"],
                       "previous_pid_alive": hb["pid_alive"],
                       "takeover": args.takeover}
    _atomic_write_json(CHECKPOINT, ck)
    if not args.no_heartbeat:
        write_heartbeat("ingest_resume")

    if args.json:
        print(json.dumps(ck, ensure_ascii=False, indent=2))
    else:
        render(ck)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print("FAIL", type(exc).__name__, exc, file=sys.stderr)
        sys.exit(2)
