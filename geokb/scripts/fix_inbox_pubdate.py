"""把投放箱入库的英文论文日期精确到「Available online」那一天（2026-10-25）。

背景：inbox_ingest 早期只能从 DOI 拿到年份（10.1016/j.lithos.2026.108824 → 2026），
于是台账 date 落到 YYYY-01-01、条目 pub_date 落到 YYYY-01。但 Elsevier 首页其实印了
「Available online 22 October 2026」，可以精确到日。

本脚本对 source=manual_inbox 且 date 形如 YYYY-01-01 的台账行：
  ① 读 NAS 上 PDF 首页，抽 Available online 日期；
  ② 回写 state/papers_index.jsonl 的 date（仅改动行重写，其余行原样保留）；
  ③ 回写 content/papers/paper-<sha12>.md 的 pub_date；
  ④ 回写 index/geokb.db entries.pub_date。

不动 NAS 文件名（避免 repo_path 变更带来的连锁修改），只改「日期」这一层。

    python scripts/fix_inbox_pubdate.py            # 干跑
    python scripts/fix_inbox_pubdate.py --apply    # 实际写入
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))

LEDGER = BASE / "state" / "papers_index.jsonl"
CONTENT = BASE / "content" / "papers"
DB = BASE / "index" / "geokb.db"

AVAIL_RE = re.compile(
    r"available\s+on\s?line\s*(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+((?:19|20)\d{2})", re.I)
# 少数 in-press 稿没有 Available online，只有 Accepted —— 退到「年月」
ACCEPT_RE = re.compile(
    r"accepted\s*(\d{1,2})?\s*([A-Za-z]{3,9})\.?\s+((?:19|20)\d{2})", re.I)
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
FM_RE = re.compile(r"^(---\r?\n)(.*?)(\r?\n---)", re.S)


def best_date(pdf: Path, doi: str = ""):
    """返回 (台账 date, 展示 pub_date)。有 Available online 到日，否则按 Accepted 到月。

    Accepted 只对 Elsevier（10.1016）启用：其余出版社从接收到见刊可能隔几个月
    （如 GSA Geology 接收 5 月、9 月号见刊），用接收月会写错月份。
    """
    try:
        import pymupdf
        doc = pymupdf.open(str(pdf))
        try:
            t = doc[0].get_text() or ""
        finally:
            doc.close()
    except Exception:
        return "", ""
    m = AVAIL_RE.search(t)
    if m:
        mon = MONTHS.get(m.group(2).lower()[:3])
        if mon:
            d = "%s-%02d-%02d" % (m.group(3), mon, int(m.group(1)))
            return d, d
    if str(doi or "").lower().startswith("10.1016"):
        m = ACCEPT_RE.search(t)
        if m:
            mon = MONTHS.get(m.group(2).lower()[:3])
            if mon:
                return "%s-%02d-01" % (m.group(3), mon), "%s-%02d" % (m.group(3), mon)
    return "", ""


def patch_fm_pub_date(text: str, pub: str) -> str:
    m = FM_RE.match(text)
    if not m:
        return text
    fm = m.group(2)
    line = "pub_date: '%s'" % pub
    if re.search(r"^pub_date:.*$", fm, re.M):
        fm2 = re.sub(r"^pub_date:.*$", line, fm, count=1, flags=re.M)
    else:
        fm2 = fm + "\n" + line
    return m.group(1) + fm2 + m.group(3) + text[m.end():]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    raw_lines = LEDGER.read_text(encoding="utf-8", errors="ignore").splitlines()
    out_lines, changed, stat = [], [], {"cand": 0, "hit": 0, "skip": 0}
    for ln in raw_lines:
        s = ln.strip()
        if not s:
            out_lines.append(ln)
            continue
        try:
            r = json.loads(s)
        except Exception:
            out_lines.append(ln)
            continue
        date = str(r.get("date") or "")
        if r.get("source") != "manual_inbox" or not re.match(r"^\d{4}-01-01$", date):
            out_lines.append(ln)
            continue
        stat["cand"] += 1
        if a.limit and stat["hit"] >= a.limit:
            out_lines.append(ln)
            continue
        rp = Path(str(r.get("repo_path") or ""))
        pub = ""
        if rp.exists():
            new_date, pub = best_date(rp, str(r.get("doi") or ""))
        if not pub:
            stat["skip"] += 1
            out_lines.append(ln)
            continue
        stat["hit"] += 1
        r["date"] = new_date
        changed.append((r.get("sha1") or "", r.get("eid") or "", pub, rp.name))
        out_lines.append(json.dumps(r, ensure_ascii=False))

    print("台账行 %d | 候选 %d | 命中 %d | 取不到日期 %d | apply=%s"
          % (len(raw_lines), stat["cand"], stat["hit"], stat["skip"], a.apply))
    for sha, eid, pub, fn in changed[:30]:
        print("  %s  %s  %s" % (pub, eid or sha[:12], fn[:60]))
    if not a.apply or not changed:
        return

    # ① 台账：逐行替换，未改动行保持原样（含 CRLF 以外的一切字符）
    bak = LEDGER.with_suffix(".jsonl.bak-fixpub")
    if not bak.exists():
        bak.write_text("\n".join(raw_lines) + "\n", encoding="utf-8")
    LEDGER.write_text("\n".join(out_lines) + "\n", encoding="utf-8", newline="")

    # ② 条目 frontmatter + ③ 索引
    conn = sqlite3.connect(DB)
    n_fm = n_db = 0
    for sha, eid, pub, _fn in changed:
        p = CONTENT / ("%s.md" % (eid or ("paper-" + sha[:12])))
        if p.exists():
            t = p.read_text(encoding="utf-8")
            t2 = patch_fm_pub_date(t, pub)
            if t2 != t:
                p.write_text(t2, encoding="utf-8")
                n_fm += 1
        cur = conn.execute("UPDATE entries SET pub_date=? WHERE id=?", (pub, p.stem))
        n_db += cur.rowcount
    conn.commit()
    conn.close()
    print("已写台账 %d 行 | 条目 %d 个 | 索引 %d 行 | 备份 %s"
          % (len(changed), n_fm, n_db, bak.name))


if __name__ == "__main__":
    main()
