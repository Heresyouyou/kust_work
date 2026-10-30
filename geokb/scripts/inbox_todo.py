# -*- coding: utf-8 -*-
"""批量入库待补清单 —— 「自动下载失败、需要人工补投」的论文工作表。

单一真源，三个消费者共用：
  · 5556 `GET  /api/inbox/todo`      → JSON（AI / 外部脚本）
  · 5556 `POST /api/inbox/mark`      → 勾选「已下载 / 撤销」
  · 5555 `/geokb/inbox`              → 渲染成「批量入库」页面
  · 命令行 `python scripts/inbox_todo.py [--limit N] [--json]`

三个来源合并去重（都满足「自动流程试过但没拿到全文」）：

| 源 | 文件 | 含义 |
|---|---|---|
| A | `state/journal_no_pdf.jsonl` | 每日管线当天抓取失败（最新鲜，页面排最前） |
| B | `state/history_manifest.jsonl` 里 `status=parked` | 历史队列连试 3 次仍失败 |
| C | `state/papers_index.jsonl` 里 `source=metadata_only` | 早期口径留下的纯题录 |

三种状态：

- `pending`    待下载 —— 页面给原文链接，人去出版社下
- `downloaded` 已下载待入库 —— 投放箱里探测到这篇的 PDF，或用户手动勾选
- `done`       已入库 —— 台账里已有真实 PDF，移入下方历史记录

「已归档」一律看 `repo_path` 是否含 `papers_repo/` 且 `size>0`，
不看标题键 —— 只看标题会让纯题录被误判成已下载（MEMORY 第 4 节）。

⚠️ 本模块**不 import 其它 scripts 模块、也不往 sys.path 插 scripts**。
   5556 用 importlib 按文件路径加载它；一旦插入 scripts，
   `scripts/ingest.py` 会遮住 `server/ingest.py`（同名不同物），整站检索崩。
   所以标题归一 / DOI 提取 / 台账判定全部本地重写（各 3~8 行，代价远小于风险）。
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
STATE = BASE / "state"
LEDGER = STATE / "papers_index.jsonl"
MANIFEST = STATE / "history_manifest.jsonl"
NO_PDF = STATE / "journal_no_pdf.jsonl"
MARKS = STATE / "inbox_marks.json"
PROBE_CACHE = STATE / "inbox_todo_probe.json"
INBOX = BASE / "inbox" / "papers"
LEGACY_INBOX = STATE / "inbox"

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.I)

# 来源优先级：越小越靠前（同一条论文若多源命中，保留小值）
SRC_RANK = {"no_pdf": 0, "parked": 1, "metadata_only": 2}
SRC_LABEL = {
    "no_pdf": "今日未下到",
    "parked": "重试 3 次失败",
    "metadata_only": "历史遗留",
}
HISTORY_LIMIT = 60
PROBE_BUDGET = 25        # 单次请求最多新探测多少个投放箱 PDF（防止首屏超时）


# ─── 小工具（本地重写，见文件头说明）──────────────────────────────────────
def norm_title_key(s: str) -> str:
    """标题归一：与 inbox_ingest / user_upload 的 norm_title 同规则。"""
    return re.sub(r"\W+", "", (s or "").lower())


def norm_doi(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.rstrip(".,;)]}>\"'")
    if s.startswith("https://doi.org/"):
        s = s[16:]
    elif s.startswith("http://doi.org/"):
        s = s[15:]
    elif s.startswith("doi:"):
        s = s[4:]
    return s


def doi_of(url: str, explicit: str = "") -> str:
    d = norm_doi(explicit)
    if d:
        return d
    m = DOI_RE.search(url or "")
    return norm_doi(m.group(0)) if m else ""


def has_real_pdf(rec: dict) -> bool:
    """「已归档」判据 —— 必须看 repo_path，不能只看标题。"""
    rp = (rec.get("repo_path") or "").replace("\\", "/")
    try:
        size = int(rec.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    return "papers_repo/" in rp and size > 0


def entry_key(title: str, doi: str = "", url: str = "") -> str:
    d = doi_of(url, doi)
    if d:
        return "doi:" + d
    t = norm_title_key(title)
    if t:
        return "t:" + t[:160]
    return "u:" + (url or "")


def _read_jsonl(path: Path):
    if not path.exists():
        return []
    out = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return out
    return out


def _page_url(doi: str, url: str) -> str:
    if doi:
        return "https://doi.org/" + doi
    return url or ""


# ─── 投放箱探测：哪些论文的 PDF 已经在箱子里了 ───────────────────────────
def _inbox_files():
    files = []
    for root in (INBOX, INBOX / "_failed", LEGACY_INBOX):
        if not root.is_dir():
            continue
        try:
            files += sorted(p for p in root.iterdir()
                            if p.is_file() and p.suffix.lower() == ".pdf"
                            and not p.name.startswith("~$"))
        except OSError:
            continue
    return files


def _load_probe_cache() -> dict:
    try:
        doc = json.loads(PROBE_CACHE.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_probe_cache(doc: dict):
    try:
        PROBE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PROBE_CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, PROBE_CACHE)
    except OSError:
        pass


def _probe_pdf(p: Path) -> dict:
    """抽 DOI + 首页标题。只在 pymupdf 可用时做，失败就退化成文件名。"""
    head = ""
    title = ""
    try:
        import pymupdf                      # noqa: PLC0415  延迟导入：没装就跳过
        doc = pymupdf.open(str(p))
        try:
            pages = min(3, doc.page_count)
            head = "\n".join((doc[i].get_text() or "") for i in range(pages))
        finally:
            doc.close()
    except Exception:                        # noqa: BLE001  损坏/加密/未装
        return {"doi": doi_of(p.stem), "title": p.stem.replace("_", " ")}
    m = DOI_RE.search(head) or DOI_RE.search(p.stem)
    doi = norm_doi(m.group(0)) if m else ""
    for line in head.splitlines():
        line = line.strip()
        if 12 <= len(line) <= 300:
            title = line
            break
    return {"doi": doi, "title": title}


def inbox_probes() -> list:
    """投放箱里每个 PDF 的 {name, size, key, doi, title}，带 mtime+size 缓存。

    单次最多新探测 PROBE_BUDGET 个（每次 3 页文本，~0.2s/篇）：
    一次投几十篇时首屏不至于把 5556 请求拖超时，剩下的随 45s 缓存周期补齐。
    """
    cache = _load_probe_cache()
    fresh, changed, budget = {}, False, PROBE_BUDGET
    out = []
    for p in _inbox_files():
        try:
            st = p.stat()
        except OSError:
            continue
        sig = "%d:%d" % (int(st.st_mtime), st.st_size)
        hit = cache.get(str(p))
        if hit and hit.get("sig") == sig:
            info = {"doi": hit.get("doi", ""), "title": hit.get("title", "")}
        elif budget > 0:
            budget -= 1
            info = _probe_pdf(p)
            changed = True
        else:
            fresh[str(p)] = hit or {"sig": "", "doi": "", "title": ""}
            continue        # 本轮不探测，下次再说
        fresh[str(p)] = {"sig": sig, **info}
        out.append({
            "name": p.name,
            "size": st.st_size,
            "doi": info.get("doi", ""),
            "title": info.get("title", ""),
            "key": entry_key(info.get("title", ""), info.get("doi", "")),
        })
    if changed or set(fresh) != set(cache):
        _save_probe_cache(fresh)
    return out


# ─── 人工勾选（「我下好了」/ 撤销）────────────────────────────────────────
def load_marks() -> dict:
    try:
        doc = json.loads(MARKS.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_marks(doc: dict):
    MARKS.parent.mkdir(parents=True, exist_ok=True)
    tmp = MARKS.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, MARKS)


def mark(key: str, state: str = "downloaded") -> dict:
    """标记/撤销。state=downloaded 记录时间；state=pending 删除该键。"""
    key = (key or "").strip()
    if not key or len(key) > 300:
        raise ValueError("bad key")
    if state not in ("downloaded", "pending"):
        raise ValueError("bad state")
    doc = load_marks()
    if state == "pending":
        doc.pop(key, None)
    else:
        doc[key] = {"state": "downloaded",
                    "ts": datetime.now().isoformat(timespec="seconds")}
    save_marks(doc)
    return {"key": key, "state": state, "total": len(doc)}


# ─── 主逻辑 ──────────────────────────────────────────────────────────────
REASON_LABEL = {
    "blocked_captcha": "被反爬拦截",
    "paywalled": "付费墙",
    "not_open_access": "非开放获取",
    "no_pdf_url": "页面无全文链接",
    "err_exception": "请求异常",
    "landing_no_pdf": "落地页无全文",
    "timeout": "请求超时",
}


def _clean_reason(s: str) -> str:
    """把 traceback 压缩成异常行（File "..." 那种栈帧行没有信息量）。"""
    s = (s or "").strip()
    if not s:
        return ""
    if "Traceback" in s:
        lines = [l.strip() for l in s.splitlines() if l.strip()]
        picked = ""
        for line in reversed(lines):
            if re.search(r"\b\w*(Error|Exception)\b", line):
                picked = line
                break
        if picked:
            s = picked
        else:
            # manifest 里 last_error 写入时被截断，只剩栈帧 → 退回第一行的错误码
            s = re.sub(r":?\s*Traceback.*$", "", lines[0] if lines else "").strip() \
                or "exception"
    return s[:140]


def _reason_label(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    if re.search(r"\b\w*(Error|Exception)\b", s):
        return REASON_LABEL["err_exception"]
    code = re.split(r"[:\s]", s, maxsplit=1)[0]
    return REASON_LABEL.get(code, "")


def _item(src: str, title="", journal="", date="", doi="", url="",
          reason="", attempts=0, ts="") -> dict:
    key = entry_key(title, doi, url)
    reason = _clean_reason(reason)
    return {
        "key": key,
        "title": (title or "").strip(),
        "journal": (journal or "").strip(),
        "date": (date or "")[:10],
        "doi": doi_of(url, doi),
        "page_url": _page_url(doi_of(url, doi), url),
        "reason": reason,
        "reason_label": _reason_label(reason),
        "attempts": attempts or 0,
        "ts": ts or "",
        "source": src,
        "source_label": SRC_LABEL.get(src, src),
        "rank": SRC_RANK.get(src, 9),
    }


def _collect_items():
    """三源合并，同 key 保留优先级最高的那条。"""
    bucket = {}

    def put(it):
        if not it["key"] or it["key"] == "u:":
            return
        old = bucket.get(it["key"])
        if old is None or it["rank"] < old["rank"]:
            # 保留更高优先级的来源，但补上它缺的字段
            if old:
                for f in ("reason", "attempts", "date"):
                    it[f] = it[f] or old.get(f, "")
            bucket[it["key"]] = it
        elif not old["reason"] and it.get("reason"):
            old["reason"] = it["reason"]

    for r in _read_jsonl(NO_PDF):
        put(_item("no_pdf",
                  title=r.get("title"), journal=r.get("journal"),
                  date=r.get("date"), url=r.get("url"),
                  reason=r.get("reason"), ts=r.get("ts", "")))

    for r in _read_jsonl(MANIFEST):
        if r.get("status") != "parked":
            continue
        put(_item("parked",
                  title=r.get("title"), journal=r.get("journal"),
                  date=r.get("date"), doi=r.get("doi"), url=r.get("url"),
                  reason=r.get("last_error") or "",
                  attempts=r.get("attempts") or 0,
                  ts=r.get("last_try") or r.get("added_at") or ""))

    for r in _read_jsonl(LEDGER):
        if r.get("source") != "metadata_only":
            continue
        put(_item("metadata_only",
                  title=r.get("title"), journal=r.get("journal"),
                  date=r.get("date"), doi=r.get("doi"), url=r.get("url"),
                  reason="早期口径只存了题录，没有全文",
                  ts=r.get("indexed_at") or ""))

    return list(bucket.values())


def snapshot(history_limit: int = HISTORY_LIMIT) -> dict:
    ledger = _read_jsonl(LEDGER)

    # 台账里「有真实 PDF」的索引：doi 键 + 标题键 双写，任一命中即算已归档
    have = {}
    for r in ledger:
        if not has_real_pdf(r):
            continue
        for k in (entry_key(r.get("title", ""), r.get("doi", ""), r.get("url", "")),
                  "t:" + norm_title_key(r.get("title", ""))[:160]):
            if k and k not in ("t:", "u:"):
                have.setdefault(k, r)

    marks = load_marks()
    probes = inbox_probes()
    probe_by_key = {}
    for p in probes:
        if p["key"] and p["key"] != "u:":
            probe_by_key.setdefault(p["key"], p)

    pending, downloaded, done_from_list = [], [], []
    for it in _collect_items():
        hit = have.get(it["key"])
        if hit:
            it["ledger"] = {
                "eid": hit.get("eid", ""),
                "repo_path": (hit.get("repo_path") or "").replace("\\", "/"),
                "size": hit.get("size") or 0,
                "source": hit.get("source", ""),
                "indexed_at": hit.get("indexed_at", ""),
            }
            done_from_list.append(it)
            continue
        pr = probe_by_key.get(it["key"])
        if pr:
            it["file"] = pr["name"]
            it["file_size"] = pr["size"]
            it["why"] = "投放箱已找到该 PDF"
            downloaded.append(it)
            continue
        mk = marks.get(it["key"])
        if mk and mk.get("state") == "downloaded":
            it["marked_at"] = mk.get("ts", "")
            it["why"] = "已手动勾选「已下载」"
            downloaded.append(it)
            continue
        pending.append(it)

    # 历史：① 从投放箱入库的（source=manual_inbox）② 清单里已被补上的
    history, seen = [], set()
    for it in done_from_list:
        d = dict(it)
        d["origin"] = "auto"
        d["origin_label"] = "已自动补全"
        history.append(d)
        seen.add(d["key"])
    for r in ledger:
        if r.get("source") != "manual_inbox":
            continue
        key = entry_key(r.get("title", ""), r.get("doi", ""), r.get("url", ""))
        if key in seen:
            continue
        seen.add(key)
        history.append({
            "key": key,
            "title": (r.get("title") or "").strip(),
            "journal": (r.get("journal") or "").strip(),
            "date": (r.get("date") or "")[:10],
            "doi": doi_of(r.get("url", ""), r.get("doi", "")),
            "page_url": _page_url(doi_of(r.get("url", ""), r.get("doi", "")),
                                  r.get("url", "")),
            "source": "manual_inbox",
            "source_label": "人工投放",
            "origin": "folder",
            "origin_label": "人工投放入库",
            "ledger": {
                "eid": r.get("eid", ""),
                "repo_path": (r.get("repo_path") or "").replace("\\", "/"),
                "size": r.get("size") or 0,
                "source": r.get("source", ""),
                "indexed_at": r.get("indexed_at", ""),
            },
        })
    history.sort(key=lambda x: (x["ledger"].get("indexed_at") or ""), reverse=True)
    total_history = len(history)

    # 待下载排序：先按时间倒序（今日未下到的排最前），再按来源优先级稳定排序
    # —— 两次 sort，第二次是稳定排序，所以同来源内部仍保持时间倒序。
    for bucket in (pending, downloaded):
        bucket.sort(key=lambda x: (x.get("ts") or ""), reverse=True)
        bucket.sort(key=lambda x: x["rank"])
    history = history[:history_limit]

    by_source = {}
    for it in pending:
        by_source[it["source"]] = by_source.get(it["source"], 0) + 1

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": {
            "pending": len(pending),
            "downloaded": len(downloaded),
            "history": total_history,
            "inbox_files": len(_inbox_files()),
            "marked": len(marks),
            "by_source": by_source,
        },
        "pending": pending,
        "downloaded": downloaded,
        "history": history,
        "inbox": {
            "dir": str(INBOX),
            "files": [{"name": p["name"], "size": p["size"],
                       "title": p["title"][:120], "doi": p["doi"]} for p in probes],
        },
    }


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="批量入库待补清单（自动下载失败 → 人工补投）")
    ap.add_argument("--json", action="store_true", help="输出完整 JSON")
    ap.add_argument("--limit", type=int, default=40, help="终端最多列几条，默认 40")
    ap.add_argument("--mark", default="", help="标记某个 key 为已下载")
    ap.add_argument("--unmark", default="", help="撤销某个 key 的已下载标记")
    args = ap.parse_args(argv)

    if args.mark:
        print(json.dumps(mark(args.mark, "downloaded"), ensure_ascii=False))
        return 0
    if args.unmark:
        print(json.dumps(mark(args.unmark, "pending"), ensure_ascii=False))
        return 0

    snap = snapshot()
    if args.json:
        print(json.dumps(snap, ensure_ascii=False, indent=1))
        return 0

    c = snap["counts"]
    print("待下载 %d ｜ 已下载待入库 %d ｜ 已入库 %d ｜ 投放箱文件 %d"
          % (c["pending"], c["downloaded"], c["history"], c["inbox_files"]))
    print("投放目录：%s\n" % snap["inbox"]["dir"])

    if snap["pending"]:
        print("── 待下载 ─────────────────────────────────────────────────────")
        for it in snap["pending"][:args.limit]:
            print("[%s] %s  %s" % (it["source_label"], it["journal"] or "-",
                                   (it["title"] or "")[:70]))
            if it["doi"]:
                print("        %s" % it["page_url"])
            if it["reason"]:
                print("        ↳ %s" % it["reason"][:100])
    if snap["downloaded"]:
        print("\n── 已下载待入库（12:01 / 18:01 自动入库）─────────────────────")
        for it in snap["downloaded"]:
            print("[%s] %s  %s" % (it.get("why", ""), it["journal"] or "-",
                                   (it["title"] or "")[:70]))
    if snap["history"]:
        print("\n── 已入库（最近 %d 条）────────────────────────────────────" % len(snap["history"]))
        for it in snap["history"][:min(args.limit, 15)]:
            print("[%s] %s  %s" % (it["origin_label"], it["journal"] or "-",
                                   (it["title"] or "")[:70]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
