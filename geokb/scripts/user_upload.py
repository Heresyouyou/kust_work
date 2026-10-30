#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""GeoKB 用户自定义上传文献（Web 上传入口的后端核心，2026-10-21）。

定位
----
门户 /geokb/upload 的「上传 PDF + 补充文献信息」由本脚本落地；
Flask 侧（PostNews/Translate/modules/geokb_public/routes.py）只做
收文件、起后台线程、转发状态，全部业务逻辑在本文件，CLI 可独立复跑。

流程（与需求文档 docs/用户上传文献功能_20261021.md 一一对应）
----------------------------------------------------------
    ① 校验：扩展名 / magic(%PDF) / 大小上限(100MB) / 可打开
    ② 查重：D1 SHA-1 逐字节 → D2 DOI 归一化精确 → D3 标题归一化
             → D4 内容条目(paper-<sha12>.md 已存在)
             命中且库内记录**有 PDF** → 返回库内链接，不再上传
             命中但库内记录**无 PDF**（纯题录）→ 升级式上传（upgraded_from）
    ③ 存储：NAS <pdf_root>/Addition/<年>/<日期>_<安全标题>.pdf
             （不归属任何期刊，与 by_journal 平级）
    ④ 台账：state/papers_index.jsonl 追加，source=user_upload，
             sha1 必写（后续上传的 D1 判据），含用户补充元数据
    ⑤ 入库：pdf_corpus_ingest.py --only（DeepSeek 摘要→content/papers）
             → kp_extract.py --sha（DeepSeek 拆知识点→content/kpoints）
             → index_one_entry.py ×N（FTS5+向量，秒级可检索）
    ⑥ 状态：每步写 spool/<task_id>/status.json，前端轮询渲染

用法
----
    # Web 侧（routes.py 调用）：
    python user_upload.py --spool state/upload_spool/<task_id>
    # 目录内放：*.pdf + meta.json（title/authors/keywords/abstract/tags/doi/date/url）

    # CLI 独立用：
    python user_upload.py --pdf a.pdf --meta-json '{"title":"...","tags":["矽卡岩"]}'
    python user_upload.py --pdf a.pdf --meta-json @meta.json --no-llm   # 离线联调
    python user_upload.py --spool <dir> --status                        # 只看状态

    ⚠️ --no-llm / 扫描版路径不调 DeepSeek；正常路径会调 API。
    本脚本**不设时间闸门**（业务链路必须全天可用），高峰自觉由调用方掌握。

退出码：0 成功/查重命中/恢复完成；1 失败；2 参数错误。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import paths as geokb_paths  # noqa: E402  NAS 根路径唯一真源

SPOOL_ROOT = BASE / "state" / "upload_spool"
LEDGER = BASE / "state" / "papers_index.jsonl"
PAPERS_DIR = BASE / "content" / "papers"
KP_DIR = BASE / "content" / "kpoints"
UPLOAD_LOG = BASE / "state" / "upload_log.jsonl"
LOCK_FILE = BASE / "state" / "pdf_ingest.lock"
PDF_STATE = BASE / "state" / "pdf_state.jsonl"

MAX_PDF_BYTES = 100 * 1024 * 1024    # 100MB，与前端校验一致
LOCK_WAIT_MAX = 20 * 60              # 入库锁最长等待（历史队列夜间逐篇占用同一把锁）
_NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
SPOOL_KEEP_HOURS = 48                # 失败任务 spool 保留时长（成功即清 PDF）

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.I)
YEAR_RE = re.compile(r"\b(19[7-9]\d|20[0-4]\d)\b")
DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")

# pythonw（无控制台）下裸 print 会抛 OSError:[Errno 22]，统一兜住
_builtin_print = print


def print(*a, **kw):        # noqa: A001  仅本模块内生效
    try:
        _builtin_print(*a, **kw)
    except Exception:
        pass


# ─── 基础工具（与 manual_inbox / history_queue 同规则，避免各说各话）────────
def norm_doi(s: str) -> str:
    s = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", (s or "").strip(), flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    return s.strip().rstrip(".,;)]}>").lower()


def norm_title(s: str) -> str:
    return re.sub(r"\W+", "", (s or "").lower())


def safe_name(s, max_len=80) -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(s))
    s = re.sub(r"_+", "_", s).strip("_ ")
    return s[:max_len] or "untitled"


def sha1_file(p, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def head_text(path, pages: int = 3) -> str:
    import pymupdf
    doc = pymupdf.open(str(path))
    try:
        parts = [doc[i].get_text() or "" for i in range(min(pages, doc.page_count))]
    finally:
        doc.close()
    return "\n".join(parts)


def pdf_meta(path):
    import pymupdf
    doc = pymupdf.open(str(path))
    try:
        return dict(doc.metadata or {}), doc.page_count
    finally:
        doc.close()


def guess_doi(text: str) -> str:
    """从正文前几页抽 DOI：取出现次数最多者（页眉/页脚会重复出现）。"""
    hits = [norm_doi(m) for m in DOI_RE.findall(text or "")]
    hits = [h for h in hits if len(h) > 8 and not h.endswith("/")]
    if not hits:
        return ""
    from collections import Counter
    return Counter(hits).most_common(1)[0][0]


def as_list(v) -> list:
    """表单值 → 列表：接受 list，或逗号/顿号/分号/换行分隔的字符串。"""
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if not v:
        return []
    return [x.strip() for x in re.split(r"[,，、;；\n]", str(v)) if x.strip()]


def normalize_date(v) -> str:
    v = (v or "").strip().replace("/", "-")
    if DATE_RE.match(v):
        y, m, d = v.split("-")
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    if re.match(r"^\d{4}$", v):
        return v
    return datetime.now().strftime("%Y-%m-%d")


# ─── 台账 ─────────────────────────────────────────────────────────────────
def load_ledger() -> list:
    rows = []
    if LEDGER.exists():
        for line in open(LEDGER, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def has_pdf(rec) -> bool:
    rp = str((rec or {}).get("repo_path") or "")
    return "papers_repo/" in rp.replace("\\", "/") and rp.lower().endswith(".pdf")


def pdf_on_nas(rec) -> bool:
    """台账声明有 PDF **且文件此刻真实存在**（NAS 文件可能被外部清理）。"""
    if not has_pdf(rec):
        return False
    try:
        return Path(str(rec.get("repo_path") or "")).exists()
    except OSError:
        return False


def _md_title(path: Path) -> str:
    """从条目 md 的 frontmatter 读 title（查重命中时展示用）。"""
    try:
        m = re.match(r"^---\n(.*?)\n---", path.read_text(encoding="utf-8"), re.S)
        if m:
            t = re.search(r"^title:\s*(.+)$", m.group(1), re.M)
            if t:
                return t.group(1).strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


# ─── 查重（D1-D4，维度显式声明）──────────────────────────────────────────
def find_duplicate(rows, sha1: str, doi: str, title: str,
                   skip_content_entry: bool = False):
    """返回 None 或 {dimension, record, note}。维度：sha1 / doi / title / content_entry

    skip_content_entry
        跳过 D4 维度。用于 **Agent 亲自入库** 路径：该模式下条目由 Agent
        先写好、再登记台账，D4（条目存在）必然命中，若参与判重会把正常登记
        误判为重复。
    """
    # D4 内容条目：同一文件已入库过（eid 由 sha1 派生，最可靠）
    if not skip_content_entry:
        eid4 = f"paper-{sha1[:12]}"
        p4 = PAPERS_DIR / f"{eid4}.md"
        if p4.exists():
            rec4 = {"eid": eid4, "title": _md_title(p4), "journal": "Addition"}
            # 台账里同 sha1/eid 的行通常带更全的信息（repo_path/doi/date），合并展示
            for r in rows:
                if (r.get("sha1") or "").lower() == sha1 or r.get("eid") == eid4:
                    rec4 = {**r, **rec4}
                    break
            return {"dimension": "content_entry", "record": rec4,
                    "note": "内容库已有同一文件生成的条目（SHA-1 相同）"}

    # D1 文件哈希：台账记录了 sha1（user_upload 链路必写）
    for r in rows:
        if (r.get("sha1") or "").lower() == sha1:
            return {"dimension": "sha1", "record": r,
                    "note": "台账已登记同一文件（SHA-1 逐字节一致）"}

    # D2 DOI：台账 doi 字段 → url 字段包含
    d = norm_doi(doi)
    if d:
        for r in rows:
            if norm_doi(r.get("doi") or "") == d:
                return {"dimension": "doi", "record": r, "note": f"DOI 命中：{d}"}
        for r in rows:
            if d and d in norm_doi(r.get("url") or ""):
                return {"dimension": "doi", "record": r,
                        "note": f"DOI 命中（记录 url 字段）：{d}"}

    # D3 标题：归一化全等；长标题前 80 字符相等且唯一
    k = norm_title(title)
    if k:
        for r in rows:
            if norm_title(r.get("title") or "") == k:
                return {"dimension": "title", "record": r,
                        "note": "标题归一化后完全一致"}
        if len(k) >= 70:
            cands = [r for r in rows
                     if norm_title(r.get("title") or "")[:80] == k[:80]]
            if len(cands) == 1:
                return {"dimension": "title", "record": cands[0],
                        "note": "长标题前 80 字符一致且唯一"}
    return None


def confirm_hash(rec) -> str:
    """D2/D3 命中后，实时计算候选 NAS 文件的 SHA-1，确认是否同一文件。"""
    rp = str((rec or {}).get("repo_path") or "")
    if "papers_repo/" in rp.replace("\\", "/") and os.path.isfile(rp):
        try:
            return sha1_file(rp)
        except Exception:
            return ""
    return ""


def entry_link(eid: str, title: str) -> str:
    """库内链接：条目已生成 → 详情页直链；否则退化为搜索页。"""
    if eid and (PAPERS_DIR / f"{eid}.md").exists():
        return f"/geokb/entry/{eid}"
    return "/geokb/search?q=" + urllib.parse.quote(title or "")


# ─── Addition 存储 ────────────────────────────────────────────────────────
def addition_path(title: str, date: str) -> Path:
    """NAS Addition/<年>/<日期>_<安全标题>.pdf（与 by_journal 命名风格一致）。

    不含冲突处理——同名文件可能是上次失败重试的同一内容，由调用方判 sha1 复用。
    """
    year = (date or "")[:4] or datetime.now().strftime("%Y")
    root = Path(geokb_paths.addition())
    return root / year / f"{date}_{safe_name(title, 60)}.pdf"


# ─── 台账写入（带并发二次查重）────────────────────────────────────────────
def append_ledger(rec: dict, allow_entry: bool = False):
    """返回 (ok, dup)。写盘前再扫一次台账，防两路并发上传同一文件都落盘。

    allow_entry
        True 时判重**忽略 D4（内容条目）维度**——Agent 亲自入库路径下条目
        已由 Agent 写好，D4 必然命中，会误判为重复。


    写后校验：追加后回读文件尾，确认本行真实落盘。曾遇到外部进程在
    上传窗口内重写台账（read-modify-write 竞态）导致追加行静默丢失，
    校验不过即按失败处理（PDF 已保留，可重试）。
    """
    dup = find_duplicate(load_ledger(), rec["sha1"], rec.get("doi", ""),
                         rec["title"], skip_content_entry=allow_entry)
    if dup and pdf_on_nas(dup.get("record") or {}):
        return False, dup
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
    try:
        # 语义校验：回读尾部窗口，按 sha1+eid 找本行。不按字节比对——
        # Windows 文本模式会把 \n 写成 \r\n，且别的进程可能并发追加。
        size = os.path.getsize(LEDGER)
        with open(LEDGER, "rb") as f:
            f.seek(max(0, size - 65536))
            tail = f.read().decode("utf-8", errors="ignore")
        found = False
        for ln in tail.splitlines():
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("sha1") == rec["sha1"] and r.get("eid") == rec["eid"]:
                found = True
                break
        if not found:
            return False, {"dimension": "write_verify_failed",
                           "note": "台账追加后校验失败（疑似外部重写竞态）"}
    except OSError:
        pass
    return True, None


def audit(action: str, **kw):
    try:
        UPLOAD_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(UPLOAD_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                                "action": action, **kw}, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ─── 任务状态机 ───────────────────────────────────────────────────────────
class Task:
    """spool/<task_id>/status.json 即前端轮询的唯一真相。"""

    def __init__(self, spool: Path):
        self.spool = spool
        self.task_id = spool.name
        self.path = spool / "status.json"

    def set(self, state: str, message: str = "", **extra):
        doc = {"task_id": self.task_id, "state": state, "message": message,
               "result": extra.pop("result", None), "error": extra.pop("error", None),
               "updated_at": datetime.now().isoformat(timespec="seconds")}
        doc.update(extra)
        tmp = self.path.with_suffix(".tmp")
        try:
            self.spool.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            pass


# ─── 入库锁协作（历史队列夜间逐篇占用同一把锁）────────────────────────────
def lock_holder_alive() -> bool:
    try:
        pid = int(LOCK_FILE.read_text().strip())
    except Exception:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def wait_lock(max_wait: int, cb) -> bool:
    t0 = time.time()
    announced = False
    while time.time() - t0 < max_wait:
        if not LOCK_FILE.exists() or not lock_holder_alive():
            return True
        if not announced:
            cb("queued", "入库队列繁忙（历史补爬在跑），已排队，等待空位…")
            announced = True
        time.sleep(20)
    return False


def pdf_state_status(sha1: str) -> str:
    if not PDF_STATE.exists():
        return ""
    st = ""
    for line in open(PDF_STATE, encoding="utf-8", errors="ignore"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("hash") == sha1:
            st = r.get("status", "")
    return st


# ─── 入库编排 ─────────────────────────────────────────────────────────────
def run_ingest(task: Task, repo_path: Path, sha1: str, meta: dict, no_llm: bool):
    """返回 (state, info, notes)。state ∈ done / partial / failed"""
    import pdf_corpus_ingest as pci

    notes = []
    eid = f"paper-{sha1[:12]}"
    state = "done"

    # ① 全文文本抽取（本地；缓存 state/text/<sha1>.txt，kp 任务共用）
    task.set("extracting", "正在解析 PDF 全文…")
    try:
        text = pci.extract_text(str(repo_path), sha1)
    except Exception as e:
        return "failed", None, [f"PDF 文本抽取失败: {type(e).__name__}: {e}"]
    scanned = len(text) < pci.MIN_TEXT_CHARS

    # ② 摘要提取 + papers 条目
    if no_llm or scanned:
        # 离线联调 / 扫描版：不调 DeepSeek，直接题录条目（进程内，无锁）
        reason = "扫描版 PDF（无文本层），已按题录入库，未拆知识点" if scanned \
            else "离线模式（--no-llm）：未调用 DeepSeek，正则兜底抽取"
        try:
            data = pci.fallback_extract(text, meta.get("title") or "")
            pci.write_entry(sha1, meta, data, "fallback", user_meta=meta)
        except Exception as e:
            return "failed", None, [f"题录条目生成失败: {type(e).__name__}: {e}"]
        notes.append(reason)
        if scanned:
            state = "partial"
    else:
        task.set("ingest_summary", "正在调用 DeepSeek 提取摘要…")
        if not wait_lock(LOCK_WAIT_MAX, lambda s, m: task.set(s, m)):
            return "failed", None, [
                "入库锁被占用超过 20 分钟（历史补爬在跑），PDF 与台账已保留，可稍后重试"]
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        try:
            subprocess.run(
                [sys.executable, str(SCRIPTS / "pdf_corpus_ingest.py"),
                 "--only", str(repo_path)],
                cwd=str(BASE), capture_output=True, text=True, encoding="utf-8",
                errors="ignore", timeout=600, env=env, creationflags=_NO_WIN)
        except subprocess.TimeoutExpired:
            notes.append("摘要提取超时（600s）")
        st = pdf_state_status(sha1)
        if st in ("done", "fallback"):
            notes.append("摘要提取成功（DeepSeek）" if st == "done"
                         else "摘要提取走了正则兜底")
        else:
            # needs_ocr / failed / 空：补一条题录保底，上传不丢；failed 状态
            # 不写死，稍后重跑同一路径会重新提取并覆盖为 LLM 版
            why = {"needs_ocr": "扫描版 PDF（无文本层）",
                   "failed": f"DeepSeek 提取失败（{st or 'unknown'}）"}.get(st, "提取未完成")
            try:
                data = pci.fallback_extract(text, meta.get("title") or "")
                pci.write_entry(sha1, meta, data, "fallback", user_meta=meta)
                notes.append(f"{why}，已按题录入库，可稍后重试提取")
            except Exception as e:
                return "failed", None, [f"{why}，且题录兜底失败: {type(e).__name__}: {e}"]
            state = "partial"

    # ③ 知识点拆分（DeepSeek，一次调用，最多 6 条）
    kp_before = len(list(KP_DIR.glob(f"kp-{sha1[:12]}-*.md")))
    if no_llm or scanned:
        notes.append("未拆知识点（离线模式）" if no_llm else "未拆知识点（扫描版无文本）")
    else:
        task.set("ingest_kp", "正在拆分知识点…")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        try:
            subprocess.run([sys.executable, str(SCRIPTS / "kp_extract.py"),
                            "--sha", sha1],
                           cwd=str(BASE), capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", timeout=300, env=env,
                           creationflags=_NO_WIN)
        except subprocess.TimeoutExpired:
            notes.append("知识点拆分超时（300s）")
        kp_after = len(list(KP_DIR.glob(f"kp-{sha1[:12]}-*.md")))
        if kp_after > 0:
            notes.append(f"拆分知识点 {kp_after} 条")
        else:
            notes.append("知识点未产出（JSON 解析失败或文本不足），不影响论文条目")
            state = "partial" if state == "done" else state

    # ④ 增量索引（本地 fastembed，不调 API；与 rebuild 插入逻辑同源）
    task.set("indexing", "正在建立检索索引…")
    eids = [eid] + [p.stem for p in sorted(KP_DIR.glob(f"kp-{sha1[:12]}-*.md"))]
    ok = run_index(eids)
    if ok < len(eids):
        notes.append(f"索引部分失败（{ok}/{len(eids)}），可跑 rebuild_index.py 全量兜底")
        state = "partial" if state == "done" else state

    return state, {"eid": eid, "kp_count": len(list(KP_DIR.glob(f"kp-{sha1[:12]}-*.md")))}, notes


def run_index(eids) -> int:
    """进程内复用 index_one_entry（~0.6s/条），避免每篇起子进程。"""
    if not eids:
        return 0
    try:
        sys.path.insert(0, str(SCRIPTS))
        import index_one_entry as ioe
        old = sys.argv
        ok = 0
        for e in eids:
            sys.argv = ["index_one_entry.py", e]
            try:
                if ioe.main() == 0:
                    ok += 1
            except SystemExit as ex:
                if ex.code == 0:
                    ok += 1
            except Exception:
                pass
        sys.argv = old
        return ok
    except Exception:
        return 0


# ─── spool 清理 ───────────────────────────────────────────────────────────
def gc_spool():
    """清理 48h 前的失败任务 PDF（成功任务的 PDF 在结束时即删）。"""
    now = time.time()
    for d in SPOOL_ROOT.glob("*"):
        if not d.is_dir():
            continue
        try:
            age_h = (now - d.stat().st_mtime) / 3600
        except OSError:
            continue
        if age_h < SPOOL_KEEP_HOURS:
            continue
        for p in d.glob("*.pdf"):
            try:
                p.unlink()
            except OSError:
                pass


# ─── 主流程 ───────────────────────────────────────────────────────────────
def run_task(spool: Path, no_llm: bool = False) -> int:
    task = Task(spool)
    gc_spool()

    pdfs = sorted(p for p in spool.glob("*.pdf") if not p.name.startswith("~$"))
    meta_file = spool / "meta.json"
    if not pdfs:
        task.set("failed", "未找到 PDF 文件（spool 目录为空）")
        return 1
    pdf = pdfs[0]
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    except Exception:
        meta = {}
    meta = {
        "title": (meta.get("title") or "").strip(),
        "authors": as_list(meta.get("authors")),
        "keywords": as_list(meta.get("keywords")),
        "abstract": (meta.get("abstract") or "").strip(),
        "tags": as_list(meta.get("tags")),
        "doi": (meta.get("doi") or "").strip(),
        "date": (meta.get("date") or "").strip(),
        "url": (meta.get("url") or "").strip(),
        "remarks": (meta.get("remarks") or "").strip(),
    }

    # ① 校验
    task.set("checking", "正在校验文件并查重…")
    if pdf.stat().st_size > MAX_PDF_BYTES:
        task.set("failed", f"文件超过 {MAX_PDF_BYTES // 1048576}MB 上限（{pdf.stat().st_size // 1048576}MB）")
        return 1
    try:
        with open(pdf, "rb") as f:
            if f.read(5) != b"%PDF-":
                task.set("failed", "不是有效的 PDF 文件（文件头魔校验失败）")
                return 1
    except OSError as e:
        task.set("failed", f"文件读取失败: {e}")
        return 1
    try:
        sha1 = sha1_file(pdf)
        text = head_text(pdf)
        mdoc, pages = pdf_meta(pdf)
    except Exception as e:
        task.set("failed", f"PDF 无法打开（文件损坏或加密）: {type(e).__name__}: {e}")
        return 1

    doi = norm_doi(meta["doi"]) or guess_doi(text)
    title = meta["title"] or (mdoc.get("title") or "").strip()
    if len(title) < 12:   # 元数据标题常是文件名/空串，退化到首页第一行
        first = [ln.strip() for ln in text.splitlines() if len(ln.strip()) > 12]
        title = first[0] if first else pdf.stem
    date = normalize_date(meta["date"])

    # ② 查重
    rows = load_ledger()
    hit = find_duplicate(rows, sha1, doi, title)
    resume_rec = None
    if hit:
        rec = hit.get("record") or {}
        eid_rec = rec.get("eid") or ""
        entry_exists = bool(eid_rec) and (PAPERS_DIR / f"{eid_rec}.md").exists()
        nas_pdf = pdf_on_nas(rec)
        if hit["dimension"] in ("doi", "title"):
            # D2/D3 命中 → 实时算候选 NAS 文件哈希，确认是否同一文件
            cand = confirm_hash(rec)
            if cand and cand == sha1:
                hit["dimension"] = "sha1"
                hit["note"] += "；候选文件 SHA-1 与上传文件一致"
                nas_pdf = True
            elif not has_pdf(rec):
                # 纯题录（有记录无 PDF）→ 升级式上传，不判重
                resume_rec = rec
                hit = None
        if hit:
            if entry_exists:
                # 库内已有可检索条目 → 按需求返回链接，不再上传
                eid = eid_rec
                link = entry_link(eid, rec.get("title") or title)
                task.set("duplicate",
                         f"库中已存在相同文献（{hit['dimension']} 命中），未执行上传",
                         result={
                             "state": "duplicate", "dimension": hit["dimension"],
                             "eid": eid, "link": link,
                             "existing": {"title": rec.get("title"),
                                          "journal": rec.get("journal"),
                                          "date": rec.get("date"),
                                          "doi": rec.get("doi"),
                                          "repo_path": rec.get("repo_path")},
                             "note": hit["note"], "title": title, "sha1": sha1})
                audit("duplicate", sha1=sha1, dimension=hit["dimension"],
                      eid=eid, title=rec.get("title") or title)
                _cleanup_spool(spool, keep_pdf=False)
                return 0
            if nas_pdf and rec.get("source") == "user_upload":
                # 本链路上次已存 NAS+台账但入库未完成 → 恢复入库（幂等）
                resume_rec = rec
                hit = None
            elif nas_pdf:
                # 已下载未解析的期刊论文（pending ingest）：按需求返回链接
                eid = eid_rec
                task.set("duplicate",
                         f"库中已存在相同文献（{hit['dimension']} 命中），未执行上传",
                         result={
                             "state": "duplicate", "dimension": hit["dimension"],
                             "eid": eid, "link": entry_link(eid, rec.get("title") or title),
                             "existing": {"title": rec.get("title"),
                                          "journal": rec.get("journal"),
                                          "date": rec.get("date"),
                                          "doi": rec.get("doi"),
                                          "repo_path": rec.get("repo_path")},
                             "note": hit["note"] + "（该文献已下载但尚未解析入库，链接为检索页）",
                             "title": title, "sha1": sha1})
                audit("duplicate", sha1=sha1, dimension=hit["dimension"], eid=eid)
                _cleanup_spool(spool, keep_pdf=False)
                return 0
            else:
                # 台账有记录但 NAS 文件缺失 → 升级式重传
                resume_rec = rec
                hit = None

    # ③ NAS Addition 存储（恢复模式跳过：上次已落盘）
    if resume_rec and pdf_on_nas(resume_rec):
        dest = Path(resume_rec["repo_path"])
        rec = dict(resume_rec)
        task.set("resuming", "检测到未完成的入库任务（PDF 已在 NAS），继续入库…")
    else:
        dest = addition_path(title, date)
        task.set("saving", "正在保存到 NAS Addition 文件夹…")
        try:
            data = pdf.read_bytes()
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                # 重试场景：同名文件已在 NAS —— 内容相同则复用，不同则加序号
                try:
                    same = sha1_file(dest) == sha1
                except OSError:
                    same = False
                if not same:
                    n = 2
                    while True:
                        cand = dest.with_name(f"{dest.stem}_{n}.pdf")
                        if not cand.exists():
                            dest = cand
                            break
                        n += 1
            if not (dest.exists() and sha1_file(dest) == sha1):
                with open(dest, "wb") as f:
                    f.write(data)
            if dest.stat().st_size != len(data):
                raise IOError("写入后大小不一致")
        except Exception as e:
            task.set("failed",
                     f"NAS 保存失败: {type(e).__name__}: {e}（PDF 未入库，请检查 NAS 连接后重试）")
            audit("failed", sha1=sha1, title=title, stage="nas_save", error=str(e)[:120])
            return 1

        # ④ 台账登记（写盘前二次查重，防并发竞态）
        rec = {
            "title": title, "url": meta["url"], "doi": doi,
            "journal": "Addition", "date": date, "pdf_url": "",
            "repo_path": dest.as_posix(), "size": len(data),
            "source": "user_upload", "sha1": sha1, "eid": f"paper-{sha1[:12]}",
            "indexed_at": datetime.now().isoformat(timespec="seconds"),
            "authors": meta["authors"], "keywords": meta["keywords"],
            "abstract": meta["abstract"], "tags": meta["tags"],
            "uploaded_by": "web",
        }
        if meta["remarks"]:
            rec["remarks"] = meta["remarks"]
        if resume_rec:
            rec["upgraded_from"] = resume_rec.get("source") or "metadata_only"
        task.set("saving_ledger", "正在登记台账…")
        ok, dup2 = append_ledger(rec)
        if not ok:
            if (dup2 or {}).get("dimension") == "write_verify_failed":
                # 追加行没落住：保留 NAS PDF 与任务，标记失败可重试
                task.set("failed",
                         "台账登记后校验失败（疑似外部进程重写台账），PDF 已保留，请重试",
                         result={"eid": rec.get("eid"), "repo_path": rec.get("repo_path"),
                                 "title": title, "sha1": sha1})
                audit("failed", sha1=sha1, title=title, stage="ledger_verify",
                      error="write_verify_failed")
                return 1
            # 并发上传同一文件：另一路已登记且有 PDF → 删我们这份（内容相同）
            try:
                dest.unlink()
            except OSError:
                pass
            r2 = (dup2 or {}).get("record") or {}
            eid2 = r2.get("eid") or ""
            task.set("duplicate", "并发上传检测：库中已登记同一文件，未执行上传",
                     result={"state": "duplicate", "dimension": "sha1",
                             "eid": eid2, "link": entry_link(eid2, r2.get("title") or title),
                             "existing": {"title": r2.get("title"),
                                          "journal": r2.get("journal"),
                                          "repo_path": r2.get("repo_path")},
                             "note": (dup2 or {}).get("note", ""), "title": title, "sha1": sha1})
            audit("duplicate", sha1=sha1, dimension="sha1_race", eid=eid2)
            _cleanup_spool(spool, keep_pdf=False)
            return 0

    # ⑤ 入库：解析 → 摘要 → 知识点 → 索引
    state, info, notes = run_ingest(task, dest, sha1, rec, no_llm)
    if state == "failed":
        task.set("failed", "；".join(notes),
                 result={"eid": rec.get("eid"), "repo_path": rec.get("repo_path"),
                         "title": title, "sha1": sha1,
                         "hint": "PDF 与台账已保留，可重试入库（重复提交同一文件即可恢复）"})
        audit("failed", sha1=sha1, title=title, stage="ingest", error="；".join(notes)[:160])
        return 1

    result = {"state": state, "eid": rec.get("eid"),
              "link": entry_link(rec.get("eid"), title),
              "repo_path": rec.get("repo_path"), "title": title, "sha1": sha1,
              "doi": rec.get("doi") or doi, "date": rec.get("date") or date,
              "journal": "Addition",
              "kp_count": (info or {}).get("kp_count", 0), "notes": notes}
    task.set(state, "上传并入库完成" if state == "done" else "上传完成（部分降级，见备注）",
             result=result)
    audit("uploaded" if state == "done" else "partial", sha1=sha1, eid=rec.get("eid"),
          title=title, kp=result["kp_count"])
    _cleanup_spool(spool, keep_pdf=False)
    return 0


def _cleanup_spool(spool: Path, keep_pdf: bool):
    """成功后清掉 PDF 与 meta（status.json 留下供前端最终读取）；失败全留。"""
    if keep_pdf:
        return
    for name in ("*.pdf", "meta.json"):
        for p in spool.glob(name):
            try:
                p.unlink()
            except OSError:
                pass


def dry_run(spool: Path):
    """只校验 + 查重 + 预演路径，不写任何盘。"""
    pdfs = sorted(p for p in spool.glob("*.pdf") if not p.name.startswith("~$"))
    if not pdfs:
        print(json.dumps({"ok": False, "error": "no_pdf"}, ensure_ascii=False))
        return 2
    pdf = pdfs[0]
    try:
        with open(pdf, "rb") as f:
            magic_ok = f.read(5) == b"%PDF-"
        sha1 = sha1_file(pdf)
        text = head_text(pdf)
        mdoc, pages = pdf_meta(pdf)
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"open_failed: {e}"}, ensure_ascii=False))
        return 1
    meta_file = spool / "meta.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    doi = norm_doi(meta.get("doi") or "") or guess_doi(text)
    title = (meta.get("title") or mdoc.get("title") or "").strip() or pdf.stem
    date = normalize_date(meta.get("date"))
    rows = load_ledger()
    hit = find_duplicate(rows, sha1, doi, title)
    out = {"ok": True, "sha1": sha1, "doi": doi, "title": title, "date": date,
           "pages": pages, "size": pdf.stat().st_size,
           "magic_ok": magic_ok,
           "planned_path": addition_path(title, date).as_posix(),
           "duplicate": None}
    if hit:
        rec = hit.get("record") or {}
        if hit["dimension"] in ("doi", "title"):
            cand = confirm_hash(rec)
            if cand and cand == sha1:
                hit["dimension"] = "sha1"
        out["duplicate"] = {"dimension": hit["dimension"],
                            "record_title": rec.get("title"),
                            "record_journal": rec.get("journal"),
                            "has_pdf": has_pdf(rec), "note": hit["note"]}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser(description="GeoKB 用户上传文献：查重 → Addition → 入库")
    ap.add_argument("--spool", default="", help="任务目录（含 *.pdf + meta.json，写 status.json）")
    ap.add_argument("--pdf", default="", help="单个 PDF 路径（CLI 模式，自动建 spool）")
    ap.add_argument("--meta-json", default="", help="JSON 字符串或 @文件路径")
    ap.add_argument("--no-llm", action="store_true", help="离线联调：不调 DeepSeek")
    ap.add_argument("--dry-run", action="store_true", help="只校验+查重+预演路径")
    ap.add_argument("--status", action="store_true", help="只读 spool/status.json")
    args = ap.parse_args()

    if args.status:
        if not args.spool:
            print("need --spool")
            return 2
        p = Path(args.spool) / "status.json"
        print(p.read_text(encoding="utf-8") if p.exists() else '{"state":"unknown"}')
        return 0

    if args.spool:
        spool = Path(args.spool)
    elif args.pdf:
        spool = SPOOL_ROOT / f"cli_{datetime.now():%Y%m%d_%H%M%S}"
        spool.mkdir(parents=True, exist_ok=True)
        shutil.copy(args.pdf, spool / Path(args.pdf).name)
        raw = args.meta_json or ""
        if raw.startswith("@"):
            raw = Path(raw[1:]).read_text(encoding="utf-8")
        try:
            meta = json.loads(raw) if raw.strip() else {}
        except Exception:
            meta = {}
        (spool / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    else:
        print("need --spool or --pdf")
        return 2

    if args.dry_run:
        return dry_run(spool)
    rc = run_task(spool, no_llm=args.no_llm)
    st = spool / "status.json"
    if st.exists():
        try:
            doc = json.loads(st.read_text(encoding="utf-8"))
            print(json.dumps({"state": doc.get("state"), "message": doc.get("message"),
                              "result": doc.get("result")}, ensure_ascii=False))
        except Exception:
            pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
