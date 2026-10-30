"""台账标题回填：用条目里的完整标题修正被截断的台账标题（2026-10-25）。

## 为什么需要

`inbox_ingest.guess_title()` 从 PDF 首页按「最大字号」取标题。中文刊常见两种断层：
  · 副标题字号更小（20.6pt 主标题 + 16.4pt「——以…为例」）→ 被 0.8 字号阈值挡掉；
  · 第二行很短（「与多期成矿研究」7 字）→ 被长度门槛挡掉。
结果台账 title 只剩主标题，而**条目 title 来自 DeepSeek 通读全文**，是完整的。
期刊更新页用 `journal_feed.norm_title()` 拿源站题录去台账里找 PDF，键对不上 →
页面显示「在线抓取」、下载按钮不出现，但 NAS 上文件其实已经在了。

本脚本把「台账 title」对齐到「条目 title」。**只做前缀扩展**：
只有当 `norm(条目标题)` 以 `norm(台账标题)` 开头且更长时才改 —— 这样
「英文译名」「另外一套标题」等换题情形一律不动，避免反向把好数据改坏。

    python scripts/sync_inbox_titles.py             # 干跑
    python scripts/sync_inbox_titles.py --apply     # 实际写入（自动备份）
    python scripts/sync_inbox_titles.py --source manual_inbox   # 只处理投放箱
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
LEDGER = BASE / "state" / "papers_index.jsonl"
CONTENT = BASE / "content" / "papers"

FM_TITLE = re.compile(r'^title:\s*"?(.*?)"?\s*$', re.M)


def norm(s: str) -> str:
    r"""与 journal_feed.norm_title 同规则（\W 去掉、转小写）。"""
    return re.sub(r"\W+", "", (s or "").lower())


def _unquote(t: str) -> str:
    t = (t or "").strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "'\"":
        t = t[1:-1].strip()
    return t


def entry_title(eid: str) -> str:
    p = CONTENT / ("%s.md" % eid)
    if not p.exists():
        return ""
    try:
        head = p.read_text(encoding="utf-8", errors="ignore")[:4000]
    except OSError:
        return ""
    end = head.find("\n---", 5)
    m = FM_TITLE.search(head[:end] if end > 0 else head)
    return _unquote(m.group(1)) if m else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--source", default="", help="只处理某个 source（默认全部）")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    raw = LEDGER.read_text(encoding="utf-8", errors="ignore").splitlines()
    out, changed = [], []
    for ln in raw:
        s = ln.strip()
        if not s:
            out.append(ln)
            continue
        try:
            r = json.loads(s)
        except Exception:
            out.append(ln)
            continue
        if a.source and r.get("source") != a.source:
            out.append(ln)
            continue
        eid = str(r.get("eid") or "")
        old = str(r.get("title") or "")
        if not eid or not old:
            out.append(ln)
            continue
        new = entry_title(eid)
        if not new or new == old:
            out.append(ln)
            continue
        no, nn = norm(old), norm(new)
        # 只认「真的少了一截」：归一化后必须更长且以前缀开头。
        # 全角/半角冒号、破折号宽度、空格这类等价变体归一后长度相同 → 不动。
        if not no or not nn.startswith(no) or len(nn) <= len(no):
            out.append(ln)
            continue
        if a.limit and len(changed) >= a.limit:
            out.append(ln)
            continue
        changed.append((eid, old, new, r.get("source")))
        r["title"] = new
        out.append(json.dumps(r, ensure_ascii=False))

    print("台账行 %d | 需回填 %d | apply=%s" % (len(raw), len(changed), a.apply))
    for eid, old, new, src in changed[:40]:
        print("  %-10s %s" % (src, eid))
        print("    旧: %s" % old)
        print("    新: %s" % new)
    if len(changed) > 40:
        print("  ... 另 %d 条" % (len(changed) - 40))
    if not a.apply or not changed:
        return

    bak = LEDGER.with_suffix(".jsonl.bak-synctitle")
    if not bak.exists():
        bak.write_text("\n".join(raw) + "\n", encoding="utf-8")
    tmp = LEDGER.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8", newline="")
    tmp.replace(LEDGER)
    print("已写台账 %d 行（备份 %s）" % (len(changed), bak.name))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
