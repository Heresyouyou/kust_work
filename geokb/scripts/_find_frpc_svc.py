# -*- coding: utf-8 -*-
"""在注册表中查找 frpc 相关服务项（避免 heredoc 反斜杠被改写）"""
import winreg

def scan(hive, path):
    out = []
    try:
        k = winreg.OpenKey(hive, path)
    except Exception:
        return out
    try:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(k, i)
            except OSError:
                break
            i += 1
            try:
                sk = winreg.OpenKey(hive, path + "\\" + sub)
                img, _ = winreg.QueryValueEx(sk, "ImagePath")
                out.append((sub, img))
            except Exception:
                pass
    finally:
        k.Close()
    return out

hits = []
for hive, p in [
    (winreg.HKEY_LOCAL_MACHINE, "SYSTEM\\CurrentControlSet\\Services"),
    (winreg.HKEY_CURRENT_USER, "Software\\Microsoft\\Windows\\CurrentVersion\\Run"),
    (winreg.HKEY_LOCAL_MACHINE, "Software\\Microsoft\\Windows\\CurrentVersion\\Run"),
]:
    for sub, img in scan(hive, p):
        if "frpc" in sub.lower() or "frpc" in str(img).lower() or "chml" in sub.lower() or "chml" in str(img).lower():
            hits.append((p, sub, img))

for p, sub, img in hits:
    print(f"[{p}]\n  key={sub}\n  ImagePath={img}")

if not hits:
    print("(no frpc service/run entry found)")

# 计划任务目录列一遍
import os, glob
print("\n== Tasks containing frp/chml ==")
for f in glob.glob("C:\\Windows\\System32\\Tasks\\**\\*", recursive=True):
    if os.path.isfile(f):
        name = os.path.basename(f).lower()
        if "frp" in name or "chml" in name:
            print(" ", f)
