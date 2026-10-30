# -*- coding: utf-8 -*-
"""《矿物岩石地球化学通报》当期缺口批量补下（只下载，不调 DeepSeek）。

清单来源：CrossRef ISSN 1007-2802 最近条目 − 台账已有。
下载走 journal_pdf_fetch.fetch_pdf → dl_sciengine（2026-10-22 已修正失效端点误判）。
落盘沿用 by_journal/<刊>/<年>/<日期>_<标题>_fetch.pdf，写台账 + 待入库队列。

用法：
    python scripts/kwys_issue_gap.py --list
    python scripts/kwys_issue_gap.py --run [--limit N]
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = BASE / "scripts"
STATE = BASE / "state"
LEDGER = STATE / "papers_index.jsonl"
JOURNAL = "矿物岩石地球化学通报"
ISSN = "1007-2802"
GAP = 3.0
REPORT = STATE / "kwys_issue_gap.json"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if "D:/PostNews" not in sys.path:
    sys.path.insert(0, "D:/PostNews")


def log(m: str) -> None:
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), m), flush=True)


def _nt(s: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", (s or "")).lower()


def _has_pdf(rec) -> bool:
    rp = (rec.get("repo_path") or "").replace("\\", "/")
    return "papers_repo/" in rp and (rec.get("size") or 0) > 0


def _clean_title(t: str) -> str:
    """CrossRef 标题常见 `&amp;mdash;` 双层转义，需反复 unescape 到稳定。"""
    t = t or ""
    for _ in range(3):
        n = html.unescape(t)
        if n == t:
            break
        t = n
    t = re.sub(r"</?(italic|sub|sup|b|i|scp)>", "", t)
    return re.sub(r"\s+", " ", t).strip()


def crossref_items() -> list:
    """拉 CrossRef 最近条目（只要 2026-07 起，覆盖第 4 期尾与第 5 期）。"""
    import requests
    s = requests.Session()
    s.trust_env = False
    r = s.get("https://api.crossref.org/journals/%s/works" % ISSN,
              params={"rows": 150, "sort": "published", "order": "desc",
                      "select": "DOI,title,published,volume,issue,page,type",
                      "mailto": "geokb@kust.work"}, timeout=45)
    r.raise_for_status()
    out = []
    for it in r.json()["message"]["items"]:
        if it.get("type") != "journal-article":
            continue
        y = (it.get("published", {}).get("date-parts") or [[0]])[0]
        date = "-".join("%02d" % x if i else str(x) for i, x in enumerate(y))
        if date < "2026-07-01":
            continue
        out.append({"doi": it["DOI"], "title": _clean_title((it.get("title") or [""])[0]),
                    "date": date, "volume": it.get("volume") or "",
                    "issue": it.get("issue") or "", "page": it.get("page") or ""})
    return out


def ledger_index():
    by_doi, by_title = set(), set()
    if not LEDGER.exists():
        return by_doi, by_title
    for line in open(LEDGER, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if JOURNAL not in (r.get("journal") or ""):
            continue
        blob = " ".join(str(r.get(k) or "") for k in ("url", "pdf_url", "doi"))
        for m in re.findall(r"10\.3724/j\.issn\.1007-2802\.[0-9A-Za-z]+", blob):
            by_doi.add(m.upper())
        t = _nt(r.get("title"))
        if t:
            by_title.add(t[:60])
    return by_doi, by_title


def pending() -> list:
    by_doi, by_title = ledger_index()
    todo = []
    for it in crossref_items():
        if it["doi"].upper() in by_doi:
            continue
        if _nt(it["title"])[:60] in by_title:
            continue
        todo.append(it)
    return todo


def save_pdf(data: bytes, title: str, date: str) -> Path:
    import pdf_downloader as pdl
    pdir = Path(pdl.REPO) / "by_journal" / pdl.safe_name(JOURNAL, 40) / date[:4]
    pdir.mkdir(parents=True, exist_ok=True)
    stem = "%s_%s" % (date, pdl.safe_name(title, 60))
    sha = hashlib.sha1(data).hexdigest()
    p, n = pdir / ("%s.pdf" % stem), 2
    while p.exists():
        try:
            if p.stat().st_size == len(data) and \
                    hashlib.sha1(p.read_bytes()).hexdigest() == sha:
                return p
        except OSError:
            pass
        p = pdir / ("%s_%d.pdf" % (stem, n))
        n += 1
    p.write_bytes(data)
    return p


def run(limit: int = 0, dry: bool = True) -> int:
    todo = pending()
    log("CrossRef 2026-07 起缺口：%d 篇" % len(todo))
    for it in todo:
        log("  · %s  %-10s  %s" % (it["doi"], it["date"], it["title"][:50]))
    if dry:
        return 0
    if limit:
        todo = todo[:limit]

    from journal_pdf_fetch import fetch_pdf
    from daily_archive_papers import append_index
    import ingest_policy

    results = []
    ok = fail = 0
    for i, it in enumerate(todo, 1):
        log("── [%d/%d] %s" % (i, len(todo), it["title"][:46]))
        url = "https://doi.org/%s" % it["doi"]
        rec = {"doi": it["doi"], "title": it["title"], "date": it["date"],
               "url": url, "state": "", "note": "", "repo_path": "", "eid": ""}
        try:
            r = fetch_pdf(JOURNAL, it["title"], url, date=it["date"], doi=it["doi"])
            st = r.get("status")
            if st != "ok" or not r.get("pdf_bytes"):
                rec["state"] = "no_pdf"
                rec["note"] = "%s: %s" % (st, str(r.get("note") or "")[:120])
                log("   ✗ %s" % rec["note"])
                fail += 1
                results.append(rec)
                time.sleep(GAP)
                continue
            data = r["pdf_bytes"]
            sha = hashlib.sha1(data).hexdigest()
            pdf = save_pdf(data, it["title"], it["date"])
            log("   ✓ %s（%.1f MB）" % (pdf.name[:60], len(data) / 1e6))
            meta = {
                "title": it["title"], "url": url, "journal": JOURNAL,
                "date": it["date"], "doi": it["doi"],
                "pdf_url": r.get("pdf_url", ""), "repo_path": str(pdf),
                "size": len(data), "source": "sciengine",
                "eid": "paper-%s" % sha[:12],
                "indexed_at": datetime.now().isoformat(timespec="seconds"),
            }
            append_index(meta)
            ingest_policy.queue_pending({
                "eid": meta["eid"], "title": it["title"], "journal": JOURNAL,
                "pdf": str(pdf), "url": url, "doi": it["doi"], "from": "cn_issue",
            })
            rec.update(repo_path=str(pdf), eid=meta["eid"], state="downloaded",
                       note="只下载：已入待入库队列，未调 API")
            ok += 1
        except Exception as e:  # noqa: BLE001
            rec["state"] = "error"
            rec["note"] = "%s: %s" % (type(e).__name__, str(e)[:140])
            log("   ✗ 异常 %s" % rec["note"])
            fail += 1
        results.append(rec)
        time.sleep(GAP)

    REPORT.write_text(json.dumps({
        "journal": JOURNAL, "finished_at": datetime.now().isoformat(timespec="seconds"),
        "tried": len(todo), "ok": ok, "failed": fail, "items": results,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    log("完成：成功 %d，失败 %d；报告 %s" % (ok, fail, REPORT))
    return 0 if fail == 0 else 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)
    return run(limit=a.limit, dry=not a.run)


if __name__ == "__main__":
    sys.exit(main())
