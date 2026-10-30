#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""统一投放箱：手动下载的论文 PDF → NAS 期刊目录 → 台账 → 入库（2026-10-21）。

为什么有这个脚本
----------------
新版口径（用户 2026-10-21）：**当天没能下载到 PDF 的论文，一律不入库**。
不写纯题录、不建 external-feed 条目、不写 metadata_only 台账 —— 知识库里
只有「有全文」的文献。没抓到的，由用户手动下载后投到本文件夹，批量入库。

覆盖面：**不止 ScienceDirect**。凡是自动链路没拿到 PDF 的（SD 的 CF 内容
防护、OUP/GSW 风控、付费墙、需要机构登录的），手动下载后都丢进同一个
文件夹，本脚本按 DOI/ISSN/台账统一识别归属期刊。

链路（与「网页上传」同源，scripts/user_upload.py）
------------------------------------------------
    扫描 → 校验 → 识别（台账/DOI/ISSN/刊名）→ 查重
      → 移动到 NAS by_journal/<期刊>/<年>/ → 写台账
      → DeepSeek 摘要 → 知识点 → 增量索引 → 清空投放目录

与 user_upload 的差别
--------------------
1. **归属**：上传入口一律进 `Addition/`（不归属期刊）；本链路必须匹配期刊目录，
   落到 `by_journal/<期刊>/<年>/`，`/journals` 页的下载按钮才会亮
   （journal_feed.is_pdf_entry 只认 repo_path 含 papers_repo/）。
2. **不写元数据兜底**：不提 upgraded_from、不做题录升级；有 PDF 就入库。

期刊归属怎么定（全离线，四级）
------------------------------
    ① 台账命中（sha1 / DOI / PII / 标题）→ 沿用该记录的 journal
    ② DOI 前缀 `10.1016/j.<code>` → 地学 SD 刊表（OGR/Lithos/EPSL 等）
    ③ 首页 ISSN → 刊名表
    ④ 首页文本里出现已知刊名（台账 + PostNews/journal_ledger.json 的刊名集合）
    ⑤ 都不中 → OTHERS

目录名不按刊名硬造，而是**从台账反推**：历史遗留 `OGR` / `LITHOS` /
`Ore Geology Reviews` 混用，硬编码会造出平行目录。反推取「同一 journal 值
在台账里出现最多的目录名」，逐刊继承既有惯性。

用法
----
    python inbox_ingest.py --run                 # 正式跑（计划任务用这个）
    python inbox_ingest.py --run --dry-run       # 只识别与预演，不写盘
    python inbox_ingest.py --run --limit 3       # 只处理前 3 篇
    python inbox_ingest.py --run --no-llm        # 离线联调（不调 DeepSeek）
    python inbox_ingest.py --status              # 看上次运行报告
    python inbox_ingest.py --run --folder D:/x   # 临时换目录

    计划任务：每天 12:01 与 18:01 各跑一次（均在 DeepSeek 低谷窗口内）。
    本脚本**不设时间闸门** —— 业务链路必须可随时手动触发，时段由计划任务掌握。

退出码：0 全部成功/无事可做；1 有失败；2 参数错。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import paths as geokb_paths          # noqa: E402  NAS 根路径唯一真源
import pdf_downloader as pdl         # noqa: E402  safe_name / 落盘命名同源
import user_upload as uu             # noqa: E402  台账、锁、索引复用，避免两套规则

LEDGER = BASE / "state" / "papers_index.jsonl"
PAPERS_DIR = BASE / "content" / "papers"
KP_DIR = BASE / "content" / "kpoints"
LOG_FILE = BASE / "state" / "sd_inbox_log.jsonl"      # 沿用旧名，历史日志连续
STATUS_FILE = BASE / "state" / "sd_inbox_status.json"
CONFIG = BASE / "config" / "inbox.yaml"
POSTNEWS = Path("D:/PostNews")

DEFAULT_INBOX = BASE / "inbox" / "papers"
LEGACY_INBOX = BASE / "state" / "inbox"        # manual_inbox.py 时代的收件箱
SUFFIX = "_manual"                             # NAS 文件名后缀，标识人工投放
MIN_AGE_SEC = 45         # 太新的文件可能还在写入（Edge 走 .crdownload，落地即完整，45s 足够）
MAX_PDF_BYTES = 200 * 1024 * 1024
LOCK_WAIT_MAX = 25 * 60                        # 与历史队列共用 pdf_ingest.lock
# 本脚本可能被「无控制台」的父进程拉起（门户按钮、计划任务）。那种环境下每 spawn
# 一个 console 子进程都会弹一个黑窗，必须显式隐藏（2026-10-26 实测踩坑）。
_NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.I)
PII_RE = re.compile(r"\bS\d{15,17}\b")
ISSN_RE = re.compile(r"\b(\d{4}-\d{3}[\dXx])\b")
YEAR_RE = re.compile(r"\((19[89]\d|20[0-4]\d)\)")

# ── ScienceDirect DOI 前缀 → 规范刊名（`10.1016/j.<code>` 之后的 code）────────
# 其余出版社（Wiley/Springer/MDPI/Elsevier 非 j.* 前缀）无法从前缀定刊，
# 交给 ③ISSN / ④刊名文本 兜底。
SD_JOURNALS = {
    "oregeorev": "Ore Geology Reviews",
    "lithos": "Lithos",
    "epsl": "Earth and Planetary Science Letters",
    "precamres": "Precambrian Research",
    "chemgeo": "Chemical Geology",
    "gexplo": "Journal of Geochemical Exploration",
    "gr": "Gondwana Research",
    "jsg": "Journal of Structural Geology",
    "sedgeo": "Sedimentary Geology",
    "mineng": "Minerals Engineering",
    "apgeochem": "Applied Geochemistry",
    "earscirev": "Earth-Science Reviews",
    "tecto": "Tectonophysics",
    "cretres": "Cretaceous Research",
    "jvolgeores": "Journal of Volcanology and Geothermal Research",
    "marpetgeo": "Marine and Petroleum Geology",
    "jaes": "Journal of Asian Earth Sciences",
    "geomorph": "Geomorphology",
    "jog": "Journal of Geodynamics",
    "geothermics": "Geothermics",
    "jafrearsci": "Journal of African Earth Sciences",
    "chemer": "Geochemistry",
}

# ── ISSN → 规范刊名（PDF 首页兜底；DOI 缺失时）────────────────────────────
SD_ISSN = {
    "0169-1368": "Ore Geology Reviews",
    "0024-4937": "Lithos",
    "0012-821X": "Earth and Planetary Science Letters",
    "0301-9268": "Precambrian Research",
    "0009-2541": "Chemical Geology",
    "0375-6742": "Journal of Geochemical Exploration",
    "1342-937X": "Gondwana Research",
    "0191-8141": "Journal of Structural Geology",
    "0037-0738": "Sedimentary Geology",
    "0892-6875": "Minerals Engineering",
    "0883-2927": "Applied Geochemistry",
    "0012-8252": "Earth-Science Reviews",
    "0040-1951": "Tectonophysics",
    "0195-6671": "Cretaceous Research",
    "0377-0273": "Journal of Volcanology and Geothermal Research",
    "0264-8172": "Marine and Petroleum Geology",
    "1367-9120": "Journal of Asian Earth Sciences",
    "0264-3707": "Journal of Geodynamics",
    "0375-6505": "Geothermics",
    "1464-343X": "Journal of African Earth Sciences",
}

# 台账反推时要排除的「非期刊」桶
NON_JOURNAL_DIRS = {"_feed_migration", "_probe", "Addition", "OTHERS"}

# ── 中文刊：DOI 前缀 / 刊号 → 规范刊名 ────────────────────────────────────
# 中文 PDF 抽出的 DOI 常带空格（"Doi: 10. 16111/j. 0258-7106. 2026. 05. 001"），
# 匹配前一律先压掉空白。ISSN 同理（中文刊常写「文章编号：0258-7106」）。
CN_DOI_JOURNALS = [
    (re.compile(r"^10\.16111/j\.0258-7106", re.I), "矿床地质"),
]
CN_ISSN = {
    "0258-7106": "矿床地质",
}

# 中文刊刊头（"2026年10月  第45卷 第5期"）能定到年月
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
DOI_YEAR_RE = re.compile(r"[./(]((?:19|20)\d{2})[.)/]")
CN_YM_RE = re.compile(r"((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*月")
# Elsevier 首页：「… LITHOS 546–547 (2026) 108824  Available online 22 October 2026」
AVAIL_RE = re.compile(
    r"available\s+on\s?line\s*(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+((?:19|20)\d{2})", re.I)
# 少数 in-press 稿只有 Accepted（无 Available online）→ 只能到「年月」
ACCEPT_RE = re.compile(
    r"accepted\s*(\d{1,2})?\s*([A-Za-z]{3,9})\.?\s+((?:19|20)\d{2})", re.I)
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
# 明显不是标题的行（中文刊首页常把 DOI/文章编号/卷期放在大字号旁边）
NOT_TITLE_RE = re.compile(
    r"^(doi\b|https?://|www\.|10\.\d{4,9}/|文章编号|收稿日期|修回日期|第\s*\d+\s*卷|"
    r"vol\.?\s*\d+|issn\b|网络首发|doi[:：])", re.I)

_builtin_print = print


def print(*a, **kw):        # noqa: A001  pythonw 无控制台时 print 抛 OSError
    try:
        _builtin_print(*a, **kw)
    except Exception:
        pass


def log(msg: str):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE.with_suffix(".txt"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ─── 投放目录 ─────────────────────────────────────────────────────────────
def inbox_dir(cli: str = "") -> Path:
    """--folder > GEOKB_INBOX > config/inbox.yaml > 默认 inbox/papers。"""
    if cli:
        return Path(cli)
    env = os.environ.get("GEOKB_INBOX")
    if env:
        return Path(env)
    try:
        import yaml
        if CONFIG.exists():
            cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
            p = cfg.get("inbox")
            if p:
                return Path(str(p))
    except Exception:
        pass
    return DEFAULT_INBOX


# ─── 期刊识别 ─────────────────────────────────────────────────────────────
def journal_dir_map(rows) -> dict:
    """{journal_norm: 目录名}，历史惯性优先（取篇数最多的目录）。"""
    votes = defaultdict(Counter)
    for r in rows:
        j = (r.get("journal") or "").strip()
        rp = str(r.get("repo_path") or "").replace("\\", "/")
        if not j or "by_journal/" not in rp:
            continue
        d = rp.split("by_journal/", 1)[1].split("/", 1)[0]
        if not d or d in NON_JOURNAL_DIRS:
            continue
        votes[j.lower()][d] += 1
    return {j: c.most_common(1)[0][0] for j, c in votes.items() if c}


def pick_dir(journal: str, dmap: dict) -> str:
    j = (journal or "").strip()
    if not j:
        return "OTHERS"
    return dmap.get(j.lower()) or pdl.safe_name(j, 40)


def known_journals(rows) -> list:
    """已知刊名集合（长名优先匹配，避免 'Geology' 抢走 'Economic Geology'）。"""
    names = set()
    try:
        led = POSTNEWS / "journal_ledger.json"
        if led.exists():
            d = json.loads(led.read_text(encoding="utf-8"))
            names.update((d.get("journals") or {}).keys())
    except Exception:
        pass
    for r in rows:
        j = (r.get("journal") or "").strip()
        if j and j.upper() != "OTHERS" and j != "Addition":
            names.add(j)
    return sorted((n for n in names if len(n) >= 5), key=len, reverse=True)


def journal_from_doi(doi: str) -> str:
    d = re.sub(r"\s+", "", doi or "").lower()
    for pat, name in CN_DOI_JOURNALS:
        if pat.match(d):
            return name
    m = re.match(r"^10\.1016/j\.([a-z0-9]+)[./]", d)
    return SD_JOURNALS.get(m.group(1), "") if m else ""


def journal_from_issn(text: str) -> str:
    for issn in ISSN_RE.findall(text or ""):
        j = SD_ISSN.get(issn.upper()) or CN_ISSN.get(issn.upper())
        if j:
            return j
    return ""


def journal_from_text(text: str, names) -> str:
    low = (text or "").lower()
    for n in names:
        if n.lower() in low:
            return n
    return ""


# ─── 标题 ─────────────────────────────────────────────────────────────────
def _clean_title(t: str) -> str:
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    return re.sub(r"^(ORIGINAL ARTICLE|Research Paper|Article|Research article)[:\s]+",
                  "", t, flags=re.I)


def _looks_like_title(t: str) -> bool:
    t = (t or "").strip()
    cjk = len(CJK_RE.findall(t))
    if not t or len(t) > 300:
        return False
    if cjk < 6 and len(t) < 15:
        return False
    low = t.lower()
    if low.endswith((".pdf", ".doc", ".docx", ".tmp", ".html")):
        return False
    if re.match(r"^(microsoft word|untitled|document\d*|download)\b", low):
        return False
    if re.search(r"contents lists available|science\s*direct|elsevier|all rights reserved", low):
        return False
    if re.match(r"^\d+[\s\-_]*$", t):
        return False
    if NOT_TITLE_RE.match(t.strip()):
        return False
    # 中文标题没有空格分词，不能用「≥4 个词」判断
    if len(CJK_RE.findall(t)) >= 6:
        return True
    return len(t.split()) >= 4


def guess_title(doc, head_text: str, fallback: str) -> str:
    """PDF 元数据 → 首页最大字号块 → 首页首个像标题的行 → 文件名。"""
    md = _clean_title((doc.metadata or {}).get("title") or "")
    if _looks_like_title(md):
        return md
    try:
        blocks = []
        for b in doc[0].get_text("dict").get("blocks", []):
            if b.get("type") != 0:
                continue
            for ln in b.get("lines", []):
                txt = "".join(s.get("text", "") for s in ln.get("spans", [])).strip()
                if len(txt) < 12 and len(CJK_RE.findall(txt)) < 8:
                    continue
                size = max((s.get("size", 0) for s in ln.get("spans", [])), default=0)
                blocks.append((round(size, 1), ln.get("bbox", (0, 0))[1], txt))
        if blocks:
            mx = max(b[0] for b in blocks)
            cand = sorted([b for b in blocks if b[0] >= mx - 0.8], key=lambda x: x[1])
            # 中文标题分行直接拼接（英文行间才加空格），否则会写出「相关 杂岩体」
            parts = [c[2] for c in cand[:3]]
            joined = ""
            for p in parts:
                joined = p if not joined else (joined + p if CJK_RE.match(p) else joined + " " + p)
            t = _clean_title(joined)
            if _looks_like_title(t):
                return t
    except Exception:
        pass
    for ln in (head_text or "").splitlines():
        t = _clean_title(ln)
        if _looks_like_title(t):
            return t
    return _clean_title(fallback) or "untitled"


def guess_date(head: str, doi: str, fallback: str):
    """返回 (台账 date: YYYY-MM-DD, 展示用 pub_date: YYYY | YYYY-MM)。

    年份顺序：DOI > 刊头括号年 > 首个 20xx > 当前年。
    中文刊刊头有「2026年10月」时可精化到年月（只看首页前 800 字，避开参考文献）。
    """
    y = ""
    m = DOI_YEAR_RE.search(re.sub(r"\s+", "", doi or ""))
    if m:
        y = m.group(1)
    if not y:
        m = YEAR_RE.search(head or "")
        if m:
            y = m.group(1)
    if not y:
        m = re.search(r"\b(20[0-4]\d)\b", head or "")
        y = m.group(1) if m else ""
    y = y or fallback

    # ① Elsevier 首页有「Available online 22 October 2026」→ 真实发布日期
    av = AVAIL_RE.search((head or "")[:9000])
    if av:
        mon = MONTHS.get(av.group(2).lower()[:3])
        if mon and (not y or av.group(3) == y):
            d = "%s-%02d-%02d" % (av.group(3), mon, int(av.group(1)))
            return d, d

    # ② 只有 Accepted（in-press 稿）→ 年月。仅 Elsevier：别的出版社从接收到见刊
    #    可能隔几个月（GSA Geology 收 5 月、9 月号见刊），用接收月会写错月份。
    if re.sub(r"\s+", "", doi or "").lower().startswith("10.1016"):
        ac = ACCEPT_RE.search((head or "")[:9000])
        if ac:
            mon = MONTHS.get(ac.group(2).lower()[:3])
            if mon and (not y or ac.group(3) == y):
                return ("%s-%02d-01" % (ac.group(3), mon),
                        "%s-%02d" % (ac.group(3), mon))

    # ③ 中文刊刊头「2026年10月」→ 年月
    mm = CN_YM_RE.search((head or "")[:800])
    if mm and mm.group(1) == y:
        mon = int(mm.group(2))
        if 1 <= mon <= 12:
            return "%s-%02d-01" % (y, mon), "%s-%02d" % (y, mon)
    return "%s-01-01" % y, y


def guess_year(text: str, fallback: str) -> str:
    m = YEAR_RE.search(text or "")
    if m:
        return m.group(1)
    m = re.search(r"\b(20[0-4]\d)\b", text or "")
    return m.group(1) if m else fallback


def norm_title_key(s: str) -> str:
    return re.sub(r"\W+", "", (s or "").lower())


# ─── 探测 ─────────────────────────────────────────────────────────────────
def probe(pdf: Path) -> dict:
    import pymupdf
    size = pdf.stat().st_size
    sha1 = uu.sha1_file(pdf)
    doc = pymupdf.open(str(pdf))
    head1 = ""
    try:
        pages = doc.page_count
        md = dict(doc.metadata or {})
        head1 = doc[0].get_text() or ""
        head = "\n".join((doc[i].get_text() or "") for i in range(min(3, pages)))
        title = guess_title(doc, head, pdf.stem)
    finally:
        doc.close()
    pii = ""
    m = PII_RE.search(pdf.stem) or PII_RE.search(head)
    if m:
        pii = m.group(0)
    doi = uu.guess_doi(head)
    if not doi:
        # 中文刊 PDF 常把 DOI 抽成 "10. 16111/j. 0258-7106"，压掉空白再试一次
        try:
            doi = uu.guess_doi(re.sub(r"\s+", "", head1))
        except Exception:
            doi = ""
    return {"path": pdf, "size": size, "sha1": sha1, "pages": pages,
            "meta": md, "head": head, "title": title,
            "doi": doi, "pii": pii}


# ─── 台账匹配（有 PDF 才算重复）──────────────────────────────────────────
def match_ledger(rows, pr: dict):
    """返回 (row, how)。sha1 > doi > pii > title。"""
    sha1 = pr["sha1"]
    for r in rows:
        if (r.get("sha1") or "").lower() == sha1:
            return r, "sha1"

    d = uu.norm_doi(pr["doi"])
    if d:
        for r in rows:
            if uu.norm_doi(r.get("doi") or "") == d:
                return r, "doi"
        for r in rows:
            if d in (r.get("url") or "").lower():
                return r, "doi"

    pii = pr["pii"]
    if len(pii) >= 15:
        for r in rows:
            if pii.lower() in (r.get("url") or "").lower():
                return r, "pii"

    t = norm_title_key(pr["title"])
    if t and len(t) >= 25 and not _looks_like_title(pr["path"].stem):
        for r in rows:
            if norm_title_key(r.get("title") or "") == t:
                return r, "title"
    return None, ""


def has_real_pdf(rec) -> bool:
    if not uu.has_pdf(rec):
        return False
    try:
        return Path(str(rec.get("repo_path") or "")).exists()
    except OSError:
        return False


# ─── 落盘 ─────────────────────────────────────────────────────────────────
def target_path(journal_dir: str, title: str, date: str) -> Path:
    """与 pdf_downloader._save_pdf 完全一致的目录/命名约定。"""
    year = (date or "")[:4] or datetime.now().strftime("%Y")
    jdir = Path(geokb_paths.by_journal()) / pdl.safe_name(journal_dir, 40) / year
    return jdir / ("%s_%s%s.pdf" % (date, pdl.safe_name(title, 60), SUFFIX))


def move_to_nas(src: Path, dest: Path, sha1: str, size: int):
    """写 NAS 并校验。返回 (dest, reused, error)。同名不同内容自动加序号。"""
    data = src.read_bytes()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        try:
            if uu.sha1_file(dest) == sha1:
                return dest, True, None
        except OSError:
            pass
        n = 2
        while True:
            cand = dest.with_name("%s_%d.pdf" % (dest.stem, n))
            if not cand.exists():
                dest = cand
                break
            try:
                if uu.sha1_file(cand) == sha1:
                    return cand, True, None
            except OSError:
                pass
            n += 1
    try:
        with open(dest, "wb") as f:
            f.write(data)
        if dest.stat().st_size != size:
            raise IOError("写入后大小不一致")
        if uu.sha1_file(dest) != sha1:
            raise IOError("写入后 SHA-1 不一致")
    except Exception as e:
        try:
            dest.unlink()
        except OSError:
            pass
        return None, False, "%s: %s" % (type(e).__name__, e)
    return dest, False, None


# ─── 入库 ─────────────────────────────────────────────────────────────────
def _run_py(args, timeout):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    try:
        r = subprocess.run([sys.executable] + args, cwd=str(BASE), capture_output=True,
                           text=True, encoding="utf-8", errors="ignore",
                           timeout=timeout, env=env, creationflags=_NO_WIN)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "timeout after %ss" % timeout


def ingest(nas: Path, sha1: str, meta: dict, no_llm=False):
    """摘要 → 条目 → 知识点 → 索引。返回 (state, notes)，state ∈ done/partial/failed。"""
    import pdf_corpus_ingest as pci

    notes, state = [], "done"
    try:
        text = pci.extract_text(str(nas), sha1)
    except Exception as e:
        return "failed", ["PDF 文本抽取失败: %s: %s" % (type(e).__name__, e)]
    scanned = len(text) < pci.MIN_TEXT_CHARS

    if no_llm or scanned:
        why = ("扫描版 PDF（无文本层），按题录入库" if scanned
               else "离线模式（--no-llm），正则兜底抽取")
        try:
            data = pci.fallback_extract(text, meta.get("title") or "")
            # 不传 user_meta：否则正文会写「user upload / Addition」，对期刊论文是错的
            pci.write_entry(sha1, meta, data, "fallback")
        except Exception as e:
            return "failed", ["条目生成失败: %s: %s" % (type(e).__name__, e)]
        notes.append(why)
        if scanned:
            state = "partial"
    else:
        if not uu.wait_lock(LOCK_WAIT_MAX, lambda s, m: log("  " + m)):
            return "failed", ["入库锁被占用超过 %d 分钟，PDF 与台账已保留，可重跑"
                              % (LOCK_WAIT_MAX // 60)]
        rc, _out = _run_py([str(SCRIPTS / "pdf_corpus_ingest.py"), "--only", str(nas)], 900)
        st = uu.pdf_state_status(sha1)
        if st in ("done", "fallback"):
            notes.append("摘要提取成功（DeepSeek）" if st == "done" else "摘要走了正则兜底")
            if st == "fallback":
                state = "partial"
        else:
            why = {"needs_ocr": "扫描版无文本层",
                   "failed": "DeepSeek 提取失败"}.get(st, "提取未完成 rc=%s" % rc)
            try:
                data = pci.fallback_extract(text, meta.get("title") or "")
                pci.write_entry(sha1, meta, data, "fallback")
                notes.append("%s，已按题录入库（可重跑覆盖为 LLM 版）" % why)
            except Exception as e:
                return "failed", ["%s，且兜底失败: %s: %s" % (why, type(e).__name__, e)]
            state = "partial"

    if no_llm or scanned:
        notes.append("未拆知识点（%s）" % ("离线模式" if no_llm else "扫描版"))
    else:
        kp_before = len(list(KP_DIR.glob("kp-%s-*.md" % sha1[:12])))
        _rc, _out = _run_py([str(SCRIPTS / "kp_extract.py"), "--sha", sha1], 420)
        kp_after = len(list(KP_DIR.glob("kp-%s-*.md" % sha1[:12])))
        if kp_after > kp_before:
            notes.append("拆分知识点 %d 条" % kp_after)
        elif kp_after:
            notes.append("知识点已存在 %d 条" % kp_after)
        elif "KP_RESULT" in _out:
            # 判定 0 条是合法结论（内容不可复用），不是失败
            notes.append("判定 0 条知识点（内容对后续研究无可复用价值）")
        else:
            notes.append("知识点未产出（JSON 解析失败或文本不足），不影响论文条目")
            if state == "done":
                state = "partial"

    eid = "paper-%s" % sha1[:12]
    eids = [eid] + [p.stem for p in sorted(KP_DIR.glob("kp-%s-*.md" % sha1[:12]))]
    ok = uu.run_index(eids)
    if ok < len(eids):
        notes.append("索引部分失败（%d/%d），可跑 rebuild_index.py 兜底" % (ok, len(eids)))
        if state == "done":
            state = "partial"
    return state, notes


# ─── 单篇 ─────────────────────────────────────────────────────────────────
def process_one(pdf: Path, rows, dmap, names, dry=False, no_llm=False) -> dict:
    out = {"file": pdf.name, "state": "", "notes": [], "title": "", "journal": "",
           "journal_dir": "", "repo_path": "", "sha1": "", "doi": "", "date": ""}
    try:
        if pdf.stat().st_size > MAX_PDF_BYTES:
            return {**out, "state": "failed",
                    "notes": ["超过 %dMB 上限" % (MAX_PDF_BYTES // 1048576)]}
        with open(pdf, "rb") as f:
            if f.read(5) != b"%PDF-":
                return {**out, "state": "failed",
                        "notes": ["文件头不是 %PDF-（多半另存成了网页而非 PDF）"]}
        pr = probe(pdf)
    except Exception as e:
        return {**out, "state": "failed",
                "notes": ["PDF 无法打开: %s: %s" % (type(e).__name__, e)]}

    out.update(sha1=pr["sha1"], doi=pr["doi"])
    old, how = match_ledger(rows, pr)

    # 期刊：台账 > DOI 前缀 > ISSN > 刊名文本 > OTHERS
    journal, jhow = "", ""
    if old and (old.get("journal") or "").strip():
        journal, jhow = old["journal"].strip(), "台账"
    if not journal:
        journal = journal_from_doi(pr["doi"])
        jhow = "DOI前缀" if journal else ""
    if not journal:
        journal = journal_from_issn(pr["head"])
        jhow = "ISSN" if journal else ""
    if not journal:
        journal = journal_from_text(pr["head"], names)
        jhow = "刊名匹配" if journal else ""
    if not journal:
        journal, jhow = "OTHERS", "未识别"

    # 标题：台账已有则沿用 —— 仅为了 journals 页按列表标题能匹配上，
    # 不代表「升级题录」（本脚本不自造题录，也不写 upgraded_from）
    title = (old.get("title") or "").strip() if old else ""
    if not title:
        title = pr["title"]
        # 首页最大字号块常把页眉刊名一起并进来（"Ore Geology Reviews In-situ …"）
        jn = journal.strip()
        if jn and jn.upper() != "OTHERS" and title.lower().startswith(jn.lower()):
            stripped = title[len(jn):].lstrip(" :–—-·")
            if len(stripped) > 15:
                title = stripped

    date = (old.get("date") or "").strip() if old else ""
    # 老行只有「年」精度（YYYY-01-01，题录阶段留下的）时，仍以 PDF 首页的实际
    # 在线日期为准 —— 否则拿着 PDF 反而停在「2026-01」这种假月份上。
    if re.match(r"^\d{4}-\d{2}-\d{2}$", date) and not date.endswith("-01-01"):
        pub_date = date[:7] if date.endswith("-01") else date
    else:
        nd, npd = guess_date(pr["head"], pr["doi"], datetime.now().strftime("%Y"))
        if nd and (not date or nd[:4] == date[:4]):
            date, pub_date = nd, npd
        else:
            pub_date = date[:7] if date.endswith("-01") else date

    jdir = pick_dir(journal, dmap)
    dest = target_path(jdir, title, date)
    out.update(title=title, journal=journal, journal_dir=jdir, date=date,
               pub_date=pub_date,
               repo_path=dest.as_posix(), journal_how=jhow, matched=how or "-")

    if old and has_real_pdf(old):
        eid = old.get("eid") or ""
        entry = PAPERS_DIR / ("%s.md" % eid) if eid else None
        done = bool(entry and entry.exists())
        out.update(state="duplicate", notes=[
            "库内已有同一文献（%s 命中%s）" % (how, "，条目已生成" if done else "，尚未解析")])
        return out

    if dry:
        out.update(state="dry", notes=["预演 → %s" % dest.as_posix()])
        return out

    moved, reused, err = move_to_nas(pdf, dest, pr["sha1"], pr["size"])
    if err:
        out.update(state="failed", notes=["NAS 保存失败: %s" % err])
        return out
    out["repo_path"] = moved.as_posix()

    # 台账：先写后入库（pdf_corpus_ingest 靠 repo_path 回查 meta）
    url = (old.get("url") if old else "") or ""
    if not url:
        if pr["doi"]:
            url = "https://doi.org/%s" % uu.norm_doi(pr["doi"])
        elif pr["pii"]:
            url = "https://www.sciencedirect.com/science/article/pii/%s" % pr["pii"]

    rec = {
        "title": title, "url": url,
        "doi": uu.norm_doi(pr["doi"]) or ((old or {}).get("doi") or ""),
        "journal": journal, "date": date, "pdf_url": "",
        "repo_path": moved.as_posix(), "size": pr["size"],
        "source": "manual_inbox", "sha1": pr["sha1"],
        "eid": "paper-%s" % pr["sha1"][:12],
        "indexed_at": datetime.now().isoformat(timespec="seconds"),
        "uploaded_by": "folder", "file_name": pdf.name,
    }
    ok, dup = uu.append_ledger(rec)
    if not ok:
        if (dup or {}).get("dimension") == "write_verify_failed":
            out.update(state="failed", notes=[
                "台账登记后校验失败（疑似外部重写竞态），PDF 已保留，可重跑"])
            return out
        try:
            moved.unlink()
        except OSError:
            pass
        out.update(state="duplicate", notes=[
            "库内已登记同一文献（%s）" % (dup or {}).get("note", "sha1")])
        return out

    st, notes = ingest(moved, pr["sha1"], dict(rec, pub_date=pub_date), no_llm=no_llm)
    if reused:
        notes.append("NAS 上已有同内容文件，复用未重写")
    notes.append("已移到 NAS，投放目录已清空")
    out.update(state=st, notes=notes)
    return out


# ─── 运行 ─────────────────────────────────────────────────────────────────
def _subdirs(root: Path):
    d, dup, fail = root / "_done", root / "_duplicate", root / "_failed"
    for p in (d, dup, fail):
        p.mkdir(parents=True, exist_ok=True)
    return d, dup, fail


def _move_aside(pdf: Path, dst_dir: Path) -> Path:
    """重复/失败文件移到旁挂目录。**不删用户文件**。"""
    dst = dst_dir / pdf.name
    if dst.exists():
        dst = dst_dir / ("%s_%s%s" % (pdf.stem, datetime.now().strftime("%H%M%S"), pdf.suffix))
    try:
        shutil.move(str(pdf), str(dst))
        return dst
    except Exception as e:
        log("  无法移动 %s: %s" % (pdf.name, e))
        return pdf


def collect(folder: Path, also_legacy=True):
    """收集待处理 PDF：主目录 + 旧 state/inbox 残留。"""
    roots = [folder]
    if also_legacy and LEGACY_INBOX.is_dir() and LEGACY_INBOX.resolve() != folder.resolve():
        roots.append(LEGACY_INBOX)
    files = []
    for root in roots:
        if not root.is_dir():
            continue
        files += sorted(p for p in root.iterdir()
                        if p.is_file() and p.suffix.lower() == ".pdf"
                        and not p.name.startswith("~$"))
    return files


def run_once(folder: Path, dry=False, limit=0, no_llm=False, also_legacy=True) -> int:
    t0 = time.time()
    log("扫描 %s%s" % (folder, "（dry-run，不写盘）" if dry else ""))
    if not folder.is_dir() and not dry:
        folder.mkdir(parents=True, exist_ok=True)
        log("目录不存在，已创建：%s" % folder)

    done_dir, dup_dir, fail_dir = _subdirs(folder) if not dry else (folder,) * 3
    files = collect(folder, also_legacy)
    if not files:
        log("投放目录为空（共 0 个 PDF）")
        _write_status({"ts": datetime.now().isoformat(timespec="seconds"),
                       "inbox": str(folder), "scanned": 0, "items": [],
                       "counts": {"ok": 0, "partial": 0, "duplicate": 0,
                                  "failed": 0, "skipped": 0},
                       "note": "无待处理文件", "elapsed_s": round(time.time() - t0, 1)})
        return 0

    now = time.time()
    ready, too_new = [], []
    for p in files:
        try:
            age = now - p.stat().st_mtime
        except OSError:
            continue
        (ready if age >= MIN_AGE_SEC else too_new).append(p)
    if too_new:
        log("跳过 %d 个刚写入的文件（< %ds）：%s" % (
            len(too_new), MIN_AGE_SEC, ", ".join(p.name[:40] for p in too_new[:3])))

    rows = uu.load_ledger()
    dmap = journal_dir_map(rows)
    names = known_journals(rows)
    log("台账 %d 条｜期刊目录映射 %d 个｜已知刊名 %d 个｜待处理 %d 篇"
        % (len(rows), len(dmap), len(names), len(ready)))

    items, counts = [], Counter()
    n = 0
    for pdf in ready:
        if limit and n >= limit:
            log("已达 --limit %d，剩余留待下次" % limit)
            break
        n += 1
        log("[%d/%d] %s" % (n, len(ready), pdf.name))
        res = process_one(pdf, rows, dmap, names, dry=dry, no_llm=no_llm)
        counts[res["state"]] += 1
        log("    → %s｜%s（%s）｜%s｜%s" % (
            res["state"], res.get("journal") or "-", res.get("journal_how") or "-",
            res.get("journal_dir") or "-", "；".join(res.get("notes") or [])[:220]))
        items.append(res)

        if not dry:
            if res["state"] in ("done", "partial"):
                try:
                    pdf.unlink()          # 移动语义：NAS 校验通过后才删
                except OSError as e:
                    log("    ⚠ 本地文件删除失败（已入库，可手动清）：%s" % e)
            elif res["state"] == "duplicate":
                _move_aside(pdf, dup_dir)
            else:
                _move_aside(pdf, fail_dir)

        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                                    **res}, ensure_ascii=False) + "\n")
        except OSError:
            pass

        # 逐篇刷新状态（2026-10-25）：journals 页「补缺申请」按钮要显示
        # 「补缺中，x/y 篇」，只能靠这里每处理完一篇就落一次盘。
        if not dry:
            _write_status({"ts": datetime.now().isoformat(timespec="seconds"),
                           "inbox": str(folder), "scanned": len(files),
                           "running": True,
                           "progress": {"done": n, "total": len(ready)},
                           "counts": {"ok": counts["done"], "partial": counts["partial"],
                                      "duplicate": counts["duplicate"],
                                      "failed": counts["failed"], "skipped": len(too_new)},
                           "items": items, "elapsed_s": round(time.time() - t0, 1),
                           "dry_run": dry})

    log("完成：成功 %d（部分降级 %d）｜重复 %d｜失败 %d｜跳过 %d｜用时 %.0fs" % (
        counts["done"] + counts["partial"], counts["partial"], counts["duplicate"],
        counts["failed"], len(too_new), time.time() - t0))
    _write_status({"ts": datetime.now().isoformat(timespec="seconds"),
                   "inbox": str(folder), "scanned": len(files),
                   "running": False,
                   "progress": {"done": counts["done"] + counts["partial"]
                                + counts["duplicate"] + counts["failed"],
                                "total": len(ready)},
                   "counts": {"ok": counts["done"], "partial": counts["partial"],
                              "duplicate": counts["duplicate"],
                              "failed": counts["failed"], "skipped": len(too_new)},
                   "items": items, "elapsed_s": round(time.time() - t0, 1),
                   "dry_run": dry})

    # 台账标题回填（2026-10-25）：guess_title 只按「最大字号」取标题，中文刊的
    # 小字号副标题（「——以…为例」）与超短第二行（「与多期成矿研究」）会丢，
    # journals 页拿源站完整题录去台账里找不到 → 下载按钮不出现。
    # 条目 title 是 DeepSeek 通读全文得到的完整标题，这里做一次前缀扩展对齐。
    if not dry and (counts["done"] + counts["partial"]) > 0:
        try:
            _rc, _out = _run_py([str(SCRIPTS / "sync_inbox_titles.py"), "--apply"], 300)
            tail = [l for l in (_out or "").splitlines()
                    if l.startswith("已写台账") or l.startswith("台账行")]
            if tail:
                log("标题回填：%s" % tail[-1][:140])
        except Exception as e:
            log("标题回填跳过（不影响入库）：%s: %s" % (type(e).__name__, e))

    # 分面重建（2026-10-26）：业务管线（pdf_corpus_ingest）自己不建分面，
    # 以前每批都得手工补跑 build_facet_index，否则新条目在站点的分面筛选里
    # 根本不出现（实测 10-25 那批 15 篇一直没有）。整批跑一次，约 10–20s。
    if not dry and (counts["done"] + counts["partial"]) > 0:
        try:
            _rc, _out = _run_py([str(SCRIPTS / "build_facet_index.py")], 900)
            tail = [l for l in (_out or "").splitlines() if l.startswith("条目")]
            if tail:
                log("分面：%s" % tail[-1][:140])
        except Exception as e:
            log("分面重建跳过（不影响入库）：%s: %s" % (type(e).__name__, e))

    # 元数据红线（2026-10-26）：与 Agent 入库共用 scripts/meta_guard.py。
    # 标题用了译名/哈希/文件名、出版日期缺失或格式非法 → 这里汇总报出来。
    if not dry:
        eids = [it.get("eid") for it in items if it.get("eid")]
        if eids:
            try:
                _rc, _out = _run_py([str(SCRIPTS / "meta_guard.py"), "--eids"] + eids, 300)
                head = [l for l in (_out or "").splitlines() if l.startswith("检查 ")]
                bad = [l for l in (_out or "").splitlines() if l.startswith("✘")]
                if head:
                    log("元数据红线：%s" % head[0][:110])
                for b in bad[:6]:
                    log("   %s" % b[:120])
            except Exception as e:
                log("元数据检查跳过：%s: %s" % (type(e).__name__, e))

    return 1 if counts["failed"] else 0


def _write_status(doc: dict):
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        doc = dict(doc)
        # pid 让门户能「零子进程」判断是否还在跑（否则每 2s 要枚举一次进程，
        # 服务无控制台时每次都会弹一个黑窗）。
        doc.setdefault("pid", os.getpid())
        tmp = STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATUS_FILE)
    except Exception:
        pass


def show_status(folder: Path):
    if STATUS_FILE.exists():
        try:
            doc = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
            print(json.dumps(doc, ensure_ascii=False, indent=2)[:4000])
        except Exception as e:
            print("状态文件损坏: %s" % e)
    else:
        print("尚无运行记录（%s 不存在）" % STATUS_FILE)
    pend = collect(folder) if folder.exists() else []
    print("\n投放目录：%s\n待处理 PDF：%d" % (folder, len(pend)))
    for p in pend[:20]:
        print("  -", p.name)


def show_todo(limit: int = 30):
    """打印「自动没拿到全文」的待补清单。

    ⚠️ 数据源已上收到 `scripts/inbox_todo.py`（三源合并：今日抓取失败 +
    历史队列 parked + 早期 metadata_only），与 5555 `/geokb/inbox` 页面
    **同一份快照** —— 这里若自己再读一遍 journal_no_pdf.jsonl，
    命令行和网页就会各说一个数。
    """
    import inbox_todo as todo        # 同目录，sys.path 已在文件头插入

    snap = todo.snapshot()
    c = snap["counts"]
    print("待下载 %d ｜ 已下载待入库 %d ｜ 已入库 %d ｜ 投放箱文件 %d\n"
          % (c["pending"], c["downloaded"], c["history"], c["inbox_files"]))
    src = c.get("by_source") or {}
    if src:
        print("来源：" + "  ".join("%s %d" % (todo.SRC_LABEL.get(k, k), v)
                                   for k, v in sorted(src.items(), key=lambda x: x[1])))
        print("")

    if not snap["pending"]:
        print("没有待下载的论文 —— 自动流程都拿到了全文。")
        return 0

    print("%-13s %-30s %s" % ("来源", "期刊", "标题 / 下载页"))
    print("-" * 108)
    for it in snap["pending"][:limit]:
        print("%-13s %-30s %s" % (it["source_label"], (it["journal"] or "-")[:30],
                                  (it["title"] or "")[:74]))
        if it["page_url"]:
            print("%-13s %-30s %s" % ("", "", it["page_url"]))
        if it["reason_label"] or it["reason"]:
            print("%-13s %-30s ↳ %s" % ("", "", (it["reason_label"] or it["reason"])[:70]))
    if len(snap["pending"]) > limit:
        print("\n… 还有 %d 篇，用 --todo %d 看更多；"
              "网页版：https://kust.work/geokb/inbox"
              % (len(snap["pending"]) - limit, len(snap["pending"])))

    if snap["downloaded"]:
        print("\n── 已下载待入库（12:01 / 18:01 自动入库）──")
        for it in snap["downloaded"][:limit]:
            print("  · %s %s" % ((it["journal"] or "-")[:28], (it["title"] or "")[:66]))
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="统一投放箱：手动下载的论文 PDF → 期刊目录 → 台账 → 入库")
    ap.add_argument("--run", action="store_true", help="执行一次扫描入库（计划任务用）")
    ap.add_argument("--dry-run", action="store_true", help="只识别与预演，不写盘")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 篇")
    ap.add_argument("--no-llm", action="store_true", help="离线：不调 DeepSeek")
    ap.add_argument("--folder", default="", help="临时覆盖投放目录")
    ap.add_argument("--no-legacy", action="store_true", help="不扫旧的 state/inbox")
    ap.add_argument("--status", action="store_true", help="查看上次运行报告")
    ap.add_argument("--todo", type=int, nargs="?", const=30, default=-1,
                    help="打印待补清单（今天没下到 PDF 的论文），默认 30 条")
    args = ap.parse_args()

    folder = inbox_dir(args.folder)
    if args.todo >= 0:
        return show_todo(args.todo)
    if args.status:
        show_status(folder)
        return 0
    if not args.run and not args.dry_run:
        ap.print_help()
        return 2
    return run_once(folder, dry=args.dry_run, limit=args.limit,
                    no_llm=args.no_llm, also_legacy=not args.no_legacy)


if __name__ == "__main__":
    sys.exit(main())
