# -*- coding: utf-8 -*-
"""判定 PDF 版本：正式版 / 录用稿 / 校样 / 预印本。

背景（2026-10-27 用户提问）
--------------------------
American Mineralogist 等 GSW 平台论文的 PDF 首页带三段声明：
    This is the peer-reviewed, final accepted version for American
    Mineralogist, published by the Mineralogical Society of America.
    The published version is subject to change. Cite as Authors (Year)
    Title. American Mineralogist, in press.
    DOI: https://doi.org/10.2138/am-2026-10198.
且全篇带行号。这是**录用稿（accepted manuscript / "Just Accepted"）**：

  · 同行评审已通过、作者定稿，但**出版社未排版**
  · 行号是审稿/校对版标记（方便指着「第 123 行」提意见），不是正式排版
  · 无卷期页码（in press），"published version is subject to change"
    → 与正式版内容可能不同，**不能当作定稿引用**
  · 一般只对订阅者/作者开放，**不属 OA**

入库危害：把录用稿当正式文献收编，后续引用页码、版本一致性、
统计口径全都会偏。

⚠️ 误杀风险（2026-10-27 实测，不是理论）
---------------------------------------
台账 70 篇 PDF 里唯一命中 "in press" 的那篇，实际是**正式排版版**：
    American Mineralogist, Volume 111, pages 685–697, 2026
它的 "in press" 出现在**第 2 页参考文献**里（引用别人未刊文献，极常见）。
因此本模块有两条硬约束：
  ① **只扫第 1 页** —— 正式版的卷期页码永远在首页页眉
  ② **只用强判据**（整句声明），不裸搜 `in press`
放开任一条，参考文献里的 "in press" 就会成批误伤。

用法
----
    from pdf_version_guard import check_path, check_bytes
    r = check_path(pdf_path)      # {'version': 'accepted', 'evidence': [...], ...}
    r = check_bytes(pdf_bytes)

version 取值：
    published  第 1 页有卷期/页码 → 正式版（默认可入库）
    accepted   命中录用稿声明     → 不入库
    in_press   无卷期但有 in press/just accepted 弱信号 → 提示人工确认
    other      判不出来（多数 OA 仓储副本属此类，照常入库）

CLI：
    python scripts/pdf_version_guard.py <pdf> [<pdf> ...] [--json]
    python scripts/pdf_version_guard.py --scan-pending   # 扫待入库池
"""
import json
import os
import re
import sys
import tempfile

# 强判据：这些**整句**只出现在录用稿/校样上，不会出现在正式版正文里
STRONG = (
    'final accepted version',
    'final accepted manuscript',
    'this is the peer-reviewed',
    'author accepted manuscript',
    'accepted manuscript',
    'uncorrected proof',
    'just accepted',
    'do not cite or quote',
    'not for distribution',
)

# 弱信号：单独出现不足以定案（参考文献里太常见），只在「无卷期」时提示
WEAK_INPRESS = ('in press', 'advance article', 'ahead of print', 'online first')

RE_VOLUME = re.compile(r'\b(?:volume|vol\.?)\s+\d+\b', re.I)
RE_PAGES = re.compile(r'\bpages?\s+\d+\s*[\u2013\u2014\-]\s*\d+\b', re.I)


def _flat(s):
    return " ".join((s or "").lower().split())


def line_numbered(txt):
    """行号排版特征：首页前 80 个非空行里 ≥8 行以「数字 + 空白」开头。

    只作辅助证据（投稿/校样版的典型排版），不单独定案 ——
    表格、目录页也可能有类似形态。
    """
    lines = [ln.strip() for ln in (txt or "").splitlines() if ln.strip()]
    if len(lines) < 20:
        return False
    hit = sum(1 for ln in lines[:80] if re.match(r'^\d{1,3}[\s\t]', ln))
    return hit >= 8


def classify_text(txt):
    """按第 1 页文本判版本。返回 dict。"""
    low = _flat(txt)
    numbered = line_numbered(txt)
    if not low:
        return {"version": "other", "evidence": [], "numbered": numbered,
                "note": "未取到文本（扫描件/加密）"}

    strong = [kw for kw in STRONG if kw in low]
    if strong:
        return {"version": "accepted", "evidence": strong, "numbered": numbered,
                "note": "录用稿/校样声明"}

    if RE_PAGES.search(low) or RE_VOLUME.search(low):
        ev = []
        if RE_VOLUME.search(low):
            ev.append("volume")
        if RE_PAGES.search(low):
            ev.append("pages")
        return {"version": "published", "evidence": ev, "numbered": numbered,
                "note": "首页有卷期/页码"}

    weak = [kw for kw in WEAK_INPRESS if kw in low]
    if weak:
        return {"version": "in_press", "evidence": weak, "numbered": numbered,
                "note": "无卷期 + in press 弱信号，需人工确认"}
    return {"version": "other", "evidence": [], "numbered": numbered,
            "note": "无明确版本特征"}


def _head_text(path, pages=1):
    try:
        import fitz  # pymupdf
    except Exception:
        try:
            import pymupdf as fitz
        except Exception:
            return ""
    try:
        doc = fitz.open(str(path))
        try:
            n = min(pages, doc.page_count)
            return "\n".join((doc[i].get_text() or "") for i in range(n))
        finally:
            doc.close()
    except Exception:
        return ""


def check_path(path, pages=1):
    """判一个 PDF 文件。异常一律返回 other（不能因为检查器坏了拦住入库）。"""
    try:
        return classify_text(_head_text(path, pages))
    except Exception as e:  # noqa: BLE001
        return {"version": "other", "evidence": [], "numbered": False,
                "note": "检查跳过: %s" % str(e)[:80]}


def check_bytes(data, pages=1):
    """判 PDF 字节流（入库前拦截用）。"""
    if not data or data[:4] != b'%PDF':
        return {"version": "other", "evidence": [], "numbered": False,
                "note": "非 PDF"}
    fd, tmp = tempfile.mkstemp(suffix='.pdf')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        return check_path(tmp, pages=pages)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def is_accepted(r):
    return (r or {}).get("version") == "accepted"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv
    if not args:
        print(__doc__)
        return
    bad = 0
    for p in args:
        r = check_path(p)
        if as_json:
            print(json.dumps({"path": p, **r}, ensure_ascii=False))
        else:
            print("%-9s %-46s %s" % (r["version"], os.path.basename(p)[:46],
                                     ",".join(r["evidence"]) or r["note"]))
        if r["version"] == "accepted":
            bad += 1
    if len(args) > 1:
        print("\n合计 %d 个，录用稿 %d 个" % (len(args), bad))


if __name__ == "__main__":
    main()
