# -*- coding: utf-8 -*-
"""探测本机曾映射的网络驱动器（S: 等）与 NAS 共享，用于定位 PDF 迁移目标。"""
import winreg

print("=== HKCU\\Network（历史映射盘符）===")
try:
    k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Network")
    i = 0
    while True:
        try:
            name = winreg.EnumKey(k, i)
            sk = winreg.OpenKey(k, name)
            try:
                remote = winreg.QueryValueEx(sk, "RemotePath")[0]
            except Exception:
                remote = "(无)"
            print("  盘符 %s -> %s" % (name, remote))
            i += 1
        except OSError:
            break
except Exception as e:
    print("  读取失败:", e)

print("=== MountedDevices 中含 UNC 的卷 ===")
try:
    k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, "SYSTEM\\MountedDevices")
    i = 0
    for _ in range(300):
        try:
            name, val, _typ = winreg.EnumValue(k, i)
        except OSError:
            break
        if b"\\" in val and (b"\\\\" in val or b"\x00\\" in val):
            print("  ", name[:46], val[:70])
        i += 1
except Exception as e:
    print("  读取失败:", e)
