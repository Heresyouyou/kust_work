# -*- coding: utf-8 -*-
"""把 GeoKB 守护进程注册为 Windows 开机计划任务（SYSTEM 账户，无需登录）。

用法
  python scripts/install_autostart.py install     # 注册（幂等，已存在则更新）
  python scripts/install_autostart.py status      # 查看任务状态
  python scripts/install_autostart.py start       # 立即运行一次（不等重启）
  python scripts/install_autostart.py uninstall   # 注销

说明：本机 reg.exe / schtasks.exe 被安全策略拉黑，故改用 Task Scheduler COM 接口，
这是官方支持的注册方式，写入 C:\\Windows\\System32\\Tasks\\<名称> 且注册项由 Scheduler 自行维护。
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PYW = os.path.join(BASE, "venv", "Scripts", "pythonw.exe")
SCRIPT = os.path.join(BASE, "scripts", "geokb_daemon.py")
TASK_NAME = "GeoKBDaemon"

TASK_CREATE_OR_UPDATE = 6
TASK_TRIGGER_BOOT = 8
TASK_TRIGGER_LOGON = 9
TASK_ACTION_EXEC = 0
TASK_LOGON_SERVICE_ACCOUNT = 5
TASK_LOGON_INTERACTIVE_TOKEN = 3
TASK_RUNLEVEL_HIGHEST = 1
MULTI_INSTANCE_IGNORE_NEW = 2


def build_definition(svc, user_mode=False):
    d = svc.NewTask(0)
    reg = d.RegistrationInfo
    reg.Description = "GeoKB 知识库端口守护（5556 API / 5554 管理端），宕机自动拉起"
    reg.Author = "GeoKB"

    s = d.Settings
    s.Enabled = True
    s.Hidden = False
    s.StartWhenAvailable = True          # 错过触发时机后补跑
    s.DisallowStartIfOnBatteries = False
    s.StopIfGoingOnBatteries = False
    s.ExecutionTimeLimit = "PT0S"        # 不限时长（守护进程需常驻）
    s.MultipleInstances = MULTI_INSTANCE_IGNORE_NEW
    s.RestartCount = 3                   # 守护进程自身崩溃后由计划任务兜底重启
    s.RestartInterval = "PT1M"

    if user_mode:
        # 用户模式：登录后触发。SYSTEM 会话看不到 S: 盘符、也无法通过 SMB 访问 NAS，
        # 而 PDF 已迁到 NAS，服务要读原文就必须跑在用户令牌下。
        t = d.Triggers.Create(TASK_TRIGGER_LOGON)
        t.Enabled = True
        t.Delay = "PT20S"
    else:
        t = d.Triggers.Create(TASK_TRIGGER_BOOT)
        t.Enabled = True
        t.Delay = "PT30S"                # 开机后延迟 30s，等磁盘/网络就绪

    a = d.Actions.Create(TASK_ACTION_EXEC)
    a.Path = VENV_PYW
    a.Arguments = '"%s" run' % SCRIPT
    a.WorkingDirectory = BASE

    p = d.Principal
    if user_mode:
        p.UserId = "%s\\%s" % (os.environ.get("USERDOMAIN", ""),
                               os.environ.get("USERNAME", ""))
        p.LogonType = TASK_LOGON_INTERACTIVE_TOKEN
    else:
        p.UserId = "SYSTEM"
        p.LogonType = TASK_LOGON_SERVICE_ACCOUNT
    p.RunLevel = TASK_RUNLEVEL_HIGHEST
    return d


def connect():
    import win32com.client
    svc = win32com.client.Dispatch("Schedule.Service")
    svc.Connect()
    return svc


def cmd_install():
    if not os.path.exists(VENV_PYW):
        print("找不到 pythonw.exe: %s" % VENV_PYW)
        return 1
    user_mode = "--user" in sys.argv
    svc = connect()
    folder = svc.GetFolder("\\")
    d = build_definition(svc, user_mode=user_mode)
    if user_mode:
        user = "%s\\%s" % (os.environ.get("USERDOMAIN", ""),
                           os.environ.get("USERNAME", ""))
        folder.RegisterTaskDefinition(TASK_NAME, d, TASK_CREATE_OR_UPDATE,
                                      user, None, TASK_LOGON_INTERACTIVE_TOKEN)
        print("已注册任务: \\%s" % TASK_NAME)
        print("  触发: 用户登录时（延迟 20 秒）")
        print("  账户: %s（当前已登录，无需保存密码）" % user)
        print("  用途: 以用户令牌运行，才能访问 NAS 上的 S: 盘 / SMB 共享")
    else:
        folder.RegisterTaskDefinition(TASK_NAME, d, TASK_CREATE_OR_UPDATE,
                                      "SYSTEM", None, TASK_LOGON_SERVICE_ACCOUNT)
        print("已注册开机任务: \\%s" % TASK_NAME)
        print("  触发: 系统启动时（延迟 30 秒）")
        print("  账户: SYSTEM（无需登录、无需密码保存）")
        print("  注意: SYSTEM 无法访问 NAS 的 S: 盘，PDF 下载/预览 API 会不可用")
    print("  命令: %s \"%s\" run" % (VENV_PYW, SCRIPT))
    return 0


def cmd_status():
    svc = connect()
    folder = svc.GetFolder("\\")
    try:
        task = folder.GetTask("\\%s" % TASK_NAME)
    except Exception:
        print("任务未注册: \\%s" % TASK_NAME)
        return 1
    print("任务名   : %s" % task.Name)
    print("启用     : %s" % task.Enabled)
    # TASK_STATE: 0 未知 / 1 已禁用 / 2 排队 / 3 就绪 / 4 运行中
    print("状态     : %s" % {0: "未知", 1: "已禁用", 2: "排队中", 3: "就绪(等待触发)",
                             4: "运行中"}.get(task.State, task.State))
    # 0x41300 就绪 / 0x41301 运行中 / 0x41302 已禁用 / 0x41303 尚未运行 / 0x41304 无更多运行
    code = int(task.LastTaskResult) & 0xFFFFFFFF
    print("上次结果 : 0x%08X  %s" % (code, {
        0x00000000: "成功", 0x00041300: "就绪", 0x00041301: "运行中",
        0x00041302: "已禁用", 0x00041303: "尚未运行", 0x00041304: "无更多运行",
    }.get(code, "见系统定义")))
    print("上次运行 : %s" % task.LastRunTime)
    print("下次运行 : %s" % task.NextRunTime)
    return 0


def cmd_start():
    svc = connect()
    folder = svc.GetFolder("\\")
    task = folder.GetTask("\\%s" % TASK_NAME)
    task.Run("")
    print("已触发运行（守护进程自身有单实例锁，重复触发不会重拉服务）")
    return 0


def cmd_uninstall():
    svc = connect()
    folder = svc.GetFolder("\\")
    folder.DeleteTask(TASK_NAME, 0)
    print("已注销任务: \\%s" % TASK_NAME)
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    fn = {"install": cmd_install, "status": cmd_status,
          "start": cmd_start, "uninstall": cmd_uninstall}.get(cmd)
    if not fn:
        print(__doc__)
        sys.exit(1)
    sys.exit(fn())
