#Requires -Version 5.1
<#
.SYNOPSIS
    PostNews NAS 回收任务 —— 注册 / 卸载 / 查询 Windows 计划任务。

.DESCRIPTION
    每日本地时间 09:00 与 21:00 各执行一次 tools\nas_reaper.py：
    把本地存在超过 48 小时的译文三版本（原文 / 中文 / 双语）搬到 NAS 文献仓库，
    校验无误后删除本地副本释放空间。

    完全依赖 Windows 任务计划程序，不依赖任何外部服务、后台进程或第三方调度。
    运行日志由 nas_reaper.py 自己写入 logs\nas_reaper.log（超过 2 MB 自动轮转）。

.EXAMPLE
    install_task.ps1                 # 安装（默认：用户登录时运行，可能闪一下窗口）
    install_task.ps1 -RunAlways      # 静默后台模式：完全无窗口、关机错过也会补跑（需管理员）
    install_task.ps1 -Uninstall      # 卸载任务
    install_task.ps1 -Status         # 查看任务状态 + 最近日志
    install_task.ps1 -RunNow         # 立刻手动触发一次（用于验证）
    install_task.ps1 -Times 08:00,20:00 -Hours 24   # 自定义时间与保留时长
#>
param(
    [string[]]$Times = @('09:00', '21:00'),
    [int]$Hours = 48,
    [switch]$RunAlways,
    [switch]$Uninstall,
    [switch]$Status,
    [switch]$RunNow,
    [string]$Python = ''
)

$ErrorActionPreference = 'Stop'

$TaskName    = 'PostNews-NASReaper'
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir '..\..')).Path
$Reaper      = Join-Path $ProjectRoot 'tools\nas_reaper.py'
$LogFile     = Join-Path $ProjectRoot 'logs\nas_reaper.log'

# ───────────────────────── 工具函数 ─────────────────────────

function Write-Head($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Find-Python {
    param([string]$Hint = '')
    # 优先级：显式指定 > 环境变量 > 项目固定路径 > PATH > py 启动器
    $candidates = @()
    if ($Hint)                          { $candidates += $Hint }
    if ($env:POSTNEWS_PYTHON)           { $candidates += $env:POSTNEWS_PYTHON }
    $candidates += 'D:\Python 3.12.4\python.exe'
    if ($env:LOCALAPPDATA) {
        $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
    }
    $candidates += 'python.exe'

    foreach ($c in $candidates) {
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if ($cmd -and (Test-Path $cmd.Source)) { return $cmd.Source }
    }
    # 最后兜底：py 启动器解析出真实路径
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        try {
            $p = & $py.Source -3.12 -c 'import sys; print(sys.executable)' 2>$null
            if ($p -and (Test-Path $p.Trim())) { return $p.Trim() }
        } catch {}
    }
    return $null
}

# ───────────────────────── 卸载 ─────────────────────────

if ($Uninstall) {
    Write-Head "卸载计划任务 $TaskName"
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $t) { Write-Host '任务不存在，无需卸载。' -ForegroundColor Yellow; exit 0 }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host '[OK] 已卸载，本地 PDF 不再被定时回收。' -ForegroundColor Green
    exit 0
}

# ───────────────────────── 查询 ─────────────────────────

if ($Status) {
    Write-Head "计划任务状态"
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $t) {
        Write-Host "任务 $TaskName 未安装。运行 install_task.bat 安装。" -ForegroundColor Yellow
    } else {
        $i = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Host ("  任务名     : {0}" -f $t.TaskName)
        Write-Host ("  状态       : {0}" -f $t.State)
        Write-Host ("  触发时间   : {0}" -f (($t.Triggers | ForEach-Object { $_.StartBoundary }) -join ', '))
        Write-Host ("  下次运行   : {0}" -f $i.NextRunTime)
        Write-Host ("  上次运行   : {0}" -f $i.LastRunTime)
        Write-Host ("  上次结果   : {0}" -f $i.LastTaskResult)
        Write-Host ("  命令行     : {0} {1}" -f $t.Actions[0].Execute, $t.Actions[0].Arguments)
    }
    Write-Head "最近日志 (logs\nas_reaper.log)"
    if (Test-Path $LogFile) {
        Get-Content $LogFile -Tail 12 -Encoding UTF8
    } else {
        Write-Host '（尚无日志，任务还没跑过）' -ForegroundColor Yellow
    }
    exit 0
}

# ───────────────────────── 立即运行 ─────────────────────────

if ($RunNow) {
    Write-Head "立即触发一次回收"
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $t) { Write-Host '任务未安装，请先安装。' -ForegroundColor Red; exit 1 }
    Start-ScheduledTask -TaskName $TaskName
    Write-Host '[OK] 已触发，等待 10 秒后读取日志…' -ForegroundColor Green
    Start-Sleep -Seconds 10
    if (Test-Path $LogFile) { Get-Content $LogFile -Tail 20 -Encoding UTF8 }
    exit 0
}

# ───────────────────────── 安装 ─────────────────────────

Write-Head "安装计划任务 $TaskName"

if (-not (Test-Path $Reaper)) {
    Write-Host "[X] 找不到回收脚本：$Reaper" -ForegroundColor Red
    exit 1
}

$py = Find-Python -Hint $Python
if (-not $py) {
    Write-Host '[X] 找不到 Python。请设置环境变量 POSTNEWS_PYTHON 或用 -Python 参数指定。' -ForegroundColor Red
    exit 1
}
Write-Host ("  Python     : {0}" -f $py)
Write-Host ("  项目根目录 : {0}" -f $ProjectRoot)
Write-Host ("  每日时间   : {0}" -f ($Times -join ', '))
Write-Host ("  保留时长   : {0} 小时" -f $Hours)

# 用隐藏窗口的 PowerShell 包一层执行 python，避免出现控制台黑窗。
# 日志由 nas_reaper.py 自己写文件，因此这里不需要捕获标准输出。
$psExe  = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path $psExe)) { $psExe = 'powershell.exe' }
# 强制 UTF-8 输出：定时任务的控制台代码页是 cp936，打印中文/emoji 会让 Python 直接崩掉。
# 保留时长通过环境变量传给脚本（nas_archive 启动时读取 NAS_TTL_HOURS）。
$prefix = "`$env:PYTHONIOENCODING='utf-8'; "
if ($env:NAS_TTL_HOURS -ne $Hours) { $prefix += "`$env:NAS_TTL_HOURS='$Hours'; " }
# ★ 路径必须用单引号：任务里的参数是二次解析的，内层再用双引号会被 powershell.exe
#   拆开，"D:\Python 3.12.4\python.exe" 这种带空格的路径会当场失败。
$inner  = $prefix + "& '$py' '$Reaper'"
$action = New-ScheduledTaskAction -Execute $psExe `
    -Argument "-NoProfile -NoLogo -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"$inner`"" `
    -WorkingDirectory $ProjectRoot

$triggers = @()
foreach ($t in $Times) {
    $triggers += New-ScheduledTaskTrigger -Daily -At $t -RandomDelay (New-TimeSpan -Minutes 0)
}

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew

if ($RunAlways) {
    if (-not (Test-Admin)) {
        Write-Host '[X] -RunAlways 需要管理员权限：请右键“以管理员身份运行”本脚本。' -ForegroundColor Red
        exit 1
    }
    # S4U：不管用户是否登录都运行，完全无窗口，也不需要保存密码
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Highest
    Write-Host '  运行模式   : 后台静默（不登录也执行，无窗口）'
} else {
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Write-Host '  运行模式   : 用户登录时执行（如需完全无窗口，用管理员身份加 -RunAlways）'
}

try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
        -Settings $settings -Principal $principal -Force | Out-Null
} catch {
    Write-Host "[X] 注册失败：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "`n[OK] 安装完成" -ForegroundColor Green
Write-Host ("  下次运行   : {0}" -f $info.NextRunTime)
Write-Host ("  日志       : {0}" -f $LogFile)
Write-Host "`n常用命令："
Write-Host "  查看状态   : powershell -ExecutionPolicy Bypass -File `"$ScriptDir\install_task.ps1`" -Status"
Write-Host "  立刻验证   : powershell -ExecutionPolicy Bypass -File `"$ScriptDir\install_task.ps1`" -RunNow"
Write-Host "  卸载       : powershell -ExecutionPolicy Bypass -File `"$ScriptDir\install_task.ps1`" -Uninstall"
exit 0
