# -*- coding: utf-8 -*-
"""GeoKB 端口守护进程 —— 保证 5556 / 5554 常驻。

设计要点
  1. 单实例：state/daemon.lock 独占创建 + PID 存活性校验，避免重复拉起。
  2. 健康判定：TCP 连通 + HTTP GET /health 返回 200，任一失败即视为宕。
  3. 收养而非抢占：端口已被**健康**服务占用时不重复启动；被不明进程占用则告警不硬抢。
  4. 崩溃退避：连续失败按 1,2,4,8…秒退避，上限 60 秒，避免刷屏与资源耗尽。
  5. 退出清理：收到退出信号或异常终止时回收自己拉起的子进程。

用法
  python scripts/geokb_daemon.py run      # 前台守护循环（计划任务调用此命令）
  python scripts/geokb_daemon.py status   # 打印当前状态
  python scripts/geokb_daemon.py stop     # 停止守护进程（不关服务）
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(BASE, "venv", "Scripts", "python.exe")
PYEXE = VENV_PY if os.path.exists(VENV_PY) else sys.executable

STATE = os.path.join(BASE, "state")
LOGDIR = os.path.join(BASE, "logs")
LOCK = os.path.join(STATE, "daemon.lock")
os.makedirs(STATE, exist_ok=True)
os.makedirs(LOGDIR, exist_ok=True)

CHECK_INTERVAL = int(os.environ.get("GEOKB_DAEMON_INTERVAL", "10"))
BOOT_GRACE = int(os.environ.get("GEOKB_DAEMON_GRACE", "25"))   # 启动后等待多久才判定失败
MAX_BACKOFF = 60

# ── 受管服务定义 ────────────────────────────────────────────────────────────
SERVICES = [
    {
        "name": "geokb-api",
        "title": "GeoKB AI API",
        "port": 5556,
        "probe_host": "127.0.0.1",
        "script": os.path.join(BASE, "server", "app.py"),
        "env": {"GEOKB_PORT": "5556", "GEOKB_HOST": "0.0.0.0",
                "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
    },
    {
        "name": "geokb-admin",
        "title": "GeoKB Admin(本地)",
        "port": 5554,
        "probe_host": "127.0.0.1",
        "script": os.path.join(BASE, "server", "admin", "app.py"),
        "env": {"GEOKB_ADMIN_PORT": "5554", "PYTHONUNBUFFERED": "1",
                "PYTHONIOENCODING": "utf-8",
                "GEOKB_ADMIN_SECRET": "geokb-admin-local"},
    },
]


# ── 附加常驻任务（非端口服务，按 pid 文件判活）─────────────────────────────
# 历史文献清单爬取调度器：必须由**守护进程**拉起而不是由终端/agent 会话拉起，
# 否则父进程一退出就被系统 Job 回收（2026-10-18 实测：DETACHED_PROCESS +
# CREATE_BREAKAWAY_FROM_JOB 也照样被回收）。交给 daemon 托管才能真正常驻。
EXTRA_PROCS = [
    {
        "name": "history-queue",
        "title": "历史清单爬取调度器",
        "script": os.path.join(BASE, "scripts", "history_queue_loop.py"),
        "pid_file": os.path.join(STATE, "history_queue_loop.pid"),
        "min_gap": 120,          # 两次拉起的最小间隔（秒），避免启动期重复拉起
    },
]


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    # ⚠️ 必须先落盘再 print：pythonw 下 sys.stdout 无效，print 会抛 OSError 22。
    # 原来 print 在写文件之前，一旦抛异常整条日志就再也不写了（2026-10-26
    # 实测：守护进程从 17:49 起日志全断，看着像死了，其实还在跑）。
    try:
        with open(os.path.join(LOGDIR, "daemon.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


# ── 单实例锁 ────────────────────────────────────────────────────────────────
def pid_alive(pid):
    if pid <= 0:
        return False
    # 2026-10-26：原 ctypes 实现对**已死 pid 也返回 True**（实测 pid=61872，
    # 进程早已不存在），守护进程因此认定队列还在跑、永不重启。改用 psutil
    # 权威判定；拿不到 psutil 时才退回 ctypes，且不再用 GetLastError 猜。
    try:
        import psutil
        return bool(psutil.pid_exists(pid))
    except Exception:
        pass
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED
    if h:
        k.CloseHandle(h)
        return True
    return False


def acquire_lock():
    if os.path.exists(LOCK):
        try:
            old = int(open(LOCK, encoding="utf-8").read().strip() or 0)
        except Exception:
            old = 0
        if old and old != os.getpid() and pid_alive(old):
            return False
        log("发现失效锁(pid=%s)，清理后接管" % old)
        try:
            os.remove(LOCK)
        except Exception:
            pass
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except OSError:
        return False
    return True


def release_lock():
    try:
        if os.path.exists(LOCK):
            cur = open(LOCK, encoding="utf-8").read().strip()
            if cur == str(os.getpid()):
                os.remove(LOCK)
    except Exception:
        pass


# ── 健康检查 ────────────────────────────────────────────────────────────────
def port_listening(host, port, timeout=1.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_health(port, timeout=5.0):
    url = "http://127.0.0.1:%d/health" % port
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            if r.status != 200:
                return False, "HTTP %s" % r.status
            body = json.loads(r.read().decode("utf-8", "ignore"))
            return bool(body.get("ok")), "ok"
    except urllib.error.HTTPError as e:
        # 5554 对非本地来源返回 403，本地调用应为 200；404 说明路由缺失
        return False, "HTTPError %s" % e.code
    except Exception as e:
        return False, type(e).__name__


def healthy(svc):
    if not port_listening(svc["probe_host"], svc["port"]):
        return False, "端口未监听"
    ok, why = http_health(svc["port"])
    return (ok, "正常" if ok else "健康检查失败(%s)" % why)


# ── 进程管理 ────────────────────────────────────────────────────────────────
CREATE_NO_WINDOW = 0x08000000


def start_service(svc):
    env = dict(os.environ)
    env.update(svc["env"])
    out = open(os.path.join(LOGDIR, "%s.out.log" % svc["name"]), "a", encoding="utf-8")
    err = open(os.path.join(LOGDIR, "%s.err.log" % svc["name"]), "a", encoding="utf-8")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out.write("\n===== %s restart by daemon =====\n" % stamp)
    err.write("\n===== %s restart by daemon =====\n" % stamp)
    try:
        p = subprocess.Popen(
            [PYEXE, svc["script"]], cwd=BASE, env=env,
            stdout=out, stderr=err, stdin=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
        return p
    except Exception as e:
        log("[%s] 启动失败: %s" % (svc["name"], e))
        return None


def extra_alive(ex):
    """附加任务是否在跑（pid 文件 + 命令行双重确认）。

    只看 pid 文件不够：进程死后 pid 会被系统复用，一旦复用就会误判「还在跑」
    而永不拉起（2026-10-26 队列停了 4 分钟没人管的直接原因）。
    """
    try:
        pid = int(open(ex["pid_file"], encoding="utf-8").read().strip() or 0)
    except Exception:
        return False
    if pid <= 0 or not pid_alive(pid):
        return False
    try:
        import psutil
        cl = " ".join(psutil.Process(pid).cmdline() or [])
        name = os.path.basename(ex.get("script", ""))
        if name and name not in cl:
            return False          # pid 被别的进程复用了
    except Exception:
        pass
    return True


def start_extra(ex):
    out = open(os.path.join(LOGDIR, "%s.out.log" % ex["name"]), "a", encoding="utf-8")
    try:
        env = dict(os.environ)
        # 子进程 stdout 默认走 GBK，被 utf-8 打开的文件接收会乱码
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUNBUFFERED", "1")
        # 用带控制台的 python.exe（PYEXE）+ CREATE_NO_WINDOW：pythonw.exe 的
        # sys.stdout 是无效句柄，一行 print 就会抛 OSError 打断长任务。
        p = subprocess.Popen(
            [PYEXE, ex["script"]], cwd=BASE, env=env,
            stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
        return p
    except Exception as e:
        log("[%s] 启动失败: %s" % (ex["name"], e))
        return None


def main_loop():
    if not acquire_lock():
        log("已有守护进程在运行，本次退出（避免重复拉起）")
        return 1
    log("=" * 62)
    log("GeoKB 守护进程启动 pid=%s  解释器=%s" % (os.getpid(), PYEXE))
    log("巡检间隔 %ss，受管服务: %s" % (CHECK_INTERVAL, ", ".join(
        "%s(%d)" % (s["name"], s["port"]) for s in SERVICES)))
    procs = {}
    fails = {s["name"]: 0 for s in SERVICES}
    extra_last = {ex["name"]: 0.0 for ex in EXTRA_PROCS}
    try:
        while True:
            # ── 附加常驻任务：不在跑就拉起（限最小间隔，避免启动期重复拉起）
            for ex in EXTRA_PROCS:
                if extra_alive(ex):
                    continue
                now = time.time()
                if now - extra_last.get(ex["name"], 0) < ex.get("min_gap", 120):
                    continue
                log("[%s] 未运行，拉起：%s" % (ex["name"], ex["script"]))
                start_extra(ex)
                extra_last[ex["name"]] = now
                time.sleep(3)     # 等它写 pid 文件

            for svc in SERVICES:
                name, port = svc["name"], svc["port"]
                ok, why = healthy(svc)
                if ok:
                    if fails[name]:
                        log("[%s] 已恢复正常 (%s)" % (name, why))
                    fails[name] = 0
                    continue

                p = procs.get(name)
                if p is not None and p.poll() is None:
                    waited = time.time() - getattr(p, "_t0", 0)
                    if waited < BOOT_GRACE:
                        continue          # 还在启动中，耐心等待
                    log("[%s] 启动超时 %.0fs，杀掉重来" % (name, waited))
                    try:
                        p.kill()
                    except Exception:
                        pass
                    procs[name] = None

                if port_listening(svc["probe_host"], port):
                    log("[%s] 端口 %d 被**其他进程**占用且健康检查不通过，保持观察不硬抢"
                        % (name, port))
                    time.sleep(1)
                    continue

                fails[name] += 1
                backoff = min(2 ** (fails[name] - 1), MAX_BACKOFF)
                log("[%s] 宕机(%s)，第 %d 次重启，退避 %ss" % (name, why, fails[name], backoff))
                time.sleep(backoff)
                np_ = start_service(svc)
                if np_ is not None:
                    np_._t0 = time.time()
                    procs[name] = np_
                    log("[%s] 已拉起 pid=%s" % (name, np_.pid))
            time.sleep(CHECK_INTERVAL)
    except KeyboardInterrupt:
        log("收到中断信号，退出")
    finally:
        for n, p in procs.items():
            if p is not None and p.poll() is None:
                log("回收子进程 [%s] pid=%s" % (n, p.pid))
                try:
                    p.kill()
                except Exception:
                    pass
        release_lock()
        log("守护进程退出")
    return 0


def port_owner(pid_map_needed=True):
    """返回 {port: pid} —— 当前监听 5556/5554 的进程，用于不停机交接与维护停机。"""
    import ctypes
    import ctypes.wintypes as wt
    iphlp = ctypes.WinDLL("iphlpapi", use_last_error=True)
    size = wt.DWORD(0)
    iphlp.GetExtendedTcpTable(None, ctypes.byref(size), False, 2, 5, 0)
    buf = ctypes.create_string_buffer(size.value)
    if iphlp.GetExtendedTcpTable(buf, ctypes.byref(size), False, 2, 5, 0):
        return {}
    import struct as _s
    n = _s.unpack("<I", buf[:4])[0]
    out, off = {}, 4
    want = {s["port"] for s in SERVICES}
    for _ in range(n):
        st, _la, lp, _ra, _rp, pid = _s.unpack("<IIIIII", buf[off:off + 24])
        port = _s.unpack(">H", buf[off + 8:off + 10])[0]
        off += 24
        if st == 2 and port in want:      # LISTENING
            out[port] = pid
    return out


def kill_pid(pid):
    import ctypes
    h = ctypes.windll.kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if not h:
        return False
    r = bool(ctypes.windll.kernel32.TerminateProcess(h, 0))
    ctypes.windll.kernel32.CloseHandle(h)
    return r


def srv_stop():
    """停止守护进程 + 其掌管的两个服务（维护/交接场景用）。"""
    if os.path.exists(LOCK):
        try:
            pid = int(open(LOCK, encoding="utf-8").read().strip())
            if pid != os.getpid() and pid_alive(pid):
                log("停止守护进程 pid=%s" % pid)
                kill_pid(pid)
        except Exception:
            pass
        time.sleep(1)
    owners = port_owner()
    if not owners:
        print("端口 %s 无监听进程" % sorted({s["port"] for s in SERVICES}))
    for port, pid in sorted(owners.items()):
        print("停止端口 %d 的进程 pid=%s -> %s" % (port, pid, "成功" if kill_pid(pid) else "失败"))
    try:
        if os.path.exists(LOCK):
            os.remove(LOCK)
    except Exception:
        pass
    return 0


def status():
    print("=" * 62)
    print("GeoKB 服务状态  %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 62)
    if os.path.exists(LOCK):
        try:
            pid = int(open(LOCK, encoding="utf-8").read().strip())
            print("守护进程: pid=%s %s" % (pid, "运行中" if pid_alive(pid) else "已停止"))
        except Exception:
            print("守护进程: 未识别")
    else:
        print("守护进程: 未运行")
    print("-" * 62)
    for svc in SERVICES:
        ok, why = healthy(svc)
        mark = "✓" if ok else "✗"
        print("  %s %-14s :%-6d %s" % (mark, svc["name"], svc["port"], why))
    print("=" * 62)
    return 0


def stop():
    if not os.path.exists(LOCK):
        print("守护进程未运行")
        return 0
    pid = int(open(LOCK, encoding="utf-8").read().strip())
    if not pid_alive(pid):
        print("守护进程(pid=%s)已停止，清理锁" % pid)
        try:
            os.remove(LOCK)
        except Exception:
            pass
        return 0
    import ctypes
    # CTRL_BREAK 不适用于此场景，直接 TerminateProcess
    h = ctypes.windll.kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if h and ctypes.windll.kernel32.TerminateProcess(h, 0):
        print("已停止守护进程 pid=%s" % pid)
    else:
        print("停止失败")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    fn = {"run": main_loop, "status": status, "stop": stop,
          "srv-stop": srv_stop}.get(cmd, status)
    sys.exit(fn())
