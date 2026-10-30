# -*- coding: utf-8 -*-
"""Phase 2 资源获取：USGS MCS2025 + ICS 国际年代地层表。仅下载公开/公共领域资源。"""
import json
import re
import ssl
import time
import urllib.request

UA = {"User-Agent": "GeoKB-Academic/0.1 (non-commercial research)"}
ctx = ssl.create_default_context()
OUT = r"D:/ZSK/geokb/raw_sources"
registry = []

def fetch(url, dest, note, org, confidence):
    t0 = time.time()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
        data = r.read()
    with open(dest, "wb") as f:
        f.write(data)
    rec = {"url": url, "file": dest, "org": org, "confidence": confidence,
           "bytes": len(data), "accessed": "2026-10-13", "note": note,
           "ms": int((time.time() - t0) * 1000)}
    registry.append(rec)
    print(f"[OK] {note}: {len(data)} bytes -> {dest}")

# 1) USGS Mineral Commodity Summaries 2025（美国联邦政府出版物，公共领域）
fetch("https://pubs.usgs.gov/periodicals/mcs2025/mcs2025.pdf",
      OUT + r"/usgs_mcs2025.pdf",
      "USGS Mineral Commodity Summaries 2025",
      "U.S. Geological Survey", "A")

# 2) ICS 国际年代地层表：先取 chart 页面找最新 PDF 链接
req = urllib.request.Request("https://stratigraphy.org/chart", headers=UA)
with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
    html = r.read().decode("utf-8", "ignore")
links = re.findall(r'href="([^"]*ChronostratChart[^"]*\.pdf)"', html, re.I)
if not links:
    links = re.findall(r'href="([^"]+\.pdf)"', html, re.I)
print("ICS page pdf links:", links[:5])
if links:
    link = links[0]
    if link.startswith("/"):
        link = "https://stratigraphy.org" + link
    fetch(link, OUT + r"/ics_chronostrat_chart.pdf",
          "ICS International Chronostratigraphic Chart",
          "International Commission on Stratigraphy (IUGS)", "A")

with open(OUT + r"/sources_registry.json", "w", encoding="utf-8") as f:
    json.dump(registry, f, ensure_ascii=False, indent=2)
print("registry saved")
