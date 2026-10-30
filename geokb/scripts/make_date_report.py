"""汇总出版日期审计结果，产出给人看的 Markdown 报告。"""
import collections
import json
import sqlite3
from datetime import datetime
from pathlib import Path

BASE = Path("D:/ZSK/geokb")
DB = BASE / "index" / "geokb.db"
TSV = BASE / "logs" / "pub_date_audit_20261026.tsv"
OUT = BASE / "logs" / "出版日期审计_20261026.md"
LEDGER = BASE / "state" / "papers_index.jsonl"


def bucket(d):
    d = (d or "").strip()
    if not d:
        return "无日期"
    if len(d) == 10:
        return "到日" if not d.endswith("-01") else "到月"
    if len(d) == 7:
        return "到月"
    if len(d) == 4:
        return "仅年"
    return "异常"


c = sqlite3.connect(DB)
papers = list(c.execute(
    "select id,title,pub_date from entries where module='papers'").fetchall())
# journal 不在索引列里 → 从条目 frontmatter 取
import re
FM_J = re.compile(r"^journal:\s*\"?'?(.*?)'?\"?\s*$", re.M)


def journal_of(eid):
    p = BASE / "content" / "papers" / ("%s.md" % eid)
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")[:2500]
    except OSError:
        return ""
    m = FM_J.search(t)
    return (m.group(1) if m else "").strip('"').strip("'")[:24]


dist = collections.Counter(bucket(p[2]) for p in papers)
no_year = [p for p in papers if bucket(p[2]) in ("无日期", "异常")]
year_only = sorted([p for p in papers if bucket(p[2]) == "仅年"], key=lambda x: x[2])

rows = []
# 两轮审计的明细合并（第一轮月→日、第二轮年→月）
for ts in (TSV, BASE / "logs" / "pub_date_audit_round2.tsv"):
    if not ts.exists():
        continue
    with open(ts, encoding="utf-8") as f:
        f.readline()
        for ln in f:
            q = ln.rstrip("\n").split("\t")
            if len(q) >= 6:
                rows.append(q)

ev = collections.Counter(r[4] for r in rows)
cross_year = [r for r in rows if r[2][:4] != r[3][:4]]

# 台账里没有对应行的条目（孤儿）
led_eids = set()
for ln in LEDGER.read_text(encoding="utf-8", errors="ignore").splitlines():
    s = ln.strip()
    if not s:
        continue
    try:
        led_eids.add(json.loads(s).get("eid"))
    except Exception:
        pass

L = []
A = L.append
A("# 知识库论文「出版日期」审计报告")
A("")
A("生成时间：%s　·　范围：GeoKB `module=papers` 全部 **%d** 篇" %
  (datetime.now().strftime("%Y-%m-%d %H:%M"), len(papers)))
A("")
A("## 一、结论")
A("")
A("| 项 | 数值 |")
A("|---|---|")
A("| 论文总数 | %d |" % len(papers))
A("| 本次改动 | **%d 篇**（其中 %d 篇补到了「日」）|" %
  (len(rows), ev.get("published", 0) + ev.get("available_online", 0)))
A("| 到日（YYYY-MM-DD） | %d |" % dist.get("到日", 0))
A("| 到月（YYYY-MM） | %d |" % dist.get("到月", 0))
A("| 仅年（YYYY） | %d |" % dist.get("仅年", 0))
A("| **完全没有年份** | **%d**（见第五节清单）|" % len(no_year))
A("")
A("## 二、审计前后精度分布")
A("")
A("| 精度 | 审计前 | 审计后 |")
A("|---|---|---|")
before = {"到日": 344, "到月": 1335, "仅年": 142, "无日期": 8}
for k in ("到日", "到月", "仅年", "无日期"):
    A("| %s | %d | %d |" % (k, before.get(k, 0), dist.get(k, 0)))
A("")
A("> 注：审计前数字为 2026-10-25 快照（1829 篇）；期间新增入库若干，故两列总数不等。")
A("")
A("## 三、%d 篇改动按证据来源" % len(rows))
A("")
A("| 证据 | 含义 | 篇数 |")
A("|---|---|---|")
A("| published | 首页 `PUBLISHED` / `Published online:` / `First published:` | %d |" % ev.get("published", 0))
A("| available_online | Elsevier `Available online <日 月 年>` | %d |" % ev.get("available_online", 0))
A("| cn_masthead | 中文刊刊头「YYYY年M月」 | %d |" % ev.get("cn_masthead", 0))
A("| text_weak | 首页首个 19xx/20xx（弱证据，补到年） | %d |" % ev.get("text_weak", 0))
A("")
A("逐条明细见 `logs/pub_date_audit_20261026.tsv`（含 eid、旧值、新值、证据、标题）。")
A("")
if cross_year:
    A("## 四、⚠️ 跨年改动 %d 篇（需你确认口径）" % len(cross_year))
    A("")
    A("这些文章的**在线首发日**落在上一年，而卷期属于下一年。当前口径是「以在线首发日为准」，"
      "所以年份变小了。若你希望改按**卷期年**，说一声，我可以据此回退（台账有 `.bak-dates` 备份）。")
    A("")
    A("| 刊 | 原 | 现（在线首发日） | 标题 |")
    A("|---|---|---|---|")
    for r in cross_year[:50]:
        A("| %s | %s | %s | %s |" % (r[1][:16], r[2], r[3], r[5][:44].replace("|", "/")))
    if len(cross_year) > 50:
        A("| … | | | 另 %d 篇 |" % (len(cross_year) - 50))
    A("")
A("## 五、完全没有年份的论文（%d 篇，清单）" % len(no_year))
A("")
A("| # | 刊 | 标题 | 情况 |")
A("|---|---|---|---|")
for n, (eid, title, pub) in enumerate(no_year, 1):
    j = journal_of(eid) or "-"
    if eid not in led_eids:
        why = "条目缺 frontmatter（无 sources/year），台账无对应行"
    else:
        why = "PDF 首页找不到任何年份（多来自 feed 迁移）"
    A("| %d | %s | %s | %s |" % (n, j, str(title)[:60].replace("|", "/"), why))
A("")
A("## 六、只有年份、无法到月的论文（%d 篇）" % len(year_only))
A("")
A("| # | 年 | 刊 | 标题 |")
A("|---|---|---|---|")
for n, (eid, title, pub) in enumerate(year_only, 1):
    A("| %d | %s | %s | %s |" % (n, pub, journal_of(eid) or "-",
                                 str(title)[:58].replace("|", "/")))
A("")
A("## 七、日期冲突（%d 篇，本次未改）" % 30)
A("")
A("台账与 PDF 都是「到日」，但相差 1–2 天（多为 `Available online` 与 `Published online` "
  "两个字段不同）。明细见审计报告的「冲突」小节 `logs/pub_date_audit_20261026.md`；"
  "如要统一改成 PDF 印刷的日期，可再跑一次并开 `--prefer-pdf`。")
A("")
A("## 八、方法与复现")
A("")
A("```")
A("python scripts/audit_pub_dates.py --workers 6            # 干跑出报告")
A("python scripts/audit_pub_dates.py --workers 6 --apply    # 写台账 date")
A("python scripts/migrate_pub_date.py --apply               # 台账 → 条目/知识点 pub_date")
A("python scripts/backfill_pub_date_index.py                # → index/geokb.db")
A("```")
A("")
A("判定优先级：`Available online` → `PUBLISHED`/`Published online` → 中文刊刊头年月 → "
  "Elsevier `Accepted`（只到月）→ 刊头 `Month YYYY` → DOI 年份 → 首页首个年份。"
  "**只升不降**：已有「日」的不会被更粗的结果覆盖。")

OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
print("报告已写：%s（%d 行）" % (OUT, len(L)))
print("分布:", dict(dist), "| 无年份", len(no_year), "| 仅年", len(year_only),
      "| 跨年改动", len(cross_year))
