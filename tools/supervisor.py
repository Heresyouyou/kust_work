#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PostNews 服务守护进程 (supervisor)
=================================
常驻监护服务，挂了自动拉起：

  1. flask  → web_app.py        (端口 5555, 门户/翻译/归档/阅读器)
  2. llama  → llama-server.exe  (端口 8090, **仅本地翻译模式**)
  3. tunnel → ssh.exe           (SSH 反向隧道, kust.work 公网通路)

⚠️ 2026-09-17 起 llama 默认**不监护**：翻译引擎已切到云端 DeepSeek API，
   llama-server 没有调用方，继续拉起来只会白占显存、和蒸馏任务抢 GPU。
   是否监护跟随 USE_DEEPSEEK 开关（1=云端→不监护 / 0=本地→监护），
   另有 --with-llama 可强制监护、--no-llama 可强制不监护。

设计要点
--------
* **单实例**: 文件锁 + PID 存活性判定 (ctypes OpenProcess, 不用 psutil ——
  后者遇 AccessDenied 会静默跳过, 表现为"以为没实例其实有")。
* **参数零漂移**: llama 的启动参数从 llama_http_engine import 常量/模型选择函数,
  保证与翻译引擎自己拉起时完全一致 (否则两边参数不一致会互相打架)。
* **引擎开关单一来源**: 「翻译用不用 GPU」与「守护占不占 GPU」共用
  USE_DEEPSEEK, 避免两处各说各话 (翻译切云端了, 守护还在拉 llama)。
* **加载宽限**: llama 冷加载 2.7GB 权重实测可超 1 分钟。进程在但 /health 未通
  → 判定 STARTING 而非 FAILED, 超过 grace 才算失败。否则会陷入
  "加载中→判死→杀掉重来" 的死循环。
* **代理免疫**: 健康检查的 HTTP 请求显式清空 ProxyHandler, 否则 HTTP_PROXY
  环境变量会把 127.0.0.1 的请求也交给代理 (本机经典坑)。
* **停止标记**: 存在 .supervisor_stop 时只监控不拉起 —— 方便手动维护/调试。

用法
----
    python tools/supervisor.py            # 常驻守护 (前台, 计划任务用 pythonw 跑)
    python tools/supervisor.py --once     # 检查一轮就退出 (调试)
    python tools/supervisor.py --status   # 打印状态 JSON
    python tools/supervisor.py --with-llama   # 强制监护 llama (本地翻译调试)
"""
import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# ══════════════════════════════════════════════════════════════
#  路径与常量
# ══════════════════════════════════════════════════════════════
ROOT = Path(__file__).resolve().parent.parent          # D:\PostNews\Translate
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "supervisor.log"
LOCK_FILE = ROOT / ".supervisor.lock"
STOP_FILE = ROOT / ".supervisor_stop"                 # 存在 → 只监控不拉起
QUIT_FILE = ROOT / ".supervisor_quit"                 # 存在 → 守护优雅退出
RESTART_FLAG = ROOT / ".supervisor_restart_flask"     # 存在 → 下次守护启动时强制重启 Flask
RESTART_HTTPREDIR_FLAG = ROOT / ".supervisor_restart_httpredir"  # 存在 → 下次守护启动时强制重启 httpredir
PIDS_FILE = LOG_DIR / "supervisor_pids.json"

CHECK_INTERVAL = 15          # 常规检查间隔 (秒)
FAIL_THRESHOLD = 3           # 连续失败几次才拉起 (避免瞬时抖动误判)
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUPS = 3

# llama 冷加载宽限: 从"进程已起"开始计时, 这段时间内容忍 /health 不通
LLAMA_GRACE = int(os.environ.get("SUPERVISOR_LLAMA_GRACE", "300"))

# 拉起失败后的退避 (秒), 按连续失败次数取, 超出用最后一个
BACKOFF = [10, 30, 60, 120, 300]


def log(msg):
    """写日志。pythonw 下 sys.stdout 是 None, print 会抛 AttributeError → 必须兜住。"""
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        if sys.stdout:
            print(line, flush=True)
    except Exception:
        pass


def _rotate_log():
    try:
        if not LOG_FILE.exists() or LOG_FILE.stat().st_size < LOG_MAX_BYTES:
            return
        for i in range(LOG_BACKUPS - 1, 0, -1):
            src, dst = LOG_FILE.with_suffix(f".log.{i}"), LOG_FILE.with_suffix(f".log.{i+1}")
            if src.exists():
                src.replace(dst)
        LOG_FILE.replace(LOG_FILE.with_suffix(".log.1"))
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
#  进程工具
# ══════════════════════════════════════════════════════════════
STILL_ACTIVE = 259


def pid_alive(pid):
    """判定 PID 是否存活。用 OpenProcess, 不用 psutil (AccessDenied 会静默跳过)。

    ★ 必须二次确认退出码: 进程已终止但内核对象还没销毁时 (别的进程仍持有它的句柄),
      OpenProcess **照样成功** —— 只判句柄会把死进程当活的, 表现为单实例锁被一个
      早已不存在的 PID 占住, 守护再也起不来。退出码 != STILL_ACTIVE(259) 即已死。
    """
    if not pid or pid <= 0:
        return False
    try:
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if k32.GetExitCodeProcess(h, ctypes.byref(code)):
                if code.value != STILL_ACTIVE:
                    return False
        finally:
            k32.CloseHandle(h)
        return True
    except Exception:
        pass
    return False


def kill_pid(pid):
    """强杀。taskkill 在本沙箱会被拦, 直接用 TerminateProcess。"""
    if not pid_alive(pid):
        return False
    try:
        h = ctypes.windll.kernel32.OpenProcess(0x0001, False, int(pid))  # PROCESS_TERMINATE
        if h:
            ctypes.windll.kernel32.TerminateProcess(h, 1)
            ctypes.windll.kernel32.CloseHandle(h)
            return True
    except Exception:
        pass
    return False


def http_probe(url, timeout=3.0):
    """返回 (reachable, status)。

    reachable=True 表示"进程活着且端口在听" —— 即使返回 404/503 也算活着。
    连接被拒/超时 → reachable=False。
    显式清空代理, 否则 HTTP_PROXY 会连 127.0.0.1 也发给代理。
    """
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        r = opener.open(url, timeout=timeout)
        return True, r.status
    except urllib.error.HTTPError as e:
        return True, e.code
    except Exception:
        return False, None


def tcp_probe(host, port, timeout=3.0):
    """TCP 连通性探测 (给不做 HTTP 探活的服务用, 如 TLS 终止层)。"""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, None
    except Exception:
        return False, None


# ══════════════════════════════════════════════════════════════
#  PID 记录 (守护重启后仍能认领上次拉起的子进程)
# ══════════════════════════════════════════════════════════════
def _load_pids():
    try:
        return json.loads(PIDS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_pids(d):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        PIDS_FILE.write_text(json.dumps(d, indent=2), encoding="utf-8")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
#  服务定义
# ══════════════════════════════════════════════════════════════
def python_exe():
    """用 python.exe 而非 pythonw.exe —— 子进程会用日志重定向, pythonw 的
    sys.stdout=None 会让 print 抛 AttributeError。"""
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        exe = exe[: -len("pythonw.exe")] + "python.exe"
    return exe


def child_env():
    """子进程环境 —— 必须强制 UTF-8!

    踩过的坑: web_app.py 里大量 print 带 emoji (✅ ❌ ⚠️), 而 stdout 被重定向到
    文件时 Python 按 Windows 默认 locale 用 **GBK** 编码 →
    `UnicodeEncodeError: 'gbk' codec can't encode '\u2705'` → 进程刚起来就崩,
    表现为"守护反复拉起 Flask 但端口永远起不来"。
    项目里其它计划任务(PostNews-JournalTitleZH)也用同样的 PYTHONIOENCODING 处理。
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


class Service:
    def __init__(self, name, port, health_url, grace=0):
        self.name = name
        self.port = port
        self.health_url = health_url
        self.grace = grace            # 启动宽限 (秒)
        self.fail_count = 0
        self.started_at = 0.0         # 本轮拉起时刻
        self.pid = (_load_pids().get(name) or {}).get("pid")

    # -- 状态 ------------------------------------------------
    def probe(self):
        """返回 'ready' | 'starting' | 'down'"""
        ok, status = http_probe(self.health_url, timeout=3.0)
        if ok and status == 200:
            return "ready", status
        if ok:
            # 端口在听但没就绪 (llama 加载中会 503)
            return "starting", status
        # 端口不通: 进程还在 → 算启动中; 进程没了 → down
        if self.pid and pid_alive(self.pid):
            return "starting", None
        return "down", None

    def is_down_too_long(self):
        """启动宽限内不认定失败"""
        if not self.started_at:
            return False
        return (time.time() - self.started_at) < self.grace

    def record_pid(self, pid):
        self.pid = pid
        d = _load_pids()
        d[self.name] = {"pid": pid, "started_at": time.time(),
                        "started_at_str": f"{datetime.now():%Y-%m-%d %H:%M:%S}"}
        _save_pids(d)

    def clear_pid(self):
        self.pid = None
        d = _load_pids()
        d.pop(self.name, None)
        _save_pids(d)


class FlaskService(Service):
    def __init__(self):
        super().__init__("flask", 5555, "http://127.0.0.1:5555/", grace=60)

    def start(self):
        """拉起 web_app.py。stdout/stderr 重定向到日志, 脱离守护进程独立存活。"""
        log_fp = LOG_DIR / "flask.out.log"
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        f = open(log_fp, "a", encoding="utf-8", errors="replace")
        # 清掉陈旧 PID 锁: web_app 启动时会读 .flask_5555.pid,
        # 旧进程已死但锁文件还在 → 它会自己清理, 但保险起见这里也清
        pid_file = ROOT / ".flask_5555.pid"
        try:
            if pid_file.exists():
                old = int(pid_file.read_text().strip())
                if not pid_alive(old):
                    pid_file.unlink(missing_ok=True)
                    log(f"[flask] 清理陈旧 PID 锁 (pid={old} 已死)")
        except Exception:
            try:
                pid_file.unlink(missing_ok=True)
            except Exception:
                pass

        p = subprocess.Popen(
            [python_exe(), str(ROOT / "web_app.py")],
            cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT, env=child_env(),
            creationflags=0x00000008 | 0x00000200,   # DETACHED_PROCESS | NEW_PROCESS_GROUP
            close_fds=True,
        )
        self.record_pid(p.pid)
        log(f"[flask] 已拉起 pid={p.pid} (日志 {log_fp.name})")
        return p.pid


class LlamaService(Service):
    def __init__(self):
        super().__init__("llama", 8090, "http://127.0.0.1:8090/health", grace=LLAMA_GRACE)

    def _build_cmd(self):
        """构造 llama-server 命令行 —— 参数与 llama_http_engine.start_server 保持一致。

        从引擎 import 常量而不是抄一份, 避免以后改了一处忘了另一处
        (两边参数不一致会导致引擎认为 server 不合规、杀掉重启)。
        """
        sys.path.insert(0, str(ROOT))
        import llama_http_engine as E   # noqa: E402

        model = E._find_model()         # 模型优先级由引擎决定 (4B-Q4 优先)
        return [
            str(E.LLAMA_SERVER),
            "-m", str(model),
            "-ngl", "-1",
            "-c", str(E.N_CTX),
            "-t", str(E.N_PARALLEL),
            "-b", "2048",
            "-ub", "512",
            "-cb",
            "--parallel", str(E.N_PARALLEL),
            "--kv-unified",
            "--cache-prompt", "--reasoning", "off",
            "--cache-idle-slots",
            "--host", "127.0.0.1",
            "--port", str(E.SERVER_PORT),
        ], model

    def _env(self):
        """CUDA DLL 注入: 与引擎一致, 把 torch/lib 前置到 PATH"""
        env = child_env()
        try:
            import torch
            env["PATH"] = os.path.join(os.path.dirname(torch.__file__), "lib") + \
                os.pathsep + env.get("PATH", "")
        except Exception:
            pass
        return env

    def start(self):
        try:
            cmd, model = self._build_cmd()
        except Exception as e:
            log(f"[llama] ❌ 构造命令失败: {e}")
            return None

        log_fp = LOG_DIR / "llama.out.log"
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        f = open(log_fp, "a", encoding="utf-8", errors="replace")
        try:
            p = subprocess.Popen(
                cmd, cwd=str(ROOT / "llama-cpp"),
                stdout=f, stderr=subprocess.STDOUT, env=self._env(),
                creationflags=0x00000008 | 0x00000200,
                close_fds=True,
            )
        except Exception as e:
            log(f"[llama] ❌ Popen 失败: {e}")
            return None
        self.record_pid(p.pid)
        log(f"[llama] 已拉起 pid={p.pid} 模型={model.name} "
            f"-c={cmd[cmd.index('-c')+1]} parallel={cmd[cmd.index('--parallel')+1]} "
            f"(冷加载约 1-2 分钟, 宽限 {self.grace}s)")
        return p.pid


# ══════════════════════════════════════════════════════════════
#  SSH 反向隧道 (kust.work 公网通路)
# ══════════════════════════════════════════════════════════════
# 公网访问链路:
#   kust.work → <server-ip>:80 nginx(basic auth) → 127.0.0.1:7081
#             → 【本隧道】 → 本机 127.0.0.1:5555 Flask
#
# 为什么不用 frp: frpc 二进制被 Windows Defender 当 PUA 直接删掉 (本机非管理员,
# 加不了排除项), 且云安全组的 frps 7000 端口没放行。SSH 走已放行的 22, 零额外依赖。
#
# ★ 绑定地址必须显式写 127.0.0.1 —— 服务器 sshd 是 GatewayPorts=yes, 反向隧道
#   默认会绑到 0.0.0.0, 那样公网就能直连 7081 绕过 nginx 的密码。显式指定回环地址时
#   sshd 会尊重客户端的绑定请求。
SSH_EXE = (os.environ.get("KUST_TUNNEL_SSH") or shutil.which("ssh")
           or r"C:\Windows\System32\OpenSSH\ssh.exe")
SSH_KEY = os.environ.get("KUST_TUNNEL_KEY") or str(Path.home() / ".ssh" / "kustwork_txymac.pem")
SSH_HOST = os.environ.get("KUST_TUNNEL_HOST", "root@<server-ip>")
TUNNEL_REMOTE_PORT = int(os.environ.get("KUST_TUNNEL_REMOTE_PORT", "7081"))
TUNNEL_LOCAL_PORT = int(os.environ.get("KUST_TUNNEL_LOCAL_PORT", "5555"))
TUNNEL_AUDIT_INTERVAL = 600      # 远端端口核对间隔 (秒)
CREATE_NO_WINDOW = 0x08000000    # pythonw 下不给 ssh.exe 弹黑框


class TunnelService(Service):
    def __init__(self):
        super().__init__("tunnel", TUNNEL_LOCAL_PORT, None, grace=30)
        self._last_audit = 0.0

    def _ssh_opts(self):
        return [
            "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ExitOnForwardFailure=yes",   # 转发建不起来就退出, 不许假装活着
            "-o", "ConnectTimeout=15",
            "-o", "ServerAliveInterval=20",
            "-o", "ServerAliveCountMax=3",      # 断网/服务器不可达 ~60s 内自行退出
            "-o", "TCPKeepAlive=yes",
        ]

    def _audit_once(self):
        """核对远端 127.0.0.1:7081 是否真的在监听。

        进程活着 ≠ 隧道没断 (远端端口可能已释放)。每 10 分钟花一次 ssh 连接戳一下,
        比"本地看进程还在"可信得多 —— 否则隧道断了会一直 502 却没人发现。
        """
        cmd = [SSH_EXE, "-i", SSH_KEY] + self._ssh_opts() + [
            SSH_HOST,
            f"ss -lntH | awk '{{print $4}}' | grep -qx '127.0.0.1:{TUNNEL_REMOTE_PORT}'",
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=25,
                               creationflags=CREATE_NO_WINDOW)
            return r.returncode == 0
        except Exception:
            return False

    def probe(self):
        if not (self.pid and pid_alive(self.pid)):
            return "down", None
        now = time.time()
        if now - self._last_audit >= TUNNEL_AUDIT_INTERVAL:
            self._last_audit = now
            if not self._audit_once():
                log(f"[tunnel] ⚠️ ssh 进程在, 但远端 127.0.0.1:{TUNNEL_REMOTE_PORT} "
                    f"未监听 → 杀掉重连")
                return "down", None
        return "ready", None

    def start(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        f = open(LOG_DIR / "tunnel.out.log", "a", encoding="utf-8", errors="replace")
        cmd = [SSH_EXE, "-i", SSH_KEY, "-N"] + self._ssh_opts() + [
            "-R", f"127.0.0.1:{TUNNEL_REMOTE_PORT}:127.0.0.1:{TUNNEL_LOCAL_PORT}",
            SSH_HOST,
        ]
        try:
            p = subprocess.Popen(
                cmd, stdout=f, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW | 0x00000200, close_fds=True,
            )
        except Exception as e:
            log(f"[tunnel] ❌ Popen 失败: {e}")
            return None
        self.record_pid(p.pid)
        self._last_audit = time.time()      # 下一次核对推到 10 分钟后
        log(f"[tunnel] 已拉起 pid={p.pid} "
            f"隧道 服务器127.0.0.1:{TUNNEL_REMOTE_PORT} → 本机127.0.0.1:{TUNNEL_LOCAL_PORT}")
        return p.pid


# ══════════════════════════════════════════════════════════════
#  本地 TLS 终止层 (ChmlFrp https 隧道的本地 HTTPS 入口)
# ══════════════════════════════════════════════════════════════
# ChmlFrp 的 https 隧道是 **TLS 透传**(frps 只按 SNI 路由, 不终止 TLS), 证书必须挂在
# 本地站点上; 而本地上游只讲明文 HTTP。本层用对应域名证书在本地终结 TLS, 再把解密
# 后的字节转发给上游明文端口。frpc.ini 里该隧道的 local_port 须指向本层端口。
# 两个域名各起一个实例, 同一个 tls_front.py, 靠 TLS_FRONT_* 环境变量区分:
#   · open.kust.work → 监听 5443, 上游 5555, 证书 SSL\open.kust.work\
#   · sjck.kust.work → 监听 5087, 上游 5556(GeoKB), 证书 SSL\sjck.kust.work\
#     （2026-10-15 起由 lib.kust.work 切换而来：lib 易被外部 AI 误判为
#      "昆明理工大学图书馆"域名。环境变量名保留 TLS_LIB_* 以兼容既有部署。）
TLS_FRONT_PORT = int(os.environ.get("TLS_FRONT_PORT", "5443"))
TLS_FRONT_CERT = Path(os.environ.get(
    "TLS_FRONT_CERT", str(ROOT.parent / "SSL" / "open.kust.work" / "fullchain.pem")))
TLS_LIB_PORT = int(os.environ.get("TLS_LIB_PORT", "5087"))
TLS_LIB_UPSTREAM = int(os.environ.get("TLS_LIB_UPSTREAM", "5556"))
TLS_LIB_CERT = Path(os.environ.get(
    "TLS_LIB_CERT", str(ROOT.parent / "SSL" / "sjck.kust.work" / "fullchain.pem")))


class TlsFrontService(Service):
    """TLS 终止层: 端口在听即视为 ready (不做 HTTP 探活 —— 探它要过 TLS 握手)。"""

    def __init__(self, name="tlsfront", port=None, cert=None, upstream=5555,
                 label="open.kust.work"):
        self.label = label
        self.cert = Path(cert or TLS_FRONT_CERT)
        self.key = self.cert.parent / "privkey.pem"
        self.upstream = upstream
        super().__init__(name, port or TLS_FRONT_PORT, None, grace=10)

    def probe(self):
        ok, _ = tcp_probe("127.0.0.1", self.port, timeout=3.0)
        return ("ready" if ok else "down"), None

    def start(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        f = open(LOG_DIR / f"{self.name}.out.log", "a", encoding="utf-8", errors="replace")
        env = child_env()
        env["TLS_FRONT_PORT"] = str(self.port)
        env["TLS_FRONT_UPSTREAM"] = str(self.upstream)
        env["TLS_FRONT_CERT"] = str(self.cert)
        env["TLS_FRONT_KEY"] = str(self.key)
        try:
            p = subprocess.Popen(
                [python_exe(), str(ROOT / "tools" / "tls_front.py")],
                cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT, env=env,
                creationflags=0x00000008 | 0x00000200,   # DETACHED_PROCESS | NEW_PROCESS_GROUP
                close_fds=True,
            )
        except Exception as e:
            log(f"[{self.name}] ❌ Popen 失败: {e}")
            return None
        self.record_pid(p.pid)
        log(f"[{self.name}] 已拉起 pid={p.pid} TLS 127.0.0.1:{self.port} → "
            f"127.0.0.1:{self.upstream} ({self.label})")
        return p.pid


# ══════════════════════════════════════════════════════════════
#  open.kust.work http→https 302 跳转层 (浏览器输入域名先走 80 时顶到 HTTPS)
# ══════════════════════════════════════════════════════════════
# ChmlFrp 的 http 隧道需要一个本地明文端点; 本服务对任意请求回 302 到
# https://open.kust.work。与 https 链路 (tls_front:5443) 无关。
HTTP_REDIRECT_PORT = int(os.environ.get("HTTP_REDIRECT_PORT", "5080"))


class HttpRedirectService(Service):
    """http→https 302 层: 端口在听即视为 ready (纯明文, 不做 HTTP 探活)。"""

    def __init__(self):
        super().__init__("httpredir", HTTP_REDIRECT_PORT, None, grace=10)

    def probe(self):
        ok, _ = tcp_probe("127.0.0.1", HTTP_REDIRECT_PORT, timeout=3.0)
        return ("ready" if ok else "down"), None

    def start(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        f = open(LOG_DIR / "httpredirect.out.log", "a", encoding="utf-8", errors="replace")
        try:
            p = subprocess.Popen(
                [python_exe(), str(ROOT / "tools" / "http_redirect.py")],
                cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT, env=child_env(),
                creationflags=0x00000008 | 0x00000200,   # DETACHED_PROCESS | NEW_PROCESS_GROUP
                close_fds=True,
            )
        except Exception as e:
            log(f"[httpredir] ❌ Popen 失败: {e}")
            return None
        self.record_pid(p.pid)
        log(f"[httpredir] 已拉起 pid={p.pid} http 127.0.0.1:{HTTP_REDIRECT_PORT} → 302 https://open.kust.work")
        return p.pid


# ══════════════════════════════════════════════════════════════
#  证书同步 (云服务器 → NAS 内网 HTTPS 入口)
# ══════════════════════════════════════════════════════════════
# 公网入口 (kust.work:443 / www.kust.work) 用云服务器上 acme.sh (DNS-01) 自动续期的
# Let's Encrypt 证书, 那个目录不动。校园内网 HTTPS 走的是 NAS 上那份拷贝
# (NAS:443, 见 /etc/config/apache/extra/apache-kust-ssl.conf), 用的是**腾讯云证书**
# (2026-10-10 起, 由用户从腾讯云控制台签发, 到期前手动重签), 放在云服务器的
# /etc/kustwork-tencent/ 作为同步源。这里定期比对两端的 SHA256, 变了才传输 +
# graceful reload —— 平时零副作用, 且不会把 NAS 上的腾讯证书覆盖回 LE。
# ★ 时间对齐: 只在指纹不同时动 NAS, 避免每次检查都重载 QTS 的 Web 服务。
CERT_SYNC_INTERVAL = int(os.environ.get("KUST_CERT_SYNC_INTERVAL", 6 * 3600))
CERT_SRC = os.environ.get("KUST_CERT_SRC", "root@<server-ip>")
CERT_SRC_DIR = os.environ.get("KUST_CERT_SRC_DIR", "/etc/kustwork-tencent")
CERT_NAS_HOST = os.environ.get("KUST_NAS_HOST", "<lan-ip>")
CERT_NAS_USER = os.environ.get("KUST_NAS_USER", "<redacted-secret>")
CERT_NAS_PASS = os.environ.get("KUST_NAS_PASS", "<redacted-secret>")
CERT_NAS_DIR = "/etc/config/apache"
# 源文件名 → NAS 上的落盘名 (权限: 链 644 / 私钥 600)
CERT_FILES = {"fullchain.pem": ("kust.work-fullchain.pem", 0o644),
              "privkey.pem":   ("kust.work-privkey.pem",   0o600)}
CERT_NAS_RELOAD = ("/usr/local/apache/bin/apache -k graceful "
                   "-c 'PidFile /var/lock/apache.pid' "
                   "-f /etc/config/apache/apache.conf")


def sync_cert_to_nas():
    """比对并同步证书。返回 (changed, message)。"""
    try:
        import hashlib
        import paramiko
    except Exception as e:
        return False, f"paramiko 不可用: {e}"

    # ── 1) 云服务器侧读取 ──
    user, _, host = CERT_SRC.rpartition("@")
    try:
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(host or CERT_SRC, username=user or "root",
                  key_filename=SSH_KEY, timeout=15)
        remote = {}
        for name in CERT_FILES:
            _, so, _ = c.exec_command(f"cat {CERT_SRC_DIR}/{name}", timeout=15)
            data = so.read()
            if so.channel.recv_exit_status() != 0 or b"BEGIN" not in data:
                c.close()
                return False, f"云服务器读取 {name} 失败"
            remote[name] = data
        c.close()
    except Exception as e:
        return False, f"连云服务器失败: {e}"

    # ── 2) 与 NAS 侧比对 ──
    try:
        n = paramiko.SSHClient()
        n.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        n.connect(CERT_NAS_HOST, username=CERT_NAS_USER,
                  password=CERT_NAS_PASS, timeout=15)
        sf = n.open_sftp()

        stale = []
        for name, (target, _mode) in CERT_FILES.items():
            path = f"{CERT_NAS_DIR}/{target}"
            try:
                with sf.open(path, "rb") as f:
                    cur = f.read()
            except IOError:
                cur = b""            # NAS 上还没有 → 视为需同步
            if hashlib.sha256(cur).hexdigest() != hashlib.sha256(remote[name]).hexdigest():
                stale.append((name, target, path))

        if not stale:
            sf.close()
            n.close()
            return False, "两端指纹一致, 无需同步"

        for name, target, path in stale:
            with sf.open(path, "wb") as f:
                f.write(remote[name])
            sf.chmod(path, CERT_FILES[name][1])
        sf.close()

        # ── 3) 重载 QTS Web 服务 (graceful: 不断现有连接) ──
        _, so, se = n.exec_command(CERT_NAS_RELOAD, timeout=40)
        rc = so.channel.recv_exit_status()
        n.close()
        names = ", ".join(t for _n, t, _p in stale)
        if rc != 0:
            return True, f"已推送 {names} 但 reload 失败 rc={rc} {se.read().decode('utf-8', 'replace')[:200]}"
        return True, f"已同步 {names} 并 reload NAS:443"
    except Exception as e:
        return False, f"NAS 同步失败: {e}"


# ══════════════════════════════════════════════════════════════
#  单实例锁
# ══════════════════════════════════════════════════════════════
def acquire_lock():
    """保证只有一个守护在跑。返回 True 表示本进程拿到了锁。"""
    if LOCK_FILE.exists():
        try:
            old = int(LOCK_FILE.read_text(encoding="utf-8").strip())
            if pid_alive(old) and old != os.getpid():
                log(f"[锁] 已有守护在跑 (pid={old}) → 本进程退出")
                return False
            log(f"[锁] 发现陈旧锁 pid={old} (已死) → 接管")
        except Exception:
            pass
    LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    return True


def release_lock():
    try:
        if LOCK_FILE.exists():
            if LOCK_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
                LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
#  主循环
# ══════════════════════════════════════════════════════════════
def check_and_heal(svc, allow_start=True):
    """检查一个服务, 必要时拉起。返回状态字符串。"""
    state, status = svc.probe()

    if state == "ready":
        if svc.fail_count:
            log(f"[{svc.name}] ✅ 恢复正常")
        svc.fail_count = 0
        svc.started_at = 0.0
        return state

    if state == "starting":
        # 进程在、端口在听但没就绪 (或还在加载)
        if not svc.started_at:
            # 首次见到"进程在但未就绪" → 从现在开始计宽限。
            # ★ 不设这个会把 started_at=0 当成"已等了 1.7e9 秒", 立刻判超宽限,
            #   守护一重启就把健康的 llama 杀掉重来 (白等 2 分钟加载)。
            svc.started_at = time.time()
            log(f"[{svc.name}] ⏳ 发现进程在跑但未就绪 → 给 {svc.grace}s 宽限")
        if svc.is_down_too_long():
            return state                      # 宽限期内, 不打扰
        log(f"[{svc.name}] ⏳ 超过宽限 {svc.grace}s 仍未就绪 → 判定失败 (status={status})")
        state = "down"

    # state == "down"
    if not allow_start:
        return state                          # 停止标记下只记录, 不计数
    svc.fail_count += 1
    log(f"[{svc.name}] ⚠️ 不可用 (连续失败 {svc.fail_count}/{FAIL_THRESHOLD})")

    if not allow_start:
        return state
    if svc.fail_count < FAIL_THRESHOLD:
        return state

    # 达到阈值 → 拉起。先清理可能半死的旧进程
    if svc.pid and pid_alive(svc.pid):
        log(f"[{svc.name}] 清理半死进程 pid={svc.pid}")
        kill_pid(svc.pid)
        time.sleep(2)
    svc.clear_pid()

    backoff = BACKOFF[min(svc.fail_count - FAIL_THRESHOLD, len(BACKOFF) - 1)]
    if svc.fail_count > FAIL_THRESHOLD:
        log(f"[{svc.name}] 退避 {backoff}s 后重试")
        time.sleep(backoff)

    svc.started_at = time.time()
    svc.fail_count = 0                # 重置, 下一轮重新计数
    svc.start()
    return "restarting"


def main():
    ap = argparse.ArgumentParser(description="PostNews 服务守护进程")
    ap.add_argument("--once", action="store_true", help="检查一轮后退出")
    ap.add_argument("--status", action="store_true", help="打印状态 JSON 后退出")
    ap.add_argument("--no-llama", action="store_true", help="不监护 llama-server")
    ap.add_argument("--with-llama", action="store_true",
                    help="强制监护 llama-server（即使翻译已切云端，调试用）")
    ap.add_argument("--no-tunnel", action="store_true", help="不监护 SSH 反向隧道")
    ap.add_argument("--sync-cert", action="store_true",
                    help="只把 kust.work 证书同步到 NAS 内网入口, 然后退出")
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if args.sync_cert:
        changed, msg = sync_cert_to_nas()
        print(f"{'🔄 ' if changed else '✓ '}[cert] {msg}")
        return 0

    # ── 是否监护 llama：由翻译引擎开关推导 ──
    # 2026-09-17: 翻译默认切到云端 DeepSeek 后, llama-server 已无调用方。
    # 若守护进程仍按老逻辑把它拉起来, 会白占一大块显存、和蒸馏任务打架;
    # 更糟的是没人看得出「为什么显存被吃掉」。所以这里跟随同一个开关,
    # 保证「翻译不用 GPU」与「守护不占 GPU」不会各说各话。
    #   USE_DEEPSEEK=1 (默认) → 不监护 llama
    #   USE_DEEPSEEK=0        → 监护 llama（本地翻译需要它常驻）
    _use_ds = os.environ.get("USE_DEEPSEEK", "1") == "1"
    _want_llama = args.with_llama or not _use_ds
    if args.no_llama:
        _want_llama = False
    _llama_why = (
        "强制 --with-llama" if args.with_llama and _want_llama
        else ("USE_DEEPSEEK=0 → 本地翻译需要 llama 常驻" if _want_llama
              else ("显式 --no-llama" if args.no_llama
                    else "翻译走云端 DeepSeek, GPU 让给蒸馏"))
    )

    services = [FlaskService()]
    if _want_llama:
        services.append(LlamaService())

    # ── kust.work 公网通路: SSH 反向隧道 ──
    # 私钥不在 (换了机器 / 从公开仓库 clone 下来) 就跳过 —— 不影响 Flask 本身的守护
    if args.no_tunnel:
        _tunnel_why = "显式 --no-tunnel"
    elif not Path(SSH_KEY).exists():
        _tunnel_why = f"未找到私钥 {SSH_KEY}"
    else:
        _tunnel_why = ""
        services.append(TunnelService())

    # ── open.kust.work 本地 TLS 终止层 (HTTPS 入口) ──
    # 证书不在就跳过(不影响其它服务); frpc.ini 的 local_port 指向它(5443)。
    if TLS_FRONT_CERT.exists():
        _tlsfront_why = ""
        services.append(TlsFrontService())
    else:
        _tlsfront_why = f"未找到证书 {TLS_FRONT_CERT}"

    # ── sjck.kust.work 本地 TLS 终结层 (HTTPS 入口, 上游 GeoKB 5556) ──
    # 证书不在就跳过; frpc.ini 的 SJCK_KUST_WORK local_port 指向它(5087)。
    # 2026-10-15 起由 lib.kust.work 切换（lib 易被误判为"昆明理工大学图书馆"域名）。
    if TLS_LIB_CERT.exists():
        _tlslib_why = ""
        services.append(TlsFrontService(name="tlsfrontlib", port=TLS_LIB_PORT,
                                        cert=TLS_LIB_CERT, upstream=TLS_LIB_UPSTREAM,
                                        label="sjck.kust.work"))
    else:
        _tlslib_why = f"未找到证书 {TLS_LIB_CERT}"

    # ── open.kust.work http→https 302 跳转层 (ChmlFrp http 隧道的本地端点) ──
    services.append(HttpRedirectService())

    if args.status:
        out = {}
        for s in services:
            st, code = s.probe()
            out[s.name] = {"state": st, "http": code, "pid": s.pid,
                           "pid_alive": pid_alive(s.pid) if s.pid else False}
        out["stopped"] = STOP_FILE.exists()
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if not acquire_lock():
        return 0

    log("=" * 60)
    log(f"守护进程启动 pid={os.getpid()}  python={python_exe()}")
    log(f"监护: {', '.join(s.name for s in services)}   检查间隔 {CHECK_INTERVAL}s")
    if _want_llama:
        log(f"[llama] 纳入监护 —— 原因: {_llama_why}")
    else:
        log(f"[llama] ⏭️  不监护 llama-server —— 原因: {_llama_why}")
        log("[llama]    如需恢复本地翻译: 设 USE_DEEPSEEK=0 后重启守护,")
        log("[llama]    或临时用 --with-llama 强制监护。")
    if _tunnel_why:
        log(f"[tunnel] ⏭️  不监护 SSH 反向隧道 —— 原因: {_tunnel_why}")
    else:
        log(f"[tunnel] 纳入监护 —— 服务器 127.0.0.1:{TUNNEL_REMOTE_PORT} "
            f"→ 本机 127.0.0.1:{TUNNEL_LOCAL_PORT}")
    if _tlsfront_why:
        log(f"[tlsfront] ⏭️  不监护 TLS 终止层 —— 原因: {_tlsfront_why}")
    else:
        log(f"[tlsfront] 纳入监护 —— TLS 127.0.0.1:{TLS_FRONT_PORT} → 本机 127.0.0.1:5555")
    if _tlslib_why:
        log(f"[tlsfrontlib] ⏭️  不监护 sjck TLS 终止层 —— 原因: {_tlslib_why}")
    else:
        log(f"[tlsfrontlib] 纳入监护 —— TLS 127.0.0.1:{TLS_LIB_PORT} "
            f"→ 本机 127.0.0.1:{TLS_LIB_UPSTREAM} (sjck.kust.work)")
    log(f"停止标记: {STOP_FILE} (存在时只监控不拉起)")
    log(f"退出标记: {QUIT_FILE} (存在时优雅退出, 用于停掉提升权限的守护)")
    log("=" * 60)

    # ── 一次性 Flask 重启标记 ──
    # 背景: 守护以 Highest 权限拉起的 Flask, 普通权限 shell 杀不掉
    # (taskkill/Stop-Process 均 Access Denied), 改完 web_app.py 或 modules/**
    # 的代码后没有无提权的重启通道。用法: touch 本标记 + .supervisor_quit
    # 让旧守护退出, 再触发计划任务/手动启动新守护 → 这里借守护的手杀旧
    # Flask 并立即拉起新代码实例。
    if RESTART_FLAG.exists():
        try:
            RESTART_FLAG.unlink()
        except Exception:
            pass
        old = None
        try:
            old = int((ROOT / ".flask_5555.pid").read_text(encoding="utf-8").strip())
        except Exception:
            pass
        if old and pid_alive(old):
            log(f"[flask] 🔄 重启标记: 杀旧实例 pid={old}")
            kill_pid(old)
            time.sleep(2)
        else:
            log("[flask] 🔄 重启标记: 旧实例已不在")
        try:
            (ROOT / ".flask_5555.pid").unlink(missing_ok=True)
        except Exception:
            pass
        flask_svc = next((s for s in services if isinstance(s, FlaskService)), None)
        if flask_svc:
            flask_svc.started_at = time.time()
            flask_svc.start()
            log("[flask] 🔄 已按重启标记拉起新实例")

    # ── 一次性 httpredir 重启标记 ──
    # httpredir 由本守护(提权)拉起, 普通权限 shell 杀不掉 → 改代码后没有无提权的
    # 重启通道。用法同 Flask: touch 本标记 + .supervisor_quit, 旧守护退出、计划任务
    # 拉起新守护 → 这里借守护(提权)的手杀旧实例并立即拉起新代码。
    if RESTART_HTTPREDIR_FLAG.exists():
        try:
            RESTART_HTTPREDIR_FLAG.unlink()
        except Exception:
            pass
        redir_svc = next((s for s in services if isinstance(s, HttpRedirectService)), None)
        if redir_svc:
            if redir_svc.pid and pid_alive(redir_svc.pid):
                log(f"[httpredir] 🔄 重启标记: 杀旧实例 pid={redir_svc.pid}")
                kill_pid(redir_svc.pid)
                time.sleep(2)
            else:
                log("[httpredir] 🔄 重启标记: 旧实例已不在")
            redir_svc.clear_pid()
            redir_svc.started_at = time.time()
            redir_svc.start()
            log("[httpredir] 🔄 已按重启标记拉起新实例")

    # 守护刚启动: 认领上一任守护拉起、仍在跑的进程, 并给宽限
    for s in services:
        if s.pid and pid_alive(s.pid):
            s.started_at = time.time()
            log(f"[{s.name}] 认领已运行进程 pid={s.pid} (宽限 {s.grace}s)")

    t0 = time.time()
    tick = 0
    next_cert_sync = 0.0          # 启动后立刻比对一次, 之后每 CERT_SYNC_INTERVAL
    try:
        while True:
            _rotate_log()
            tick += 1

            # 优雅退出: 计划任务以 Highest 权限启动本进程时, 普通权限杀不掉它
            # (OpenProcess 成功但 TerminateProcess 被拒 / Stop-ScheduledTask 只结束"任务"
            #  而不结束进程)。所以留一个文件信号, touch 一下即可让它自己退出。
            if QUIT_FILE.exists():
                log("🛑 检测到退出标记 (.supervisor_quit) → 优雅退出")
                try:
                    QUIT_FILE.unlink()
                except Exception:
                    pass
                break

            stopped = STOP_FILE.exists()
            if stopped and not getattr(main, "_stop_logged", False):
                log("⏸ 检测到停止标记 → 只监控, 不拉起")
                main._stop_logged = True
            if not stopped:
                main._stop_logged = False

            summary = []
            for s in services:
                try:
                    summary.append(f"{s.name}={check_and_heal(s, allow_start=not stopped)}")
                except Exception as e:
                    summary.append(f"{s.name}=ERR")
                    log(f"[{s.name}] 检查异常: {e}")

            # 心跳: 5 分钟一条, 便于长期挂机时确认守护还活着
            if tick % 20 == 1:
                up = int(time.time() - t0)
                log(f"💓 心跳 uptime={up // 3600}h{up % 3600 // 60}m  " + "  ".join(summary) +
                    ("  [STOPPED]" if stopped else ""))

            # 证书同步: 云服务器续期后, 把 NAS 内网入口那份也换掉 (指纹相同则不动)
            if time.time() >= next_cert_sync:
                next_cert_sync = time.time() + CERT_SYNC_INTERVAL
                try:
                    changed, msg = sync_cert_to_nas()
                    log(f"[cert] {'🔄 ' if changed else ''}{msg}")
                except Exception as e:
                    log(f"[cert] 异常: {e}")

            if args.once:
                break
            time.sleep(CHECK_INTERVAL)
    except KeyboardInterrupt:
        log("收到中断信号, 退出")
    finally:
        release_lock()
        log("守护进程退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
