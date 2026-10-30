"""全库标题审计：条目标题 vs 原文 PDF（2026-10-26）

要求（用户口径）：
  · 标题以**原文**为准，不参考翻译版；
  · 查乱码、查错配、查截断。

判定不用「解析出的标题」做唯一依据（PDF 标题抽取本身会截断副标题），而是三级：

  ① **命中**：把标题与 PDF 前两页正文都归一化（NFKC → 去空白/标点 → 小写）后，
     标题是正文的子串 → 说明标题确实出自这篇原文（最强证据）；
  ② **近似**：未命中但与「首页最大字号块」的相似度 ≥ 0.90 → 多为连字符/上下标差异；
  ③ **可疑**：其余。再看两个细节判断性质：
       · 条目标题是 PDF 标题的**前缀** → 截断；
       · 条目标题的**文字种类**与原文标题块相反（原文中文、条目全英文）→ 疑似用了译名；
       · 都不满足 → 错配（很可能是另一篇的标题）。

    python scripts/audit_titles.py                 # 干跑出报告
    python scripts/audit_titles.py --apply        # 只修「截断」与「译名」两类
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))
from audit_pub_dates import NAS, rel_path  # noqa: E402

DB = BASE / "index" / "geokb.db"
LEDGER = BASE / "state" / "papers_index.jsonl"
CONTENT = BASE / "content" / "papers"

CJK = re.compile(r"[\u3400-\u9fff]")
PRIVATE = re.compile(r"[\ue000-\uf8ff]")
# 审稿系统盖在首页的大字水印，不是标题
STAMP = re.compile(r"^(accepted manuscript|revision\s*\d+|retracted|scientific communications|"
                   r"correction data.*|in-confidence|article|preprint)$", re.I)


def clean_body(s: str) -> str:
    """去掉 PDF 行号（单行数字）与被拆开的行号，否则标题永远「不在正文里」。"""
    s = re.sub(r"\n\s*\d{1,4}\s*\n", "\n", s or "")
    s = re.sub(r"\s{2,}\d{1,4}\s{2,}", " ", s)
    return s


def garbled(body: str) -> bool:
    """PDF 文本层是不是私有区编码/乱码（地学前缘等老排版常见）。"""
    if not body:
        return False
    pu = len(PRIVATE.findall(body))
    if pu > 30:
        return True
    letters = sum(1 for ch in body if ch.isalnum())
    cjk = len(CJK.findall(body))
    return len(body) > 300 and letters / len(body) < 0.30 and cjk < 30
# 真正的乱码特征：替换符、UTF-8 被当 Latin-1 解码后的典型双字节序列
MOJI = re.compile(r"\ufffd|Ã[\u0080-\u00bf]|Â[\u0080-\u00bf]|â€|ï¿½|æ[\u0080-\u00bf]|å[\u0080-\u00bf]")


def norm(s: str) -> str:
    """归一化到只剩字母数字与汉字（去空白、标点、连字符、上下标差异）。"""
    s = unicodedata.normalize("NFKC", s or "")
    s = s.lower()
    return re.sub(r"[\W_]+", "", s, flags=re.U)


def pdf_texts(p: Path, pages: int = 2):
    """返回 (前两页正文, 首页最大字号标题块)。打不开返回 ('\\x00', '')。"""
    try:
        import pymupdf
        doc = pymupdf.open(str(p))
    except Exception:
        return "\x00", ""
    try:
        body = "\n".join((doc[i].get_text() or "") for i in range(min(pages, doc.page_count)))
        title = ""
        if doc.page_count:
            lines = []
            for b in doc[0].get_text("dict").get("blocks", []):
                if b.get("type") != 0:
                    continue
                for line in b.get("lines", []):
                    txt = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                    if not txt:
                        continue
                    sz = max((s.get("size", 0) for s in line.get("spans", [])), default=0)
                    lines.append((sz, line.get("bbox", (0, 0, 0, 0))[1], txt))
            if lines:
                mx = max(l[0] for l in lines)
                cand = sorted([l for l in lines if l[0] >= mx - 0.8], key=lambda x: x[1])
                title = "".join(c[2] for c in cand[:3])
        return body, title
    finally:
        doc.close()


def ratio(a: str, b: str) -> float:
    import difflib
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--report", default="")
    a = ap.parse_args()

    led = {}
    for ln in LEDGER.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = ln.strip()
        if not s:
            continue
        try:
            r = json.loads(s)
        except Exception:
            continue
        led[r.get("eid")] = r

    c = sqlite3.connect(DB)
    papers = list(c.execute("select id,title,pub_date from entries where module='papers'"))
    if a.limit:
        papers = papers[:a.limit]
    print("条目 %d｜有台账 %d" % (len(papers), len(led)), flush=True)

    todo, no_pdf = [], []
    for eid, title, pub in papers:
        rel = rel_path(led.get(eid) or {})
        p = NAS / rel if rel else None
        if p and p.is_file():
            todo.append((eid, title or "", p, led.get(eid) or {}))
        else:
            no_pdf.append((eid, title or "", pub))

    t0 = time.time()
    done = [0]

    def work(item):
        eid, title, p, r = item
        body, pt = pdf_texts(p)
        done[0] += 1
        if done[0] % 300 == 0:
            print("  … %d/%d (%.0fs)" % (done[0], len(todo), time.time() - t0), flush=True)
        return eid, title, body, pt, r

    res = []
    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
        for out in ex.map(work, todo):
            res.append(out)

    buckets = collections.defaultdict(list)
    for eid, title, body, pt, r in res:
        if body.startswith("\x00"):
            buckets["pdf_unreadable"].append((eid, title, "", 0.0, r))
            continue
        if not body.strip():
            buckets["pdf_no_text"].append((eid, title, "", 0.0, r))
            continue
        nt, nb = norm(title), norm(clean_body(body))
        hit = bool(nt) and nt in nb
        block = pt.strip()
        block_ok = (len(block) >= 10 and not STAMP.match(block)
                    and not garbled(block))
        npt = norm(block) if block_ok else ""
        sim = ratio(nt, npt) if npt else 0.0
        t_cjk = bool(CJK.search(title))
        p_cjk = bool(CJK.search(block))

        if garbled(body):
            buckets["pdf_garbled"].append((eid, title, block, 0.0, r))
            continue
        if MOJI.search(title):
            buckets["mojibake"].append((eid, title, block, sim, r))
        elif hit:
            # 命中正文即视为同源；再顺带判断是否被截断（原文标题明显更长）
            if npt and nt != npt and npt.startswith(nt) and len(npt) - len(nt) >= 8:
                buckets["truncated"].append((eid, title, block, sim, r))
            else:
                buckets["ok"].append((eid, title, block, sim, r))
        elif sim >= 0.90:
            buckets["approx"].append((eid, title, block, sim, r))
        elif npt and npt.startswith(nt) and len(npt) - len(nt) >= 8:
            buckets["truncated"].append((eid, title, block, sim, r))
        elif (t_cjk and not p_cjk) or (p_cjk and not t_cjk):
            buckets["script_mismatch"].append((eid, title, block, sim, r))
        elif sim >= 0.60:
            buckets["approx_low"].append((eid, title, block, sim, r))
        else:
            buckets["mismatch"].append((eid, title, block, sim, r))

    print("\n=== 结果 ===")
    for k in ("ok", "approx", "truncated", "script_mismatch", "approx_low",
              "mismatch", "mojibake", "pdf_garbled", "pdf_no_text", "pdf_unreadable"):
        if buckets[k]:
            print("  %-16s %d" % (k, len(buckets[k])))
    print("  无 PDF          %d" % len(no_pdf))
    print("用时 %.0fs" % (time.time() - t0))

    rep = Path(a.report) if a.report else BASE / "logs" / (
        "title_audit_%s.md" % datetime.now().strftime("%Y%m%d_%H%M"))
    rep.parent.mkdir(parents=True, exist_ok=True)
    L = ["# 论文标题一致性审计（%s）" % datetime.now().strftime("%Y-%m-%d %H:%M"), "",
         "条目 %d｜有 PDF 可核 %d｜无 PDF %d" % (len(papers), len(todo), len(no_pdf)), "",
         "## 分布", "", "| 判定 | 篇数 | 说明 |", "|---|---|---|",
         "| ok | %d | 标题在原文正文中逐字命中 |" % len(buckets["ok"]),
         "| approx | %d | 与原文标题相似度 ≥0.90（连字符/上下标差异）|" % len(buckets["approx"]),
         "| truncated | %d | 条目标题是原文标题的截断（缺副标题）|" % len(buckets["truncated"]),
         "| script_mismatch | %d | 文字种类与原文标题相反（疑似用了译名）|" % len(buckets["script_mismatch"]),
         "| approx_low | %d | 相似度 0.60–0.90，需人工看 |" % len(buckets["approx_low"]),
         "| mismatch | %d | 相似度 <0.60，疑似错配 |" % len(buckets["mismatch"]),
         "| mojibake | %d | 标题含乱码字符 |" % len(buckets["mojibake"]),
         "| pdf_garbled | %d | PDF 文本层是私有区编码/乱码，无法比对 |" % len(buckets["pdf_garbled"]),
         "| pdf_no_text | %d | PDF 无文本层（扫描件），无法比对 |" % len(buckets["pdf_no_text"]),
         "| pdf_unreadable | %d | PDF 打不开 |" % len(buckets["pdf_unreadable"]),
         "| 无 PDF | %d | 台账里没有可读文件 |" % len(no_pdf), ""]

    def block(key, title_txt, note=""):
        items = buckets[key]
        if not items:
            return
        L.extend(["## %s（%d）%s" % (title_txt, len(items), note), "",
                  "| # | 刊 | 条目标题 | 原文标题块 | 相似度 |", "|---|---|---|---|---|"])
        for n, (eid, title, pt, sim, r) in enumerate(sorted(items, key=lambda x: x[3])[:200], 1):
            L.append("| %d | %s | %s | %s | %.2f |" % (
                n, str(r.get("journal") or "-")[:14],
                str(title)[:56].replace("|", "/"),
                str(pt)[:56].replace("|", "/"), sim))
        L.append("")

    block("mismatch", "疑似错配", "（条目标题与原文对不上，优先核查）")
    block("script_mismatch", "疑似用了译名/译文标题")
    block("truncated", "标题被截断")
    block("approx_low", "疑似差异较大")
    block("mojibake", "乱码")
    block("pdf_garbled", "PDF 文本层乱码（非条目标题问题）")
    block("pdf_no_text", "PDF 无文本层")
    block("pdf_unreadable", "PDF 打不开")
    if no_pdf:
        L.extend(["## 无 PDF 可核（%d）" % len(no_pdf), "", "| # | 标题 | 日期 |", "|---|---|---|"])
        for n, (eid, title, pub) in enumerate(no_pdf[:120], 1):
            L.append("| %d | %s | %s |" % (n, str(title)[:64].replace("|", "/"), pub))
        L.append("")
    rep.write_text("\n".join(L) + "\n", encoding="utf-8")
    # 明细 TSV：给「修标题」环节用，避免再扫一遍 PDF
    tsv = rep.with_suffix(".tsv")
    with open(tsv, "w", encoding="utf-8") as f:
        f.write("eid\tbucket\tjournal\ttitle\tblock\tsim\tledger_title\tsource\n")
        for key in ("mismatch", "script_mismatch", "truncated", "approx_low",
                    "approx", "mojibake"):
            for eid, title, blk, sim, r in buckets[key]:
                f.write("%s\t%s\t%s\t%s\t%s\t%.3f\t%s\t%s\n" % (
                    eid, key, str(r.get("journal") or "").replace("\t", " "),
                    str(title).replace("\t", " "), str(blk).replace("\t", " "),
                    sim, str(r.get("title") or "").replace("\t", " "),
                    r.get("source") or ""))
    print("明细：%s" % tsv)
    print("报告：%s" % rep)


if __name__ == "__main__":
    main()
