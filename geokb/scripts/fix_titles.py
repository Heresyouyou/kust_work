"""按审计结果修正条目标题（2026-10-26）

只用「原文/台账」里的真标题替换被译名、被截断、被占位的标题：

  候选优先级：① 台账 title（来自期刊站点，通常是原文标题）
              ② PDF 首页最大字号标题块（去掉刊名前缀/审稿戳）
  两道保险：候选必须 ≥12 字符、不是 slug/哈希、不与现标题相同；
            **本地没有可靠候选的一律不动**，留给人工。

    python scripts/fix_titles.py                 # 只预览
    python scripts/fix_titles.py --apply
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))
CONTENT = BASE / "content" / "papers"
PYEXE = BASE / "venv" / "Scripts" / "python.exe"
NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

SLUG = re.compile(r"^[A-Za-z0-9_\-]+$")
HASH = re.compile(r"^[0-9a-f]{8,}$")
FNAME = re.compile(r"^\d{4}-\d{2}-\d{2}_")
CJK = re.compile(r"[\u3400-\u9fff]")
FM_TITLE = re.compile(r"^title:.*$", re.M)


def looks_real(t: str) -> bool:
    """候选标题像不像真的：够长、不是 slug/哈希、有正常的字。"""
    t = (t or "").strip()
    if len(t) < 12:
        return False
    if HASH.match(t):
        return False
    if " " not in t and SLUG.match(t):
        return False
    cjk = len(CJK.findall(t))
    letters = sum(1 for ch in t if ch.isalpha())
    if cjk < 4 and letters < 10:
        return False          # 乱码块（如 "*+UV-VWXYZ[\\]^&_`ST"）
    return True


def invalid(cur: str) -> str:
    """当前标题为什么不可信；空串表示「看起来正常」。"""
    c = (cur or "").strip()
    if HASH.match(c):
        return "哈希占位"
    if len(c) < 12:
        return "过短"
    if " " not in c and SLUG.match(c):
        return "slug 占位"
    if FNAME.match(c):
        return "文件名当标题"
    if "\\" in c:
        return "转义破损"
    return ""


def strip_journal_prefix(block: str, journal: str) -> str:
    """PDF 标题块常把刊名并进来（'Ore Geology ReviewsGarnet and …'）。"""
    if not journal:
        return block
    pat = re.compile(r"^\s*" + r"\s*".join(map(re.escape, journal.split())), re.I)
    out = pat.sub("", block).strip()
    return out if len(out) >= 12 else block



def title_block(p) -> str:
    """重新从 PDF 抽标题块，**换行处按语言补空格**（英文断行常常粘成一个词）。"""
    try:
        import pymupdf
        doc = pymupdf.open(str(p))
    except Exception:
        return ""
    try:
        if not doc.page_count:
            return ""
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
        if not lines:
            return ""
        mx = max(l[0] for l in lines)
        cand = [c[2] for c in sorted([l for l in lines if l[0] >= mx - 0.8], key=lambda x: x[1])]
        out = ""
        for seg in cand[:3]:
            if not out:
                out = seg
                continue
            a, b2 = out[-1], seg[0]
            if a.isascii() and b2.isascii() and (a.isalnum() or a in ")]") and b2.isalnum():
                out += " " + seg          # 英文断行 → 补空格
            else:
                out += seg                # 中文/连字符 → 直接拼
        return out.strip()
    finally:
        doc.close()


def set_title(path: Path, title: str) -> bool:
    """替换 frontmatter 里的 title。

    ⚠️ 必须吃掉**多行标量的续行**（原文件里 title 常折成 2–3 行），
    否则只改首行会留下孤立续行，整个 frontmatter 直接解析失败（2026-10-26 踩过）。
    """
    raw = path.read_text(encoding="utf-8", newline="")
    m = re.match(r"^---\r?\n(.*?)\r?\n---", raw, re.S)
    if not m:
        return False
    fm = m.group(1)
    lines = fm.split("\n")
    out, i, hit = [], 0, False
    while i < len(lines):
        ln = lines[i]
        if re.match(r"^title:", ln):
            out.append("title: %s" % json.dumps(title, ensure_ascii=False))
            hit = True
            i += 1
            while i < len(lines) and lines[i][:1] in (" ", "\t"):
                i += 1
            continue
        out.append(ln)
        i += 1
    if not hit:
        out.insert(0, "title: %s" % json.dumps(title, ensure_ascii=False))
    new_fm = "\n".join(out)
    new = raw[:m.start(1)] + new_fm + raw[m.end(1):]
    if new == raw:
        return False
    path.write_text(new, encoding="utf-8", newline="")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tsv", default=str(BASE / "logs" / "title_audit_20261026.tsv"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    _led = {}
    for ln in (BASE / "state" / "papers_index.jsonl").read_text(encoding="utf-8",
                                                               errors="ignore").splitlines():
        s = ln.strip()
        if not s:
            continue
        try:
            _led[json.loads(s).get("eid")] = json.loads(s)
        except Exception:
            pass

    rows = []
    with open(a.tsv, encoding="utf-8") as f:
        head = f.readline()
        for ln in f:
            q = ln.rstrip("\n").split("\t")
            if len(q) >= 8:
                rows.append(dict(zip(head.rstrip("\n").split("\t"), q)))

    plan, skipped = [], []
    for r in rows:
        eid = r["eid"]
        cur = (r["title"] or "").strip()
        led = (r["ledger_title"] or "").strip()
        blk = strip_journal_prefix(r["block"] or "", r["journal"])
        bad = invalid(cur)

        cand, why = "", ""
        # 台账偶尔给全大写（期刊站点排版），这种情况让位给 PDF 标题块
        if led.isupper() and len(led) > 20 and blk and not blk.isupper():
            led, blk = blk, led
        # 候选优先级：台账（期刊站点标题）> PDF 标题块
        for src, val in (("台账", led), ("PDF标题块", blk)):
            if not looks_real(val) or val == cur:
                continue
            if src == "PDF标题块":
                r2 = _led.get(eid) or {}
                from audit_pub_dates import NAS, rel_path
                rel = rel_path(r2)
                pp = NAS / rel if rel else None
                if pp and pp.is_file():
                    val = strip_journal_prefix(title_block(pp) or val, r["journal"])
                if len(val) < 45:        # 短块多半被截断，不敢自动覆盖
                    continue
            cand, why = val, src
            break
        if not cand:
            skipped.append((eid, cur, r["bucket"], led, blk, "无可靠候选"))
            continue

        cur_cjk = bool(CJK.search(cur))
        cand_cjk = bool(CJK.search(cand))
        same_script = cur_cjk == cand_cjk
        superset = cand.replace(" ", "").startswith(cur.replace(" ", "")) and len(cand) > len(cur)

        if bad:
            reason = bad
        elif not same_script:
            reason = "用了译名/译文标题"
        elif superset:
            reason = "被截断"
        else:
            skipped.append((eid, cur, r["bucket"], led, blk, "两版标题差异大，需人工确认"))
            continue
        plan.append((eid, cur, cand, why, reason))

    print("待修 %d 条｜跳过（无可靠候选）%d 条" % (len(plan), len(skipped)))
    for eid, cur, cand, why, reason in plan:
        print("\n[%s] %s" % (reason, eid))
        print("  旧: %s" % cur[:96])
        print("  新: %s   ← %s" % (cand[:96], why))
    if skipped:
        print("\n--- 未自动处理（%d） ---" % len(skipped))
        for eid, cur, b, led, blk, note in skipped:
            print("  [%s] %s  —— %s" % (b, eid, note))
            print("    条目: %s" % cur[:80])
            print("    台账: %s" % led[:80])
            print("    块  : %s" % blk[:80])

    if not a.apply or not plan:
        return
    ok = 0
    for eid, cur, cand, why, reason in plan:
        p = CONTENT / ("%s.md" % eid)
        if not p.exists():
            print("  缺失条目文件:", eid)
            continue
        if set_title(p, cand):
            ok += 1
    print("\n已改条目 %d 个，开始重建索引…" % ok)
    for eid, cur, cand, why, reason in plan:
        try:
            subprocess.run([str(PYEXE), str(BASE / "scripts" / "index_one_entry.py"), eid],
                           cwd=str(BASE), capture_output=True, timeout=180,
                           creationflags=NO_WIN)
        except Exception as e:
            print("  索引失败", eid, e)
    print("索引重建完成")


if __name__ == "__main__":
    main()
