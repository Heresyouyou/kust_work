# -*- coding: utf-8 -*-
"""Phase 2 复测：IMA（证书问题复核）、hf-mirror（HF 镜像）、USGS MCS 直链。"""
import json
import ssl
import time
import urllib.request

def probe(name, url, ctx, maxread=512):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GeoKB-Academic-Probe/0.1"})
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            r.read(maxread)
            return {"name": name, "url": url, "ok": True, "status": r.status, "ms": int((time.time()-t0)*1000)}
    except Exception as e:
        return {"name": name, "url": url, "ok": False, "status": None, "ms": int((time.time()-t0)*1000),
                "error": f"{type(e).__name__}: {e}"}

default_ctx = ssl.create_default_context()
noverify_ctx = ssl.create_default_context()
noverify_ctx.check_hostname = False
noverify_ctx.verify_mode = ssl.CERT_NONE

tests = [
    ("IMA (标准证书链)", "https://www.ima-mineralogy.org/", default_ctx),
    ("IMA (跳过校验复核)", "https://www.ima-mineralogy.org/", noverify_ctx),
    ("hf-mirror 镜像", "https://hf-mirror.com/BAAI/bge-small-zh-v1.5", default_ctx),
    ("USGS MCS2025 直链PDF(HEAD)", "https://pubs.usgs.gov/periodicals/mcs2025/mcs2025.pdf", default_ctx),
]
for name, url, ctx in tests:
    r = probe(name, url, ctx)
    mark = "OK " if r["ok"] else "FAIL"
    print(f"[{mark}] {r['name']:<26} status={r['status']} {r['ms']}ms {r.get('error','')}")
