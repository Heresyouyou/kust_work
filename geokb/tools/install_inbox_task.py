# -*- coding: utf-8 -*-
"""注册「统一投放箱」计划任务：每天 12:01 与 18:01 各批量入库一次。

为什么用 Task Scheduler 而不是守护进程
------------------------------------
geokb_daemon.py 的 EXTRA_PROCS 是「按 pid 判活的常驻进程」，适合 7×24 的
history_queue_loop。投放箱是**定点一次性任务**，用 CalendarTrigger 更贴合：
跑完即退，不占常驻名额，也能被 StartWhenAvailable 在开机后补跑。

为什么不能用 schtasks.exe
------------------------
本机安全策略拉黑 schtasks / reg / wmic，必须走 Schedule.Service COM
（与 tools/restart_daemon.py 同一套路）。

用法
----
    python tools/install_inbox_task.py --install    # 注册/更新（幂等）
    python tools/install_inbox_task.py --check      # 查看当前状态
    python tools/install_inbox_task.py --delete     # 删除

⚠️ 两个时点都必须在 DeepSeek 低谷窗口内：12:01–13:59、18:01–24:00。
   改时间就改这里的 TRIGGERS，然后重跑 --install。
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta

TASK_NAME = "GeoKB-InboxIngest"
BASE = r"D:\ZSK\geokb"
PYTHON = r"D:\ZSK\geokb\venv\Scripts\python.exe"
SCRIPT = r"D:\ZSK\geokb\scripts\inbox_ingest.py"
TRIGGERS = ["12:01", "18:01"]      # 每天的批量入库时点（北京时间）


def _user_id() -> str:
    dom = os.environ.get("USERDOMAIN") or ""
    usr = os.environ.get("USERNAME") or ""
    return f"{dom}\\{usr}" if dom else usr


def build_xml() -> str:
    # 起始日期取明天，避免注册当天错过时点又立刻补跑一次
    day = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    trig = "\n".join(
        f"""    <CalendarTrigger>
      <StartBoundary>{day}T{t}:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>""" for t in TRIGGERS)

    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>GeoKB</Author>
    <Description>统一投放箱：把手动下载的论文 PDF（不限来源）归到期刊目录、写台账、入库（12:01 / 18:01）</Description>
  </RegistrationInfo>
  <Triggers>
{trig}
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{_user_id()}</UserId>
      <RunLevel>LeastPrivilege</RunLevel>
      <LogonType>InteractiveToken</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <Duration>PT10M</Duration>
      <WaitTimeout>PT1H</WaitTimeout>
      <StopOnIdleEnd>true</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{PYTHON}</Command>
      <Arguments>"{SCRIPT}" --run</Arguments>
      <WorkingDirectory>{BASE}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def connect():
    import win32com.client
    svc = win32com.client.Dispatch("Schedule.Service")
    svc.Connect()
    return svc


def install():
    from win32com.client import Dispatch  # noqa: F401  确保 pywin32 可用
    svc = connect()
    root = svc.GetFolder("\\")
    xml = build_xml()
    # flags: 6 = TASK_CREATE_OR_UPDATE；logonType: 3 = InteractiveToken
    root.RegisterTask(TASK_NAME, xml, 6, _user_id(), None, 3)
    print(f"已注册 {TASK_NAME}")
    print(f"  时点：{'、'.join(TRIGGERS)}")
    print(f"  命令：{PYTHON} {SCRIPT} --run")
    check()


def delete():
    svc = connect()
    root = svc.GetFolder("\\")
    try:
        root.DeleteTask(TASK_NAME, 0)
        print(f"已删除 {TASK_NAME}")
    except Exception as e:
        print(f"删除失败（可能不存在）：{e}")


def check():
    svc = connect()
    root = svc.GetFolder("\\")
    try:
        t = root.GetTask(TASK_NAME)
    except Exception:
        print(f"{TASK_NAME} 未注册")
        return 1
    d = t.Definition
    print(f"任务：{TASK_NAME}")
    print(f"  状态：{'启用' if d.Settings.Enabled else '停用'}")
    for i in range(1, d.Triggers.Count + 1):
        tr = d.Triggers.Item(i)
        print(f"  触发 {i}：{tr.StartBoundary}（每天）")
    for i in range(1, d.Actions.Count + 1):
        a = d.Actions.Item(i)
        print(f"  动作：{a.Path} {a.Arguments}")
    try:
        info = t.GetRunTimes(0)
        if info:
            print(f"  上次运行：{info[0]}")
    except Exception:
        pass
    return 0


def main():
    ap = argparse.ArgumentParser(description="注册统一投放箱计划任务")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--delete", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.install:
        install()
    elif args.delete:
        delete()
    else:
        return check()
    return 0


if __name__ == "__main__":
    sys.exit(main())
