"""元数据红线 —— 两套入库（DeepSeek 管线 / Agent 亲自入库）**共用的唯一判据**（2026-10-26）

背景：2026-10-26 做了两次全库审计（出版日期、标题），查出的问题都是**入库时元数据没守住**，
而不是检索/展示的问题。两条入库链路代码不同，但都得过同一道闸，所以规则只写在这里：

    errors  → 硬红线，拦截（commit 阶段拒绝，管线记 ERROR）
    warns   → 软提示，只记录（可能合法：如标题与 PDF 排版差异）

## 标题（以原文为准，不参考翻译版）
E1 空 / 过短(<8)          E2 以引号开头（`title: '"Xxx'`）
E3 哈希占位 `331170fb1909`  E4 slug 占位 `Huang_Weijia_W_Skarn`
E5 文件名 `2024-01-01_降珂楠等…_hist`   E6 乱码（`�` / `Ã©` / 私有区 `\ue000-\uf8ff`）
W1 标题不在 PDF 前两页正文里（归一化、去行号后比对）
W2 标题文字种类与原文标题块相反（英文刊写了中文译名 / 中文刊写了英文）
W3 含换行/制表符

## 出版日期（= 在线首发日；年+月为默认，有日补日）
E7 缺失或格式非法（只允许 YYYY / YYYY-MM / YYYY-MM-DD）
W4 PDF 里的 `Available online`/`PUBLISHED` 与库内不符
W5 库内比 PDF 更粗（可以补精度）

## 分面
W6 无 facets（业务管线曾经漏建，导致站点筛选里不出现）

用法：
    python scripts/meta_guard.py --eids paper-xxx paper-yyy
    python scripts/meta_guard.py --recent-hours 6
    python scripts/meta_guard.py --all --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))

CONTENT = BASE / "content" / "papers"
LEDGER = BASE / "state" / "papers_index.jsonl"

CJK = re.compile(r"[\u3400-\u9fff]")
HASH = re.compile(r"^[0-9a-f]{8,}$")
SLUG = re.compile(r"^[A-Za-z0-9_\-]+$")
FNAME = re.compile(r"^(?:\d{4}-\d{2}-\d{2}_|\d{4}_[A-Za-z]+_)")
MOJI = re.compile(r"\ufffd|Ã[\u0080-\u00bf]|Â[\u0080-\u00bf]|â€|ï¿½|[\ue000-\uf8ff]")
DATE_OK = re.compile(r"^\d{4}(?:-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12]\d|3[01]))?)?$")


def _norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"[\W_]+", "", s, flags=re.U)


def _clean_body(s: str) -> str:
    s = re.sub(r"\n\s*\d{1,4}\s*\n", "\n", s or "")      # 行号
    return re.sub(r"\s{2,}\d{1,4}\s{2,}", " ", s)


def _pdf_text(p: Path):
    """(前两页正文, 首页最大字号块)；打不开返回 (None, '')。"""
    try:
        import pymupdf
        doc = pymupdf.open(str(p))
    except Exception:
        return None, ""
    try:
        body = "\n".join((doc[i].get_text() or "") for i in range(min(2, doc.page_count)))
        blk = ""
        if doc.page_count:
            lines = []
            for b in doc[0].get_text("dict").get("blocks", []):
                if b.get("type") != 0:
                    continue
                for line in b.get("lines", []):
                    t = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                    if t:
                        sz = max((s.get("size", 0) for s in line.get("spans", [])), default=0)
                        lines.append((sz, line.get("bbox", (0, 0, 0, 0))[1], t))
            if lines:
                mx = max(l[0] for l in lines)
                blk = "".join(x[2] for x in sorted(
                    [l for l in lines if l[0] >= mx - 0.8], key=lambda z: z[1])[:3])
        return body, blk
    finally:
        doc.close()


def _fm(md: Path):
    import yaml
    try:
        raw = md.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}
    m = re.match(r"^---\r?\n(.*?)\r?\n---", raw, re.S)
    if not m:
        return {}
    try:
        return yaml.safe_load(m.group(1)) or {}
    except Exception:
        return {"__yaml_error__": True}


def check_title(title: str, pdf: Path | None) -> tuple[list, list]:
    err, warn = [], []
    t = (title or "").strip()
    if not t:
        return ["E1 标题为空"], warn
    if len(t) < 8:
        err.append("E1 标题过短：%r" % t)
    if t[0] in "\"'":
        err.append("E2 标题以引号开头：%r" % t[:30])
    if HASH.match(t):
        err.append("E3 哈希占位：%s" % t)
    if " " not in t and SLUG.match(t):
        err.append("E4 slug 占位：%s" % t)
    if FNAME.match(t):
        err.append("E5 文件名当标题：%s" % t[:40])
    if MOJI.search(t):
        err.append("E6 标题含乱码：%r" % t[:40])
    if "\n" in t or "\t" in t:
        warn.append("W3 标题含换行/制表符")

    if pdf and pdf.is_file():
        body, blk = _pdf_text(pdf)
        if body is None or not body.strip():
            warn.append("W1 跳过：PDF 无文本层/打不开")
        elif _norm(t) not in _norm(_clean_body(body)):
            warn.append("W1 标题未在原文正文命中（可能是译名或排版差异）")
        if blk and len(blk) >= 10:
            if bool(CJK.search(t)) != bool(CJK.search(blk)):
                warn.append("W2 标题文字种类与原文相反（疑似译文）：原文块 %r" % blk[:40])
    return err, warn


def check_date(pub_date: str, pdf: Path | None) -> tuple[list, list]:
    err, warn = [], []
    d = (pub_date or "").strip()
    if not d:
        return ["E7 出版日期为空"], warn
    if not DATE_OK.match(d):
        err.append("E7 出版日期格式非法：%r" % d)
        return err, warn
    if pdf and pdf.is_file():
        try:
            from audit_titles import pdf_texts
            body, _ = pdf_texts(pdf)
        except Exception:
            body = ""
        if body and not body.startswith("\x00"):
            try:
                from audit_pub_dates import best_date
                best, how = best_date(body, "")
            except Exception:
                best, how = "", ""
            if best:
                if len(best) > len(d) and best[:len(d)] == d:
                    warn.append("W5 可补精度：库内 %s，原文 %s（%s）" % (d, best, how))
                elif best[:4] != d[:4]:
                    warn.append("W4 年份不符：库内 %s，原文 %s（%s）" % (d, best, how))
    return err, warn


def check_entry(eid: str, ledger: dict) -> dict:
    md = CONTENT / ("%s.md" % eid)
    rel = str((ledger.get(eid) or {}).get("repo_path") or "").replace("\\", "/")
    i = rel.find("papers_repo/")
    pdf = None
    if i >= 0:
        from audit_pub_dates import NAS
        pdf = NAS / rel[i + len("papers_repo/"):]
    fm = _fm(md)
    out = {"eid": eid, "errors": [], "warns": []}
    if not fm:
        out["errors"].append("E0 条目缺失或 frontmatter 无法解析")
        return out
    if fm.get("__yaml_error__"):
        out["errors"].append("E0 frontmatter YAML 解析失败")
        return out
    e, w = check_title(str(fm.get("title") or ""), pdf)
    out["errors"] += e
    out["warns"] += w
    e, w = check_date(str(fm.get("pub_date") or ""), pdf)
    out["errors"] += e
    out["warns"] += w
    if not fm.get("facets"):
        out["warns"].append("W6 无 facets")
    out["title"] = str(fm.get("title") or "")[:80]
    out["pub_date"] = str(fm.get("pub_date") or "")
    return out


def load_ledger() -> dict:
    led = {}
    if LEDGER.exists():
        for ln in LEDGER.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = ln.strip()
            if not s:
                continue
            try:
                r = json.loads(s)
            except Exception:
                continue
            led[r.get("eid")] = r
    return led


def check_eid(eid: str) -> dict:
    """单篇检查（自己读台账，供两套入库链路直接调用）。"""
    return check_entry(eid, load_ledger())


def check_many(eids) -> list:
    led = load_ledger()
    out = []
    for eid in eids:
        r = check_entry(eid, led)
        if r["errors"] or r["warns"]:
            out.append(r)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eids", nargs="*", default=[])
    ap.add_argument("--recent-hours", type=float, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.eids:
        eids = a.eids
    else:
        files = sorted(CONTENT.glob("paper-*.md"))
        if a.recent_hours:
            cut = time.time() - a.recent_hours * 3600
            files = [p for p in files if p.stat().st_mtime >= cut]
        eids = [p.stem for p in files]

    res = check_many(eids)
    hard = [r for r in res if r["errors"]]
    if a.json:
        print(json.dumps({"checked": len(eids), "hard": len(hard), "items": res},
                         ensure_ascii=False, indent=2))
    else:
        print("检查 %d 条｜红线 %d｜仅提示 %d" % (len(eids), len(hard), len(res) - len(hard)))
        for r in res[:40]:
            flag = "✘" if r["errors"] else "·"
            print("%s %s  %s" % (flag, r["eid"], r.get("title", "")[:52]))
            for m in r["errors"]:
                print("     [红线] " + m)
            for m in r["warns"][:3]:
                print("     [提示] " + m)
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
