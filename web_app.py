"""
GeoTranslate Web v4 — 原生 iframe PDF + 云端 DeepSeek / 本地 llama.cpp 双引擎翻译
启动: py -3.12 web_app.py
打开: http://127.0.0.1:5555
"""
# ═══════════════════════════════════════════════════════════
# 必须在 import cv2 / numpy / torch 之前! 禁 OpenCL 防硬崩
# (OpenCV 5.0 + 大图 → CL_OUT_OF_RESOURCES → terminate() 杀进程)
# ═══════════════════════════════════════════════════════════
import os as _os_early
_os_early.environ['OPENCV_OPENCL_RUNTIME'] = 'disabled'
_os_early.environ['OPENCV_OPENCL_DEVICE'] = 'disabled'

# ═══════════════════════════════════════════════════════════
# 原生崩溃取证 —— 必须早于 onnxruntime / torch / cv2 加载
# 2026-09-29：翻译跑到约 2 分钟时进程被 NVIDIA 用户态驱动带走。
#   WER 报告：故障模块 nvwgf2umx.dll / 异常码 c0000005(访问违规) / 偏移 0x12a9d7
#              （2026-09-28 10:28 的崩溃是同签名，两处逐字段一致）
# 原生崩溃不产生 Python traceback，日志只是「凭空断掉」，事后完全无从定位 ——
# 这正是本次排查绕远路的原因。faulthandler 在 Windows 上会接管访问违规等 Win32
# 异常，把**崩溃当时的 Python 调用栈**落盘，是唯一能把「哪个 Python 调用踩到驱动」
# 钉死的手段。
# 单独落一个文件（不混进 flask.out.log）：stdout 重定向到文件时是块缓冲，
# 崩溃瞬间缓冲区里的内容会整段丢失，取证不能用它。
# ═══════════════════════════════════════════════════════════
try:
    import faulthandler as _fh
    _fh_dir = _os_early.path.join(_os_early.path.dirname(
        _os_early.path.abspath(__file__)), 'logs')
    _os_early.makedirs(_fh_dir, exist_ok=True)
    _fh_file = open(_os_early.path.join(_fh_dir, 'faulthandler.log'),
                    'a', encoding='utf-8', buffering=1)   # 行缓冲，崩溃不丢现场
    _fh.enable(file=_fh_file, all_threads=True)
except Exception:
    pass

import os, sys, time, json, uuid, threading, logging, subprocess, shutil, glob, math, re
from pathlib import Path
from queue import Queue
from flask import Flask, request, jsonify, send_from_directory, render_template_string, Response, redirect

# ── 日志（★ 必须最先初始化）──
# 下面的可选依赖（bilingual / nas_archive）在自己的 except 分支里就要用 logger,
# 若把 logger 放到它们后面定义, 一旦真的导入失败就会 NameError 盖掉原始原因。
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger('geo')

# ── 翻译引擎（2026-09-17 变更: 默认云端 DeepSeek API, 本地 GPU 让给蒸馏）──
# 用 setdefault: 尊重运维的显式设置（USE_DEEPSEEK=0 时仍可回本地），
# 但保证「什么都没设」时默认就是云端。实际取值在启动日志里打印，便于发现残留。
os.environ.setdefault('USE_DEEPSEEK', '1')
TRANSLATE_ENGINE = 'deepseek' if os.environ.get('USE_DEEPSEEK', '1') == '1' else 'local'

# ── 双语拼接版（翻译完成后自动生成：左原文 / 右译文）──
try:
    from bilingual import (find_translated, mono_to_dual_name,
                           make_bilingual, ensure_dual)
except Exception as _e:  # 绝不能因为双语模块挂掉翻译服务
    logger.warning('bilingual 模块不可用，双语版将跳过: %s', _e)
    find_translated = mono_to_dual_name = make_bilingual = ensure_dual = None

# ── NAS 文献仓库（译文三版本归档 / 本地索引检索 / 48h 回收）──
# 与双语模块同款防护：NAS 可能没挂载，绝不能因此拖垮翻译主流程。
try:
    import nas_archive
except Exception as _nas_e:
    logger.warning('nas_archive 模块不可用，NAS 归档将跳过: %s', _nas_e)
    nas_archive = None

# ── 目录 ──
BASE = Path(__file__).parent.resolve()
UPLOAD_DIR = BASE / 'uploads'
TRANSLATED_DIR = BASE / 'translated'

# 「对外入口」——队列下载按钮用它生成绝对地址（相对路径从别人机器上看就是「本机」，
# 分享出去就废了）。与 modules/stitch、modules/archive 同一约定。
# ★ 2026-10-10 起**跟随用户实际访问协议**：https 访问就生成 https 链接，http 访问就生成
#   http 链接（否则 http 环境下混入 https 链接会打不开）。协议取自 X-Forwarded-Proto
#   （见 ProxyFix）；无请求上下文（后台任务/离线）时默认 https://kust.work。
# 可用环境变量 PUBLIC_BASE 固定覆盖，设成空串则退回相对路径。
_ENV_PUBLIC_BASE = os.environ.get('PUBLIC_BASE')  # None = 未设 → 跟随当前请求


def _public_base():
    """当前请求的对外入口（scheme 跟随实际访问协议）；无请求上下文时默认 https。"""
    if _ENV_PUBLIC_BASE is not None:
        return _ENV_PUBLIC_BASE.strip().rstrip('/')
    try:
        return request.host_url.rstrip('/')
    except Exception:
        return 'https://kust.work'


def _public_dl(path):
    """相对资源路径 → 绝对下载地址（对外入口 + ?dl=1 强制附件下载）。"""
    if not path:
        return None
    base = _public_base()
    return (base + path if base else path) + '?dl=1'

# ── 双语拼接版模式: lr=左右对照 / tb=上下对照 / alt=逐页交替 ──
# 改这里即可全局切换，无需动其它代码
BILINGUAL_MODE = os.environ.get('BILINGUAL_MODE', 'lr').strip().lower()
if BILINGUAL_MODE not in ('lr', 'tb', 'alt'):
    BILINGUAL_MODE = 'lr'

# ── 端口 & 进程锁 (防多 Flask 启动冲突) ──
PORT = 5555
PID_FILE = BASE / f'.flask_{PORT}.pid'

def _port_in_use(port):
    """测试端口是否已被监听"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(('0.0.0.0', port))
        s.close()
        return False  # bind 成功 = 没人占
    except OSError:
        return True   # bind 失败 = 已被占用

def _pid_alive(pid):
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x100000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            kernel32.CloseHandle(handle)
            return True
    except: pass
    return False

def _kill_pid(pid):
    """跨平台 kill 进程 (Windows 用 taskkill, Unix 用 os.kill + SIGTERM/SIGKILL)"""
    import subprocess, time, platform
    try:
        if platform.system() == 'Windows':
            subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True, timeout=5)
        else:
            try: os.kill(pid, 15)  # SIGTERM
            except: pass
            time.sleep(0.5)
            try: os.kill(pid, 9)   # SIGKILL
            except: pass
    except Exception:
        pass
    time.sleep(1)  # 等端口释放

def _find_pids_on_port(port):
    """返回所有占用指定端口的 PID 列表 (Windows netstat)"""
    import subprocess, re
    try:
        out = subprocess.run(['netstat', '-ano'], capture_output=True, text=True, timeout=5).stdout
        pids = set()
        for line in out.splitlines():
            m = re.search(rf':{port}\s+.*?LISTENING\s+(\d+)', line)
            if m:
                pids.add(int(m.group(1)))
        return pids
    except Exception:
        return set()

def _acquire_flask_lock():
    """启动前检查 + 拿锁 (三层, 冲突时自动清 —— 用户手动重启友好):
    1. PID file 旧进程 → kill
    2. 端口被占但 PID file 对不上 → 按端口反查 PID → kill
    3. 端口被占且 netstat 找得到 → 直接 kill
    返回 True 表示本进程可以启动
    """
    import subprocess, time

    # 1. PID file 记录了旧 Flask
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            if _pid_alive(old_pid):
                # 旧进程还活着 → 不管端口状态, 直接 kill (用户手动重启意图明确)
                print(f'[锁] 发现旧 Flask PID={old_pid} → kill')
                _kill_pid(old_pid)
            PID_FILE.unlink(missing_ok=True)
        except Exception:
            PID_FILE.unlink(missing_ok=True)

    # 2. 端口仍被占 (残留进程 / supervisor 拉的 / 其他实例)
    if _port_in_use(PORT):
        pids = _find_pids_on_port(PORT)
        # 排除自己 (pid 1, 4 等系统进程)
        pids.discard(os.getpid())
        pids.discard(4)
        if pids:
            print(f'[锁] 端口 {PORT} 被 PID {pids} 占用 → kill')
            for pid in pids:
                _kill_pid(pid)
        # 再等一下, 如果还被占说明不是 Python 进程或系统进程
        for _ in range(10):
            time.sleep(0.5)
            if not _port_in_use(PORT):
                break
        if _port_in_use(PORT):
            print(f'[锁] ❌ 端口 {PORT} 仍被占用 (可能是系统进程或非 Python) → 拒绝启动')
            return False

    # 3. 拿锁
    PID_FILE.write_text(str(os.getpid()))
    import atexit
    def _release_lock():
        try:
            if PID_FILE.exists():
                PID_FILE.unlink()
        except: pass
    atexit.register(_release_lock)
    print(f'[锁] ✅ 拿锁成功 PID={os.getpid()} port={PORT}')
    return True

# ── onnxruntime provider：DocLayout-YOLO 走哪个后端 ──
# ★ 2026-09-29：默认从「DML 优先」改为 **CPU**。
#   证据：全项目只有 DocLayout-YOLO 一处经 onnxruntime 走 DirectML
#   （→ D3D12 → nvwgf2umx.dll），而 09-28 / 09-29 两次原生崩溃的故障模块
#   正是这个 NVIDIA 用户态驱动，且异常码与偏移逐字段相同。
#   翻译引擎已是云端 DeepSeek（零 GPU），整条翻译链路本就不需要显卡 ⇒
#   用 CPU 跑版式检测（每篇多花十秒量级）换掉一条会把整个进程带崩、
#   而且崩了连 Python 栈都留不下的驱动路径。
#   恢复旧行为（DML 优先）：设环境变量 ORT_PROVIDER=dml 后重启。
_ORT_PROVIDER = (os.environ.get('ORT_PROVIDER') or 'cpu').strip().lower()
try:
    import onnxruntime as _ort
    _real_providers = _ort.get_available_providers()
    if _ORT_PROVIDER == 'dml' and 'DmlExecutionProvider' in _real_providers:
        _ort.get_available_providers = lambda: ['DmlExecutionProvider', 'CPUExecutionProvider']
    else:
        # babeldoc 的 DocLayoutModel 只认 dml|cuda|cpu 三类（doclayout.py:114-122），
        # 所以「只报 CPU」= 彻底不走 GPU，同时也不会误落到有 801 错误的 CUDA。
        _ort.get_available_providers = lambda: ['CPUExecutionProvider']
    print('[ORT] DocLayout provider = %s  (ORT_PROVIDER=%s, 实测可用 %s)'
          % (_ort.get_available_providers(), _ORT_PROVIDER, _real_providers), flush=True)
except Exception as _e:
    print('[ORT] provider 设定失败（用库默认值）: %s' % _e, flush=True)

# ── onnxruntime CPU EP 自旋等待：必须关掉（2026-09-29 根因修复）──
# 现象：翻译能进队列，但 Parse Page Layout 阶段进度条长时间不动。
#   _babel_callback.log 实测该阶段 28 页耗时 218.0s（7.79s/页），
#   而同模型单页正常只要 0.48s ⇒ 慢了 17×。
#
# 实测根因（i7-13620H / 16 逻辑核 / ORT 1.24.4 / CPU EP）：
#   session.intra_op.allow_spinning 默认为 1（自旋等待）。连续推理到第 12~13 次时
#   单页耗时从 0.48s 断崖劣化到 7~9s 并**不再恢复**：
#     [predict] #1~#4  = 0.47~0.49s
#     [predict] #5~#28 = 7.07~9.26s   （累计 203.6s）
#   纯独立脚本跑满 28 页同样复现（p1~p12 0.48s / p13~p28 7.04~9.75s），
#   主线程栈确认卡在 onnxruntime_inference_collection.py:296 run 内部
#   ⇒ 与 pdf 内容、与 BabelDOC 逻辑无关，就是 ORT 的线程自旋调度问题。
#   设 allow_spinning='0' 后断崖消失（24/24 稳定，总耗时 26.5s）。
#
# 挂载点说明：babeldoc/docvision/doclayout.py:123 建 session 时只传了 providers，
#   没有传 SessionOptions，因此无法从配置层关闭。这里包一层 InferenceSession，
#   在没显式传 options 时补上（显式传了的调用方不受影响）。
try:
    import onnxruntime as _ort
    if not getattr(_ort.InferenceSession, '_geotranslate_nospin', False):
        _RealInferenceSession = _ort.InferenceSession

        class _NoSpinInferenceSession(_RealInferenceSession):
            _geotranslate_nospin = True

            def __init__(self, path_or_bytes, sess_options=None, **kwargs):
                if sess_options is None:
                    sess_options = _ort.SessionOptions()
                    sess_options.add_session_config_entry(
                        'session.intra_op.allow_spinning', '0')
                super().__init__(path_or_bytes, sess_options, **kwargs)

        _ort.InferenceSession = _NoSpinInferenceSession
    print('[ORT] CPU EP allow_spinning=0 (已关自旋等待)', flush=True)
except Exception as _e:
    print('[ORT] allow_spinning 补丁失败: %s' % _e, flush=True)

# ── 预加载全局资源 (只加载一次, 所有翻译复用) ──
# DocLayout-YOLO (PyTorch CPU 推理, 每次加载 ~5s!)
try:
    from babeldoc.docvision.doclayout import DocLayoutModel
    print('[预加载] DocLayout-YOLO...', flush=True)
    DOC_LAYOUT_MODEL = DocLayoutModel.load_available()
    print('[预加载] DocLayout 就绪', flush=True)
except Exception as _e:
    print(f'[预加载] DocLayout 失败: {_e}', flush=True)
    DOC_LAYOUT_MODEL = None

# 注: 此处原本有一句「预导入」 `from run_babeldoc import _get_translator`,
# 但 run_babeldoc 里并不存在该符号 → 导入必然失败, 而 except 是静默 pass,
# 所以它从未起到预检作用, 只是白白吃掉一条异常。已删除。
# 真正的引擎在 _run_translate() 里按需导入(懒加载, 不拖慢启动)。

# ── 启动期把「实际生效的引擎」打进日志 ──
# 这一段是运维唯一需要看的行: 引擎选错了(比如计划任务残留 USE_DEEPSEEK=0)
# 会在翻译开始前就暴露, 而不是等到发现译文质量不对才回头查。
if TRANSLATE_ENGINE == 'deepseek':
    logger.info(
        '🧠 翻译引擎 = 云端 DeepSeek API | model=%s | 零 GPU 占用 '
        '(本地 llama-server 已停用, GPU 让给蒸馏)',
        os.environ.get('DEEPSEEK_MODEL', 'deepseek-flash'),
    )
    if os.environ.get('DS_FALLBACK_LOCAL', '0') == '1':
        logger.warning('   ⚠️ DS_FALLBACK_LOCAL=1: 云端不可用时会回退本地引擎, 将占用 GPU')
else:
    logger.warning(
        '🧠 翻译引擎 = 本地 llama.cpp (USE_DEEPSEEK=0) | 需要 GPU —— '
        '若正在跑蒸馏, 请改为云端: 删除 USE_DEEPSEEK 环境变量后重启'
    )

UPLOAD_DIR.mkdir(exist_ok=True)
TRANSLATED_DIR.mkdir(exist_ok=True)

# ── Flask ──
app = Flask(__name__, static_folder=None)
# 反代（NAS apache :443 设 X-Forwarded-Proto=https，:80 为 http）后，Werkzeug 默认只看到
# 内层 http。ProxyFix 让 request.scheme/host_url 反映用户**实际访问的协议与主机**，
# 从而对外链接（下载/分享）自动与访问环境一致。
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
# 显微拼图需要一次上传上百张原图（2752×2208 JPEG 约 5MB/张，91 张已达 485MB）。
# 200MB 会让「100 张拼图」在传输层就被 413 拒绝，因此放宽到 2GB。
# 注意：这是 Flask 层限制，wsgi/waitress 与任何反向代理（NAS 上的 nginx）
# 若有独立上限需同步放宽，否则仍会在更外层被拦。
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2GB

# ── 加载 HTML 模板 ──
INDEX_HTML_PATH = BASE / 'index.html'
if INDEX_HTML_PATH.exists():
    INDEX_HTML = INDEX_HTML_PATH.read_text(encoding='utf-8')
    logger.info('HTML template loaded from index.html (%d bytes)', len(INDEX_HTML))
else:
    INDEX_HTML = '<h1>index.html not found</h1>'
    logger.error('index.html not found, extract from pyc first')

# ── Job 状态 (thread-safe dict) ──
jobs = {}
jobs_lock = threading.Lock()
MAX_JOBS = 12
QUEUE = Queue(maxsize=64)
_worker_started = False

# ── 卡死防护（★ 队列卡死的根因就在这里） ──
# 单任务硬超时：跑这么久还没结束，调度线程就放弃等待、继续消费下一个任务。
# 没有它，一个卡住的翻译任务会让整个队列永久停摆。
TRANSLATE_HARD_TIMEOUT = 30 * 60
# 无进度软超时：这么久没收到进度回调 → 先发取消信号，让 BabelDOC 自己退出（更优雅）。
# 比硬超时温和：能自己退就自己退，退不了才由硬超时兜底。
TRANSLATE_STALL_TIMEOUT = 10 * 60
WATCHDOG_INTERVAL = 30

# ══════════════════════════════════════════════════════════════
#  统一任务队列 · 任务种类（kind）
#
#  队列原本只服务「文献翻译」。图像拼合同样吃 GPU / 内存，而它自己那条
#  串行通道与本队列**互不相识** —— 一个翻译 + 一个拼合同时开跑会互相抢
#  显存与内存、双双变慢。这里把拼合并到**同一条串行通道**：任何时刻只有
#  一个「重活」在跑，排队互斥、先来先服务。
#
#  跨模块调用沿用既有约定：模块**不反向 import web_app**（web_app → modules
#  → web_app 会循环导入），改由 web_app 单向下发句柄（与 get_queue 注入同思路）。
# ══════════════════════════════════════════════════════════════
KIND_TRANSLATE = 'translate'
KIND_STITCH = 'stitch'

# 共享串行道。两条路径都要拿它：
#   ① 队列消费线程（每个任务开跑前后 acquire/release）
#   ② 拼合的**同步**入口 `/api/stitch/run`（它不进队列，直接跑）
# 没有 ②，一个同步拼合就能和队列里正在跑的翻译并发 —— 正是要避免的情况。
TASK_LANE = threading.Semaphore(1)

# kind -> 执行函数 fn(job_id, ctx)。由模块在 init_xxx 时注册。
_TASK_RUNNERS = {}

# 各 kind 的超时与取消语义（秒；None = 不启用该项）
#   hard  —— 超过就放弃等待、判失败、队列继续消费（防「一个卡死拖垮整条队列」）
#   stall —— 这么久没收到进度回报就发取消信号。**仅对 cancelable=True 的
#            kind 生效**：拼合不消费 cancel_event（OpenCV 循环里没有检查点），
#            对它发信号只会把「健康的长任务」误判成失败，故为 None。
_KIND_TIMEOUTS = {
    KIND_TRANSLATE: {'hard': TRANSLATE_HARD_TIMEOUT,
                     'stall': TRANSLATE_STALL_TIMEOUT, 'cancelable': True},
    # 91 张 + 大画布实测 5~6 分钟；给 60 分钟硬上限足够，且不误杀大任务
    KIND_STITCH:    {'hard': 60 * 60, 'stall': None, 'cancelable': False},
}

# kind -> 页面上的中文名（快照里带出去，前端不必硬编码）
_KIND_LABEL = {KIND_TRANSLATE: '翻译', KIND_STITCH: '拼合'}


class TaskCtx:
    """交给外部 kind 的执行句柄。

    只暴露必要的写操作，模块拿不到 jobs 内部结构，也就无法把队列改坏。
    心跳（last_progress_at）由 report() 统一维护 —— 那是看门狗判断
    「任务到底有没有在动」的唯一依据。

    `lane_held = True`：调度线程在 join 本任务期间**已经持着共享串行道**
    （TASK_LANE）。执行体据此知道自己不必（也绝不能）再 acquire 一次 ——
    同一信号量重入会自锁。
    """

    lane_held = True

    __slots__ = ('job_id', 'payload', '_cancel_event')

    def __init__(self, job_id, payload=None, cancel_event=None):
        self.job_id = job_id
        self.payload = payload if payload is not None else {}
        self._cancel_event = cancel_event

    def report(self, percent=None, message=None, log=None):
        """上报进度。percent 单调不减（避免阶段回退导致进度条倒退）。"""
        with jobs_lock:
            j = jobs.get(self.job_id)
            if not j:
                return
            if percent is not None:
                try:
                    j['percent'] = max(float(j.get('percent') or 0), float(percent))
                except (TypeError, ValueError):
                    pass
            if message is not None:
                j['message'] = message
            if log is not None:
                j['progress_log'] = list(log)
            j['last_progress_at'] = time.time()

    def cancelled(self):
        ce = self._cancel_event
        return bool(ce is not None and ce.is_set())

    def _stamp_end(self, j):
        j['ended_at'] = time.time()
        j['elapsed'] = j['ended_at'] - j.get('started_at', j['created_at'])

    def finish(self, message='完成', percent=100, **extra):
        """标记成功。已被超时/取消接管的任务不覆盖（与 _run_translate 同守卫）。"""
        with jobs_lock:
            j = jobs.get(self.job_id)
            if j and j.get('status') == 'translating':
                j['status'] = 'done'
                j['percent'] = percent
                j['message'] = message
                j.update(extra)
                self._stamp_end(j)

    def fail(self, error):
        with jobs_lock:
            j = jobs.get(self.job_id)
            if j and j.get('status') == 'translating':
                j['status'] = 'failed'
                j['error'] = str(error)
                j['message'] = '失败'
                self._stamp_end(j)


def register_task_runner(kind, run_fn):
    """登记某个 kind 的执行函数 fn(job_id, ctx)。"""
    _TASK_RUNNERS[kind] = run_fn
    return kind


def submit_task(kind, name, *, payload=None, job_id=None, display=None):
    """通用入队：任意 kind 都走这条串行队列。返回 job_id。

    job_id 可由调用方传入 —— 拼合模块用它把「自己那张任务表的主键」与
    「队列里的工单号」对齐，避免维护第二套 id 映射。
    """
    cfg = _KIND_TIMEOUTS.get(kind) or {}
    job_id = job_id or uuid.uuid4().hex
    now = time.time()

    with jobs_lock:
        to_del = [k for k, v in jobs.items()
                  if v.get('status') in ('done', 'failed', 'cancelled')
                  and now - v.get('ended_at', 0) > 3600]
        for k in to_del:
            del jobs[k]

        jobs[job_id] = {
            'job_id': job_id,
            'kind': kind,
            'display': display or _KIND_LABEL.get(kind) or kind,
            'name': name,
            'status': 'queued',
            'progress': 0,
            'percent': 0,
            'progress_log': [],
            'message': '等待中...',
            'created_at': now,
            'started_at': 0,
            'ended_at': 0,
            'elapsed': 0,
            'error': None,
            'last_progress_at': now,
            'hard_timeout': cfg.get('hard') or TRANSLATE_HARD_TIMEOUT,
            'stall_timeout': cfg.get('stall'),
            'cancelable': bool(cfg.get('cancelable')),
            'cancel_event': threading.Event(),
            # 私有载荷（路径、选项…）。以下划线开头 => 快照/状态接口一律不外泄。
            '_payload': payload or {},
            'path': (payload or {}).get('path'),
        }

    QUEUE.put(job_id)
    _ensure_worker()
    return job_id


def get_task(job_id, *, with_payload=False):
    """给外部 kind 用的只读任务快照（字段少、可 JSON 序列化）。

    刻意不带 `progress`（那是翻译的 0~100 阶段总进度）、也不带 `_payload`：
    模块只需要知道自己这张工单的状态与位次。
    """
    with jobs_lock:
        j = jobs.get(job_id)
        if not j:
            return None
        d = {k: j.get(k) for k in
             ('job_id', 'kind', 'display', 'name', 'status', 'message',
              'percent', 'progress_log', 'error',
              'created_at', 'started_at', 'ended_at', 'elapsed')}
        d['queue_position'] = _queue_position(job_id) if d['status'] == 'queued' else 0
        if with_payload:
            d['payload'] = j.get('_payload')
    return d

# ── 引擎单例（本地引擎; 云端模式禁止构造）──
_engine = None
_engine_lock = threading.Lock()

def get_translator():
    """本地 llama.cpp 引擎单例。仅 USE_DEEPSEEK=0 时才有意义。

    ⚠️ 云端模式下直接报错 —— 构造 LlamaBatchEngine 会去拉起 llama-server,
    把正在跑蒸馏的显存抢走, 属于「静默破坏别人的任务」, 必须拦死。
    """
    global _engine
    if TRANSLATE_ENGINE == 'deepseek':
        raise RuntimeError(
            '当前为云端 DeepSeek 模式, 不应构造本地引擎 (会占用 GPU)。'
            '翻译请统一走 run_babeldoc.translate_pdf()。'
        )
    with _engine_lock:
        if _engine is None:
            from llama_http_engine import LlamaBatchEngine
            _engine = LlamaBatchEngine()
        return _engine

# ── 缓存头 (让 PDF 可缓存 1 小时) ──
@app.after_request
def _set_cache(response):
    p = request.path
    # 阅读器框架的资源自己声明缓存策略（vendor 下的 pdf.js 长缓存，
    # kpdf/ui 源码走 no-cache + ETag 回验）。这里的规则是「按后缀一刀切」，
    # 会把它覆盖成 1 小时强缓存 —— 改完框架代码刷不出来，故直接放行。
    if p.startswith('/api/reader/assets/') or p.startswith('/api/triangle/assets/'):
        return response
    # /theme.js 是「配置型」资源（浅色变量表 + 日出日落参数），调完配色要立刻生效，
    # 不能让它跟着 .js 走 1 小时强缓存 —— 否则改完主题还得等缓存过期才看得到。
    if p == '/theme.js':
        response.headers['Cache-Control'] = 'no-cache'
    elif p.endswith('.pdf') or p.endswith('.js') or p.endswith('.css'):
        response.headers['Cache-Control'] = 'public, max-age=3600, must-revalidate'
    else:
        response.headers['Cache-Control'] = 'no-cache'
    return response

# ── 校园网探测：已移至 http 阶段（见 tools/http_redirect.py）──
# 原 https 公网入口的「图片探针」已移除。改由 open.kust.work 的 http 入口先出判定页：
# 浏览器 no-cors fetch 昆工认证端点 <server-ip>:9090（仅校园网内可达, 公网 IP
# 不触发 LNA 授权弹窗）判内网, 可达 → https://kust.work<path>；否则 → https://open.kust.work<path>。

# ── https 入口静默识别（open.kust.work → 先弹到 http 判定阶段）──
# 直接从 https://open.kust.work 进来的用户, 在 https 页面上探测不了 http 信标
# (混合内容拦截, 且 <server-ip>:9090 无 TLS 可用), 无法静默判内网。故把
# 「导航型 HTML 请求」先 302 弹到 http 判定阶段(tools/http_redirect.py:5080),
# 由它静默探测 9090 后分流。http 判定层会回写 __sp=1 会话 cookie; 带 cookie 再
# 回到 https 时直接放行, 避免 https↔http 死循环。
# 仅拦导航请求(GET + Accept 含 text/html), API/子资源不受影响; 只认 Host
# open.kust.work, 内网入口 kust.work / 其它主机不触发。
@app.before_request
def _open_kust_https_silent_probe():
    if request.method != 'GET':
        return None
    if (request.host or '').split(':')[0].lower() != 'open.kust.work':
        return None
    if request.cookies.get('__sp') == '1':
        return None
    if 'text/html' not in (request.headers.get('Accept') or '').lower():
        return None
    target = 'http://open.kust.work' + request.full_path.rstrip('?')
    resp = redirect(target, code=302)
    resp.headers['Cache-Control'] = 'no-store'
    return resp


# ── 翻译页 ──
# 根路径 / 已交给门户（portal 包）接管，翻译页退到 /translate。
# ⚠️ 前端 index.html 的 4 处 fetch 与后端产出的 PDF URL 都是「根绝对路径」，
#    它们必须继续留在根路径，不能跟着一起加 /translate 前缀，否则全 404。
@app.route('/translate')
def index():
    return render_template_string(INDEX_HTML)

# ── 缓存查找: 按文件名 stem 匹配 translated 目录 ──
def _find_cache(name):
    """返回 {exists, mono_url, dual_url, stem} 或 None"""
    if not name: return None
    name = Path(name).name
    stem = Path(name).stem
    # 去掉 hash 后缀: _xxxxxxxx 或 __xxxxxxxx (8 hex)
    stem_clean = re.sub(r'[_]{1,2}[a-f0-9]{8}$', '', stem, flags=re.IGNORECASE)

    # 1. 精确 stem 匹配
    for f in TRANSLATED_DIR.iterdir():
        if f.is_file() and stem in f.stem:
            if f.name.endswith('.zh.mono.pdf'):
                return {
                    'exists': True,
                    'mono_url': f'/translated/{f.name}',
                    'dual_url': _dual_url_for(f),
                    'stem': stem,
                }

    # 2. 模糊匹配: 去 hash 后的 stem 包含
    if stem_clean != stem:
        for f in TRANSLATED_DIR.iterdir():
            if f.is_file() and stem_clean in f.stem and f.name.endswith('.zh.mono.pdf'):
                return {
                    'exists': True,
                    'mono_url': f'/translated/{f.name}',
                    'dual_url': _dual_url_for(f),
                    'stem': stem,
                }

    # 3. NAS 文献仓库兜底
    #    本地没有 → 查本地 JSON 索引（不扫网络）→ 命中则把三版本拷回本地。
    #    ⚠️ 必须拷回本地：队列与归档只读本地，前端 URL 也是 /translated/xxx.pdf，
    #       拷回后 URL 完全一致，前端与归档逻辑都不用改。
    if nas_archive is not None:
        try:
            item = nas_archive.lookup(name)
            if item:
                rr = nas_archive.restore(item, UPLOAD_DIR, TRANSLATED_DIR)
                if rr.get('ok'):
                    logger.info('📦 NAS 命中并已取回本地: %s → %s',
                                name, [x['name'] for x in rr['restored']])
                    for f in TRANSLATED_DIR.iterdir():
                        if f.is_file() and stem in f.stem and f.name.endswith('.zh.mono.pdf'):
                            return {
                                'exists': True,
                                'mono_url': f'/translated/{f.name}',
                                'dual_url': _dual_url_for(f),
                                'stem': stem,
                                'from_nas': True,
                            }
                else:
                    logger.warning('NAS 索引命中但取回失败: %s → %s', name, rr)
        except Exception as e:
            logger.warning('NAS 缓存查找失败（非致命）: %s', e)
    return None

# ── API: health + 缓存检查 ──
@app.route('/api/check', methods=['GET', 'POST'])
def api_check():
    # POST: {name: "xxx.pdf"} → 查缓存
    if request.method == 'POST':
        data = request.get_json(force=True) if request.data else {}
        name = data.get('name', '')
        cache = _find_cache(name)
        if cache:
            return jsonify({
                'exists': True,
                'translated_url': cache['mono_url'],
                'dual_url': cache['dual_url'],
                'stem': cache['stem'],
                'cache_hit': True,
            })
        return jsonify({'exists': False, 'cache_hit': False})

    # GET: 健康检查 —— 返回当前实际使用的翻译引擎
    # 2026-09-17: 默认引擎已换成云端 DeepSeek, 本机 llama-server 会被停掉释放 GPU,
    # 所以「本地 8090 不通」不再等于「服务坏了」。这里以引擎类型为准报 ok。
    _info = {'ok': True, 'engine': TRANSLATE_ENGINE,
             'use_deepseek': os.environ.get('USE_DEEPSEEK', '1') == '1'}
    if TRANSLATE_ENGINE == 'deepseek':
        _info['model'] = os.environ.get('DEEPSEEK_MODEL', 'deepseek-flash')
        _info['note'] = '云端 API, 零 GPU 占用'
        return jsonify(_info)

    # 本地引擎才去探 8090
    try:
        # trust_env=False: 本地回环不走 HTTP_PROXY
        # (否则代理把请求行改写为绝对 URI -> llama.cpp 路由匹配不上 -> 404)
        _req = __import__('requests')
        _sess = _req.Session()
        _sess.trust_env = False
        r = _sess.get('http://127.0.0.1:8090/health', timeout=2)
        _info['llama'] = r.json()
        return jsonify(_info)
    except Exception as e:
        _info['ok'] = False
        _info['error'] = str(e)
        return jsonify(_info), 503

# ── API: upload + 入队 ──
def _validate_pdf(f):
    if not f or not f.filename.lower().endswith('.pdf'):
        return 'only .pdf'
    f.seek(0, 2)
    if f.tell() > 200 * 1024 * 1024:
        return 'too large (>200MB)'
    f.seek(0)
    return None

@app.route('/api/upload', methods=['POST'])
def api_upload():
    f = request.files.get('file')
    err = _validate_pdf(f)
    if err: return jsonify({'error': err}), 400

    fname = f.filename
    stem = Path(fname).stem
    safe = f"{stem}_{uuid.uuid4().hex[:8]}.pdf"
    save_path = UPLOAD_DIR / safe
    f.save(str(save_path))
    original_url = f'/uploads/{safe}'

    # ══ 缓存优先 ══
    cache = _find_cache(fname)
    if cache:
        logger.info('✅ Cache hit: %s → %s', fname, cache['mono_url'])
        # 历史译文可能没有双语版（本功能上线前产出的）→ 拿本次上传的原文现场补拼
        # 实测 24 页约 0.4s，同步做不会拖慢响应
        dual_url = cache.get('dual_url')
        if not dual_url and ensure_dual is not None:
            try:
                _mono = TRANSLATED_DIR / Path(cache['mono_url']).name
                if _mono.exists():
                    _dp = ensure_dual(save_path, _mono, mode=BILINGUAL_MODE)
                    if _dp:
                        dual_url = f'/translated/{_dp.name}'
                        # 补出的双语版也回写 NAS，保持仓库三版本完整
                        _nas_archive_versions(save_path, _mono, _dp,
                                              display_name=fname, source='cache-hit')
            except Exception as e:
                logger.warning('缓存命中补双语版失败（非致命）: %s', e)
        return jsonify({
            'status': 'done',
            'original_url': original_url,
            'mono_url': cache['mono_url'],
            'dual_url': dual_url,
            'name': fname,
            'cache_hit': True,
        })

    # 缓存未命中 → 正常入队
    job_id = uuid.uuid4().hex
    now = time.time()

    with jobs_lock:
        # 清理旧 job
        to_del = [k for k, v in jobs.items() if v.get('status') in ('done','failed') and now - v.get('ended_at',0) > 3600]
        for k in to_del: del jobs[k]

        jobs[job_id] = {
            'job_id': job_id,
            'name': fname,
            'path': str(save_path),
            'status': 'queued',
            'progress': 0,
            'percent': 0,
            'progress_log': [],
            'message': '等待中...',
            'created_at': now,
            'started_at': 0,
            'ended_at': 0,
            'elapsed': 0,
            'error': None,
            'last_progress_at': now,
            'original_url': original_url,
            'mono_url': None,
            'dual_url': None,
            # ⚠️ 2026-10-16 修复：本入口此前**绕过 submit_task 手写 jobs 字典**，
            # 缺 kind/hard_timeout/stall_timeout/cancelable，导致：
            #   ① 硬超时 fallback 固定 30min，38.8MB/5页超大文件必被误杀；
            #   ② stall 阈值 None → 看门狗直接跳过（大页 PDF 下反倒是蒙对了）；
            #   ③ cancelable 缺失 → /api/cancel 恒返回 409，用户无法中断。
            # 这里补齐；阈值随后由 _on_progress 按 BabelDOC 的页数自适应放大。
            'kind': KIND_TRANSLATE,
            'display': '翻译',
            'hard_timeout': TRANSLATE_HARD_TIMEOUT,
            'stall_timeout': TRANSLATE_STALL_TIMEOUT,
            'cancelable': True,
            'cancel_event': threading.Event(),  # ⚠️ 用户取消信号
            '_payload': {'path': str(save_path), 'name': fname},
        }

    QUEUE.put(job_id)
    _ensure_worker()

    return jsonify({
        'job_id': job_id,
        'status': 'queued',
        'original_url': original_url,
        'name': fname,
    })

# ── 队列 worker (单线程, 串行翻译) ──
def _ensure_worker():
    global _worker_started
    if _worker_started: return
    _worker_started = True
    t = threading.Thread(target=_queue_worker, daemon=True, name='queue-worker')
    t.start()
    w = threading.Thread(target=_stall_watchdog, daemon=True, name='stall-watchdog')
    w.start()
    logger.info('Queue worker + stall watchdog started')


def _queue_worker():
    """队列调度线程 —— 串行消费，但**每个任务交给独立工作线程**，超时可放弃。

    ⚠️ 为什么不能直接在本线程里跑翻译：
    _run_translate() 里的 babel_translate() 是同步阻塞、且无法从外部打断。
    一旦 BabelDOC / llama-server 挂起（模型无响应、GPU 卡住、大文件卡在某阶段），
    本线程就永久卡死 → 队列再也不消费 → 后面所有任务永远停在「等待中」。
    这就是过去「翻译队列卡死」的真正原因。

    改成分离后：本线程 join(timeout) 等工作线程，超时就放弃该任务、继续消费
    下一个；卡住的那个变成 daemon 孤儿线程（进程退出即结束，且因 status 守卫
    无法再改写已被接管的任务）。
    """
    while True:
        try:
            job_id = QUEUE.get(timeout=1)
        except Exception:
            continue

        try:
            with jobs_lock:
                j = jobs.get(job_id)
                if not j:
                    continue
                if j.get('status') not in ('queued', 'translating'):
                    j['status'] = 'cancelled'
                    continue
                _hard = j.get('hard_timeout') or TRANSLATE_HARD_TIMEOUT

            # 拿共享串行道：同步拼合（/api/stitch/run）会短暂占用它。
            # 排队期间保持 status='queued'，页面位次照常显示，不会被误报成「进行中」。
            TASK_LANE.acquire()
            try:
                with jobs_lock:
                    j = jobs.get(job_id)
                    if not j or j.get('status') != 'queued':
                        continue          # 排队期间被取消 → 直接跳过
                    j['status'] = 'translating'
                    j['started_at'] = time.time()
                    j['last_progress_at'] = time.time()   # 看门狗据此判定「无进度」
                    j['progress'] = 0
                    j['percent'] = 0
                    j['message'] = '初始化...'

                t = threading.Thread(target=_run_job_safely, args=(job_id,),
                                     daemon=True, name='job-' + job_id[:8])
                t.start()
                # 分段 join 并**每轮重读** hard_timeout —— 让 _on_progress 里的
                # 「按页数自适应放大」有机会生效（2026-10-16：38.8MB/5页文件
                # 固定 30min 必被误杀）。分段粒度 30s，与原一次性 join 总时长一致。
                _waited = 0.0
                while t.is_alive():
                    t.join(30)
                    _waited += 30
                    if not t.is_alive():
                        break
                    with jobs_lock:
                        j = jobs.get(job_id) or {}
                        _hard = j.get('hard_timeout') or TRANSLATE_HARD_TIMEOUT
                    if _waited >= _hard:
                        break

                if t.is_alive():
                    logger.error('Job %s 硬超时 %.0f 分钟未完成 → 放弃，队列继续消费',
                                 job_id, _hard / 60.0)
                    _mark_timeout(job_id, _hard)
            finally:
                try:
                    TASK_LANE.release()
                except Exception:
                    pass
        except Exception:
            # 调度线程绝不能死：它一死就再没人消费队列，且 _worker_started 已为 True，
            # 之后也不会重启 → 队列彻底停摆。这里吞掉异常保证线程存活。
            logger.exception('queue worker 调度异常（已跳过该任务，线程继续存活）')
            time.sleep(0.5)


def _run_job_safely(job_id):
    """工作线程入口：按 kind 分派，异常兜底，且不覆盖已被超时/取消接管的状态。"""
    with jobs_lock:
        j = jobs.get(job_id) or {}
        kind = j.get('kind') or KIND_TRANSLATE
        payload = j.get('_payload')
        ce = j.get('cancel_event')
    try:
        if kind == KIND_TRANSLATE:
            _run_translate(job_id)
            return
        fn = _TASK_RUNNERS.get(kind)
        if fn is None:
            raise RuntimeError(f'未注册的任务种类（kind={kind}）')
        fn(job_id, TaskCtx(job_id, payload, ce))
    except Exception as e:
        logger.exception('task failed (kind=%s)', kind)
        with jobs_lock:
            j = jobs.get(job_id)
            # 只有仍处于 translating 才写失败：已被超时/取消接管的不要覆盖
            if j and j.get('status') == 'translating':
                j['status'] = 'failed'
                j['error'] = str(e)
                j['message'] = '失败'
                j['ended_at'] = time.time()
                j['elapsed'] = j['ended_at'] - j.get('started_at', j['created_at'])


def _mark_timeout(job_id, seconds):
    """硬超时：标记失败，并尽力让还活着的孤儿线程尽早自行退出。"""
    with jobs_lock:
        j = jobs.get(job_id)
        if not j:
            return
        ce = j.get('cancel_event')
        if j.get('status') == 'translating':
            # 不足 1 分钟按秒显示，避免「超过 0 分钟」这种近似值
            dur = ('%.0f 分钟' % (seconds / 60.0)) if seconds >= 60 \
                else ('%.0f 秒' % seconds)
            j['status'] = 'failed'
            j['error'] = ('翻译超过 %s 未完成，已跳过。'
                          '后端可能已卡死，建议重启翻译服务。' % dur)
            j['message'] = '超时，已跳过'
            j['ended_at'] = time.time()
            j['elapsed'] = j['ended_at'] - j.get('started_at', j['created_at'])
    if ce:
        ce.set()


def _stall_watchdog():
    """软超时看门狗：任务长时间无进度 → 发取消信号，让 BabelDOC 自己退出。

    BabelDOC 会在段落翻译间隙检查 cancel_event 并抛 CancelError，工作线程随即
    正常结束，调度线程 join 立刻返回，队列马上继续 —— 比硬超时优雅得多。
    若它不响应取消，则由调度线程的硬超时兜底。两层保护缺一不可。
    """
    while True:
        try:
            time.sleep(WATCHDOG_INTERVAL)
            now = time.time()
            with jobs_lock:
                running = [(k, v) for k, v in jobs.items()
                           if v.get('status') == 'translating']
            for jid, j in running:
                # 只对「声明了软超时、且真的消费取消信号」的 kind 生效。
                # 拼合没有 cancel_event 检查点，对它发信号毫无作用，只会把
                # 一个健康的长任务标成「正在尝试中断」——宁可不管。
                stall = j.get('stall_timeout')
                if not stall or not j.get('cancelable'):
                    continue
                last = j.get('last_progress_at') or j.get('started_at') or now
                if now - last >= stall:
                    ce = j.get('cancel_event')
                    if ce and not ce.is_set():
                        gap = now - last
                        gap_s = ('%.0f 分钟' % (gap / 60.0)) if gap >= 60 \
                            else ('%.0f 秒' % gap)
                        logger.warning('Job %s 已 %s 无进度 → 发送取消信号',
                                       jid, gap_s)
                        ce.set()
                        with jobs_lock:
                            j['message'] = '长时间无进度，正在尝试中断…'
        except Exception:
            logger.exception('看门狗异常（已忽略，继续监控）')
            time.sleep(WATCHDOG_INTERVAL)

# ── 翻译主入口 ──
def _translated_path(stem, suffix):
    """定位产物。

    ⚠️ 绝不能硬拼 `{stem}.zh.{suffix}.pdf`：BabelDOC 在 NoWatermark 模式下
    产物名是 `{stem}.no_watermark.zh.mono.pdf`，硬拼路径 exists() 恒为 False，
    会导致 mono_url 拿不到、双语版永不生成（dual 长期为 0 的根因）。
    一律走 glob 匹配，命中多个取最新。
    """
    if find_translated is not None:
        found = find_translated(TRANSLATED_DIR, stem, suffix)
        if found:
            return found
    return TRANSLATED_DIR / f'{stem}.zh.{suffix}.pdf'

def _dual_path(stem):
    return TRANSLATED_DIR / f'{stem}.zh.dual.pdf'

def _dual_url_for(mono_path):
    """已存在的双语版 → URL；不存在返回 None（由调用方决定是否现拼）。"""
    if not mono_path or mono_to_dual_name is None:
        return None
    dual = Path(mono_path).parent / mono_to_dual_name(Path(mono_path).name)
    return f'/translated/{dual.name}' if dual.exists() else None

def _make_dual_pdf(orig_pdf: Path, mono_pdf: Path, dual_pdf: Path, mode: str = 'lr') -> bool:
    """PyMuPDF 拼接双语版 —— 实现已下沉到 bilingual 模块，这里保留旧签名作兼容。

    mode: lr=左右对照(默认) / tb=上下对照 / alt=逐页交替
    保留所有 link annotations (URI 外链 + 内部跳转)，右半坐标按 mode 平移。
    比 BabelDOC 自己生成 dual 快得多 (省一轮 Typesetting + Save PDF)：
    实测 24 页 0.4s。
    """
    if make_bilingual is None:
        logger.warning('bilingual 不可用，跳过双语拼接')
        return False
    return make_bilingual(orig_pdf, mono_pdf, dual_pdf, mode=mode)


def _nas_archive_versions(orig_path, mono_path, dual_path=None, *,
                          display_name='', source='translate'):
    """把译文三版本归档进 NAS 文献仓库（原文 / 中文 / 双语）。

    ★ 失败只记日志，绝不抛异常 —— NAS 可能没挂载、可能只读、可能网络断了，
      这些都不能让一次已经完成的翻译失败。
    """
    if nas_archive is None:
        return None
    try:
        _o = Path(orig_path) if orig_path and Path(orig_path).exists() else None
        _m = Path(mono_path) if mono_path and Path(mono_path).exists() else None
        _d = Path(dual_path) if dual_path and Path(dual_path).exists() else None
        if _m is None:                       # 没有译文就没必要归档
            return None
        ar = nas_archive.archive(_o, _m, _d, display_name=display_name, source=source)
        if ar.get('ok'):
            logger.info('📦 已归档到 NAS 文献仓库: %s (%s)', display_name, ar.get('stem'))
        else:
            logger.warning('NAS 归档未完整: %s → %s', display_name, ar)
        return ar
    except Exception as e:
        logger.warning('NAS 归档失败（非致命）: %s', e)
        return None


def _run_translate(job_id):
    with jobs_lock:
        j = jobs.get(job_id)
        if not j: return
        src_path = j['path']
        name = j['name']
        stem = Path(src_path).stem  # ← 用 hash 后的 src_path stem, 才对得上 BabelDOC 产物名
        cancel_event = j.get('cancel_event')  # ⚠️ 用户取消信号

    from run_babeldoc import translate_pdf as babel_translate, CancelError

    # ── 用户指定 4 大阶段权重: 解析20% 翻译70% 排版8% 输出2% ──
    _GROUP_WEIGHT = {
        'parse':    20.0,
        'translate': 70.0,
        'typeset':   8.0,
        'output':    2.0,
    }
    _STAGE_TO_GROUP = {
        'Parse PDF and Create Intermediate Representation': 'parse',
        'DetectScannedFile':             'parse',
        'Parse Page Layout':             'parse',
        'Parse Table':                   'parse',
        'Parse Paragraphs':              'parse',
        'Parse Formulas and Styles':     'parse',
        'Translate Paragraphs':          'translate',
        'Typesetting':                   'typeset',
        'Add Fonts':                     'typeset',
        'Generate drawing instructions': 'typeset',
        'Subset font':                   'output',
        'Save PDF':                      'output',
    }
    _STAGE_CN = {
        'Parse PDF and Create Intermediate Representation': '解析PDF结构',
        'DetectScannedFile':             '检测扫描件',
        'Parse Page Layout':             '页面布局识别',
        'Parse Table':                   '表格识别',
        'Parse Paragraphs':              '段落识别',
        'Parse Formulas and Styles':     '公式与样式识别',
        'Translate Paragraphs':          '翻译中',
        'Typesetting':                   '排版',
        'Add Fonts':                     '嵌入字体',
        'Generate drawing instructions': '生成绘图',
        'Subset font':                   '字体子集化',
        'Save PDF':                      '保存PDF',
    }

    # ── 超大页 PDF 的 stall/hard 自适应预算（2026-10-16 根因修复）──
    # BabelDOC 的 Parse 阶段按「页」advance（il_creater.py:306 on_page_end），
    # 38.8MB/5页的扫描大图单页解析（pdfminer + DocLayout-YOLO CPU）实测 5-10 分钟，
    # 页与页之间没有任何进度回调。固定 stall=10min 会在某页解析途中误发
    # cancel_event，该页完成的瞬间 _babel_callback 抛 CancelError（任务被误杀）；
    # 固定 hard=30min 对 5 页大文件（仅解析就 ~30min）也不够。
    # 首次拿到页数（stage_total）时把阈值抬到「页数 × 每页预算」。
    _PARSE_PAGE_STALL = 12 * 60   # 每页无进度容忍 12 分钟
    _PARSE_PAGE_HARD = 15 * 60    # 每页总预算 15 分钟（仅 parse 部分）

    # BabelDOC 官方 stage 权重（用于组内按权重分配，替代原「组内简单平均」）。
    # 简单平均的坑：parse 组 6 个 stage，只完成第 1 个的 20% → 0.67 → int() → 0，
    # 前端进度永远显示 0%，看起来像卡死（2026-10-16 用户报障即此症状）。
    try:
        from babeldoc.high_level import TRANSLATE_STAGES as _BABEL_STAGES
        _STAGE_W = {name: float(w) for name, w in _BABEL_STAGES}
    except Exception:
        _STAGE_W = {}

    _GROUP_STAGE_W = {}
    for _g in _GROUP_WEIGHT:
        _stgs = [s for s, grp in _STAGE_TO_GROUP.items() if grp == _g]
        _w = {s: _STAGE_W.get(s, 1.0) for s in _stgs}
        _tw = sum(_w.values()) or 1.0
        _GROUP_STAGE_W[_g] = {s: v / _tw for s, v in _w.items()}

    def _calc_overall(sp):
        total = 0.0
        for g, gw in _GROUP_WEIGHT.items():
            for s, w in _GROUP_STAGE_W[g].items():
                total += w * (sp.get(s, 0.0) / 100.0) * gw
        return round(total, 1)

    def _on_progress(overall_pct, stage, cur, tot, stage_prog=None):
        try:
            if not jobs_lock.acquire(timeout=2.0): return
            try:
                j = jobs.get(job_id)
                if not j: return
                if 'stage_progresses' not in j:
                    j['stage_progresses'] = {}
                j['stage_starts'] = j.get('stage_starts', {})
                j['stage_starts'][stage] = True
                # 优先用 BabelDOC 给的 stage_progress (最准!), 兜底自己算
                if stage_prog is not None and stage_prog > 0:
                    j['stage_progresses'][stage] = min(100.0, float(stage_prog))
                elif stage == 'Translate Paragraphs':
                    j['_babel_cur'] = cur
                    j['_babel_tot'] = tot
                    j['stage_progresses'][stage] = min(100.0, 100.0 * cur / max(tot, 1))
                elif tot > 0:
                    j['stage_progresses'][stage] = min(100.0, float(cur) / tot * 100.0)
                j['progress'] = max(j.get('progress', 0), _calc_overall(j['stage_progresses']))
                j['percent'] = int(j['progress'])
                # ── 超大页 PDF 自适应超时（2026-10-16 修复）──
                # BabelDOC Parse 阶段的 stage_total 就是「需翻译的页数」，
                # 首帧回调即可拿到。按页数抬升 stall/hard，避免长页解析途中被误杀。
                if _STAGE_TO_GROUP.get(stage) == 'parse' and tot >= 2:
                    _need_stall = tot * _PARSE_PAGE_STALL
                    _need_hard = tot * _PARSE_PAGE_HARD
                    if _need_stall > (j.get('stall_timeout') or 0):
                        j['stall_timeout'] = _need_stall
                    if _need_hard > (j.get('hard_timeout') or 0):
                        j['hard_timeout'] = _need_hard
                j['current_stage'] = stage
                j['last_progress_at'] = time.time()   # 看门狗据此判定「无进度」
                cn = _STAGE_CN.get(stage, stage)
                j['message'] = f'{cn} ({cur}/{tot})' if tot > 0 else cn
            finally:
                jobs_lock.release()
        except Exception as e:
            logger.debug('_on_progress err: %s', e)

    _hb_stop = threading.Event()
    _hb_thread = threading.Thread(target=lambda: _hb_stop.wait(), daemon=True)
    _hb_thread.start()

    try:
        logger.info('Translate start: %s', src_path)
        babel_translate(
            str(src_path),
            str(TRANSLATED_DIR),
            progress_callback=_on_progress,
            doc_layout_model=globals().get('DOC_LAYOUT_MODEL'),
            cancel_event=cancel_event,  # ⚠️ 用户取消信号
        )
        logger.info('Translate done')

        _hb_stop.set()

        # 检查是不是被取消了 (cancel_event.set() 后 BabelDOC 可能还能返回完)
        if cancel_event and cancel_event.is_set():
            raise CancelError('用户取消')

        mono_path = _translated_path(stem, 'mono')   # glob 匹配，兼容 .no_watermark
        mono_url = f'/translated/{mono_path.name}' if mono_path.exists() else None

        # 双语拼接版：翻译完成即自动生成（左原文 / 右译文），供前端下载
        # ⚠️ 必须在 mono 定位成功之后做 —— 否则 dual 恒为 None
        dual_url = None
        if mono_path.exists():
            try:
                # 拼接期间刷新心跳，避免看门狗误判「长时间无进度」
                with jobs_lock:
                    _jj = jobs.get(job_id)
                    if _jj:
                        _jj['message'] = '生成双语对照版…'
                        _jj['last_progress_at'] = time.time()
            except Exception:
                pass
            if ensure_dual is not None:
                _dp = ensure_dual(Path(src_path), mono_path, mode=BILINGUAL_MODE)
                if _dp:
                    dual_url = f'/translated/{_dp.name}'
            else:
                dual_path = _dual_path(stem)
                if _make_dual_pdf(Path(src_path), mono_path, dual_path):
                    dual_url = f'/translated/{dual_path.name}'

        # 兜底: 找任何译版
        if not mono_url:
            for pat in [f'{stem}*.zh.mono.pdf', f'{stem}*.zh.dual.pdf', f'{stem}*.zh.pdf']:
                found = list(TRANSLATED_DIR.glob(pat))
                if found:
                    mono_path = found[0]
                    mono_url = f'/translated/{mono_path.name}'
                    break

        # ── 归档到 NAS 文献仓库（原文 / 中文 / 双语 三版本）──
        _nas_archive_versions(
            src_path, mono_path,
            (TRANSLATED_DIR / Path(dual_url).name) if dual_url else None,
            display_name=name, source='translate',
        )

        with jobs_lock:
            j = jobs.get(job_id)
            # 已被超时/取消接管的任务不覆盖：孤儿线程晚完成时，
            # 不能把「已超时跳过」刷回「完成」，否则队列状态自相矛盾。
            if j and j.get('status') == 'translating':
                j['status'] = 'done'
                j['progress'] = 100
                j['message'] = '完成'
                j['mono_url'] = mono_url
                j['dual_url'] = dual_url
                j['ended_at'] = time.time()
                j['elapsed'] = j['ended_at'] - j.get('started_at', j['created_at'])

    except CancelError as e:
        # ⚠️ 用户取消 — 不是错误, 单独处理
        _hb_stop.set()
        logger.info('Job %s cancelled by user: %s', job_id, e)
        with jobs_lock:
            j = jobs.get(job_id)
            # 同样加守卫：已被超时接管的不改判为 cancelled
            if j and j.get('status') == 'translating':
                j['status'] = 'cancelled'
                j['message'] = '已取消'
                j['ended_at'] = time.time()
                j['elapsed'] = j['ended_at'] - j.get('started_at', j['created_at'])

    except Exception as e:
        _hb_stop.set()
        raise

# ── API: status ──
def _queue_position(job_id):
    position = 1  # 先算自己
    for qid in list(QUEUE.queue):
        if qid == job_id: break
        position += 1
    active_count = sum(1 for j in jobs.values() if j.get('status') == 'translating')
    return position + active_count

@app.route('/api/status/<job_id>')
def api_status(job_id):
    with jobs_lock:
        j = jobs.get(job_id)
        if not j: return jsonify({'error': 'not found'}), 404

        data = {k: v for k, v in j.items()
                if k not in ('path', 'cancel_event', '_payload')}

        if j['status'] == 'queued':
            data['queue_position'] = _queue_position(job_id)
        else:
            data['queue_position'] = 0

        # elapsed
        if j['started_at'] > 0:
            end = j['ended_at'] or time.time()
            data['elapsed'] = round(end - j['started_at'], 1)

        return jsonify(data)

# ── API: 取消任务 ──
@app.route('/api/cancel/<job_id>', methods=['POST'])
def api_cancel(job_id):
    with jobs_lock:
        j = jobs.get(job_id)
        if not j: return jsonify({'error': 'not found'}), 404
        if j['status'] in ('done', 'failed', 'cancelled'):
            return jsonify({'error': f"already {j['status']}"}), 400
        
        # 1. 设置取消信号
        ce = j.get('cancel_event')
        if ce: ce.set()
        
        # 2. 如果还在排队 → 直接标记 cancelled + 从 QUEUE 移除
        if j['status'] == 'queued':
            j['status'] = 'cancelled'
            j['ended_at'] = time.time()
            j['elapsed'] = j['ended_at'] - j['created_at']
            j['message'] = '已取消'
            # 从 QUEUE 里尝试移除 (Queue 没有 remove, 只能标记让 worker 跳过)
            # worker 在 get 后会检查 status, 所以这里 set cancelled 就行
            logger.info('Job %s cancelled (queued)', job_id)
            return jsonify({'ok': True, 'action': 'cancelled_queued'})
        
        # 3. translating → 已经在跑, cancel_event 会在下次 progress_callback 时触发
        #    ★ 但那只对「真的消费 cancel_event」的 kind 有效（翻译）。拼合的执行
        #      体在 OpenCV 循环里没有检查点，发信号不会让它停下来 —— 与其把
        #      按钮变成安慰剂，不如明确拒绝，让用户知道只能等它跑完。
        if not j.get('cancelable'):
            return jsonify({'ok': False,
                            'error': f"该任务类型（{j.get('display') or j.get('kind')}）"
                                     f"不支持中途取消，请等待其自然结束"}), 409
        j['message'] = '正在取消...'
        logger.info('Job %s cancel_event set (translating)', job_id)
        return jsonify({'ok': True, 'action': 'cancel_signal_sent'})

# ── API: debug ──
@app.route('/api/_debug')
def api_debug():
    with jobs_lock:
        job_data = {}
        for k, v in jobs.items():
            job_data[k] = {kk: vv for kk, vv in v.items() if kk not in ('path', 'cancel_event')}

    # 引擎状态
    eng = None
    try:
        eng = get_translator()
    except: pass

    eng_stats = {}
    if eng:
        try:
            eng_stats = {
                'server_url': eng.server_url if hasattr(eng, 'server_url') else '?',
                'gate_size': eng._GATE._value if hasattr(eng, '_GATE') else '?',
            }
        except: pass

    return jsonify({
        'jobs': job_data,
        'engine': eng_stats,
        'queue_size': QUEUE.qsize(),
    })

# ── 静态文件 ──
@app.route('/uploads/<path:f>')
def serve_upload(f):
    # ?dl=1 → 强制附件下载（Content-Disposition: attachment）。下载按钮与分享出去的
    # 绝对链接都带它；不带则保持内联，供 /PDFreader 在线预览。
    return send_from_directory(str(UPLOAD_DIR), f,
                               as_attachment=bool(request.args.get('dl')))

@app.route('/translated/<path:f>')
def serve_output(f):
    return send_from_directory(str(TRANSLATED_DIR), f,
                               as_attachment=bool(request.args.get('dl')))

# ══════════════════════════════════════════════════════════════
#  门户 + 功能模块注册（方案 B：模块化单体）
#  页面路径带前缀（/translate、/archive），API 与静态资源留在根路径。
#  这里用 try/except 包住：门户或某个模块出问题，不能让翻译服务跟着挂。
# ══════════════════════════════════════════════════════════════

def _active_jobs():
    """当前活跃任务数（queued + translating）。在锁内取数，避免跨线程迭代竞态。"""
    with jobs_lock:
        return sum(1 for j in jobs.values()
                   if j.get('status') in ('queued', 'translating'))


def _queue_snapshot():
    """文献翻译队列快照 —— 供门户「翻译队列」页只读展示（不走写路径）。

    ⚠️ 只挑可 JSON 序列化的字段：jobs 里混着 threading.Event(cancel_event)
    等活对象，直接 jsonify(jobs) 会抛 TypeError。
    在 jobs_lock 内取数，避免跨线程迭代竞态（jobs 由 worker 线程并发改写）。
    """
    _FIELDS = ('job_id', 'kind', 'display', 'name', 'status', 'progress',
               'percent', 'message', 'created_at', 'started_at', 'ended_at',
               'elapsed', 'error', 'original_url', 'mono_url', 'dual_url',
               'result')
    try:
        with jobs_lock:
            items = [{k: j.get(k) for k in _FIELDS} for j in jobs.values()]
            order = list(getattr(QUEUE, 'queue', ()))
            qsize = QUEUE.qsize()
    except Exception:
        logger.exception('❌ 翻译队列快照失败')
        return {'ok': False, 'items': [], 'stats': {}, 'queue_size': 0}

    for it in items:
        p = it.get('progress')
        it['progress'] = round(float(p), 1) if isinstance(p, (int, float)) else 0.0
        pc = it.get('percent')
        it['percent'] = round(float(pc), 1) if isinstance(pc, (int, float)) else 0.0
        it['display'] = it.get('display') or _KIND_LABEL.get(it.get('kind')) or '任务'
        # 「产物指引」白名单化：模块（如拼合）会把成品图地址写进 job['result']。
        # 只放前端真正要用的键 —— 模块实测往里塞过 4095 条逐对判据明细的 meta，
        # 而 /api/queue 是 2s 一次的高频轮询，不能背上无关的大字段。
        r = it.get('result')
        it['result'] = ({k: r.get(k) for k in
                         ('filename', 'url', 'download_url',
                          'public_url', 'public_download_url',
                          'size_kb', 'width', 'height')}
                        if isinstance(r, dict) else None)
        # 排队位次只对「还在队列里没开跑」的任务有意义
        pos = 0
        if it.get('status') == 'queued':
            try:
                pos = order.index(it.get('job_id')) + 1
            except ValueError:
                pos = 0
        it['queue_position'] = pos
        # 三版本下载地址：绝对 NAS 对外入口 + 强制附件下载（相对路径分享出去无效）
        it['original_dl_url'] = _public_dl(it.get('original_url'))
        it['mono_dl_url'] = _public_dl(it.get('mono_url'))
        it['dual_dl_url'] = _public_dl(it.get('dual_url'))

    st = {'queued': 0, 'translating': 0, 'done': 0, 'failed': 0, 'cancelled': 0}
    by_kind = {}
    for it in items:
        s = it.get('status')
        if s in st:
            st[s] += 1
        k = it.get('kind') or 'translate'
        by_kind[k] = by_kind.get(k, 0) + 1
    st['total'] = len(items)
    st['active'] = st['queued'] + st['translating']
    st['by_kind'] = by_kind

    # 排序：翻译中 → 排队中（按入队先后）→ 已结束（按结束时间倒序）
    def _key(it):
        s = it.get('status')
        rank = 0 if s == 'translating' else (1 if s == 'queued' else 2)
        t = it.get('created_at') or 0
        if rank == 2:
            t = -(it.get('ended_at') or 0)
        return (rank, t)

    items.sort(key=_key)
    return {'ok': True, 'queue_size': qsize, 'stats': st, 'items': items}


try:
    import portal
    portal.init_portal(app, get_active=_active_jobs)
except Exception:
    logger.exception('❌ 门户注册失败 —— 已降级：/ 直接打开翻译页')

    @app.route('/')
    def _portal_fallback():
        return render_template_string(INDEX_HTML)


try:
    import modules
    # DATA_DIR 指向 PostNews 根目录（Translate 的上一级）：
    # 今日早报要读 daily_push_state.json / news.json / output/*.png，
    # 期刊列表要读 journals.json 等。模块自己不猜路径，一律由这里注入。
    modules.register_all(app, upload_dir=UPLOAD_DIR, translated_dir=TRANSLATED_DIR,
                         data_dir=BASE.parent, get_queue=_queue_snapshot,
                         # 统一任务队列句柄：拼合模块据此入队 / 查工单 / 共用串行道
                         # ★ register_runner 必须有：只注入 submit_task 的话，
                         #   工单能入队但队列不知道谁执行 → 任务被标成
                         #   「未注册的任务种类（kind=stitch）」（2026-09-28 踩到）
                         submit_task=submit_task, get_task=get_task,
                         task_lane=TASK_LANE,
                         register_runner=register_task_runner)
except Exception:
    logger.exception('❌ 功能模块注册失败（门户本身仍可用）')


# ── 清理 (后台) ──
# 文件保留期。⚠️ 必须 >= modules/archive 的 ARCHIVE_WINDOW_HOURS（48h），
# 否则归档页的「最近 48 小时」窗口会永远是空的 —— 文件早在 2h 就被删了。
# 原文 + 双语 + 中文 一套约 10MB，48h 的量级对本机磁盘可以忽略。
RETENTION_SECONDS = 48 * 3600

def _cleanup():
    now = time.time()
    for d in [UPLOAD_DIR, TRANSLATED_DIR]:
        for p in d.glob('*'):
            if p.is_file() and now - p.stat().st_mtime > RETENTION_SECONDS:
                try: p.unlink()
                except: pass

threading.Thread(target=lambda: [time.sleep(300) or _cleanup() for _ in iter(int,1)], daemon=True).start()

# ── 启动 ──
if __name__ == '__main__':
    _ensure_worker()
    print('=' * 50)
    print('  GeoTranslate Web v4')
    print(f'  http://127.0.0.1:{PORT}')
    print('=' * 50)

    # ── 三层防冲突: PID file + 端口检查 + debug 强制关闭 ──
    if not _acquire_flask_lock():
        print('❌ 启动被拒绝: 已有 Flask 在运行')
        sys.exit(1)

    try:
        import waitress
        print('[启动] waitress (生产级 WSGI)')
        # waitress 默认 max_request_body_size=1GiB，会在 Flask 之前拦掉大上传，
        # 与 MAX_CONTENT_LENGTH 对齐到 2GiB，否则「上传 100 张」仍失败。
        # channel_timeout 放宽：大体积 multipart 上传耗时远超默认 120s。
        # ★ 必须关掉 waitress 的「清理不可信代理头」默认行为，否则 ProxyFix 失效：
        #   waitress 3.x 默认 clear_untrusted_proxy_headers=True，在没配 trusted_proxy 时
        #   会把 X-Forwarded-Proto/Host **全部删掉**，导致上面 ProxyFix 读不到、对外链接
        #   （下载/分享）永远退回内层 http。本机 5555 仅由 NAS 反代 + 局域网访问，
        #   关掉清理、交给 ProxyFix 处理即可（NAS :443 发 https、:80 发 http）。
        waitress.serve(app, host='0.0.0.0', port=PORT, threads=8,
                       max_request_body_size=2 * 1024 * 1024 * 1024,
                       channel_timeout=600,
                       clear_untrusted_proxy_headers=False)
    except ImportError:
        print('[启动] Flask 内置 WSGI (建议 pip install waitress)')
        app.run(host='0.0.0.0', port=PORT, threaded=True, debug=False, use_reloader=False)
