# -*- coding: utf-8 -*-
"""sciengine：换路径/换文章，判断是「文章不开放」还是「调用方式」问题"""
import sys, re
sys.path.insert(0, r"D:/PostNews/.workbuddy")
import journal_pdf_downloader as J

s = J.new_session()
doi = "10.3724/j.issn.1007-2802.20260189"

print("--- 1) 经典 doi/pdf 路径 ---")
for u in [f"https://www.sciengine.com/doi/pdf/{doi}",
          f"https://www.sciengine.com/sci-open/doi/pdf/{doi}"]:
    try:
        r = s.get(u, headers={**J.PDF_HEADERS, "Referer": f"https://www.sciengine.com/doi/{doi}"}, timeout=30)
        print(f"  {u[:70]}: {r.status_code} len={len(r.content)} ct={r.headers.get('Content-Type','')!r} head={r.content[:40]!r}")
    except Exception as e:
        print(f"  {u[:70]}: EXC {type(e).__name__}")

print("--- 2) 主站文章页找 PDF 链接 ---")
try:
    r = s.get(f"https://www.sciengine.com/doi/{doi}", headers=J.HEADERS, timeout=30)
    print("  page:", r.status_code, "len:", len(r.text))
    for pat in [r'href="([^"]*pdf[^"]*)"', r'(/doi/pdf/[^"\']+)', r'citation_pdf_url" content="([^"]+)"']:
        ms = re.findall(pat, r.text, re.I)
        print(f"  {pat[:35]!r}: {ms[:4]}")
except Exception as e:
    print("  EXC:", type(e).__name__, e)

print("--- 3) 换一篇同刊文章试 api（判断是否文章级问题）---")
# 从 backfill 报告拿另一篇 sciengine 的 DOI
import json
rep = json.load(open(r"D:/ZSK/geokb/state/backfill_report.json", encoding="utf-8"))
sci = [f for f in rep["failures"] if f["source"] == "sciengine"]
print("  sciengine 失败条目数:", len(sci))
for f in sci[:3]:
    m = re.search(r"(10\.\d{4,9}/[^\s?#]+)", f["url"])
    if not m:
        continue
    d = m.group(1)
    try:
        rp = s.get(f"https://www.sciengine.com/sci-open/doi/{d}", headers=J.HEADERS, timeout=30)
        m2 = re.search(r'https://www\.sciengine\.com/sci-open/api/v1/open/file/pdf/[0-9A-Fa-f]+', rp.text)
        if not m2:
            print(f"  {d[:45]}: page {rp.status_code} 无 api 链接")
            continue
        r2 = s.get(m2.group(0), headers={**J.PDF_HEADERS, "Referer": rp.url}, timeout=30)
        print(f"  {d[:45]}: api {r2.status_code} len={len(r2.content)} head={r2.content[:30]!r}")
    except Exception as e:
        print(f"  {d[:45]}: EXC {type(e).__name__}")
