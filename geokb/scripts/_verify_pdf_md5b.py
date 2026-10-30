# -*- coding: utf-8 -*-
"""MD5 verify #2: public download vs NAS S: original"""
import hashlib, os

PUB = r"C:\Users\youyo\AppData\Local\Temp\geokb_verify\pub2.pdf"
# bash /tmp 在 Windows 下的真实位置不确定，改用 glob 找
import glob
cands = glob.glob(r"D:\tmp\geokb_verify\pub2.pdf") + glob.glob(r"C:\tmp\geokb_verify\pub2.pdf")
PUB = cands[0] if cands else PUB
NAS = "S:/GeoKB/papers_repo/by_journal/MINPET/2019/2019-01-01_2019_MINPET_Ordosch_s09_00675_x.pdf"
UNC = "//192.168.10.1/KUST_SJCK_PUBLIC/GeoKB/papers_repo/by_journal/MINPET/2019/2019-01-01_2019_MINPET_Ordosch_s09_00675_x.pdf"

def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(8 * 1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest(), os.path.getsize(p)

for label, p in [("PUBLIC_DL", PUB), ("NAS_S", NAS), ("NAS_UNC", UNC)]:
    try:
        m, s = md5(p)
        print(f"{label}: md5={m} size={s}")
    except Exception as e:
        print(f"{label}: FAILED {type(e).__name__}: {e}")
