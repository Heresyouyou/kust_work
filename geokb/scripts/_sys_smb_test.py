# -*- coding: utf-8 -*-
"""以 SYSTEM 身份运行时由计划任务调用，检测 SMB 可访问性，结果写文件。"""
import os

OUT = r"D:/ZSK/geokb/logs/smb_test.txt"
targets = [r"\\192.168.10.1\Youyou", r"\\192.168.10.1\Public", r"\\192.168.10.1\home"]
lines = []
try:
    import getpass
    lines.append("user=%s" % getpass.getuser())
except Exception:
    pass
for p in targets:
    try:
        ls = os.listdir(p)
        lines.append("OK   %s -> %d 项" % (p, len(ls)))
    except Exception as e:
        lines.append("FAIL %s -> %s %s" % (p, type(e).__name__, e))
open(OUT, "w", encoding="utf-8").write("\n".join(lines))
