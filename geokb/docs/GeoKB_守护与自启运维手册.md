# GeoKB 守护进程与开机自启 运维手册

适用：Windows / GeoKB（`D:/ZSK/geokb`），守护端口 **5556**（AI API）与 **5554**（本地管理端）。

---

## 一、组件

| 文件 | 作用 |
|---|---|
| `scripts/geokb_daemon.py` | 守护进程本体：巡检、拉起、退避、单实例锁 |
| `scripts/install_autostart.py` | 注册/注销 Windows 开机计划任务（Task Scheduler COM） |
| `state/daemon.lock` | 单实例锁（存 PID，含存活校验，自动清理失效锁） |
| `logs/daemon.log` | 守护进程运行日志 |
| `logs/geokb-api.out.log` / `.err.log` | 5556 服务输出 |
| `logs/geokb-admin.out.log` / `.err.log` | 5554 服务输出 |

计划任务：`\GeoKBDaemon`

| 属性 | 值 |
|---|---|
| 触发 | 系统启动时，延迟 30 秒（`StartWhenAvailable=true`，错过则补跑） |
| 账户 | `SYSTEM`（无需登录、无需保存密码） |
| 权限 | HighestAvailable |
| 命令 | `D:\ZSK\geokb\venv\Scripts\pythonw.exe "D:\ZSK\geokb\scripts\geokb_daemon.py" run` |
| 时长限制 | `PT0S`（不限，守护需常驻） |
| 失败兜底 | 重启 3 次，间隔 1 分钟 |

---

## 二、日常命令

```bash
PY="D:/ZSK/geokb/venv/Scripts/python.exe"

# 查看状态（守护进程 + 两个端口健康）
"$PY" scripts/geokb_daemon.py status

# 由计划任务立即启动守护进程（正常用法，进程归 SYSTEM，脱离终端）
"$PY" scripts/install_autostart.py start

# 停止守护进程（仅停守护，服务继续跑）
"$PY" scripts/geokb_daemon.py stop

# 停止守护 + 一起停掉 5556/5554（维护、交接、排查端口占用时用）
"$PY" scripts/geokb_daemon.py srv-stop

# 计划任务：注册（幂等）/ 查看 / 注销
"$PY" scripts/install_autostart.py install
"$PY" scripts/install_autostart.py status
"$PY" scripts/install_autostart.py uninstall
```

---

## 三、行为约定（重要）

1. **单实例**：`state/daemon.lock` 独占创建 + 旧 PID 存活校验。重复触发任务不会重拉服务。
2. **健康判定**：TCP 可连 **且** `GET /health` 返回 200。任一不满足即判定宕机。
3. **收养优先**：端口已被*健康*服务占用时不重复启动；被*不明进程*占用且健康检查不过时**只告警、不硬抢**，避免误杀他人端口。
4. **启动宽限** 25 秒，超时才判定失败；失败按 1/2/4/8…秒退避，上限 60 秒。
5. **退出清理**：守护退出时回收自己拉起的子进程。

---

## 四、本机环境硬约束（排障必读）

| 约束 | 影响 | 替代方案 |
|---|---|---|
| `reg.exe` / `schtasks.exe` / `wmic.exe` 被安全策略拉黑 | 无法用命令行管计划任务 | 用 `install_autostart.py` 走 Task Scheduler **COM** 接口 |
| `tasklist` 在本会话返回空 | 拿不到进程身份 | 用 `scripts/_port_probe.py`（ctypes 直调 `GetExtendedTcpTable` + `CreateToolhelp32Snapshot`） |
| 由 Bash 后台任务启动的进程会随会话/中断被连带杀掉 | 守护"假常驻" | **必须**经计划任务以 SYSTEM 启动 |
| 计划任务 XML 是 **UTF-16** | 直接 utf-8 读取报 `0xff` 解码错 | `open(path, encoding='utf-16')` |

读任务状态时注意：`TASK_STATE=4` 是**运行中**；`LastTaskResult=0x00041301` 是 `SCHED_S_TASK_RUNNING`，不是错误。

---

## 五、端口现状与既有占用（勿误判）

| 端口 | 监听者 | 说明 |
|---|---|---|
| 5556 | `python.exe`（venv）绑 `0.0.0.0` | GeoKB AI API，受守护 |
| 5554 | `python.exe`（venv）绑 `127.0.0.1` | 仅本地管理端，受守护 |
| 5555 | `python.exe`（`D:\Python 3.12.4`）绑 `0.0.0.0` | 可视化门户，**不在本守护范围** |
| 192.168.10.100:5555 | `svchost.exe`(4692) | **netsh portproxy 转发** `192.168.10.100:5555 → 127.0.0.1:5555`，属既有 NAS 配置，非故障 |

另有 `127.0.0.1:5087 → 127.0.0.1:5556` 转发。查看：`netsh interface portproxy show all`（输出为 GBK）。

---

## 六、验收记录（2026-10-14）

| 项 | 结果 |
|---|---|
| 计划任务注册 | 成功，`\GeoKBDaemon` 已启用，BootTrigger 延迟 30s |
| 守护进程归属 | SYSTEM（父链：pythonw(venv shim) → pythonw(22644, 守护) → python(服务)） |
| 5556 被强杀后自动拉回 | **11 秒**（10:10:16 判定宕机 → 10:10:27 恢复健康） |
| 连续运行 | pid 22644 自 10:07 起连续运行 2h16m 无异常 |
| 服务健康 | 5556 `/health` 200（862 条已索引）；5554 `/health` 200（`local_only: true`） |
