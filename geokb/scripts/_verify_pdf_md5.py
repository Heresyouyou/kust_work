# -*- coding: utf-8 -*-
"""MD5 verify: public download vs NAS original (UNC path)"""
import hashlib, sys, os

PUB = r"D:/ZSK/../tmp/geokb_verify/pub.pdf"  # placeholder, replaced below
PUB = os.path.abspath(r"/tmp/geokb_verify/pub.pdf")
NAS = "//192.168.10.1/KUST_SJCK_PUBLIC/GeoKB/papers_repo/by_journal/D/2026/2026-01-01_Mineralium_Deposita_skarn_epidote_2026.no_watermark.zh.mono.pdf"

def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(8 * 1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest(), os.path.getsize(p)

for label, p in [("PUBLIC_DL", PUB), ("NAS", NAS)]:
    try:
        m, s = md5(p)
        print(f"{label}: md5={m} size={s}")
    except Exception as e:
        print(f"{label}: FAILED {type(e).__name__}: {e}")
