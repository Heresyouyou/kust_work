# -*- coding: utf-8 -*-
"""GeoKB Phase 2 连通性探测（只发起 HEAD/GET，不写任何外部目标）。"""
import json
import ssl
import time
import urllib.request

TARGETS = [
    ("USGS Publications", "https://pubs.usgs.gov/"),
    ("USGS MCS 页面", "https://www.usgs.gov/centers/national-minerals-information-center/mineral-commodity-summaries"),
    ("IMA Mineralogy", "https://www.ima-mineralogy.org/"),
    ("IMA/CNMNC 矿物名录 (RRUFF)", "https://rruff.info/ima/"),
    ("ICS 国际地层表", "https://stratigraphy.org/chart"),
    ("CrossRef API", "https://api.crossref.org/works?query=skarn&rows=1"),
    ("HuggingFace（嵌入模型备用）", "https://huggingface.co/BAAI/bge-small-zh-v1.5"),
]

ctx = ssl.create_default_context()
results = []
for name, url in TARGETS:
    t0 = time.time()
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "GeoKB-Academic-Probe/0.1 (research; contact: local)"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=12, context=ctx) as r:
            body = r.read(512)
            ms = int((time.time() - t0) * 1000)
            results.append({"name": name, "url": url, "status": r.status, "ms": ms, "ok": True})
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        results.append({"name": name, "url": url, "status": None, "ms": ms, "ok": False,
                        "error": f"{type(e).__name__}: {e}"})

for r in results:
    mark = "OK " if r["ok"] else "FAIL"
    print(f"[{mark}] {r['name']:<28} status={r['status']} {r['ms']}ms {r.get('error','')}")

with open(r"D:/ZSK/geokb/raw_sources/connectivity_probe_20261013.json", "w", encoding="utf-8") as f:
    json.dump({"probed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "results": results}, f, ensure_ascii=False, indent=2)
print("saved: raw_sources/connectivity_probe_20261013.json")
