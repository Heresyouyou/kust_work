# -*- coding: utf-8 -*-
"""用 backfill 报告里的真实失败条目测 OA 渠道"""
import sys, json, time
sys.path.insert(0, r"D:/PostNews")
sys.path.insert(0, r"D:/PostNews/.workbuddy")
import journal_pdf_oa as oa

r = json.load(open(r"D:/ZSK/geokb/state/backfill_report.json", encoding="utf-8"))
fails = r["failures"]
want = {"elsevier": 3, "gsw": 3, "oup": 2}
cnt = {k: 0 for k in want}
sel = []
for f in fails:
    s = f["source"]
    if s in want and cnt[s] < want[s]:
        sel.append(f)
        cnt[s] += 1

for f in sel:
    print("=" * 70)
    print(f"[{f['source']}] {f['title'][:60]}")
    print(f"  url: {f['url']}  (backfill note: {f['note'][:60]})")
    t0 = time.time()
    res = oa.oa_fetch(f["title"], url=f["url"], verbose=True)
    dt = time.time() - t0
    b = res.get("pdf_bytes")
    print(f"  -> status={res.get('status')} channel={res.get('channel')} "
          f"pdf_url={str(res.get('pdf_url'))[:100]} bytes={len(b) if b else 0} ({dt:.1f}s)")
    print(f"  note: {str(res.get('note'))[:250]}")
    time.sleep(1)
