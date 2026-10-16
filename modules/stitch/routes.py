# -*- coding: utf-8 -*-
"""图像拼合模块

页面：GET /stitch
接口：
  POST /api/stitch/run          同步拼接
  POST /api/stitch/submit       异步提交
  GET  /api/stitch/task/<id>    任务查询
  GET  /api/stitch/result/<fname> 预览
  GET  /api/stitch/result/<fname>/dl 下载
  GET  /api/stitch/assets/<path> 静态资源
  GET  /api/stitch/health       健康检查
"""
import os, sys, uuid, threading, json, time
from pathlib import Path
from typing import Optional

import cv2
cv2.ocl.setUseOpenCL(False)   # 防 UMat create 崩溃
import numpy as np

try:
    cv2.ocl.setUseOpenCL(False)
except Exception:
    pass

_DIR = Path(__file__).parent
sys.path.insert(0, str(_DIR))
from stitcher import Stitcher  # noqa

from flask import Blueprint, Response, send_file, request, jsonify
from portal.templating import render_html

stitch_bp = Blueprint('stitch', __name__)

_ASSETS = (_DIR / 'assets').resolve()

# ── 存储位置 ──
# 上传原图是**过程性临时文件**（拼接读取完即无用），放本地，不占 NAS。
# 拼接结果体积同样可观（上亿像素全景）且需要长期留存，落到 NAS。
# NAS 不可用时结果自动退回本模块目录，保证功能不中断。
#
# ★ NAS 根必须**优先用 UNC，不能只写盘符**（详见运维 skill §8b）：
#   `S:` 只是用户交互会话里的映射盘符（登记在 HKCU\Network\S），
#   服务/提升令牌/计划任务等上下文**看不到它**。实测本模块由 waitress
#   以服务方式启动时 `S:` 不可达，而同一台机器上我的交互 shell 里可达 ——
#   只写盘符会导致生产上静默降级到本地（health 的 results_on_nas 会暴露）。
#   策略：UNC 优先，其次盘符，最后本地。
_NAS_CANDIDATES = (
    Path(r'\\<nas-host>\<share>'),
    Path(r'S:\Daily_Poster\stitch'),
)
_LOCAL_UPLOADS = _DIR / 'uploads'
_LOCAL_RESULTS = _DIR / 'results'


def _pick_store(sub: str, local: Path, candidates=None) -> Path:
    """NAS 按候选顺序探测；全不可用则回退本地。

    探测方式：只做 mkdir + os.access 可写检查，**不写任何探针文件**。
    早期版本用「写一个探针再删掉」来判断，但删除会被环境级的安全拦截
    （safe-delete hook）挡下并抛异常，导致 NAS 明明可用却被误判为不可用。
    """
    for root in (candidates or _NAS_CANDIDATES):
        try:
            cand = root / sub
            cand.mkdir(parents=True, exist_ok=True)
            if not os.access(str(cand), os.W_OK):
                raise PermissionError(f'{cand} 不可写')
            return cand
        except Exception as exc:
            print(f'[stitch] NAS 候选不可用 {root} ({exc!r})')
    print(f'[stitch] 所有 NAS 候选均不可用，{sub} 回退到本地 {local}')
    local.mkdir(parents=True, exist_ok=True)
    return local


# 上传：始终本地（临时文件）
_LOCAL_UPLOADS.mkdir(parents=True, exist_ok=True)
_UPLOADS = _LOCAL_UPLOADS
# 结果：优先 NAS
_NAS_ROOT = _NAS_CANDIDATES[0]          # 用于 health 自证"是否落在 NAS 上"
_RESULTS = _pick_store('results', _LOCAL_RESULTS)

_MIME = {
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.html': 'text/html; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
}

# ── 成品图的对外入口（结果链接用它）──
# ★ 用户口径（2026-09-30）：结果链接**直接用 NAS 的入口**，不要给只在本机有效的地址
#   —— 相对路径 / 127.0.0.1 从别人机器上看都是「本机」，分享出去就废了。
#   `kust.work` 就是 NAS 的域名（校园网 DNS → <lan-ip> → NAS DNAT → 本机 Flask，
#   见 portal/routes.py `_request_path()` 的通路说明）。实测：本机取 15.9MB 成品图
#   **0.45s**、校园网同事也能直接打开 ⇒ 两条人群都兼顾。
#   ⚠️ **不要改成 <lan-ip>**：那只在实验室 LAN（<nas-host>/<share>
#   校园网同事打不开 —— 与 `modules/share` 是同一条坑（那边 SERVER 也必须是 kust.work）。
#   ⚠️ 别拿 SMB 的经验否定它：`//<nas-host>/<share> 挂 94s 是 **445 端口**的事，
#   HTTP 80/443 实测 0.016s，两者无关。
#   换域名/换网段：设环境变量 `STITCH_PUBLIC_BASE`；**设成空串则退回相对路径**。
#   ★ 2026-10-10 起**跟随用户实际访问协议**：https 访问生成 https 链接，http 访问生成 http
#     链接；无请求上下文时默认 https://kust.work。
_ENV_PUBLIC_BASE = os.environ.get('STITCH_PUBLIC_BASE')  # None = 未设 → 跟随当前请求


def _public_base() -> str:
    """当前请求的对外入口（scheme 跟随实际访问协议）；无请求上下文时默认 https。"""
    if _ENV_PUBLIC_BASE is not None:
        return _ENV_PUBLIC_BASE.strip().rstrip('/')
    try:
        return request.host_url.rstrip('/')
    except Exception:
        return 'https://kust.work'


def _public_url(path: str) -> str:
    """把模块内的相对路径套成对外地址（PUBLIC_BASE 为空串时原样返回）。"""
    base = _public_base()
    return (base + path) if base else path


tasks = {}

# ── 任务表上限 ──
# 必须有上限：一个 91 张任务的 `result.meta` 含 4095 条逐对判据明细
# （实测 935 KB，占任务对象 99%）。不淘汰就是长期运行的内存无界增长。
_TASKS_MAX = 48                 # 超出后淘汰最旧的**已完成**任务
_task_lock = threading.Lock()

# ── 与「统一任务队列」的接口（由 web_app 注入）──
# 拼合同样吃 GPU / 内存，必须和文献翻译排进**同一条串行通道**：两条彼此不
# 相识的通道让两个重活并跑，会互相抢显存与内存、双双变慢。
# 模块不反向 import web_app（会循环导入），沿用 web_app 单向下发句柄的约定：
#   submit_task(kind, name, payload=…, job_id=…) -> job_id     入队
#   get_task(job_id) -> {status, percent, queue_position, …}   查工单
#   task_lane        -> Semaphore(1)，同步入口与队列消费线程共用
_HOOKS = {'submit_task': None, 'get_task': None, 'lane': None}

# 未注入时的兜底串行道：让本模块被单独 import（如 `_tests/test_routes.py`
# 只调 init_stitch）时仍然自洽 —— 退化成「各自串行」，只是没有跨模块互斥。
_FALLBACK_LANE = threading.Semaphore(1)


def _lane():
    """共享串行道。两条路径都要拿它：
    ① 统一队列的消费线程（入队任务执行前后）
    ② 本模块的**同步**入口 `/api/stitch/run`（它不进队列，直接跑）
    没有 ②，一个同步拼合就能和队列里正在跑的翻译并发 —— 正是要避免的情况。
    """
    return _HOOKS.get('lane') or _FALLBACK_LANE


def _trim_tasks():
    """淘汰最旧的已完成/失败任务，保证 `tasks` 有界（dict 保持插入序）。"""
    if len(tasks) <= _TASKS_MAX:
        return
    finished = [k for k, v in tasks.items()
                if v.get('status') in ('done', 'error')]
    for k in finished[:max(0, len(tasks) - _TASKS_MAX)]:
        tasks.pop(k, None)


def _put_task(tid, d):
    with _task_lock:
        tasks[tid] = d
        _trim_tasks()


def _patch_task(tid, **kw):
    with _task_lock:
        t = tasks.get(tid)
        if t is not None:
            t.update(kw)


def _save(img, fmt: str = 'png'):
    """保存全景图（**主存 NAS，失败自动退本地**）。

    默认 **PNG 无损**：JPEG 是有损压缩，会在晶界、裂隙等高频细节上产生
    8×8 块状伪影并丢失色彩精度，对后续测量/发表不利。显微拼图是"一次生成、
    反复使用"的资产，存储换无损是划算的。
    预览用同一个文件（浏览器显示 PNG 没问题），保证"看到的就是下载到的"。

    为什么要有降级：融合一张上亿像素全景要花几分钟到十几分钟，而 `_RESULTS`
    是**启动时**探测出来的。若运行中 NAS 掉线/权限变化，直接抛异常会让整个
    任务以 error 结束 —— 图白算了。这里改为写到本地 `results/` 兜住结果，
    由 `_find_result()` 在读取侧配合找回。
    """
    fmt = (fmt or 'png').lower()
    fname = f'pano_{uuid.uuid4().hex[:12]}.{fmt}'
    if fmt == 'png':
        # 压缩级别 6 是体积/速度的平衡点；显微纹理复杂，级别再高收益很小
        buf = cv2.imencode('.png', img, [cv2.IMWRITE_PNG_COMPRESSION, 6])[1]
    else:
        buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 98])[1]
    roots = [_RESULTS]
    if _LOCAL_RESULTS.resolve() != _RESULTS.resolve():
        roots.append(_LOCAL_RESULTS)
    last = None
    for root in roots:
        try:
            root.mkdir(parents=True, exist_ok=True)
            buf.tofile(str(root / fname))
            if root is not _RESULTS:
                print(f'[stitch] ⚠ 结果已降级写入本地 {root}（NAS 不可写）')
            return fname
        except Exception as exc:
            last = exc
            print(f'[stitch] 保存到 {root} 失败: {exc!r}')
    raise IOError(f'结果保存失败：所有存储位置均不可写（{last!r}）')


def _find_result(filename) -> Optional[Path]:
    """按「NAS 优先、本地兜底」找回结果文件（配合 `_save` 的降级写入）。"""
    if not filename or '/' in filename or '\\' in filename or filename.startswith('.'):
        return None
    for root in (_RESULTS, _LOCAL_RESULTS):
        try:
            p = (root / filename)
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def _parse_opts():
    """解析前端参数。默认值已按显微平面图标定（见 stitcher.StitchConfig）。"""
    def _int(name, dflt, lo, hi):
        try:
            v = int(request.form.get(name, dflt))
        except (TypeError, ValueError):
            v = dflt
        return max(lo, min(hi, v))

    # ★ 羽化已废弃：融合段不使用羽化（消缝靠 BORDER_REPLICATE + 多频带）。
    #   用户口径：默认 0 = 不羽化；即便将来启用也必须 ≤ 10px，否则会稀释
    #   重叠区细节。这里把上限夹到 10，避免 API 侧再传出「大羽化」的意图。
    feather = _int('feather', 0, 0, 10)
    # max_side=0 表示原尺寸；0~3072 之外夹紧，但保留 0
    try:
        max_side = int(request.form.get('max_side', 0))
    except (TypeError, ValueError):
        max_side = 0
    if max_side != 0:
        max_side = max(1024, min(4096, max_side))

    # out_side = 输出图幅档位（全景最长边上限 px）。0 = 自动（按可用内存）。
    # 档位表由 StitchConfig.out_side_tiers 给出，但这里不锁死枚举，
    # 允许 API 直接指定任意尺寸（1024~65536 夹紧），前端只暴露几个常用档。
    try:
        out_side = int(request.form.get('out_side', 0))
    except (TypeError, ValueError):
        out_side = 0
    if out_side != 0:
        out_side = max(1024, min(65536, out_side))

    # 提速开关（运维/排查用；不传即用 StitchConfig 默认 = 都开）。
    # use_gpu: 'auto' | '1' | '0'；coarse: '1' | '0'。
    _ug = request.form.get('use_gpu', 'auto')
    return dict(
        feather_size=feather,
        max_side=max_side,
        out_side=out_side,
        detect_scale_bar=request.form.get('detect_scale_bar', '1') != '0',
        inpaint_holes=request.form.get('inpaint', '0') != '0',
        engine=request.form.get('engine', 'auto'),
        use_gpu={'0': False, '1': True}.get(_ug, None),
        coarse_screen=request.form.get('coarse', '1') != '0',
    )


def _sweep_uploads() -> int:
    """启动清扫：把 uploads 里残留的临时件删干净。

    上传原图是**过程性临时件**，正常路径在 `finally` 里删除。但有三条会漏：
    * 任务线程被强杀（进程退出、supervisor 重启）；
    * 清理时文件仍被占用（Windows 上删除已打开的文件会失败）；
    * `finally` 里的 `os.remove` 静默失败（`except Exception: pass`）。

    漏了没人管就会无限累积。这里在每次启动时兜底清一次。

    只删 **mtime 超过 10 分钟**的文件：正常任务在开头几秒就把图读进内存，此后
    不再碰这些文件；加时间闸门是为了防「测试用 Flask test client 调 init_stitch
    时误删线上正在跑的任务的上传件」（两者共用同一个 uploads 目录）。
    """
    import time as _t
    cutoff = _t.time() - 600
    n = 0
    try:
        for f in _UPLOADS.iterdir():
            if f.is_file():
                try:
                    if f.stat().st_mtime > cutoff:
                        continue
                    f.unlink()
                    n += 1
                except Exception:
                    pass
    except Exception:
        pass
    return n


def init_stitch(app, submit_task=None, get_task=None, task_lane=None,
                register_runner=None, **_ignored):
    """接入统一任务队列。

    ★ `submit_task` 只是「把工单塞进队列」；队列**还必须知道谁来执行**
      （`web_app._TASK_RUNNERS[kind]`）。那一步靠 `register_runner(kind, fn)`
      注入完成 —— **漏掉它就会出现「入了队但没人执行」**：
      队列把工单标成 failed「未注册的任务种类（kind=stitch）」。
      （2026-09-28 实测踩到：`/api/stitch/submit` 返回 `queued:true`，
      队列快照 `by_kind={'stitch':1}` + `failed:1`。）
    """
    # 与 web_app.py 保持一致：显微拼图一次可传上百张大图，2GB 上限。
    app.config.setdefault('MAX_CONTENT_LENGTH', 2 * 1024 * 1024 * 1024)
    app.register_blueprint(stitch_bp)
    _HOOKS['submit_task'] = submit_task
    _HOOKS['get_task'] = get_task
    _HOOKS['lane'] = task_lane
    # 把执行体交给队列（必须在任何 submit 之前完成，故放在 init 里）。
    _runner_ok = False
    if callable(register_runner):
        try:
            register_runner('stitch', _stitch_runner)
            _runner_ok = True
        except Exception as exc:
            print(f'[stitch] 注册队列执行体失败（拼合将退回自串行）: {exc!r}')
    _n = _sweep_uploads()
    if callable(submit_task) and _runner_ok:
        _q = '已接入统一任务队列（执行体已注册）'
    elif callable(submit_task):
        _q = '⚠ 已能入队但**执行体未注册**（任务会立即失败，请检查 register_runner 注入）'
    else:
        _q = '未接入统一队列（自串行兜底）'
    _extra = f'，清扫遗留临时文件 {_n} 个' if _n else ''
    print(f'[模块] 图像拼合 → GET /stitch（{_q}{_extra}）')


@stitch_bp.route('/stitch')
def stitch_page():
    return render_html(_ASSETS / 'index.html')


@stitch_bp.route('/api/stitch/assets/<path:rel>')
def stitch_asset(rel):
    try:
        target = (_ASSETS / rel).resolve()
        target.relative_to(_ASSETS)
    except (ValueError, OSError):
        return Response('/* denied */', status=403, content_type='text/plain; charset=utf-8')
    if not target.is_file():
        return Response('/* not found */', status=404, content_type='text/plain; charset=utf-8')
    resp = send_file(target, conditional=True)
    resp.headers['Content-Type'] = _MIME.get(target.suffix.lower(), 'application/octet-stream')
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


_TORCH_PROBE = None


def _accel_info():
    """匹配加速栈自述（只探测可用性，不做真实显存分配；结果缓存）。

    用于确认 `/stitch` 跑起来时到底是「GPU 距离阵」还是「CPU 全量逐对」——
    没有这个字段时，一个静默的降级（驱动/显存/版本）会让"提速了"变成空话。
    """
    global _TORCH_PROBE
    if _TORCH_PROBE is None:
        try:
            import torch
            dev = None
            cuda = bool(torch.cuda.is_available())
            if cuda:
                try:
                    dev = torch.cuda.get_device_name(0)
                except Exception:
                    dev = 'CUDA'
            _TORCH_PROBE = {'torch': torch.__version__, 'cuda': cuda, 'device': dev}
        except Exception as e:
            _TORCH_PROBE = {'torch': None, 'cuda': False, 'device': None,
                            'error': type(e).__name__}
    return dict(_TORCH_PROBE)


@stitch_bp.route('/api/stitch/health')
def stitch_health():
    try:
        import stitcher as _st
        engine = 'openstitching' if _st._HAS_OST else 'fallback_opencv'
    except Exception:
        _st = None
        engine = 'unknown'
    # 画布像素预算（MP）：前端「推荐」档位要用它收口，否则会推荐出一个注定被
    # 内存兜底的档位（例如本机 16K/12K 档实际到不了）。融合阶段按需读盘，
    # 故 holding_frames=False —— 与 `Stitcher.stitch` 里下传的值同口径。
    budget_mp = None
    try:
        if _st is not None:
            budget_mp = round(_st.canvas_pixel_budget(1, 0, holding_frames=False) / 1e6, 1)
    except Exception:
        budget_mp = None
    accel = _accel_info()
    if _st is not None:
        accel['use_gpu'] = getattr(_st.StitchConfig, 'use_gpu', None)
        accel['coarse_screen'] = getattr(_st.StitchConfig, 'coarse_screen', None)
        # 预热并行度（W0，2026-09-28）：帧间无依赖 ⇒ 并行算 SIFT。
        accel['warm_workers'] = getattr(_st.StitchConfig, 'warm_workers', None)
    try:
        import fallback as _fb
        # 内容 warp 输出原点 ROI 化（W1，2026-09-28）：只算垂直窗口那一段。
        # ★ 掩膜 warp 保持整画布（NEAREST 的 ROI 化实测非逐位）。
        accel['warp_roi'] = bool(getattr(_fb, 'WARP_ROI', False))
    except Exception:
        accel['warp_roi'] = None
    return {'ok': True, 'service': 'stitcher', 'cv2': cv2.__version__,
            'engine': engine,
            'uploads_dir': str(_UPLOADS),
            'results_dir': str(_RESULTS),
            'results_on_nas': str(_RESULTS).startswith(str(_NAS_ROOT)),
            'canvas_budget_mp': budget_mp,
            'accel': accel}


@stitch_bp.route('/api/stitch/run', methods=['POST'])
def stitch_run():
    files = request.files.getlist('files')
    if len(files) < 2:
        return jsonify({'ok': False, 'error': '至少上传 2 张图像'}), 400
    opts = _parse_opts()

    paths = []
    for f in files:
        ext = os.path.splitext(f.filename)[1] or '.jpg'
        p = str(_UPLOADS / f"{uuid.uuid4().hex}{ext}")
        f.save(p); paths.append(p)

    t = {'status': 'running', 'progress': [], 'percent': 5}
    def _onp(msg, pct):
        t['progress'] = list(s.progress)   # live snapshot
        t['percent'] = pct
    s = None
    # 与统一队列共用串行道：同步入口不进队列，但必须和「队里正在跑的任务」
    # 互斥，否则一个同步拼合就能和翻译并发跑（正是要避免的 GPU/内存互抢）。
    _lane().acquire()
    try:
        s = Stitcher(progress_callback=_onp, **opts)
        result, meta = s.stitch(paths)
        fname = _save(result)
        return jsonify({
            'ok': True,
            'result': {
                'filename': fname,
                'url': f'/api/stitch/result/{fname}',
                'size_kb': round((_find_result(fname) or _RESULTS / fname).stat().st_size / 1024, 1),
                'width': result.shape[1], 'height': result.shape[0],
                'meta': meta,
            },
            'meta': meta, 'progress': s.progress,
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e),
                        'progress': s.progress if s else []}), 500
    finally:
        try:
            _lane().release()
        except Exception:
            pass
        for p in paths:
            try: os.remove(p)
            except Exception: pass


class _NullCtx:
    """未接入统一队列时的占位句柄：只让执行体跑通，不写任何队列状态。

    `lane_held = False` 是**关键**：这条路径不经过队列调度线程，没有别人替它
    持共享串行道，所以执行体必须自己 acquire —— 否则一个兜底自跑就能和队列
    里正在跑的翻译并发（正是要避免的 GPU/内存互抢）。
    """

    lane_held = False

    __slots__ = ('payload',)

    def __init__(self, payload=None):
        self.payload = payload or {}

    def report(self, **kw): pass
    def finish(self, **kw): pass
    def cancelled(self): return False


def _drop_task_uploads(task_id):
    """删掉某任务的临时上传件。

    正常路径由执行体的 `finally` 删除。但「排队期间被取消」的任务执行体从未
    运行过 —— `finally` 不会触发，文件就留在 uploads 里了。
    """
    try:
        for f in _UPLOADS.glob(f'{task_id}_*'):
            try:
                f.unlink()
            except Exception:
                pass
    except Exception:
        pass


def _stitch_runner(job_id, ctx):
    """统一队列里的拼合执行体（由 web_app 的调度线程调用）。

    进度**双写**：
      ① 统一队列 —— 页面「任务队列」用它，同时维护心跳（看门狗判活的依据）；
      ② 本模块 `tasks` 表 —— `/api/stitch/task` 的日志流与控制台输出用它。

    ⚠️ 串行道的归属：走队列时调度线程在 join 本任务期间**已持着** TASK_LANE
    （见 web_app._queue_worker），这里再 acquire 一次就会自锁、只能等硬超时
    被误判失败。所以只在**兜底自跑**路径（ctx.lane_held 为假）自己拿锁。
    """
    try:
        paths = list((ctx.payload or {}).get('paths') or [])
        opts = dict((ctx.payload or {}).get('opts') or {})
    except Exception:
        paths, opts = [], {}
    s = None
    need_lane = not getattr(ctx, 'lane_held', False)

    def _onp(msg, pct):
        prog = list(s.progress) if s else []
        _patch_task(job_id, progress=prog, percent=pct)
        ctx.report(percent=pct, message=msg, log=prog)

    if need_lane:
        _lane().acquire()
    try:
        s = Stitcher(progress_callback=_onp, **opts)
        result, meta = s.stitch(paths)
        fname = _save(result)
        _f = _find_result(fname)
        # 「成品图指引」分两个粒度下发，别混：
        #   ① 本模块 tasks 表（/api/stitch/task/<id>）—— 带完整 meta（逐对判据明细）；
        #   ② 统一队列的 job（/api/queue）—— 只带**精简版**。
        # ② 必须精简的理由：91 张任务的 meta 有 4095 条逐对判据（见文件头注），
        #   而 /api/queue 是前端 2s 一次的高频轮询，背着它等于每次轮询搬一遍大 JSON。
        # ★ 键名叫 `result`（不是 artifacts）：web_app._queue_snapshot 的字段白名单
        #   里放行的就是它，改名要两边同时改。
        # 结果指引**同时给相对与绝对两套地址**，消费方不同，别互相顶替：
        #   · `url` / `download_url` = **相对路径**，跟随访问入口（同主机、同协议）。
        #     拼合页 `assets/app.js` 拿它当**内嵌资源**（`<img src>`、`<a download>`）——
        #     若这里换成 `http://…` 绝对地址，页面一旦从 **https** 打开就会被浏览器的
        #     混合内容策略拦掉（内嵌资源不允许降级到 http）。所以这对键**必须保持相对**。
        #   · `public_url` / `public_download_url` = **NAS 对外入口**（见 PUBLIC_BASE），
        #     给「分享出去 / 换入口打开 / 过一会儿再看」的**链接**用 —— 队列页取的就是它。
        info = {
            'filename': fname,
            'url': f'/api/stitch/result/{fname}',
            'download_url': f'/api/stitch/result/{fname}/dl',
            'public_url': _public_url(f'/api/stitch/result/{fname}'),
            'public_download_url': _public_url(f'/api/stitch/result/{fname}/dl'),
            'size_kb': (round(_f.stat().st_size / 1024, 1)
                        if _f is not None else None),
            # int() 是必要的：shape 元素虽多为 Python int，但进了 jsonify 前
            # 不能有任何 numpy 标量残留（否则 TypeError: not JSON serializable）
            'width': int(result.shape[1]), 'height': int(result.shape[0]),
        }
        _put_task(job_id, {
            'status': 'done', 'progress': s.progress, 'percent': 100,
            'queued': False, 'error': None,
            'result': dict(info, meta=meta),
        })
        ctx.finish(message='完成', result=info)
    except Exception as e:
        _put_task(job_id, {
            'status': 'error', 'error': str(e),
            'progress': s.progress if s else [],
            'percent': s.percent if s else 0,
            'queued': False,
        })
        raise                      # 失败判定交给队列，保持单一可信来源
    finally:
        if need_lane:
            try:
                _lane().release()
            except Exception:
                pass
        for p in paths:
            try: os.remove(p)
            except Exception: pass


def _spawn_local(task_id, payload):
    """兜底：未注入统一队列时（如只 init_stitch 的离线测试）自起线程跑。"""
    def _go():
        try:
            _stitch_runner(task_id, _NullCtx(payload))
        except Exception:
            pass
    threading.Thread(target=_go, daemon=True,
                     name='stitch-local-' + task_id[:8]).start()


@stitch_bp.route('/api/stitch/submit', methods=['POST'])
def stitch_submit():
    files = request.files.getlist('files')
    if len(files) < 2:
        return jsonify({'ok': False, 'error': '至少上传 2 张图像'}), 400
    opts = _parse_opts()

    task_id = uuid.uuid4().hex[:12]
    paths = []
    for f in files:
        ext = os.path.splitext(f.filename)[1] or '.jpg'
        p = str(_UPLOADS / f"{task_id}_{uuid.uuid4().hex[:8]}{ext}")
        f.save(p); paths.append(p)

    _put_task(task_id, {'status': 'running', 'progress': [], 'percent': 2,
                        'result': None, 'error': None,
                        'queued': False, 'submitted_at': time.time()})

    payload = {'paths': paths, 'opts': opts}
    submit = _HOOKS.get('submit_task')
    if callable(submit):
        # ★ 入「统一任务队列」：与文献翻译共用同一条串行通道。
        #   把本模块的 task_id 直接当工单号，省掉一层 id 映射。
        try:
            jid = submit('stitch', f'拼合 {len(files)} 张',
                         payload=payload, job_id=task_id) or task_id
        except Exception as exc:
            # 入队失败（队列未起等）→ 退回自跑。宁可串行保护弱一点，也别丢任务。
            print(f'[stitch] 入统一队列失败，改为自跑: {exc!r}')
            _spawn_local(task_id, payload)
            return jsonify({'ok': True, 'task_id': task_id, 'queued': False})
        return jsonify({'ok': True, 'task_id': jid, 'queued': True})

    _spawn_local(task_id, payload)
    return jsonify({'ok': True, 'task_id': task_id, 'queued': False})


@stitch_bp.route('/api/stitch/task/<task_id>')
def stitch_task(task_id):
    """任务查询。

    工单状态/位次以**统一队列**为准；结果 payload 留在本模块有界的 `tasks`
    表（含 91 张任务 ~935 KB 的逐对判据明细，不适合塞进队列的 jobs）。
    返回形状与历史保持一致（status: running / done / error），前端不必改。
    """
    local = tasks.get(task_id)
    getter = _HOOKS.get('get_task')
    snap = None
    if callable(getter):
        try:
            snap = getter(task_id)
        except Exception:
            snap = None

    if snap is None and local is None:
        return jsonify({'ok': False, 'error': '未找到任务（可能已被淘汰或服务重启）'}), 404

    if snap is None:
        # 队列里查不到（服务重启过）→ 退回本模块自己的记录
        out = {'ok': True, 'task_id': task_id}
        out.update(local or {})
        out['ok'] = True
        out.setdefault('status', 'running')
        return jsonify(out)

    qs = snap.get('status')
    _SMAP = {'queued': 'running', 'translating': 'running',
             'done': 'done', 'failed': 'error', 'cancelled': 'error'}
    out = {
        'ok': True,
        'task_id': task_id,
        'kind': snap.get('kind'),
        'status': _SMAP.get(qs, 'running'),
        'queued': (qs == 'queued'),
        'queue_position': snap.get('queue_position') or 0,
        'percent': snap.get('percent') or (local or {}).get('percent') or 0,
        'message': snap.get('message'),
    }

    log = list((local or {}).get('progress') or [])
    if qs == 'queued':
        pos = out['queue_position']
        log = [(f'已排队（第 {pos} 位）· 等待前一个任务完成…' if pos
                else '已排队 · 等待前一个任务完成…')] + log[-40:]
    elif not log:
        log = list(snap.get('progress_log') or [])
    out['progress'] = log

    if local:
        out['result'] = local.get('result')
        out['error'] = local.get('error')
        if qs == 'cancelled':
            out['error'] = '已取消'
            out['message'] = '已取消'
            if local.get('status') == 'running':
                # 排队期被取消 → 执行体从未跑过，finally 不会删临时件，这里补删
                _put_task(task_id, {'status': 'error', 'error': '已取消',
                                    'percent': 0, 'queued': False})
                _drop_task_uploads(task_id)
        elif out['status'] == 'error':
            out['error'] = snap.get('error') or local.get('error')
    else:
        out['result'] = None
        out['error'] = snap.get('error')
    return jsonify(out)


@stitch_bp.route('/api/stitch/result/<filename>')
def stitch_result(filename):
    target = _find_result(filename)
    if target is None:
        return Response('not found', status=404)
    return send_file(target, conditional=True,
                      mimetype=_MIME.get(target.suffix.lower(), 'image/jpeg'))


@stitch_bp.route('/api/stitch/result/<filename>/dl')
def stitch_download(filename):
    target = _find_result(filename)
    if target is None:
        return Response('not found', status=404)
    return send_file(target, as_attachment=True, download_name=filename)
