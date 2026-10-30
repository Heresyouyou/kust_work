# -*- coding: utf-8 -*-
"""用 Task Scheduler COM 以 SYSTEM 身份跑一次命令，并把 stdout 落到文件。

本机 schtasks.exe / wmic.exe 被安全策略拉黑，故走 COM 接口（与 install_autostart.py 同法）。
用法: python _run_as_system.py <脚本绝对路径>
"""
import os
import subprocess
import sys
import time

import win32com.client

TASK = "GeoKBTempSysTest"
PYW = r"D:/ZSK/geokb/venv/Scripts/python.exe"


def run(script):
    svc = win32com.client.Dispatch("Schedule.Service")
    svc.Connect()
    root = svc.GetFolder("\\")
    try:
        root.DeleteTask(TASK, 0)
    except Exception:
        pass
    td = svc.NewTask(0)
    td.RegistrationInfo.Description = "GeoKB temp SYSTEM test"
    td.Principal.UserId = "SYSTEM"
    td.Principal.LogonType = 3  # 交互式令牌（SERVICE_ACCOUNT 需密码时使用）
    td.Principal.RunLevel = 1
    td.Settings.Enabled = True
    td.Settings.Hidden = False
    td.Settings.StartWhenAvailable = True
    trig = td.Triggers.Create(1)  # TASK_TRIGGER_TIME
    t = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + 5))
    trig.StartBoundary = t
    act = td.Actions.Create(0)
    act.Path = PYW
    act.Arguments = '"%s"' % script
    act.WorkingDirectory = os.path.dirname(script)
    root.RegisterTaskDefinition(TASK, td, 6, "", "", 3)
    print("已注册，等待执行…")
    time.sleep(12)
    try:
        root.DeleteTask(TASK, 0)
    except Exception:
        pass


if __name__ == "__main__":
    run(sys.argv[1])
