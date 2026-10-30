# -*- coding: utf-8 -*-
r"""GeoKB「Agent 亲自入库」工具（离线模式 · 零 DeepSeek 调用）。

用途
----
对**已下载 PDF 但未跑全入库流程**的积压文献，由 Agent 逐个阅读原文、撰写摘要 /
标签 / 知识点，再落盘条目、建索引、登记台账。**全程不调用任何 LLM API**。

入库流程五环节（缺任一即为积压）
------------------------------
1. PDF 已下载且可解析  台账 repo_path 含 papers_repo/，文件能打开，文本 ≥1500 字符
2. 条目已生成          content/papers/paper-<sha12>.md
3. 知识点已拆分        content/kpoints/kp-<sha12>-*.md 数量 > 0
4. 分面已归属          条目 frontmatter facets 至少 1 个分面非空
5. 索引已建立          index/geokb.db entries 表有该 eid

⚠️ 关联陷阱：条目与台账的关联键是 **sha1[:12]**（`paper-<sha12>`）。
历史台账行存在 eid 字段为空的情况，用 eid 关联会大面积误判（实测误判 1425 篇）。

⚠️ 不盲跑原则：只有**能实际打开且文本足够**的 PDF 才进入知识点拆分队列；
文件缺失 / 打不开 / 文本不足（需 OCR）的一律归为 PX，需人工介入，不硬写条目。

子命令
------
    scan                                扫描积压，生成队列 + 分桶统计
    prep   [--sha X] [--limit N] [--bucket P0]    生成待读工作笔记
    write  --sha X --payload F                    落条目 + 知识点（不碰索引/台账）
    commit --sha X | --all-written                分面回写 + 索引 + 台账登记
    verify                                        全库一致性校验
    status                                        进度快照

用法示例
--------
    python scripts/agent_ingest.py scan
    python scripts/agent_ingest.py prep --limit 5 --bucket P1
    python scripts/agent_ingest.py write --sha <sha12> --payload payload.json
    python scripts/agent_ingest.py commit --sha <sha12>
    python scripts/agent_ingest.py verify
"""
import argparse
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

_NO_WIN = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW：子孙全继承隐藏控制台
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = BASE / "scripts"
STATE = BASE / "state"
CONTENT = BASE / "content"
PAPERS_DIR = CONTENT / "papers"
KPOINTS_DIR = CONTENT / "kpoints"
DB = BASE / "index" / "geokb.db"

LEDGER = STATE / "papers_index.jsonl"
QUEUE = STATE / "agent_ingest_queue.jsonl"
STATE_FILE = STATE / "agent_ingest_state.jsonl"
WIP_DIR = STATE / "agent_ingest_wip"
SKIP_FILE = STATE / "agent_ingest_skip.jsonl"   # 永久跳过（如保密审稿稿）
BATCH_DIR = STATE / "agent_ingest_batches"      # 每批 prep 的清单（断点对账用）
OPEN_BATCH = BATCH_DIR / "open_batch.json"      # 当前在飞批次（commit 时按磁盘事实收口）

MIN_TEXT_CHARS = 1500          # 与 pdf_corpus_ingest.MIN_TEXT_CHARS 一致
# 每篇知识点条数区间。**没有下限**（用户口径 2026-10-23）：
# 收录判据是「能否用于团队今后的研究」，不是「这篇该有几条」。
# 信息量低的论文写 0 条完全正常（征稿简则、书评、纯工程地质描述）；
# 8 条是上限，防止把同一篇的边角料也拆成知识点。
KP_MIN, KP_MAX = 0, 8

# ---- Agent 亲自入库时间窗（用户口径 2026-10-22）----
# 仅 23:00 – 次日 08:00 允许执行「写」操作（write / commit）；
# 窗口内**零 DeepSeek 调用**，全部由 Agent 亲自读原文撰写。
# 边界：23:00 可执行、08:00 起不可执行。
WINDOW_START_H = 23
WINDOW_END_H = 8
# 2026-10-24 用户明令取消时间限制：任何时段均可入库。
# 保留窗口代码仅用于需要时重新启用（改成 True 即可），默认不阻断，
# 以免子代理/自动化忘记 --force 而被误伤。
ENFORCE_WINDOW = False
# 只读子命令不受窗口限制（不落盘、不调 API）
READONLY_CMDS = {"scan", "prep", "verify", "status", "window", "resume"}

sys.path.insert(0, str(SCRIPTS))
import kp_select  # noqa: E402  知识点筛选/质检引擎（与 kp_extract 共用同一套判据）
import kp_store   # noqa: E402  落盘内核（与 kp_extract 共用同一份模板/字段/索引）


# ──────────────────────────────────────────────────────────────────────
# 基础工具
# ──────────────────────────────────────────────────────────────────────
def window_state(now: datetime | None = None) -> dict:
    """Agent 亲自入库时间窗状态：23:00 – 次日 08:00（跨午夜）。

    返回 {ok, now, start, end, eta_open_min, left_min, reason}
    - ok=True  → 窗口内，允许 write / commit
    - ok=False → 窗口外，只读子命令仍可执行
    """
    now = now or datetime.now()
    WINDOW_LEN_H = 24 - WINDOW_START_H + WINDOW_END_H      # 23→8 共 9 小时
    d0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if now.hour >= WINDOW_START_H:            # 23:00–23:59 → 今晚这班（窗口内）
        start = d0.replace(hour=WINDOW_START_H)
    elif now.hour < WINDOW_END_H:             # 00:00–07:59 → 昨晚那班（窗口内）
        start = d0.replace(hour=WINDOW_START_H) - timedelta(days=1)
    else:                                     # 08:00–22:59 → 窗口外，指向今晚
        start = d0.replace(hour=WINDOW_START_H)
    end = start + timedelta(hours=WINDOW_LEN_H)
    ok = start <= now < end
    mins = lambda td: int(td.total_seconds() // 60)
    return {
        "ok": ok,
        "now": now.strftime("%Y-%m-%d %H:%M"),
        "start": start.strftime("%Y-%m-%d %H:%M"),
        "end": end.strftime("%Y-%m-%d %H:%M"),
        "left_min": mins(end - now) if ok else 0,
        "eta_open_min": 0 if ok else mins(start - now),
        "reason": "" if ok else f"非 agent 亲自入库时段（窗口 {WINDOW_START_H}:00–{WINDOW_END_H}:00）",
    }


def gate(cmd: str, force: bool = False):
    """写操作闸门。ENFORCE_WINDOW=False 时完全放行（时间限制已取消）。"""
    if cmd in READONLY_CMDS:
        return
    if not ENFORCE_WINDOW:
        return
    st = window_state()
    if st["ok"]:
        return
    if force:
        print(f"[WARN] --force：跳过时间窗检查（{st['now']}，距开窗 {st['eta_open_min']} 分钟）。"
              f"请确认本次操作**未调用** DeepSeek API。", file=sys.stderr)
        return
    print(f"[BLOCK] 当前 {st['now']} 不在 agent 亲自入库时段。", file=sys.stderr)
    print(f"        允许窗口：{WINDOW_START_H}:00 – 次日 {WINDOW_END_H}:00"
          f"（本次窗口 {st['start']} → {st['end']}）", file=sys.stderr)
    print(f"        距开窗还有 {st['eta_open_min']} 分钟。", file=sys.stderr)
    print("        窗口内一律不调 DeepSeek API，全程由 Agent 亲自完成。", file=sys.stderr)
    sys.exit(2)


def cmd_window(args):
    st = window_state()
    print("=" * 70)
    print("Agent 亲自入库时间窗")
    print("=" * 70)
    print(f"  当前时间 : {st['now']}")
    print(f"  本次窗口 : {st['start']} → {st['end']}")
    print(f"  状态     : {'✅ 窗口内，可 write / commit' if st['ok'] else '⛔ 窗口外，仅只读'}")
    if st["ok"]:
        print(f"  剩余     : {st['left_min']} 分钟")
    else:
        print(f"  距开窗   : {st['eta_open_min']} 分钟")
    print("  红线     : 窗口内零 DeepSeek 调用，摘要/标签/知识点全部由 Agent 亲自撰写")
    return 0


def sha1_file(path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def norm(p) -> str:
    return str(p or "").replace("\\", "/").strip().lower()


def atomic_write_text(path: Path, text: str) -> None:
    """原子写：先写 .tmp 再 os.replace。

    直接 write_text 在崩溃/断电时可能留下半截文件，而 scan 只看文件是否存在，
    会把半截条目误判成「已入库」。原子替换保证「要么没有，要么完整」。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


@contextlib.contextmanager
def cross_process_lock(name: str, timeout: float = 60.0):
    """跨进程互斥锁（Windows 用 msvcrt，POSIX 用 flock）。

    进程崩溃时由操作系统自动释放，不会留下"僵尸锁"——这是相对 lockfile 的关键优势。
    name 即锁文件主名（如 "write-<sha>" / "commit"）。
    """
    lockdir = STATE / "locks"
    lockdir.mkdir(parents=True, exist_ok=True)
    path = lockdir / ("%s.lock" % re.sub(r"[^0-9A-Za-z_.-]", "_", name))
    f = open(path, "a+b")
    deadline = time.time() + timeout
    acquired = False
    while True:
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            break
        except OSError:
            if time.time() >= deadline:
                break
            time.sleep(0.05)
    if not acquired:
        f.close()
        raise TimeoutError("等待锁超时: %s（可能另一进程仍在写）" % name)
    try:
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:  # noqa: BLE001
            pass
        f.close()


def write_open_batch(shas: list) -> str:
    """prep 落批次清单：记录这一批派了哪些篇，供任意中断点对账。"""
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    bid = now.strftime("%Y%m%d-%H%M%S")
    rec = {"batch_id": bid,
           "created_at": now.isoformat(timespec="seconds"),
           "shas": list(shas),
           "status": "open",
           "closed_at": None,
           "outcome": {}}
    body = json.dumps(rec, ensure_ascii=False, indent=2)
    atomic_write_text(BATCH_DIR / ("batch-%s.json" % bid), body)
    atomic_write_text(OPEN_BATCH, body)
    return bid


def reconcile_open_batch() -> dict | None:
    """按**磁盘事实**收口在飞批次；不依赖 state.jsonl。

    判定：paper 文件存在 且 已进索引 且 台账有记录 → done；否则 pending。
    """
    if not OPEN_BATCH.exists():
        return None
    try:
        rec = json.loads(OPEN_BATCH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    shas = rec.get("shas") or []
    if not shas:
        return None
    idx = load_index_ids()
    ledger_rows = load_ledger()
    led_norm = {norm(r.get("repo_path")) for r in ledger_rows if r.get("repo_path")}
    led_rel = set()
    for k in list(led_norm):
        m = re.search(r"papers_repo/(.*)$", k)
        if m:
            led_rel.add(m.group(1))
    led_eid = {r.get("eid") for r in ledger_rows if r.get("eid")}

    def _led_ok(eid, path):
        if eid in led_eid:
            return True
        k = norm(path)
        if not k:
            return False
        if k in led_norm:
            return True
        return any(k.endswith(rel) for rel in led_rel)

    q_by_sha = {}
    if QUEUE.exists():
        for line in QUEUE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    x = json.loads(line)
                    q_by_sha[x.get("sha12")] = x
                except Exception:  # noqa: BLE001
                    continue

    outcome, pending = {}, []
    for sha in shas:
        p = PAPERS_DIR / ("paper-%s.md" % sha)
        eid = "paper-%s" % sha
        if p.exists() and (eid in idx) and _led_ok(eid, (q_by_sha.get(sha) or {}).get("path")):
            outcome[sha] = "done"
        elif p.exists():
            outcome[sha] = "written_uncommitted"
            pending.append(sha)
        else:
            outcome[sha] = "not_written"
            pending.append(sha)
    rec["outcome"] = outcome
    rec["pending"] = pending
    rec["status"] = "closed" if not pending else "partial"
    rec["closed_at"] = datetime.now().isoformat(timespec="seconds") if not pending else None
    atomic_write_text(OPEN_BATCH, json.dumps(rec, ensure_ascii=False, indent=2))
    atomic_write_text(BATCH_DIR / ("batch-%s.json" % rec["batch_id"]),
                      json.dumps(rec, ensure_ascii=False, indent=2))
    return rec


def load_ledger() -> list:
    rows = []
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8", errors="ignore").split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return rows


def resolve_nas_path(rec: dict):
    rp = rec.get("repo_path") or ""
    if not rp:
        return None
    for cand in (rp, rp.replace("\\", "/"), rp.replace("/", "\\")):
        try:
            p = Path(cand)
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def read_fm(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    m = re.match(r"^---\n(.*?)\n---", raw, re.S)
    if not m:
        return {}
    try:
        import yaml
        d = yaml.safe_load(m.group(1))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def load_skips() -> set:
    """永久跳过的 sha12 集合（state/agent_ingest_skip.jsonl）。

    典型用途：`FOR PEER REVIEW - CONFIDENTIAL` 的审稿中稿件——含审稿人身份与评语，
    出版方声明未经书面同意不得讨论/转发/上网。这类条目应排除，且不能让它卡在
    FIFO 队首（否则每次 scan 都排第一，永远清不动）。
    """
    if not SKIP_FILE.exists():
        return set()
    out = set()
    try:
        for l in SKIP_FILE.read_text(encoding="utf-8").splitlines():
            if l.strip():
                out.add(json.loads(l).get("sha12", ""))
    except Exception:  # noqa: BLE001
        pass
    out.discard("")
    return out


def cmd_skip(args):
    """把 sha12 加入永久跳过列表。"""
    if not args.sha:
        print("需要 --sha", file=sys.stderr)
        return 1
    SKIP_FILE.parent.mkdir(parents=True, exist_ok=True)
    rec = {"sha12": args.sha, "reason": args.reason or "",
           "at": datetime.now().isoformat(timespec="seconds")}
    with open(SKIP_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("已跳过 %s（%s）" % (args.sha, args.reason or "无备注"))
    print("下次 scan 后它不再出现在队列。")
    return 0


def probe_pdf(path: Path):
    """轻量探测 → (页数, 文本字符数)；打不开或无文本返回 (0, 0)。"""
    try:
        import pdf_corpus_ingest as pci
        sha = sha1_file(path)
        text = pci.extract_text(str(path), sha) or ""
        return (1, len(text))
    except Exception:  # noqa: BLE001
        return (0, 0)


def load_index_ids() -> set:
    if not DB.exists():
        return set()
    try:
        import sqlite_vec
        conn = sqlite3.connect(str(DB))
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        ids = {r[0] for r in conn.execute("SELECT id FROM entries")}
        conn.close()
        return ids
    except Exception as e:  # noqa: BLE001
        print("(warn) 索引读取失败:", type(e).__name__, e, file=sys.stderr)
        return set()


# ──────────────────────────────────────────────────────────────────────
# scan
# ──────────────────────────────────────────────────────────────────────
def _classify(entry_ok, kp_n, facets_ok, tags_ok, kp_marked=False,
              extraction: str = "") -> str:
    if not entry_ok:
        return "P0"
    # 离线/正则兜底稿（extraction=offline|fallback）只是**骨架**：标题取自台账、
    # 摘要是正则截的、tags 是关键词行抽的，**没有任何模型读过正文**。
    # 若按普通条目判成 P1（只等补知识点），它会永久停在 P1 无人重做 ——
    # 2026-10-28 批量轻量入库 1399 篇时暴露，故直接判 P0（= 需要完整重做）。
    if extraction in ("offline", "fallback"):
        return "P0"
    # kp_marked = 条目里有 kp_count 字段，说明知识点环节**已经判定过**。
    # 判成 0 条是合法结论（不可复用），不能再当 P1 反复回炉。
    if kp_n == 0 and not kp_marked:
        return "P1"
    if not facets_ok:
        return "P2"
    if not tags_ok:
        return "P3"
    return "P4"


TAGS_RE = re.compile(r"(?m)^tags:\s*\n(?:\s*-\s+\S.*\r?\n?)+")
FACETS_RE = re.compile(r"(?ms)^facets:\s*\n(.*?)(?:\r?\n\S|\Z)")


def quick_fm_flags(path: Path):
    """只判断 frontmatter 的 tags / facets / kp_count / extraction，不解析完整 YAML。

    逐行 yaml.safe_load 实测约 0.5s/文件（1434 篇 ≈ 12 分钟），
    而 scan 只需要几个标志，用正则即可，秒级完成。

    ⚠️ 返回 4 元组。旧版异常分支只返回 2 个值，解包会 ValueError —— 一并修掉。
    """
    empty = (False, False, False, "")
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return empty
    m = re.match(r"^---\r?\n(.*?)\r?\n---", raw, re.S)
    if not m:
        return empty
    fm = m.group(1)
    tags_ok = bool(TAGS_RE.search(fm))
    f = FACETS_RE.search(fm)
    facets_ok = bool(f and re.search(r"(?m)^\s+-\s+\S", f.group(1)))
    kp_marked = bool(re.search(r"(?m)^kp_count:\s*\d+", fm))
    ex = re.search(r"(?m)^extraction:\s*[\"']?([\w-]+)", fm)
    return tags_ok, facets_ok, kp_marked, (ex.group(1) if ex else "")


def _row(r: dict, rp, sha: str, forced_bucket, entry_files,
         kp_by_sha, indexed, chars: int, note: str = "",
         ledger_idx: int = -1) -> dict:
    """ledger_idx = 台账行号（0 基）。台账是追加式 jsonl，行号即「进入积压的先后」。

    ⚠️ FIFO 口径（用户 2026-10-23）：队列一律按 ledger_idx 升序处理，
    即**最早积压的先清**。新下载的论文天然排在队尾，不打断当前顺序，
    因此不需要「清完」——只需持续从队首往下吃。
    """
    sha12 = sha[:12] if sha else ""
    eid = ("paper-" + sha12) if sha12 else ""
    entry_ok = bool(sha12) and (eid in entry_files)
    idx_ok = bool(sha12) and (eid in indexed)
    kp_n = kp_by_sha.get(sha12, 0) if sha12 else 0
    tags_ok, facets_ok, kp_marked, extraction = (quick_fm_flags(entry_files[eid])
                                                if entry_ok
                                                else (False, False, False, ""))
    if sha12 and sha12 in load_skips():
        bucket = "SKIP"          # 永久跳过，不占队首
    else:
        bucket = forced_bucket or _classify(entry_ok, kp_n, facets_ok, tags_ok,
                                           kp_marked, extraction)
    return {
        "sha12": sha12, "sha1": sha, "eid": eid,
        "title": r.get("title") or "", "journal": r.get("journal") or "",
        "year": (r.get("date") or "")[:4], "date": r.get("date") or "", "bucket": bucket,
        "entry": entry_ok, "index": idx_ok, "kp": kp_n,
        "facets": facets_ok, "tags": tags_ok,
        "path": str(rp), "url": r.get("url") or "",
        "doi": r.get("doi") or "", "source": r.get("source") or "",
        "chars": chars, "note": note,
        "ledger_idx": ledger_idx,
        "indexed_at": r.get("indexed_at") or "",
    }


def _is_derivative(p: Path) -> bool:
    """翻译产物（.zh.mono/.zh.dual）与 _fetch 重复产物不是独立文献。"""
    n = p.name.lower()
    return any(k in n for k in (".zh.mono", ".zh.dual", "_fetch.pdf"))


def _pdf_root():
    try:
        import paths
        return paths.pdf_root()
    except Exception:  # noqa: BLE001
        return None


def cmd_scan(args):
    import time as _t
    _t0 = _t.time()

    def _m(s):
        if args.debug:
            print("  [%5.1fs] %s" % (_t.time() - _t0, s), flush=True)

    _m("load ledger")
    rows = load_ledger()
    _m("glob papers")
    entry_files = {p.stem: p for p in PAPERS_DIR.glob("*.md")}
    _m("glob kpoints")
    kp_by_sha = Counter()
    for p in KPOINTS_DIR.glob("kp-*.md"):
        parts = p.stem.split("-")
        if len(parts) >= 3:
            kp_by_sha[parts[1]] += 1
    _m("load index")
    indexed = load_index_ids()

    # 一次遍历 NAS 建立「规范化路径 → 真实 Path」映射，避免逐行 SMB stat
    _m("scan NAS")
    root = _pdf_root()
    nas_map = {}
    if root:
        for p in Path(root).glob("by_journal/**/*.pdf"):
            nas_map[norm(p)] = p
    _m("NAS files: %d" % len(nas_map))

    def _match(rp_raw: str):
        if not rp_raw:
            return None
        k = norm(rp_raw)
        if k in nas_map:
            return nas_map[k]
        # repo_path 可能是 papers_repo/... 相对形态
        m = re.search(r"papers_repo/(.*)$", k)
        if m and root:
            return nas_map.get(norm(Path(root) / m.group(1)))
        return None

    out = []
    for i, r in enumerate(rows):
        if args.debug and i % 200 == 0:
            _m("row %d/%d" % (i, len(rows)))
        sha = (r.get("sha1") or "").strip()
        rp = _match(r.get("repo_path"))
        if not rp:
            continue                      # 无可达 PDF → 不在本模式范围
        # ⚠️ 不在这里算文件 sha1：台账绝大多数行没有 sha1 字段，
        #    逐个读 NAS 上的大文件会极慢（实测 ~0.5s/篇 × 1485 ≈ 12 分钟）。
        #    sha12 优先取 sha1[:12]，否则从 eid（paper-<sha12>）反推。
        if not sha:
            eid_raw = str(r.get("eid") or "")
            if eid_raw.startswith("paper-"):
                sha = eid_raw[6:]
        chars = -1
        if args.probe:
            pages, chars = probe_pdf(rp)
            if pages <= 0 or chars < MIN_TEXT_CHARS:
                out.append(_row(r, rp, sha, "PX", entry_files, kp_by_sha,
                                indexed, chars,
                                "PDF 不可打开" if pages <= 0 else "文本不足，需 OCR",
                                ledger_idx=i))
                continue
        out.append(_row(r, rp, sha, None, entry_files, kp_by_sha,
                        indexed, chars, ledger_idx=i))

    # NAS 孤儿：有 PDF 但台账无对应记录（纯内存比对，零 SMB 探测）
    ledger_full = {norm(r.get("repo_path")) for r in rows if r.get("repo_path")}
    ledger_base = {k.rsplit("/", 1)[-1] for k in ledger_full}
    for p in sorted(nas_map.values()):
        k = norm(p)
        if k in ledger_full or k.rsplit("/", 1)[-1] in ledger_base:
            continue
        if _is_derivative(p):
            continue
        try:
            sha = sha1_file(p)
        except OSError:
            continue
            out.append({
                "sha12": sha[:12], "sha1": sha, "eid": "paper-" + sha[:12],
                "title": p.stem, "journal": p.parent.parent.name,
                "year": "", "bucket": "ORPHAN",
                "entry": False, "index": False, "kp": 0,
                "facets": False, "tags": False,
                "path": str(p), "url": "", "doi": "", "source": "",
                "chars": "台账无记录，需读 PDF 认领",
            })

    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4, "ORPHAN": 5, "PX": 9}
    out.sort(key=lambda x: (order.get(x["bucket"], 9), x["journal"] or ""))

    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    with open(QUEUE, "w", encoding="utf-8") as f:
        for it in out:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")

    c = Counter(x["bucket"] for x in out)
    print("=" * 70)
    print("积压清单")
    print("=" * 70)
    print("台账总行数 :", len(rows))
    print("PDF 可达   :", sum(1 for x in out if x["bucket"] != "PX"))
    print()
    for b in ("P0", "P1", "P2", "P3", "P4", "ORPHAN", "PX"):
        print("  %-7s: %d" % (b, c.get(b, 0)))
    need = c.get("P0", 0) + c.get("P1", 0) + c.get("P2", 0) + c.get("P3", 0)
    print()
    print(">>> 待 Agent 入库 :", need)
    print(">>> 不可入库（PX）:", c.get("PX", 0))
    print("队列文件 :", QUEUE)
    return 0


# ──────────────────────────────────────────────────────────────────────
# prep：生成待读工作笔记
# ──────────────────────────────────────────────────────────────────────
NUM_RE = re.compile(
    r"(?<![\w.])(?:\d+(?:\.\d+)?(?:\s*(?:±|–|—|-|to|~)\s*\d+(?:\.\d+)?)?)"
    r"\s*(?:Ma|Ga|ka|°C|℃|wt\.?%|ppm|ppb|‰|permil|wt%|%|km|m\b|mm|µm|g/t|mg/L)"
)
HEAD_RE = re.compile(
    r"^\s*(?:\d+\.?\s+)?(abstract|摘要|introduction|引言|methods?|方法|"
    r"results?|结果|discussion|讨论|conclusions?|结论)\s*$", re.I)


def cmd_prep(args):
    if not QUEUE.exists():
        print("队列不存在，先跑 scan", file=sys.stderr)
        return 1
    items = [json.loads(l) for l in QUEUE.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    if args.bucket:
        items = [x for x in items if x["bucket"] in args.bucket]
    if args.sha:
        items = [x for x in items if x["sha12"] in args.sha]
    items = [x for x in items if x["bucket"] not in ("P4", "SKIP")]
    # FIFO：按台账行号升序 = 最早积压的先处理（用户口径 2026-10-23）
    items.sort(key=lambda x: x.get("ledger_idx", 1 << 30))

    # ── 硬防重（2026-10-24）：条目已存在的篇一律不 prep ──────────────
    # 旧实现只信队列 bucket，队列一旦滞后就会把已入库论文重新派给子代理，
    # 既浪费额度又可能用新 payload 覆盖旧条目（质量回退）。此处以磁盘为准。
    done_already = [x["sha12"] for x in items
                    if (PAPERS_DIR / ("paper-%s.md" % x["sha12"])).exists()]
    if done_already:
        print("  [guard] 跳过 %d 篇：条目已在磁盘（视为已入库，禁止重复 prep）：%s%s"
              % (len(done_already), " ".join(done_already[:8]),
                 " ..." if len(done_already) > 8 else ""))
    items = [x for x in items
             if not (PAPERS_DIR / ("paper-%s.md" % x["sha12"])).exists()]

    if args.limit:
        items = items[: args.limit]

    WIP_DIR.mkdir(parents=True, exist_ok=True)
    print("待处理 %d 篇 → %s" % (len(items), WIP_DIR))
    prepped = []
    for it in items:
        try:
            import pdf_corpus_ingest as pci
            text = pci.extract_text(str(it["path"]), it["sha1"]) or ""
        except Exception as e:  # noqa: BLE001
            print("  跳过 %s（抽取失败: %s）" % (it["sha12"], e))
            continue
        if len(text) < MIN_TEXT_CHARS:
            print("  跳过 %s（文本不足 %d）" % (it["sha12"], len(text)))
            continue
        lines = text.splitlines()
        heads = [(i + 1, ln.strip()) for i, ln in enumerate(lines)
                 if HEAD_RE.match(ln)]
        nums = [(i + 1, ln.strip()[:110]) for i, ln in enumerate(lines)
                if NUM_RE.search(ln)]
        tpl = {
            "title": "<取自原文首页标题>",
            "lang": "en",
            "summary": "<200-400 字中文摘要，含关键数值与结论>",
            "tags": ["<taxonomy 规范词>", "<英文具体词>"],
            "abstract": "<摘要原文或凝练>",
            "key_content": "<关键内容>",
            "core_methods": ["<方法>"],
            "study_area": "<研究区>",
            "scenarios": ["<应用场景>"],
            "title_zh": "<中文译名>",
            "key_content_zh": "<中文关键内容>",
            "methods_zh": ["<中文方法>"],
            "notes": ["<待说明事项>"],
            "kps": [{"title": "<≤26 字，含对象与属性>",
                     "body": "<100-260 字：结论 + 原文数值 + 限定条件>",
                     "type": "<" + " / ".join(kp_select.TYPE_ORDER) + ">",
                     "reuse": "<" + " / ".join(kp_select.REUSE_ORDER) + ">",
                     "why": "<今后怎么用它，≤40 字>",
                     "value": "<0-10：9-10 必被引用｜7-8 存档｜5-6 一般｜≤4 局部>",
                     "evidence": "L100-L112", "tags": ["<规范词>"]}],
            "_kps_rule": "收录判据=能否用于团队今后研究（同矿种可对标/同区构造背景可类比/"
                         "新方法可迁移/解释判据可借鉴）。0-8 条，**没有下限**："
                         "信息密度低的论文写 0 条是正常结论，不要凑数。",
            "_hard_gate": [
                "禁止复用旧 payload：已存在 payload-<sha>.json 时必须逐字段重写，不得直接 write",
                "type/reuse/why/value 四要素缺一不可（缺 why→kp_why:null，缺 value→kp_ai_value:null）",
                "evidence 只写 L<起>-L<止>，多条用英文逗号；禁括号、章节名、中文注释",
                "summary 中文 200-400 字；英文 ≤1000 字符且不得整段照抄 Abstract",
                "kp body 100-260 字",
                "序号 ①-⑧ 不得紧贴数字（⑥950-900℃ 会被数字分词读成 6950 直接 FAIL）",
                "commit 后 facets 不得为 {}（为空立即补分面）",
            ],
        }
        wip = [
            "# 待入库 · `%s`" % it["sha12"],
            "",
            "| 项 | 值 |",
            "|---|---|",
            "| 期刊 | %s |" % (it.get("journal") or ""),
            "| 年份 | %s |" % (it.get("year") or ""),
            "| 台账标题 | %s |" % ((it.get("title") or "")[:80]),
            "| 分桶 | %s |" % it.get("bucket"),
            "| 文本字符数 | %d |" % len(text),
            "| 路径 | `%s` |" % it.get("path"),
            "",
            "## 开头 1200 字",
            "",
            "```text",
            text[:1200],
            "```",
            "",
            "## 各章节开头（行号）",
            "",
        ]
        for j, (i, h) in enumerate(heads[:12]):
            end = heads[j + 1][0] - 1 if j + 1 < len(heads) else len(lines)
            seg = "\n".join(lines[i:min(end, i + 45)]).strip()[:900]
            wip.append("### L%d · %s" % (i, h))
            wip.append("")
            wip.append("```text")
            wip.append(seg)
            wip.append("```")
            wip.append("")
        wip += ["", "## 数值候选行（行号）", ""]
        for i, ln in nums[:60]:
            wip.append("- L%d: %s" % (i, ln))
        wip += [
            "",
            "## 待填模板 → payload.json",
            "",
            "```json",
            json.dumps(tpl, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
        atomic_write_text(WIP_DIR / ("%s.md" % it["sha12"]), "\n".join(wip))
        prepped.append(it["sha12"])
        print("   -", it["sha12"], "|", it.get("journal"),
              "|", (it.get("title") or "")[:46])
    if prepped:
        bid = write_open_batch(prepped)
        print("批次清单: %s（%d 篇）→ %s" % (bid, len(prepped), OPEN_BATCH))
        print("  ⚠️ 这一批必须先 commit 收口，再 prep 下一批；中断后跑 ingest_resume.py 对账。")
    return 0


# ──────────────────────────────────────────────────────────────────────
# write：落条目 + 知识点
# ──────────────────────────────────────────────────────────────────────
def _yml(s) -> str:
    s = str(s or "").replace("\r", " ").replace("\n", " ").strip()
    return json.dumps(re.sub(r"\s{2,}", " ", s), ensure_ascii=False)


def dump_fm(d: dict) -> str:
    out = ["---"]
    for k in ("id", "title", "module", "lang", "summary", "confidence", "status",
              "updated", "pub_date", "journal", "year", "extraction", "origin", "remarks",
              "parent", "evidence", "source_tier", "kp_count", "kp_version",
              "kp_type", "kp_reuse", "kp_why"):
        v = d.get(k)
        if v in (None, ""):
            continue
        out.append("%s: %s" % (k, _yml(v)))
    if d.get("tags"):
        out.append("tags:")
        for t in d["tags"]:
            out.append("- %s" % _yml(t))
    if d.get("sources"):
        out.append("sources:")
        for s in d["sources"]:
            out.append("- name: %s" % _yml(s.get("name")))
            out.append("  path: %s" % _yml(s.get("path")))
            if s.get("url"):
                out.append("  url: %s" % _yml(s.get("url")))
            if s.get("access"):
                out.append("  accessed: %s" % _yml(s.get("access")))
    facets = d.get("facets") or {}
    if facets:
        out.append("facets:")
        for fk, fv in facets.items():
            if not fv:
                continue
            out.append("  %s:" % fk)
            for v in fv:
                out.append("  - %s" % _yml(v))
    out.append("---")
    return "\n".join(out)


def _rel_path(p) -> str:
    rel = str(p or "").replace("\\", "/")
    m = re.search(r"papers_repo/", rel)
    return rel[m.start():] if m else rel


def _pub_date(it: dict) -> str:
    """发布日期（2026-10-24）：台账 date -> day=01 归一到 YYYY-MM；无则退 year。"""
    dt = str(it.get("date") or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", dt):
        return dt[:7] if dt.endswith("-01") else dt
    if re.match(r"^\d{4}-\d{2}$", dt):
        return dt
    if re.match(r"^\d{4}$", dt):
        return dt
    y = str(it.get("year") or "").strip()
    return y if re.match(r"^\d{4}$", y) else ""


def build_entry_md(it: dict, payload: dict) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    _en = (payload.get("lang") or "en") == "en"
    # 英文条目 frontmatter 的 summary **必须是英文**（与 DeepSeek 流程一致）：
    # 优先 summary_en → 回退 summary（旧 payload，中文）→ 再回退英文摘要截断。
    if _en:
        _s = (payload.get("summary_en") or payload.get("summary")
              or (payload.get("abstract") or "")[:300])
    else:
        _s = payload.get("summary") or ""
    fm = {
        "id": it["eid"],
        "title": payload.get("title") or it.get("title"),
        "module": "papers",
        "lang": payload.get("lang") or "en",
        "tags": payload.get("tags") or [],
        "summary": _s,
        "confidence": payload.get("confidence") or "B",
        "status": payload.get("status") or "active",
        "updated": today,
        "pub_date": _pub_date(it),
        "sources": [{
            "name": it.get("journal") or "",
            "path": _rel_path(it.get("path")),
            "url": "https://sjck.kust.work/api/entry/%s/pdf" % it["eid"],
            "access": today,
        }],
        "journal": it.get("journal") or "",
        "year": it.get("year") or "",
        "extraction": "agent",
        # 知识点处理标记：scan 据此判断「已按新算法处理过」。
        # 没有它，0 条知识点的论文会被永久判成 P1（有条目、无知识点）反复回炉。
        "kp_count": len(payload.get("kps") or []),
        "kp_version": kp_select.KP_VERSION,
    }
    if payload.get("remarks"):
        fm["remarks"] = payload["remarks"]
    if payload.get("origin"):
        fm["origin"] = payload["origin"]

    abstract = payload.get("abstract") or payload.get("summary") or "（未能提取）"
    cm = payload.get("core_methods") or []
    sc = payload.get("scenarios") or []
    rel = _rel_path(it.get("path"))
    journal = it.get("journal") or ""
    year = it.get("year") or ""

    def md_list(items):
        items = [x for x in (items or []) if x]
        return "\n".join("- %s" % x for x in items) if items else "- （未能提取）"

    if (payload.get("lang") or "en") == "en":
        # ── 英文论文：**正文以英文原文为主**，中文仅作小字注解 ──────────
        # 与 scripts/pdf_corpus_ingest.py 的 DeepSeek 模板严格对齐。
        kc_en = payload.get("key_content_en") or payload.get("key_content")
        cm_en = payload.get("core_methods_en") or cm
        sa_en = payload.get("study_area_en") or payload.get("study_area")
        sc_en = payload.get("scenarios_en") or sc
        for _k, _v in (("key_content_en", payload.get("key_content_en")),
                       ("core_methods_en", payload.get("core_methods_en")),
                       ("study_area_en", payload.get("study_area_en")),
                       ("scenarios_en", payload.get("scenarios_en"))):
            if not _v:
                print("  [WARN] 英文条目缺 %s，已回退中文——与 DeepSeek 模板不一致，"
                      "请在 payload 中补英文字段" % _k, file=sys.stderr)
        body = (
            "## Abstract\n\n{ab}\n\n## Key Content\n\n{kc}\n\n"
            "## Core Methods\n\n{cm}\n\n## Study Area\n\n{sa}\n\n"
            "## Application Scenarios\n\n{sc}\n\n## 中文翻译\n\n"
            "<small>**标题**：{tz}</small>\n\n"
            "<small>**摘要**：{kz}</small>\n\n"
            "<small>**方法**：{mz}</small>\n\n"
            "<small>**区域**：{sz}</small>\n\n"
            "<small>**应用**：{az}</small>\n\n"
            "<small>**矿床类型**：{dz}</small>\n\n"
            "## 来源\n\n"
            "- 期刊：{journal}（{year}）\n"
            "- 中文标题：{tz}\n"
            "- 仓库文件：`{rel}`\n"
        ).format(
            ab=abstract,
            kc=kc_en or "（未能提取）",
            cm=md_list(cm_en),
            sa=sa_en or "（未能提取）",
            sc=md_list(sc_en),
            tz=payload.get("title_zh") or "（未能提取）",
            kz=payload.get("key_content_zh") or payload.get("key_content") or "（未能提取）",
            mz="、".join(payload.get("core_methods_zh")
                         or payload.get("methods_zh") or cm) or "（未能提取）",
            sz=payload.get("study_area_zh") or payload.get("study_area") or "（未能提取）",
            az="、".join(payload.get("scenarios_zh") or sc) or "（未能提取）",
            dz="、".join(payload.get("deposit_types_zh") or []) or "（未能提取）",
            journal=journal or "未知", year=year or "年份未知", rel=rel,
        )
    else:
        # ── 中文论文：中文优先（与 DeepSeek 中文模板一致，无「摘要」区块）──
        body = (
            "## 关键内容\n\n{kc}\n\n"
            "## 核心方法\n\n{cm}\n\n## 研究区域\n\n{sa}\n\n"
            "## 应用场景\n\n{sc}\n\n"
            "## 来源\n\n"
            "- 期刊：{journal}（{year}）\n"
            "- 仓库文件：`{rel}`\n"
        ).format(
            kc=payload.get("key_content") or "（未能提取）",
            cm=md_list(cm),
            sa=payload.get("study_area") or "（未能提取）",
            sc=md_list(sc),
            journal=journal or "未知", year=year or "年份未知", rel=rel,
        )

    notes = payload.get("notes") or []
    if notes:
        body += "\n## 备注\n\n" + "\n".join("- %s" % x for x in notes) + "\n"
    return dump_fm(fm) + "\n\n" + body


_PLACEHOLDER_RE = re.compile(r"^<.*>$")


def validate_payload(payload: dict) -> tuple[list, list]:
    """payload 必填与截断检查（write 的统一入口，CLI / MCP 都走这里）。

    2026-10-24 加入：实测 `{"kps": []}` 这种空 payload 能被写入并入库，
    产出「有 id、无摘要、lang 默认成 en」的垃圾条目。入库是不可逆的，
    所以必须在落盘前把「空 / 模板占位符 / 明显截断」拦掉。

    返回 (errors, warnings)：errors 非空即拒绝写入。
    """
    errs, warns = [], []
    if not isinstance(payload, dict):
        return ["payload 必须是 JSON 对象"], []

    lang = str(payload.get("lang") or "").strip().lower()
    if lang not in ("zh", "en"):
        errs.append("lang 必须显式写 zh 或 en（当前 %r）" % payload.get("lang"))

    def _empty(v):
        if v is None:
            return True
        if isinstance(v, str):
            return not v.strip()
        if isinstance(v, (list, dict)):
            return len(v) == 0
        return False

    for k in ("title", "summary", "abstract", "key_content"):
        v = payload.get(k)
        if isinstance(v, str) and _PLACEHOLDER_RE.match(v.strip()):
            errs.append("%s 仍是模板占位符：%s" % (k, v))
        elif _empty(v):
            errs.append("%s 为空" % k)

    tags = payload.get("tags")
    if _empty(tags) or not isinstance(tags, list):
        errs.append("tags 必须是非空数组")

    if "kps" not in payload or not isinstance(payload.get("kps"), list):
        errs.append("kps 字段缺失或不是数组（0 条也必须显式写 []）")
    else:
        n = len(payload["kps"])
        if not (KP_MIN <= n <= KP_MAX):
            errs.append("知识点条数 %d 不在 [%d,%d]" % (n, KP_MIN, KP_MAX))

    s = (payload.get("summary") or "")
    if lang == "zh" and s:
        if len(s) < 150:
            errs.append("summary 仅 %d 字，疑似截断（要求 200–400）" % len(s))
        elif len(s) < 200 or len(s) > 400:
            warns.append("summary %d 字，超出 200–400 的设计区间" % len(s))

    if lang == "en":
        se = payload.get("summary_en") or ""
        if _empty(se):
            errs.append("英文条目必须提供 summary_en（原文抽取，禁回译）")
        elif len(se) > 1000:
            errs.append("summary_en 超过 1000 字符")
    return errs, warns


def write_paper(sha12: str, payload: dict) -> dict:
    """落条目 + 知识点（**唯一实现**，CLI 与 MCP 共用）。

    返回结构化结果，服务层可直接回给 agent，不必解析中文 stderr：
      {ok, sha12, kp, soft[], hard[], error}
    """
    items = [json.loads(l) for l in QUEUE.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    it = next((x for x in items if x["sha12"] == sha12), None)
    if not it:
        return {"ok": False, "sha12": sha12, "error": "队列中找不到 sha12"}

    errs, warns = validate_payload(payload)
    for w in warns:
        print("  [WARN] %s" % w, file=sys.stderr)
    if errs:
        return {"ok": False, "sha12": sha12, "invalid": errs, "soft": warns,
                "error": "payload 不完整，已拒绝写入：" + "；".join(errs)}

    kps = payload.get("kps") or []
    if not (KP_MIN <= len(kps) <= KP_MAX):
        return {"ok": False, "sha12": sha12,
                "error": "知识点条数 %d 不在 [%d,%d]" % (len(kps), KP_MIN, KP_MAX)}

    # ── 知识点质检（与 API 管线同一套判据：kp_select）──────────────
    # 硬错拦截：数值在原文找不到 / 同篇重复 / 正文过短过长 / 行号格式错。
    # 软警只提示：信息量偏低、疑似套话、标题过长。
    # 原文用 state/text 全文（不是截断版），否则结论段的数值会被误判成「编造」。
    source = ""
    try:
        import pdf_corpus_ingest as pci
        source = pci.extract_text(str(it["path"]), it["sha1"]) or ""
    except Exception as e:  # noqa: BLE001
        print("  [WARN] 原文读取失败，跳过数值校验: %s" % e, file=sys.stderr)

    hard, soft = [], []
    for i, kp in enumerate(kps, start=1):
        r = kp_select.check_kp(kp, source or "", peers=[k for j, k in enumerate(kps) if j != i - 1])
        hard += ["kp%d: %s" % (i, e) for e in r["errors"]]
        soft += ["kp%d: %s" % (i, w) for w in r["warnings"]]
    for s in soft:
        print("  [WARN] %s" % s, file=sys.stderr)
    if hard:
        for e in hard:
            print("  FAIL %s" % e, file=sys.stderr)
        print("  → 请修 payload 后重试（硬错不入库，避免脏数据进索引）", file=sys.stderr)
        return {"ok": False, "sha12": sha12, "hard": hard, "soft": soft,
                "error": "硬错不入库，请修 payload 后重试"}
    if not kps:
        print("  [INFO] 本篇判定为 0 条知识点（合法）：收录判据是可复用性，"
              "不是条数。已写 kp_count=0，scan 不会再把本篇当 P1。", file=sys.stderr)

    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    KPOINTS_DIR.mkdir(parents=True, exist_ok=True)

    entry_md = build_entry_md(it, payload)
    atomic_write_text(PAPERS_DIR / ("%s.md" % it["eid"]), entry_md)

    # 知识点落盘统一走 kp_store：模板/字段/归档与 API 链路同一份代码。
    # 不再逐个 unlink 旧文件（本机批量删除守卫会在第 50 次拦截，曾中止 41 篇），
    # 改为写完 1..n 后把多余编号 rename 到 state/kp_stale/<sha12>/。
    kids, _moved = kp_store.write(it["sha12"], kps, extraction="agent-kp")

    # 校验：条目与知识点必须能被 parse_entry 解析
    sys.path.insert(0, str(BASE / "server"))
    import ingest
    if not ingest.parse_entry(str(PAPERS_DIR / ("%s.md" % it["eid"]))):
        print("  FAIL 条目解析失败，已回滚")
        (PAPERS_DIR / ("%s.md" % it["eid"])).unlink(missing_ok=True)
        for k in kids:
            (KPOINTS_DIR / ("%s.md" % k)).unlink(missing_ok=True)
        set_state(it["sha12"], "failed", notes="entry parse failed")
        return {"ok": False, "sha12": sha12, "error": "条目解析失败，已回滚"}
    for k in kids:
        if not ingest.parse_entry(str(KPOINTS_DIR / ("%s.md" % k))):
            print("  FAIL 知识点解析失败:", k)
            set_state(it["sha12"], "failed", notes="kp parse failed: %s" % k)
            return {"ok": False, "sha12": sha12, "error": "知识点解析失败: %s" % k}

    set_state(it["sha12"], "written",
              notes="entry=%s kp=%d" % (it["eid"], len(kids)))
    print("written %s | entry + %d kp | 校验通过" % (it["sha12"], len(kids)))
    return {"ok": True, "sha12": sha12, "kp": len(kids), "soft": soft}


def cmd_write(args):
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    r = write_paper(args.sha, payload)
    if not r.get("ok"):
        print("  FAIL %s" % r.get("error"), file=sys.stderr)
        return 1
    return 0


# ──────────────────────────────────────────────────────────────────────
# 状态机
# ──────────────────────────────────────────────────────────────────────
def set_state(sha12: str, stage: str, notes: str = ""):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": datetime.now().isoformat(timespec="seconds"),
           "sha12": sha12, "stage": stage, "notes": notes}
    # 多子代理并发写时，append-only 日志必须串行化，否则可能写出半行 JSON
    with cross_process_lock("state_log"):
        with open(STATE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def read_states() -> dict:
    out = {}
    if STATE_FILE.exists():
        for line in STATE_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                out[r["sha12"]] = r
            except Exception:  # noqa: BLE001
                continue
    return out


# ──────────────────────────────────────────────────────────────────────
# commit：分面回写 + 索引 + 台账
# ──────────────────────────────────────────────────────────────────────
def cmd_kps(args):
    """只重写某篇的知识点文件（条目正文、台账、索引都不动）。

    用途：条目已入库、只是知识点判得不好时，按可复用性判据重挑一轮。
    与 `write` 的区别：不要求重写摘要/tags，不动台账，风险面小。
    """
    sha12 = args.sha.strip()[:12]
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    kps = payload.get("kps") or []
    if not (KP_MIN <= len(kps) <= KP_MAX):
        print("  FAIL 知识点条数 %d 不在 [%d,%d]" % (len(kps), KP_MIN, KP_MAX),
              file=sys.stderr)
        return 1

    items = [json.loads(l) for l in QUEUE.read_text(encoding="utf-8").splitlines()
             if l.strip()] if QUEUE.exists() else []
    it = next((x for x in items if x["sha12"] == sha12), None)
    if not it:
        ep = PAPERS_DIR / ("paper-%s.md" % sha12)
        if not ep.exists():
            print("既不在队列也没有条目: %s" % sha12, file=sys.stderr)
            return 1
        fm = read_fm(ep)
        src = (fm.get("sources") or [{}])[0]
        it = {"sha12": sha12, "sha1": sha12, "eid": "paper-%s" % sha12,
              "path": src.get("path") or "", "journal": fm.get("journal") or "",
              "year": str(fm.get("year") or ""), "title": fm.get("title") or ""}

    # 原文：优先 state/text（全量、带缓存），避免再走一遍 PDF 解析
    source = ""
    hits = sorted((BASE / "state" / "text").glob("%s*.txt" % sha12))
    if not hits:  # 已出栈的篇在冷区
        hits = sorted((BASE / "state" / "_cold" / "text").glob("%s*.txt" % sha12))
    if hits:
        source = hits[0].read_text(encoding="utf-8", errors="ignore")
    elif it.get("path"):
        try:
            import pdf_corpus_ingest as pci
            source = pci.extract_text(str(it["path"]), it["sha1"]) or ""
        except Exception as e:  # noqa: BLE001
            print("  [WARN] 原文读取失败，跳过数值校验: %s" % e, file=sys.stderr)

    hard, soft = [], []
    for i, kp in enumerate(kps, start=1):
        r = kp_select.check_kp(kp, source, peers=[k for j, k in enumerate(kps) if j != i - 1])
        hard += ["kp%d: %s" % (i, e) for e in r["errors"]]
        soft += ["kp%d: %s" % (i, w) for w in r["warnings"]]
        print("  kp%d %.3f [%s] %s" % (i, r["score"], r["reuse"], (kp.get("title") or "")[:34]))
    for s in soft:
        print("  [WARN] %s" % s, file=sys.stderr)
    if hard:
        for e in hard:
            print("  FAIL %s" % e, file=sys.stderr)
        return 1

    # 与 API 链路同一份落盘实现：字段/模板/归档/kp_count 回写都交给 kp_store。
    meta = kp_store.paper_meta(sha12)
    if not meta.get("title"):
        # 条目缺失时用队列信息兜底（极端情况：条目被删、只需补知识点）
        meta.update({"title": it.get("title") or "",
                     "journal": it.get("journal") or "",
                     "year": str(it.get("year") or ""),
                     "eid": it.get("eid") or ("paper-%s" % sha12)})
    kids, moved = kp_store.write(sha12, kps, extraction="agent-kp", meta=meta)
    n_idx = kp_store.index(sha12)
    print("kps %s | 写入 %d 条%s | 已索引 %d 条"
          % (sha12, len(kids),
             ("，归档 %d 个旧编号" % len(moved)) if moved else "", n_idx))
    return 0


def commit_shas(shas=None, all_written=False, no_facet=False) -> dict:
    """提交若干篇（**唯一实现**，CLI 与 MCP 共用）。

    返回 {ok, committed[], failed[], missing_entry[], batch}
    """
    items = [json.loads(l) for l in QUEUE.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    states = read_states()
    if shas:
        targets = [x for x in items if x["sha12"] in shas]
    elif all_written:
        targets = [x for x in items
                   if (states.get(x["sha12"]) or {}).get("stage") == "written"]
    else:
        return {"ok": False, "error": "需要 shas 或 all_written"}

    if not targets:
        return {"ok": True, "committed": [], "failed": [], "missing_entry": [],
                "batch": None, "note": "没有待提交的条目"}

    # 硬防错：没有条目文件的篇不可能 commit（否则只会在索引里塞空 id）
    have, missing = [], []
    for x in targets:
        (have if (PAPERS_DIR / ("%s.md" % x["eid"])).exists() else missing).append(x)
    if missing:
        print("  [skip] 无条目文件，不能 commit（须先 write）：%s"
              % " ".join(x["sha12"] for x in missing))
    if not have:
        return {"ok": True, "committed": [], "failed": [],
                "missing_entry": [x["sha12"] for x in missing], "batch": None,
                "note": "没有可提交的条目（全部缺条目文件）"}

    # 元数据红线（与 DeepSeek 管线共用 scripts/meta_guard.py）：硬红线篇不提交
    try:
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        import meta_guard as _mg
        blocked = []
        for x in list(have):
            r = _mg.check_eid(x["eid"])
            if r["errors"]:
                blocked.append(x)
                print("  [红线] %s 不提交：%s" % (x["sha12"], "；".join(r["errors"])))
            elif r["warns"]:
                print("  [提示] %s：%s" % (x["sha12"], "；".join(r["warns"][:2])))
        if blocked:
            have = [x for x in have if x not in blocked]
            missing += blocked
            if not have:
                return {"ok": False, "committed": [], "failed": [],
                        "missing_entry": [x["sha12"] for x in missing], "batch": None,
                        "errors": {"meta_guard": "全部被元数据红线拦下"}}
    except Exception as e:
        print("  [元数据检查跳过] %s" % e)

    ok_list, fail_list = [], []
    for it in have:
        try:
            # 单篇 commit 只做索引与台账；分面留到「批次收口」时统一建一次。
            # 子代理逐篇 commit 时若每篇都跑一遍 build_facet_index，会重复 N 次全量回写。
            if _commit_one(it, no_facet=True):
                ok_list.append(it["sha12"])
            else:
                fail_list.append(it["sha12"])
        except Exception as e:  # noqa: BLE001
            print("  FAIL %s: %s: %s" % (it["sha12"], type(e).__name__, e))
            set_state(it["sha12"], "failed", notes="%s: %s" % (type(e).__name__, e))
            fail_list.append(it["sha12"])
    print("commit 完成: 成功 %d / 失败 %d" % (len(ok_list), len(fail_list)))

    # 按磁盘事实收口在飞批次：done / written_uncommitted / not_written
    rec = None
    try:
        rec = reconcile_open_batch()
    except Exception as e:  # noqa: BLE001
        print("  [WARN] 批次收口失败：%s: %s" % (type(e).__name__, e))
    if rec:
        out = rec.get("outcome") or {}
        n_done = sum(1 for v in out.values() if v == "done")
        print("批次 %s → %s（done %d/%d%s）"
              % (rec.get("batch_id"), rec.get("status"), n_done, len(out),
                 "；pending: " + " ".join(rec.get("pending") or [])
                 if rec.get("pending") else ""))
        if rec.get("pending"):
            print("  ⚠️ 仍有未收口篇（由对应子代理补 commit，父代理不介入）。")

    # 分面：批次收口（或无批次）后统一建一次，保证 G4「facets 非空」
    facet_built = False
    if ok_list and not no_facet and (rec is None or rec.get("status") == "closed"):
        try:
            _run([sys.executable, str(SCRIPTS / "build_facet_index.py")], cwd=str(BASE))
            facet_built = True
            print("分面索引已重建（本批收口）")
        except Exception as e:  # noqa: BLE001
            print("  [WARN] 分面重建失败：%s: %s" % (type(e).__name__, e))

    return {"ok": not fail_list, "committed": ok_list, "failed": fail_list,
            "missing_entry": [x["sha12"] for x in missing], "batch": rec,
            "facet_built": facet_built}


def cmd_commit(args):
    r = commit_shas(shas=args.sha, all_written=getattr(args, "all_written", False),
                    no_facet=getattr(args, "no_facet", False))
    if r.get("error"):
        print(r["error"], file=sys.stderr)
        return 1
    if r.get("note"):
        print(r["note"])
    return 0 if r.get("ok") else 1


def _commit_one(it: dict, no_facet: bool = False) -> bool:
    sha12 = it["sha12"]
    eid = it["eid"]
    # ① 分面回写（自动：tags/title/key_content → facets）
    #    批量场景请加 --no-facet，由调用方统一跑一次（每篇全量回写极慢）。
    if not no_facet:
        _run([sys.executable, str(SCRIPTS / "build_facet_index.py")], cwd=str(BASE))

    # ② 索引：paper + 其 kp
    kp_ids = sorted(p.stem for p in KPOINTS_DIR.glob("kp-%s-*.md" % sha12))
    ids = [eid] + kp_ids
    ok = _index(ids)
    if ok < len(ids):
        print("  WARN 索引不全: %d/%d" % (ok, len(ids)))
        set_state(sha12, "failed", notes="index %d/%d" % (ok, len(ids)))
        return False

    # ③ 台账登记（已存在则跳过，幂等）
    #    ⚠️ 不能用 sha1 比对：历史台账行绝大多数没有 sha1 字段（实测 1485 行仅 2 行有），
    #       用 sha1 会误判「未登记」→ 重复追加 → 又被 D4（条目已存在）判重失败。
    #    正确判据：repo_path 归一化后比对（台账该字段覆盖完整）。
    if _ledger_has_path(it.get("path")):
        set_state(sha12, "committed", notes="ledger present (repo_path)")
        return True
    rec = {
        "title": it.get("title") or "",
        "url": it.get("url") or "",
        "doi": it.get("doi") or "",
        "journal": it.get("journal") or "",
        "date": (it.get("year") or "") + "-01-01",
        "pdf_url": "",
        "repo_path": it.get("path") or "",
        "size": it.get("size") or 0,
        "source": it.get("source") or "agent_ingest",
        "sha1": it["sha1"],
        "eid": eid,
        "indexed_at": datetime.now().isoformat(timespec="seconds"),
    }
    import user_upload as uu
    # allow_entry=True：忽略 D4（内容条目已存在）——本模式下条目就是刚由 Agent 写的
    ok2, dup = uu.append_ledger(rec, allow_entry=True)
    if not ok2:
        dim = (dup or {}).get("dimension") or "unknown"
        print("  FAIL 台账登记:", dim)
        set_state(sha12, "failed", notes="ledger: %s" % dim)
        return False
    set_state(sha12, "committed", notes="indexed %d ids" % len(ids))
    return True


def _ledger_has_path(path) -> bool:
    """按 repo_path 归一化比对台账是否已登记。

    历史台账行绝大多数没有 sha1 字段（实测 1485 行仅 2 行有），
    用 sha1 比对会误判「未登记」→ 重复追加 → 又被 D4（条目已存在）判重失败。
    """
    k = norm(path)
    if not k:
        return False
    for r in load_ledger():
        rp = norm(r.get("repo_path"))
        if rp == k:
            return True
        m = re.search(r"papers_repo/(.*)$", rp)
        if m and k.endswith(m.group(1)):
            return True
    return False


def _run(cmd, cwd=None, timeout=1800):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="ignore",
                           timeout=timeout, env=env, creationflags=_NO_WIN)
        return r.returncode
    except Exception:  # noqa: BLE001
        return -1


def _index(eids):
    """批量索引一批 eid（paper + 其 kp），返回成功条数。

    旧实现是「逐个调 index_one_entry.main()」，有两个问题：
      ① main 当时没有 commit，第一次调用留下的未提交事务把库锁住，
         后续 eid 全部静默失败（实测 7 个只成功 1 个，异常还被 except 吞掉）；
      ② 每个 eid 重跑一次 init_schema + 单条 embed，慢 5~10 倍。
    现改为：一次 init、一次批量 embed、一次 commit。
    """
    if not eids:
        return 0
    try:
        import ingest
        import index_one_entry as ioe
        files = [f for f in (ioe.find_entry_file(e) for e in eids) if f]
        entries = [e for e in (ingest.parse_entry(f) for f in files) if e]
        if not entries:
            return 0
        conn = ingest.get_db()
        try:
            ingest.init_schema(conn)
            vecs = list(ioe.embed_model().embed([ioe.embed_text(e) for e in entries]))
            for e, v in zip(entries, vecs):
                ioe.upsert(conn, e, vec=v)
            conn.commit()
        finally:
            conn.close()
        return len(entries)
    except Exception as e:  # noqa: BLE001
        print("  [WARN] 索引失败: %s" % e)
        return 0


# ──────────────────────────────────────────────────────────────────────
# verify / status
# ──────────────────────────────────────────────────────────────────────
def cmd_verify(args):
    rows = load_ledger()
    entry_files = {p.stem for p in PAPERS_DIR.glob("*.md")}
    kp_by_sha = Counter()
    for p in KPOINTS_DIR.glob("kp-*.md"):
        parts = p.stem.split("-")
        if len(parts) >= 3:
            kp_by_sha[parts[1]] += 1
    indexed = load_index_ids()

    stat = Counter()
    for r in rows:
        rp = resolve_nas_path(r)
        if not rp:
            continue
        stat["pdf"] += 1
        sha = (r.get("sha1") or "").strip()
        # ⚠️ 与 scan 同款陷阱：台账绝大多数行没有 sha1 字段，
        #    必须像 _row()/scan 一样从 eid（paper-<sha12>）反推，
        #    否则 eid 退化成 "paper-" → 全部条目判为缺失。
        if not sha:
            _eid_raw = str(r.get("eid") or "")
            if _eid_raw.startswith("paper-"):
                sha = _eid_raw[6:]
        eid = "paper-" + sha[:12]
        if eid in entry_files:
            stat["entry"] += 1
        if kp_by_sha.get(sha[:12], 0) > 0:
            stat["kp"] += 1
        if eid in indexed:
            stat["index"] += 1
        ef = PAPERS_DIR / ("%s.md" % eid)
        if ef.exists():
            f = (read_fm(ef).get("facets")) or {}
            if any(isinstance(v, list) and v for v in f.values()):
                stat["facets"] += 1

    gaps = (stat["pdf"] - stat["entry"], stat["pdf"] - stat["kp"],
            stat["pdf"] - stat["facets"], stat["pdf"] - stat["index"])
    print("=" * 70)
    print("一致性校验")
    print("=" * 70)
    for k in ("pdf", "entry", "kp", "facets", "index"):
        print("  %-7s: %d" % (k, stat[k]))
    print()
    print("  缺口（条目/知识点/分面/索引）:", gaps)
    print("  全绿:", all(g == 0 for g in gaps))
    return 0


def cmd_status(args):
    items = ([json.loads(l) for l in
              QUEUE.read_text(encoding="utf-8").splitlines() if l.strip()]
             if QUEUE.exists() else [])
    states = read_states()
    b = Counter(x["bucket"] for x in items)
    s = Counter((states.get(x["sha12"]) or {}).get("stage", "pending")
                for x in items)
    print("队列: %d 篇" % len(items))
    print("  分桶:", dict(b))
    print("  状态:", dict(s))
    print("  通道: 以上 stage 来自事件日志 state.jsonl，**不是**进度真相。")
    print("        续跑/判定一律以 `python scripts/ingest_resume.py` 为准"
          "（磁盘事实：条目/索引/台账）。")
    return 0


def main():
    ap = argparse.ArgumentParser(description="GeoKB Agent 亲自入库工具（离线）")
    ap.add_argument("--force", action="store_true",
                    help="跳过 23:00–08:00 时间窗检查（仅限窗口外紧急修正，"
                         "操作者须自行确保未调用 DeepSeek API）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="扫描积压生成队列")
    p.add_argument("--probe", action="store_true",
                   help="实测每个 PDF 的可打开性与文本量（慢，但避免盲跑）")
    p.add_argument("--debug", action="store_true", help="打印分段计时")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("prep", help="生成待读工作笔记")
    p.add_argument("--sha", action="append")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--bucket", action="append")
    p.set_defaults(func=cmd_prep)

    p = sub.add_parser("write", help="落条目+知识点（需 23:00–08:00）")
    p.add_argument("--sha", required=True)
    p.add_argument("--payload", required=True)
    p.add_argument("--force", dest="force2", action="store_true", help="跳过时间窗检查")
    p.set_defaults(func=cmd_write)

    p = sub.add_parser("kps", help="只重写某篇的知识点（不动条目与台账）")
    p.add_argument("--sha", required=True)
    p.add_argument("--payload", required=True)
    p.add_argument("--force", dest="force2", action="store_true", help="跳过时间窗检查")
    p.set_defaults(func=cmd_kps)

    p = sub.add_parser("commit", help="分面回写+索引+台账登记（需 23:00–08:00）")
    p.add_argument("--sha", action="append")
    p.add_argument("--all-written", action="store_true")
    p.add_argument("--no-facet", action="store_true",
                    help="跳过①分面回写（批量时由调用方统一跑一次，避免 N 次全量计算）")
    p.add_argument("--force", dest="force2", action="store_true", help="跳过时间窗检查")
    p.set_defaults(func=cmd_commit)

    p = sub.add_parser("verify", help="全库一致性校验")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("status", help="进度快照")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("window", help="查看 Agent 亲自入库时间窗状态")
    p.set_defaults(func=cmd_window)

    p = sub.add_parser("skip", help="永久跳过某篇（如保密审稿稿），不占 FIFO 队首")
    p.add_argument("--sha", required=True)
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_skip)

    args = ap.parse_args()
    force = bool(getattr(args, "force", False) or getattr(args, "force2", False))
    gate(args.cmd, force=force)
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print("FAIL", type(exc).__name__, exc, file=sys.stderr)
        sys.exit(2)
