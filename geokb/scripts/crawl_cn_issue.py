# -*- coding: utf-8 -*-
"""按「正式期次」批量抓取中文期刊论文 → NAS → 台账 → 入库。

口径（2026-10-22 用户明确）
--------------------------
1. **「优先出版」一律不抓**。该栏目条目在源站就没有成品 PDF：
   首页「优先出版」标签页里 PDF 链接写着 `0.00 Byte`，摘要页也拿不到
   可用的 `citation_pdf_url`。抓它只会得到 `no_fulltext`，白占队列。
   只等期刊**统一发布正式期次**后按期批量下载。
2. 期次页 `/…/article/issue/{年}_{卷}_{期}` 末尾也会追加「网络预出版」条目
   （《地质论评》实测 20 条），必须在其分隔行
   `●★●以下文章网络出版（在线出版/预出版）于…` 处**截断**，否则又把
   优先出版捞回来了。
3. 抓完即入库（摘要 + 知识点 + 索引），与项目「下完一篇就入库」一致。
   ⚠️ 会调 DeepSeek，须在低谷窗口（00:00–08:59 / 12:01–13:59 / 18:01–24:00）跑。

用法
----
    python scripts/crawl_cn_issue.py --list                       # 当前期次
    python scripts/crawl_cn_issue.py --year 2026 --issue 9        # 只列清单（不下载）
    python scripts/crawl_cn_issue.py --year 2026 --issue 9 --run  # 下载 + 入库
    python scripts/crawl_cn_issue.py --year 2026 --issue 9 --run --limit 3
    python scripts/crawl_cn_issue.py --journal georev --year 2026 --issue 5 --run

状态落盘：state/cn_issue_<journal>_<year>_<issue>.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = BASE / "scripts"
LEDGER = BASE / "state" / "papers_index.jsonl"
STATE_DIR = BASE / "state"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if "D:/PostNews" not in sys.path:
    sys.path.insert(0, "D:/PostNews")

# geojournals 平台：vol = year - vol0
JOURNALS = {
    "dzxb": {"name": "地质学报", "vol0": 1926},
    "georev": {"name": "地质论评", "vol0": 1954},
}
ALIAS = {
    "地质学报": "dzxb", "dzxb": "dzxb",
    "地质论评": "georev", "georev": "georev",
}

# 目次 / 封面 / 编委会 / 讣告 / 附件 等非论文条目
NOISE = ("目次", "封面", "封底", "编委会", "刊头", "征稿", "启事", "投稿须知",
         "导读", "院士", "逝世", "附表", "附件", "本期目录", "英文目次")

# 遇到这些字样即视为「优先出版」分隔行，其后的条目全部丢弃
ONLINE_FIRST_MARK = ("●★", "网络出版", "网络预出版", "预出版", "在线出版",
                     "优先出版", "网络首发")

GAP = 3.0          # 篇间隔（中文源站 3s 基线）


def log(msg: str) -> None:
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


# ─── 取数 ────────────────────────────────────────────────────────────────
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_H = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def _get(url: str, timeout: int = 40) -> str:
    req = urllib.request.Request(url, headers=_H)
    with _OPENER.open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def current_issue(jid: str):
    """首页 `div.list > h5` 形如「2026年第100卷第9期」→ (2026, 100, 9)。"""
    from bs4 import BeautifulSoup
    raw = _get("https://www.geojournals.cn/%s/ch/index.aspx" % jid)
    soup = BeautifulSoup(raw, "html.parser")
    for blk in soup.select("div.list"):
        h = blk.find("h5")
        if not h:
            continue
        m = re.search(r"(\d{4})\s*年第\s*(\d+)\s*卷第\s*(\d+)\s*期",
                      _squash(h.get_text(" ", strip=True)))
        if m:
            return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None


def list_issue(jid: str, year: int, issue: int):
    """解析期次页，返回正式期次内的论文条目（已剔除优先出版）。"""
    from bs4 import BeautifulSoup
    meta = JOURNALS[jid]
    vol = year - meta["vol0"]
    url = "https://www.geojournals.cn/%s/%s/article/issue/%d_%d_%d" % (
        jid, jid, year, vol, issue)
    raw = _get(url)
    soup = BeautifulSoup(raw, "html.parser")

    out, cut = [], 0
    for li in soup.select("li.article_line"):
        tdiv = li.select_one("div.article_title")
        title = _squash(tdiv.get_text(" ", strip=True)) if tdiv else ""
        if not title:
            continue
        # 优先出版分隔行 → 后面的全部不要
        if any(k in title for k in ONLINE_FIRST_MARK):
            cut = len(out)
            break
        if any(k in title for k in NOISE):
            continue

        fid = li.select_one("input[name=FileNO]")
        fid = (fid.get("value") or "").strip() if fid else ""
        # 英文版用字母+数字 ID（2026endzxb04001），中文版是纯数字（2025515）
        if not re.match(r"^[0-9]{4}[A-Za-z0-9]{2,20}$", fid):
            m = re.search(r"article/abstract/(\d{6,9})", str(li))
            fid = m.group(1) if m else ""
        if not fid:
            continue

        oth = li.select_one("p.ot")
        oth = _squash(oth.get_text(" ", strip=True)) if oth else ""
        doi = pages = ""
        m = re.search(r"DOI:\s*(\S+)", oth)
        if m:
            doi = m.group(1).rstrip(",;.")
        m = re.search(r":\s*([\dA-Za-z\-]+)\s*(?:,|$)", oth)
        if m:
            pages = m.group(1)

        out.append({
            "fid": fid,
            "title": title,
            "url": "https://www.geojournals.cn/%s/%s/article/abstract/%s" % (
                jid, jid, fid),
            "doi": doi,
            "pages": pages,
            "date": "%d-%02d-01" % (year, issue),
            "volume": str(vol),
            "issue": str(issue),
            "journal": meta["name"],
        })
    log("期次页 %s → 正式条目 %d 篇%s" % (
        url, len(out), "（已在优先出版分隔处截断）" if cut else ""))
    return out


# ─── 台账去重 ────────────────────────────────────────────────────────────
def _nt(s: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", (s or "")).lower()


def _has_pdf(rec) -> bool:
    """⚠️ 台账里 repo_path 混用 `\\` 与 `/`，必须归一后判断。"""
    rp = (rec.get("repo_path") or "").replace("\\", "/")
    return "papers_repo/" in rp and (rec.get("size") or 0) > 0


def load_ledger():
    by_title, by_fid = {}, {}
    if not LEDGER.exists():
        return by_title, by_fid
    for line in open(LEDGER, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        k = _nt(r.get("title"))
        if k:
            old = by_title.get(k)
            # 有 PDF 的记录优先保留（同一标题可能有 metadata_only 旧记录）
            if old is None or (_has_pdf(r) and not _has_pdf(old)):
                by_title[k] = r
        m = re.search(r"article/abstract/(\d{6,9})", r.get("url") or "")
        if m:
            by_fid[m.group(1)] = r
    return by_title, by_fid


# ─── 落盘 ────────────────────────────────────────────────────────────────
def save_pdf(data: bytes, journal: str, title: str, date: str) -> Path:
    """沿用 pdf_downloader 的目录/命名约定，重名时同内容复用、异内容加序号。"""
    import pdf_downloader as pdl
    pdir = Path(pdl.REPO) / "by_journal" / pdl.safe_name(journal, 40) / date[:4]
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


def run(jid: str, year: int, issue: int, limit: int = 0,
        no_llm: bool = False, dry: bool = False,
        download_only: bool = False) -> int:
    """download_only=True：只下载 + 写台账 + 记待入库队列，**不调 DeepSeek**。"""
    from journal_pdf_fetch import fetch_pdf
    from daily_archive_papers import append_index

    items = list_issue(jid, year, issue)
    if not items:
        log("期次页无正式条目（可能还没上线，或全部是优先出版）")
        return 0

    by_title, by_fid = load_ledger()
    todo, already = [], []
    for it in items:
        rec = by_fid.get(it["fid"]) or by_title.get(_nt(it["title"]))
        if rec and _has_pdf(rec):
            already.append((it, rec))
        else:
            todo.append(it)

    log("本期 %d 篇：库内已有全文 %d 篇，待抓 %d 篇"
        % (len(items), len(already), len(todo)))
    for it, rec in already:
        log("  = 已有：%s（%s）" % (it["title"][:38], rec.get("eid") or "-"))
    if dry or not todo:
        for it in todo:
            log("  · 待抓：%s  %s" % (it["title"][:44], it["url"]))
        return 0

    if limit:
        todo = todo[:limit]

    results = []
    ok_n = fail_n = 0
    for i, it in enumerate(todo, 1):
        log("── [%d/%d] %s" % (i, len(todo), it["title"][:44]))
        rec = {"fid": it["fid"], "title": it["title"], "url": it["url"],
               "state": "", "notes": [], "repo_path": "", "eid": ""}
        try:
            r = fetch_pdf(it["journal"], it["title"], it["url"],
                          date=it["date"], doi=it["doi"])
            st = r.get("status")
            if st != "ok" or not r.get("pdf_bytes"):
                rec["state"] = "download_failed"
                rec["notes"] = ["%s: %s" % (st, str(r.get("note", ""))[:120])]
                log("   ✗ 下载失败 %s" % rec["notes"][0])
                fail_n += 1
                results.append(rec)
                time.sleep(GAP)
                continue

            data = r["pdf_bytes"]
            sha = hashlib.sha1(data).hexdigest()
            pdf = save_pdf(data, it["journal"], it["title"], it["date"])
            log("   ✓ 落盘 %s（%.1f MB）" % (pdf.name, len(data) / 1e6))

            meta = {
                "title": it["title"], "url": it["url"], "journal": it["journal"],
                "date": it["date"], "doi": it["doi"],
                "pdf_url": r.get("pdf_url", ""),
                "repo_path": str(pdf), "size": len(data),
                "source": "geojournals", "eid": "paper-%s" % sha[:12],
                "indexed_at": datetime.now().isoformat(timespec="seconds"),
            }
            # 台账先写、再入库：pdf_corpus_ingest 靠 repo_path 回查 journal/date
            append_index(meta)
            rec.update(repo_path=str(pdf), eid=meta["eid"])

            import inbox_ingest as ii
            if download_only:
                import ingest_policy
                ingest_policy.queue_pending({
                    "eid": meta["eid"], "title": it["title"],
                    "journal": it["journal"], "pdf": str(pdf),
                    "url": it["url"], "doi": it["doi"], "from": "cn_issue",
                })
                rec["state"] = "downloaded"
                rec["notes"] = ["只下载模式：已入待入库队列，未调用 DeepSeek"]
                log("   ⇢ 只下载：已入待入库队列（不调 API）")
                ok_n += 1
                results.append(rec)
                time.sleep(GAP)
                continue

            state, notes = ii.ingest(pdf, sha, meta, no_llm=no_llm)
            rec["state"] = state
            rec["notes"] = notes
            log("   ⇢ 入库 %s：%s" % (state, "；".join(notes)[:150]))
            ok_n += 1 if state == "done" else 0
            fail_n += 0 if state in ("done", "partial") else 1
        except Exception as e:  # noqa: BLE001
            rec["state"] = "error"
            rec["notes"] = ["%s: %s" % (type(e).__name__, str(e)[:140])]
            log("   ✗ 异常 %s" % rec["notes"][0])
            fail_n += 1
        results.append(rec)
        time.sleep(GAP)

    out = STATE_DIR / ("cn_issue_%s_%d_%d.json" % (jid, year, issue))
    out.write_text(json.dumps({
        "journal": JOURNALS[jid]["name"], "year": year, "issue": issue,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(items), "already": len(already),
        "tried": len(todo), "ok": ok_n, "failed": fail_n,
        "items": results,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    log("完成：成功 %d，失败 %d；报告 %s" % (ok_n, fail_n, out))
    return 0 if fail_n == 0 else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="中文期刊按期批量抓取入库")
    ap.add_argument("--journal", default="dzxb", help="dzxb / georev")
    ap.add_argument("--year", type=int, default=0)
    ap.add_argument("--issue", type=int, default=0)
    ap.add_argument("--list", action="store_true", help="只显示当前期次")
    ap.add_argument("--run", action="store_true", help="真正下载并入库")
    ap.add_argument("--download-only", action="store_true",
                    help="只下载 PDF + 写台账 + 记待入库队列，不调 DeepSeek")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-llm", action="store_true", help="离线入库（正则兜底）")
    a = ap.parse_args(argv)

    jid = ALIAS.get(a.journal, a.journal)
    if jid not in JOURNALS:
        print("未知刊物：%s（可选 %s）" % (a.journal, "、".join(JOURNALS)))
        return 2

    if a.list or not (a.year and a.issue):
        cur = current_issue(jid)
        if not cur:
            print("未能识别当前期次")
            return 2
        print("当前期次：%d 年第 %d 卷第 %d 期" % cur)
        if not a.year:
            a.year, a.issue = cur[0], cur[2]
        elif not a.issue:
            a.issue = cur[2]

    return run(jid, a.year, a.issue, limit=a.limit,
               no_llm=a.no_llm, dry=not a.run,
               download_only=a.download_only)


if __name__ == "__main__":
    sys.exit(main())
