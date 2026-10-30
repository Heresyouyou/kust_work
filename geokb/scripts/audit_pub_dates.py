"""全库发布日期审计：把每篇论文的「出版日期」收敛到最精确的一档（2026-10-26）。

用户口径：
  · 知识库里呈现的日期 = **出版日期**（不是入库日期）；
  · 精度 **年+月** 为默认，有「日」就补到日；
  · 没有月份，至少要定到**年**；
  · 连年份都没有的，单独列清单。

判定优先级（越靠前越可信，全部取自 PDF 首页原文）：
  ① `Available online 17 October 2026`  → 到日
  ② 中文刊刊头 `2026年10月  第45卷 第5期`  → 到月
  ③ 仅 Elsevier（10.1016）的 `Accepted 7 October 2026` → 到月
  ④ DOI 里的年份 `10.1016/j.lithos.2026.108824` → 到年
  ⑤ 首页前 1500 字里第一个 19xx/20xx → 到年（弱证据，单独标记，供复核）

只升不降：已有到日的日期不会被更粗的结果覆盖；两者不一致记为 conflict 供人工复核。

    python scripts/audit_pub_dates.py              # 干跑，出报告
    python scripts/audit_pub_dates.py --apply      # 写台账 date
之后必须接：
    python scripts/migrate_pub_date.py --apply      # 台账 date → 条目/知识点 pub_date
    python scripts/backfill_pub_date_index.py       # → index/geokb.db
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))

LEDGER = BASE / "state" / "papers_index.jsonl"
NAS = Path("S:/GeoKB/papers_repo")

# 与 inbox_ingest 共用同一套正则/月份表，避免两处口径漂移
from inbox_ingest import (  # noqa: E402
    ACCEPT_RE, AVAIL_RE, CN_YM_RE, DOI_YEAR_RE, MONTHS)

WEAK_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
# 非 Elsevier 的「出版日」写法：Frontiers `PUBLISHED 10 January 2025`、
# Springer/MDPI/Wiley `Published online: 14 March 2024` / `First published: …`
PUB_RE = re.compile(
    r"(?:first\s+)?published(?:\s+online)?\s*[:\s]\s*(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+((?:19|20)\d{2})",
    re.I)
# 刊头里的 `October 2025` / `Sep. 2026`，只在前 400 字找，避开引文年份
MONTH_YEAR = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+((?:19|20)\d{2})\b",
    re.I)


def squeeze_digits(s: str) -> str:
    """中文刊 PDF 抽出来的数字常被字距拆开：`2 0 2 6 年 9 月` → `2026年9月`。

    不做这一步，「YYYY年M月」永远匹配不上，一堆中文刊只能停在「仅年」。
    """
    return re.sub(r"(?<=\d)[ \t\u00a0]+(?=\d)", "", s or "")


def pdf_text(p: Path, pages: int = 1) -> str:
    try:
        import pymupdf
        doc = pymupdf.open(str(p))
        try:
            return "\n".join((doc[i].get_text() or "") for i in range(min(pages, doc.page_count)))
        finally:
            doc.close()
    except Exception:
        return "\x00"          # 打不开（损坏 / 无权限 / 网络失败）


def best_date(text: str, doi: str):
    """返回 (date_str 或 '', 证据标签)。date_str 只可能是 YYYY / YYYY-MM / YYYY-MM-DD。"""
    if text.startswith("\x00"):
        return "", "pdf_unreadable"
    t = text or ""
    tz = squeeze_digits(t)          # 数字被字距拆开的版本（中文刊常见）

    m = AVAIL_RE.search(t[:9000])
    if m:
        mon = MONTHS.get(m.group(2).lower()[:3])
        if mon:
            return "%s-%02d-%02d" % (m.group(3), mon, int(m.group(1))), "available_online"

    m = PUB_RE.search(t[:9000])
    if m:
        mon = MONTHS.get(m.group(2).lower()[:3])
        if mon:
            return "%s-%02d-%02d" % (m.group(3), mon, int(m.group(1))), "published"

    m = CN_YM_RE.search(tz[:1200])
    if m:
        mon = int(m.group(2))
        if 1 <= mon <= 12:
            return "%s-%02d" % (m.group(1), mon), "cn_masthead"

    if str(doi or "").lower().startswith("10.1016"):
        m = ACCEPT_RE.search(t[:9000])
        if m:
            mon = MONTHS.get(m.group(2).lower()[:3])
            if mon:
                return "%s-%02d" % (m.group(3), mon), "accepted"

    m = MONTH_YEAR.search(tz[:400])
    if m:
        mon = MONTHS.get(m.group(1).lower()[:3])
        if mon:
            return "%s-%02d" % (m.group(2), mon), "month_year"

    m = DOI_YEAR_RE.search(re.sub(r"\s+", "", doi or ""))
    if m:
        return m.group(1), "doi"

    m = WEAK_YEAR.search(tz[:1500])
    if m:
        return m.group(1), "text_weak"
    return "", "no_year"


def level(d: str) -> int:
    """0 空 / 1 仅年 / 2 到月 / 3 到日。"""
    d = str(d or "")
    if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        return 3 if not d.endswith("-01") else 2
    if re.match(r"^\d{4}-\d{2}$", d):
        return 2
    if re.match(r"^\d{4}$", d):
        return 1
    return 0


def norm_date_for_compare(d: str) -> str:
    """把台账的 date 归一成「精度等价」的形式：YYYY-MM-01 视为到月。"""
    d = str(d or "")
    if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        return d[:7] if d.endswith("-01") else d
    return d


def rel_path(r: dict) -> str:
    p = str(r.get("repo_path") or "").replace("\\", "/")
    i = p.find("papers_repo/")
    return p[i + len("papers_repo/"):] if i >= 0 else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--level-below", type=int, default=0,
                    help="只处理当前精度低于 N 的（3=到日）")
    ap.add_argument("--report", default="")
    a = ap.parse_args()

    raw = LEDGER.read_text(encoding="utf-8", errors="ignore").splitlines()
    rows, idx_of = [], {}
    for i, ln in enumerate(raw):
        s = ln.strip()
        if not s:
            continue
        try:
            r = json.loads(s)
        except Exception:
            continue
        rows.append(r)
        idx_of[id(r)] = i

    todo = []
    for r in rows:
        if a.level_below and level(r.get("date")) >= a.level_below:
            continue
        rel = rel_path(r)
        p = NAS / rel if rel else None
        if p and p.is_file():
            todo.append((r, p))
    if a.limit:
        todo = todo[:a.limit]

    print("台账 %d 行｜有 PDF 可查 %d 篇｜并发 %d｜apply=%s"
          % (len(rows), len(todo), a.workers, a.apply), flush=True)

    t0 = time.time()
    done = [0]

    def work(item):
        r, p = item
        text = pdf_text(p)
        best, how = best_date(text, r.get("doi") or "")
        done[0] += 1
        if done[0] % 200 == 0:
            print("  … %d/%d  (%.0fs)" % (done[0], len(todo), time.time() - t0), flush=True)
        return r, best, how

    results = []
    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
        for out in ex.map(work, todo):
            results.append(out)

    bucket = collections.Counter()
    changes, weak_no_year, no_year, conflicts = [], [], [], []
    for r, best, how in results:
        cur = str(r.get("date") or "")
        cur_l, best_l = level(cur), level(best)
        final = best if best_l > cur_l else cur
        if not level(final):
            # 审计后仍然没有年份 —— 这才是用户要的那份清单
            no_year.append((r, how, cur))
        if not best:
            bucket[how] += 1
            if level(cur):
                weak_no_year.append((r, how))     # PDF 复核不到，沿用已有值
            continue
        if cur_l >= 3 and best_l >= 3:
            if norm_date_for_compare(cur) == norm_date_for_compare(best):
                bucket["unchanged"] += 1
            else:
                bucket["conflict"] += 1
                conflicts.append((r, cur, best, how))
            continue
        if best_l > cur_l:
            bucket["upgrade_%d->%d" % (cur_l, best_l)] += 1
            changes.append((r, cur, best, how))
        else:
            bucket["unchanged"] += 1

    print("\n=== 结果 ===")
    for k, v in sorted(bucket.items()):
        print("  %-18s %d" % (k, v))
    print("  合计 %d" % sum(bucket.values()))
    print("提升 %d 篇｜无年份 %d 篇｜冲突 %d 篇｜PDF 未复核但沿用原值 %d 篇｜用时 %.0fs"
          % (len(changes), len(no_year), len(conflicts), len(weak_no_year),
             time.time() - t0))

    # ── 报告 ──
    rep = Path(a.report) if a.report else BASE / "logs" / ("pub_date_audit_%s.md"
                                                          % datetime.now().strftime("%Y%m%d_%H%M"))
    rep.parent.mkdir(parents=True, exist_ok=True)
    after = collections.Counter()
    for r, best, how in results:
        cur = str(r.get("date") or "")
        after[len(best) if level(best) > level(cur) else len(norm_date_for_compare(cur))] += 1
    L = ["# 出版日期审计（%s）" % datetime.now().strftime("%Y-%m-%d %H:%M"), "",
         "台账 %d 行｜可查 PDF %d 篇｜**提升 %d 篇**｜审计后仍无年份 %d 篇｜冲突 %d 篇"
         % (len(rows), len(todo), len(changes), len(no_year), len(conflicts)), "",
         "## 本次取证分布", "", "| 证据来源 | 篇数 |", "|---|---|"]
    for k, v in sorted(bucket.items()):
        L.append("| %s | %d |" % (k, v))
    L += ["", "## 审计后仍无年份 —— 清单（%d）" % len(no_year), "",
          "| # | 刊 | 标题 | 现有 date | PDF 情况 | 文件 |", "|---|---|---|---|---|---|"]
    for n, (r, how, cur) in enumerate(no_year, 1):
        L.append("| %d | %s | %s | %s | %s | %s |" % (
            n, str(r.get("journal") or "-")[:14],
            str(r.get("title") or "")[:58].replace("|", "/"),
            cur or "(空)", {"pdf_unreadable": "PDF 打不开",
                            "no_year": "首页无任何年份"}.get(how, how),
            rel_path(r)[-56:]))
    if weak_no_year:
        L += ["", "## PDF 未能复核、沿用原值（%d，前 30 条）" % len(weak_no_year), "",
              "| # | 标题 | 沿用 date |", "|---|---|---|"]
        for n, (r, how) in enumerate(weak_no_year[:30], 1):
            L.append("| %d | %s | %s |" % (
                n, str(r.get("title") or "")[:58].replace("|", "/"), r.get("date")))
    L += ["", "## 冲突（到日 vs 到日 不一致，%d）" % len(conflicts), "",
          "| # | 标题 | 台账 | PDF | 证据 |", "|---|---|---|---|---|"]
    for n, (r, cur, best, how) in enumerate(conflicts, 1):
        L.append("| %d | %s | %s | %s | %s |" % (
            n, str(r.get("title") or "")[:52].replace("|", "/"), cur, best, how))
    rep.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("报告：%s" % rep)

    # 逐条变更明细（供复核：新日期与旧日期差太多时人工看）
    tsv = rep.with_suffix(".tsv")
    with open(tsv, "w", encoding="utf-8") as f:
        f.write("eid\tjournal\told\tnew\tevidence\ttitle\n")
        for r, cur, best, how in changes:
            f.write("%s\t%s\t%s\t%s\t%s\t%s\n" % (
                r.get("eid") or "", str(r.get("journal") or "").replace("\t", " "),
                cur, best, how, str(r.get("title") or "").replace("\t", " ")[:80]))
    print("变更明细：%s（%d 行）" % (tsv, len(changes)))

    if a.apply and changes:
        bak = LEDGER.with_suffix(".jsonl.bak-dates")
        if not bak.exists():
            bak.write_text("\n".join(raw) + "\n", encoding="utf-8")
        new_by_eid = {}
        for r, cur, best, how in changes:
            eid = str(r.get("eid") or "")
            if eid:
                new_by_eid[eid] = best
        out, n_written = [], 0
        for ln in raw:
            s = ln.strip()
            if not s:
                out.append(ln)
                continue
            try:
                obj = json.loads(s)
            except Exception:
                out.append(ln)
                continue
            best = new_by_eid.get(str(obj.get("eid") or ""))
            if best:
                obj["date"] = best
                out.append(json.dumps(obj, ensure_ascii=False))
                n_written += 1
            else:
                out.append(ln)
        tmp = LEDGER.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(out) + "\n", encoding="utf-8", newline="")
        tmp.replace(LEDGER)
        print("已写台账 %d 行（备份 %s）" % (n_written, bak.name))


if __name__ == "__main__":
    main()
