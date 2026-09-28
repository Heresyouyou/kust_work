# -*- coding: utf-8 -*-
"""显微图像拼接核心算法

## 与旧版的根本区别

旧版把「拼接」实现成了「把图拖到画布上」：SIFT 匹配的结果不做几何验证，
非重叠图对产生的假位姿（位移≈0）被当作完美对齐，于是所有图叠在原点，
中间糊成一片。见 `HANDOVER.md` 第二节 Bug 2/3/4。

本版的核心是一个**几何验证层**（`geometry.py`）：只有通过三条判据的图像对
才被认可为「真的重叠」，然后才在可信的匹配图上做位姿求解与融合。

## 引擎优先级

实测结论（2026-09-23，ZK203-81.5-T2 真数据）：

1. **`fallback.py`（纯 OpenCV，默认引擎）** —— 完整相似变换 bundle adjustment +
   `warpAffine(INTER_LANCZOS4)` + Laplacian 多频带融合。
   18 张原图 52~87s 跑通，重叠区**中位残差 5~10 灰阶**，接缝连续无重影。

2. `stitching` (OpenStitching) —— 交接文档建议的升级方向，但**本机实测不可用作主引擎**：
   - `estimator='affine'` 在 OpenCV 4.13 与 5.0 上都会在 warper 里
     `cv::UMat::create` 断言失败（即 `HANDOVER.md` Bug 1；那篇文档归因于显存不足，
     实测**与内存无关**，关掉 OpenCL 也照崩，是 OpenCV 自身的缺陷）。
   - `estimator='homography'`（唯一能跑通的分支）在 **18 张规则网格**上会估出
     **退化的相机参数**：焦距 f≈1.0 像素，于是每张图被当成极端透视投影，
     融合后整幅呈**放射状拖影**（实拍验证，见 `_dbg/v18_*.jpg`）。
     6 张以内小批量正常，规模一大就崩。
   → 因此仅作为可选项（`engine='openstitching'`），且每次失败都自动回落到本引擎。

## OpenCL

OpenCV 5.0 的 Stitcher 内部会隐式分配 UMat，即使 `setUseOpenCL(False)` 也会
`u != 0` 断言失败并硬崩进程。故本模块在 import cv2 之前就设好环境变量，
并在 import 后立刻关闭 OpenCL。
"""

import os as _os

# ── 必须在 import cv2 之前（cv2 导入时就会加载 OpenCL runtime） ──
_os.environ.setdefault('OPENCV_OPENCL_RUNTIME', 'disabled')
_os.environ.setdefault('OPENCV_OPENCL_DEVICE', 'disabled')

import cv2
cv2.ocl.setUseOpenCL(False)   # 防 UMat create 崩溃

import numpy as np
from typing import Dict, List, Sequence, Tuple
import os
import threading
import time
import uuid

from geometry import (OverlapVerifier, MatchEvidence, connected_components,
                      WARM_WORKERS as G_WARM_WORKERS)

# 可选依赖：OpenStitching
try:
    import stitching as ost
    _HAS_OST = True
except Exception:
    ost = None
    _HAS_OST = False


# ════════════════════════════════════════════════════════════
# 默认参数（针对显微平面图标定）
# ════════════════════════════════════════════════════════════

class StitchConfig:
    """拼接参数。默认值按「2752×2208 显微切片、连续平移拍摄」标定。"""

    # 匹配
    nfeatures = 8000
    detector = "sift"
    min_shift_ratio = 0.04      # 位移 >= 4% 短边（≈88px）才算真重叠
    min_hull_ratio = 0.03       # 内点凸包 >= 3% 图像面积（绝对口径）
    min_hull_norm = 0.30        # 内点凸包 >= 30% 理论重叠区（尺度无关；与 min_hull_ratio 是 OR，
                                # 并兼任「平移一致性判据」的或分支 —— 大位移对内点率被稀释时的救回，
                                # 标定见 geometry.py 文件头 MIN_HULL_NORM 注释）
    min_inlier_ratio = 0.50     # 内点率 >= 50%

    # ── 匹配提速（2026-09-24 实测落地） ──
    # 两条都**可关**：关掉即回到「全量逐对 + BFMatcher」，结果口径不变。
    # 1) 粗筛：1/8 尺度先把明显不重叠的对剔掉。62 张实测 1891 对 → 候选 841 对
    #    (44.5%)，接受边漏 0 多 0；叠加连通性兜底，不连通就自动全量补齐。
    # 2) GPU 距离阵：torch.cdist 替 BFMatcher（14.2ms/对 vs 107ms/对，~7.5×）。
    #    OpenCV 是非 CUDA 构建（cv2.cuda 无 SIFT/warpAffine），所以走 torch。
    use_gpu = "auto"            # "auto" | True | False
    coarse_screen = True
    coarse_scale = 8
    coarse_nfeatures = 1000
    coarse_good_min = 20
    # 3) 并行预热：逐帧「解码 → 细 SIFT → 粗 SIFT」本就帧间无依赖，可并行。
    #    91 张实测 46.2s → 32.8s（1.41×）。warm_workers<=1 即退回串行（旧行为）。
    #    等价性由 `_crit_warm_equiv.py` 逐张 `np.array_equal` 证明（特征逐位相同）。
    warm_workers = G_WARM_WORKERS

    # 融合
    # ★ feather 已废弃：融合段**不使用羽化**（消缝靠 BORDER_REPLICATE + 多频带）。
    #   旧版 400px、后改 100px 都会稀释重叠区细节；用户口径：默认不羽化，
    #   若将来启用必须 ≤ 10px。这里保留 0 只为兼容旧调用方。
    feather = 0
    blend_strength = 5
    compensator = "gain_blocks"  # 消除照明不均导致的接缝亮度跳变

    # 输出保护
    max_canvas_pixels = 300_000_000  # 绝对上限（安全阀）；真正的约束由内存预算决定

    # 输出图幅档位（全景最长边上限，px）。0 = 自动（按可用内存，且保标定）。
    # 档位是**上限**：超出则整体等比缩小，不放大。
    # 细分到 ~1.25 倍一档（16K→1K），方便按"等效 µm/px"挑合适的出力；
    # 前端按大/中/轻分组显示，并另给「自定义」自由填 1024~65536。
    out_side_tiers = (0, 16384, 12288, 10240, 8192, 6144, 5120, 4096,
                      3072, 2560, 2048, 1536, 1280, 1024)


# ── 融合阶段的内存预算 ──
# `fallback.multiband_blend` 的峰值内存与**画布像素数**成正比，与图片数无关
# （Stage A 改造后，详见 fallback 模块 docstring）。
# 实测标定：T2-C 8 张、画布 22.24 MP、关闭 OpenCV 线程 → 峰值 1.45 GB ≈ **65 B/px**。
# 取 1.15 倍安全系数 → 75 B/px。
BLEND_BYTES_PER_PIX = 75
MEM_RESERVE_BYTES = 1 * 1024 ** 3
SIFT_DESC_BYTES_PER_IMAGE = 8000 * 128 * 4      # ≈ 4.1 MB/张


def canvas_pixel_budget(n_images: int, frame_pixels: int,
                        holding_frames: bool = True) -> int:
    """按当前可用内存推算本次拼接的画布像素上限。

    旧实现写死 `MAX_PIX = 90_000_000`，与内存无关。按实测的 65 B/画布像素，
    9000 万像素需要约 5.9GB —— 本机（总 15.6GB、常态空闲约 5GB）其实跑不动，
    这正是大画布任务 OOM 的根因。

    改为按真实可用内存推算，并**逐项扣掉已经在占用的东西**：
    * 内存紧张时自动降分辨率，先把结果做出来（不再 OOM）；
    * 内存宽裕时自动用更高分辨率（不再被写死的 9000 万卡住）。

    `holding_frames=True` 表示融合阶段仍会持有整批源图与描述子；
    若调用方改成按需读盘（`_LazyImages`），传 False 可把这部份占用释放给画布。
    """
    import psutil
    try:
        avail = psutil.virtual_memory().available
    except Exception:
        return StitchConfig.max_canvas_pixels

    held = 0
    if holding_frames:
        held += 3 * int(frame_pixels) * int(n_images)      # 源图（BGR）
        held += SIFT_DESC_BYTES_PER_IMAGE * int(n_images)  # 描述子缓存

    budget = avail - MEM_RESERVE_BYTES - held
    if budget <= 0:
        return 4_000_000                      # 极限兜底：至少给 4MP
    pix = int(budget / BLEND_BYTES_PER_PIX)
    return max(4_000_000, min(pix, StitchConfig.max_canvas_pixels))


def _image_size(path: str):
    """只读文件头拿 (w, h)，**不解码像素**。失败返回 None。

    与前端 `assets/app.js` 的 `probeImageSize()` 同一套判据（JPEG SOF / PNG IHDR），
    只读前 64KB。P0-2 用它替代「为了拿尺寸而解码整批图」：91 张 2752×2208 的
    JPEG 解码约 10~20s，而读文件头是毫秒级。
    """
    try:
        with open(path, 'rb') as fh:
            b = fh.read(65536)
    except Exception:
        return None
    if len(b) < 26:
        return None
    if b[0] == 0x89 and b[1] == 0x50 and b[2] == 0x4E and b[3] == 0x47:   # PNG
        return (int.from_bytes(b[16:20], 'big'), int.from_bytes(b[20:24], 'big'))
    if b[0] != 0xFF or b[1] != 0xD8:                                     # 非 JPEG
        return None
    i = 2
    n = len(b)
    while i + 4 <= n:
        if b[i] != 0xFF:
            i += 1
            continue
        m = b[i + 1]
        if m == 0xFF or m == 0x01 or 0xD0 <= m <= 0xD8:
            i += 2
            continue
        if m == 0xDA or m == 0xD9:          # 进入扫描数据，后面没有 SOF
            break
        seg = (b[i + 2] << 8) | b[i + 3]
        if seg < 2:
            break
        # SOF0~SOF15，但 C4=DHT、C8=JPG、CC=DAC 不是 SOF
        if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
            if i + 9 > n:
                break
            return ((b[i + 7] << 8) | b[i + 8], (b[i + 5] << 8) | b[i + 6])
        i += 2 + seg
    return None


# `_LazyImages` 是否启用**单步预读**（P1-2，2026-09-27）。
# 取第 k 张时后台顺手解码第 k+1 张，把 JPEG 解码（libjpeg 单线程）与主线程的
# SIFT / warp 计算重叠起来。**只预读 1 张**，常驻仍≈1 张（最多 2 张）。
# 实测收益有限（解码总量本来就只占全流程个位数秒），设 False 即回到纯同步解码。
LAZY_PREFETCH = True


class _LazyImages:
    """按需读盘的图像序列（可选单步预读）。

    融合/匹配都只用每张图一次，可是旧实现把整批解码结果一直挂在内存里：
    `3 × 单帧字节 × n`，91 张 2752×2208 就是 **1.66GB**，白白挤占了本可
    用来提高画布分辨率的额度。

    本类按索引现读现用、不在内部缓存（`__getitem__` 返回的数组由调用方用完释放），
    因此常驻占用为 0（预读开启时最多多留 1 张在途）。

    `prefetch=True` 时用**一个后台线程**预解码下一张（见模块级 `LAZY_PREFETCH`）。
    ⚠️ 只在**顺序访问**时预读（匹配预热、融合取图都是顺序的）；跨步访问
    （如比例尺采样的 `main_images[i]`）不触发，免得白解码。
    """

    def __init__(self, paths: Sequence[str], max_side: int = 0,
                 prefetch: bool = False):
        self._paths = list(paths)
        self._max_side = int(max_side)
        self._pf_on = bool(prefetch) and len(self._paths) > 1
        self._pf_slot: Dict[int, tuple] = {}     # 最多 1 项：{idx: (img|None, exc|None)}
        self._pf_want = None                     # 当前「还想要」的预读下标
        self._pf_thread = None
        self._pf_last = None                     # 上一次被取的下标（判是否顺序）
        self._pf_lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._paths)

    def _decode(self, k):
        p = self._paths[k]
        img = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError(f"无法读取: {os.path.basename(p)}")
        if self._max_side > 0:
            h, w = img.shape[:2]
            long_side = max(h, w)
            if long_side > self._max_side:
                s = self._max_side / long_side
                img = cv2.resize(img, (int(w * s), int(h * s)),
                                 interpolation=cv2.INTER_AREA)
        return img

    def _pf_worker(self, k):
        try:
            res = (self._decode(k), None)
        except Exception as exc:
            res = (None, exc)
        with self._pf_lock:
            if self._pf_want == k:       # 仍被需要才留下；否则丢弃（避免积压占内存）
                self._pf_slot = {k: res}
            self._pf_thread = None

    def _start_prefetch(self, k):
        if not self._pf_on or k >= len(self._paths):
            return
        with self._pf_lock:
            if self._pf_thread is not None and self._pf_thread.is_alive():
                return                    # 上一张还没读完，不排队
            if k in self._pf_slot:
                return
            self._pf_want = k
            self._pf_thread = threading.Thread(target=self._pf_worker, args=(k,),
                                               daemon=True)
            self._pf_thread.start()

    def __getitem__(self, k):
        if isinstance(k, slice):
            return [self[i] for i in range(*k.indices(len(self)))]
        with self._pf_lock:
            got = self._pf_slot.pop(k, None)
        if got is None:
            img = self._decode(k)          # 预读没赶上 → 同步解码（结果不变）
        else:
            img, exc = got
            if exc is not None:
                raise exc
        with self._pf_lock:
            seq = (self._pf_last is None) or (k == self._pf_last + 1)
            self._pf_last = k
        if seq:
            self._start_prefetch(k + 1)
        return img


# ════════════════════════════════════════════════════════════
# 主类
# ════════════════════════════════════════════════════════════

class Stitcher:
    def __init__(self,
                 feather_size: int = 0,
                 detect_scale_bar: bool = False,
                 inpaint_holes: bool = False,
                 max_side: int = 0,
                 out_side: int = 0,
                 progress_callback=None,
                 engine: str = "auto",
                 use_gpu=None,
                 coarse_screen=None):
        # feather_size 已废弃：融合段不使用羽化（见 StitchConfig.feather）。
        # 默认 0 = 不羽化；上限口径 ≤ 10px。
        self.feather_size = min(int(feather_size or 0), 10)
        self.detect_scale_bar = detect_scale_bar
        self.inpaint_holes = inpaint_holes
        self.max_side = max_side
        self.out_side = int(out_side or 0)
        self.progress_callback = progress_callback
        self.engine = engine
        # 提速开关：None = 用 StitchConfig 的默认（per-request 覆盖留给运维）
        self.use_gpu = use_gpu
        self.coarse_screen = coarse_screen
        self.percent = 0
        self.progress: List[str] = []

    # ── 日志 / 进度 ──
    def _log(self, msg: str, percent: int = -1):
        self.progress.append(msg)
        if percent >= 0:
            self.percent = int(percent)
        if self.progress_callback:
            try:
                self.progress_callback(msg, self.percent)
            except Exception:
                pass
        print(msg, flush=True)

    # ── 缩放 ──
    def _resize_if_needed(self, img: np.ndarray) -> Tuple[np.ndarray, float]:
        if self.max_side <= 0:
            return img, 1.0
        h, w = img.shape[:2]
        long_side = max(h, w)
        if long_side <= self.max_side:
            return img, 1.0
        scale = self.max_side / long_side
        return cv2.resize(img, (int(w * scale), int(h * scale)),
                          interpolation=cv2.INTER_AREA), scale

    # ── 内存安全检查（交接文档 Phase 3，必须保留） ──
    def _check_memory(self, image_paths: Sequence[str]):
        """前置内存检查。

        注意：本检查现在**只覆盖「读图 + 特征匹配」阶段**。融合阶段的画布大小事前
        未知，改由 `canvas_pixel_budget()` 按可用内存动态限幅（不会再 OOM）。

        旧实现用 `× 5` 的粗估把两个阶段混在一起，且没考虑画布项：91 张会被估成
        8.3GB 直接拒掉 —— 而真正吃内存的融合阶段反而没被算进去。
        现在（P0-2 之后）按「描述子缓存 + 单帧 + 固定工作集」计，留 2 倍余量。
        """
        import psutil as _ps
        free_gb = _ps.virtual_memory().available / (1024 ** 3)
        try:
            first = cv2.imdecode(np.fromfile(image_paths[0], dtype=np.uint8),
                                 cv2.IMREAD_COLOR)
            if first is not None:
                first, _ = self._resize_if_needed(first)
            avg_bytes = first.nbytes if first is not None else 2752 * 2208 * 3
            del first
        except Exception:
            avg_bytes = 2752 * 2208 * 3
        n = len(image_paths)
        # P0-2（2026-09-27）：匹配阶段**不再常驻整批解码图** —— 源图改为按需读盘
        # （`_LazyImages`），同一时刻只留 1 张。占用因此只剩三项：
        #   ① SIFT 描述子缓存（`SIFT_DESC_BYTES_PER_IMAGE` × n，**这是主项**）
        #   ② 单帧 + 预缩放副本（avg_bytes × 2）
        #   ③ SIFT/OpenCV 的固定工作集（约 0.25GB）
        # 旧口径 `avg_bytes × n × 2.5`（91 张 = 4.14GB）里 90% 是那批已经不再常驻的
        # 解码图，会把本来跑得动的任务误判成「内存不足」而拒掉。
        desc = SIFT_DESC_BYTES_PER_IMAGE * n
        est_gb = (desc * 2.0 + avg_bytes * 2.0 + 256 * 1024 ** 2) / (1024 ** 3)
        self._log(f"内存检查(匹配阶段): 约需 {est_gb:.1f}GB, 当前可用 {free_gb:.1f}GB",
                  percent=3)
        if free_gb < est_gb + 0.5:
            raise MemoryError(
                f"内存不足: {n} 张原图做特征匹配约需 {est_gb:.1f}GB, "
                f"当前只剩 {free_gb:.1f}GB。"
                f"建议: 关闭其他程序释放内存, 或在高级选项里限制 max_side。"
            )

    # ── 尺度条抑制 ──
    # ── 比例尺白框（叠加物）定位 ──
    # 叠加物特征：近纯白(≥235) 的实心矩形块，内部有一条黑尺线与黑字。
    # 旧实现「取最大亮连通域、面积须 3000~80000」在本批真实数据上**6 帧漏 5 帧**：
    #   ① 内部的黑尺线/黑字把白色区域**切成两半**，"最大连通域"拿不到整框；
    #   ② 视野里常有比它更大更亮的矿物，面积上限一超就被整个丢掉。
    # 改为：闭运算先把内部黑线填实 → 再按矩形容器特征筛（填充率/长宽比/尺寸）
    # → 位置越靠右下越可信；最后跨帧取中位数（叠加物在整批里位置尺寸恒定）。
    def _detect_scale_bar_box(self, img):
        """返回叠加物外接框 (x, y, w, h)；检不到返回 None。"""
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        _, wm = cv2.threshold(gray, 235, 255, cv2.THRESH_BINARY)
        k = max(15, min(61, int(round(min(h, w) / 60.0)) | 1))
        wm = cv2.morphologyEx(wm, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
        num, _lab, stats, _ = cv2.connectedComponentsWithStats(wm)
        best, best_score = None, -1.0
        for i in range(1, num):
            x, y, bw, bh, ba = (int(v) for v in stats[i])
            if bw < 40 or bh < 20:
                continue
            if bw > w * 0.6 or bh > h * 0.6:
                continue
            rect = ba / float(bw * bh)
            if rect < 0.80:                       # 必须是实心矩形
                continue
            ar = bw / float(bh)
            if not (1.1 <= ar <= 5.0):            # 标签式长宽比
                continue
            # 叠加物内必有一条贯穿的**黑尺线**（还有黑字）。用这个强特征做确认，
            # 排除"一块亮矿物"被当成标签 —— 闭运算偶尔会把上方亮区并进来使框变高，
            # 但有黑尺线这条判据仍成立，所以靠它保精度、靠跨帧中位数保稳定。
            sub = gray[y:y + bh, x:x + bw]
            if sub.size == 0 or not ((sub < 90).mean(axis=1) > 0.5).any():
                continue
            score = 0.6 * rect + 0.4 * ((x + y) / float(max(1, w + h - bw - bh)))
            if score > best_score:
                best_score, best = score, (x, y, bw, bh)
        return best

    def _scale_bar_box(self, main_images, sample: int = 10):
        """跨帧共识定位。返回 (x, y, w, h) 或 None。

        叠加物由采集软件烧在图上的固定位置，单帧检测偶有偏差（闭运算把上方亮矿物
        并进来 → 框变高）。故抽样若干帧取中位数，并用「相对中位数的容差」剔掉
        离群框后重算 —— 要求足够多的帧互相同意才认，否则宁可不遮（漏遮只是
        留个白标签，错遮会真的擦掉内容）。
        """
        n = len(main_images)
        if n <= 0:
            return None
        m = max(2, min(int(sample), n))
        idxs = sorted({int(round(i * (n - 1) / max(1, m - 1))) for i in range(m)})
        boxes = []
        for i in idxs:
            try:
                b = self._detect_scale_bar_box(main_images[i])
            except Exception:
                b = None
            if b:
                boxes.append(b)
        if len(boxes) < 2:
            return None
        arr = np.array(boxes, dtype=np.float64)
        med = np.median(arr, axis=0)
        keep = ((np.abs(arr[:, 0] - med[0]) <= max(8.0, 0.10 * med[2])) &
                (np.abs(arr[:, 1] - med[1]) <= max(8.0, 0.10 * med[3])) &
                (np.abs(arr[:, 2] - med[2]) <= max(4.0, 0.15 * med[2])) &
                (np.abs(arr[:, 3] - med[3]) <= max(4.0, 0.15 * med[3])))
        if int(keep.sum()) < max(2, int(0.4 * len(arr))):
            return None
        return tuple(int(round(v)) for v in np.median(arr[keep], axis=0))

    @staticmethod
    def _scale_bar_mask_from_box(box, shape, pad: int = 8) -> np.ndarray:
        h, w = shape[:2]
        x, y, bw, bh = box
        mask = np.zeros((h, w), np.float32)
        mask[max(0, y - pad):min(h, y + bh + pad),
             max(0, x - pad):min(w, x + bw + pad)] = 1.0
        return mask

    def _detect_scale_bar(self, img: np.ndarray) -> np.ndarray:
        """兼容旧接口：单帧检测并返回掩膜。"""
        b = self._detect_scale_bar_box(img)
        if b is None:
            return np.zeros(img.shape[:2], np.float32)
        return self._scale_bar_mask_from_box(b, img.shape[:2], pad=8)

    # ════════════════════════════════════════════════════════
    # 主入口
    # ════════════════════════════════════════════════════════
    def stitch(self, image_paths: Sequence[str]) -> Tuple[np.ndarray, Dict]:
        self.progress = []
        self.percent = 0
        meta: Dict = {
            'n_images': len(image_paths),
            'method': '',
            'warnings': [],
            'matches': [],
            'groups': [],
            'used_indices': [],
            'coverage_stats': {},
        }

        if len(image_paths) < 2:
            raise ValueError("至少需要 2 张图像才能拼接")

        self._check_memory(image_paths)

        # ── 读图（P0-2：只取尺寸，不再整批驻留解码图）──
        # 旧实现把 n 张解码图一直挂在内存里（91 张 2752×2208 = **1.66GB**），
        # 只为让匹配阶段的 `features()` 反复取用。现在特征按内容键缓存、
        # 由 `all_pairs` 的预热阶段一次性算好 ⇒ 匹配期**同一时刻只需 1 张图**。
        # 尺寸改走文件头（毫秒级）而不是解码（91 张约 10~20s）：
        #   · 头部能读出来 → 记尺寸，全程不解码；
        #   · 头部读不出来 → 退回真实解码验证一次，失败才按「无法读取」跳过
        #     （保持与旧实现一致的告警口径）。
        scales: List[float] = []
        kept_paths: List[str] = []      # 与 sizes 同序，供匹配/融合阶段按需重读
        sizes: List[Tuple[int, int]] = []
        for p in image_paths:
            wh = _image_size(p)
            if wh is None:
                img = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    meta['warnings'].append(f"无法读取: {os.path.basename(p)}")
                    continue
                wh = (img.shape[1], img.shape[0])
                del img
            w0, h0 = int(wh[0]), int(wh[1])
            long_side = max(w0, h0)
            if self.max_side > 0 and long_side > self.max_side:
                s = self.max_side / long_side
                # ⚠️ 必须与 `_resize_if_needed()` / `_LazyImages.__getitem__()`
                #    用**同一个式子**（`int(w*s)`），否则尺寸对不上、`hull_norm`
                #    的理论重叠区就会算错。
                sizes.append((int(h0 * s), int(w0 * s)))
            else:
                s = 1.0
                sizes.append((h0, w0))
            scales.append(s)
            kept_paths.append(p)
        meta['n_images_loaded'] = len(kept_paths)

        n = len(kept_paths)
        if n < 2:
            raise ValueError("有效图像不足 2 张")
        h, w = sizes[0]
        self._log(f"加载 {n} 张图像, 尺寸 {w}x{h}"
                  f"{' (已缩放)' if self.max_side else ' (原尺寸)'}", percent=5)

        # ── 几何验证：找出真正重叠的图像对 ──
        # 提速开关：per-request（实例）覆盖优先，否则用 StitchConfig 默认。
        # 两个开关关掉即回到「全量逐对 + BFMatcher」的旧口径（判据从未改变）。
        _ug = StitchConfig.use_gpu if self.use_gpu is None else self.use_gpu
        _coarse = (StitchConfig.coarse_screen if self.coarse_screen is None
                   else bool(self.coarse_screen))
        verifier = OverlapVerifier(
            nfeatures=StitchConfig.nfeatures,
            detector=StitchConfig.detector,
            min_shift_ratio=StitchConfig.min_shift_ratio,
            min_hull_ratio=StitchConfig.min_hull_ratio,
            min_hull_norm=StitchConfig.min_hull_norm,
            min_inlier_ratio=StitchConfig.min_inlier_ratio,
            use_gpu=(None if _ug in (None, "auto") else bool(_ug)),
            coarse_screen=_coarse,
            coarse_scale=StitchConfig.coarse_scale,
            coarse_nfeatures=StitchConfig.coarse_nfeatures,
            coarse_good_min=StitchConfig.coarse_good_min,
            warm_workers=StitchConfig.warm_workers)

        total = n * (n - 1) // 2
        state = {'acc': 0}

        def _onpair(done, tot, ev):
            if ev.accepted:
                state['acc'] += 1
            pct = 8 + int(42 * done / max(tot, 1))
            if done == tot or done % max(1, tot // 8) == 0:
                self._log(f"匹配进度 {done}/{tot}（已接受 {state['acc']} 条）", percent=pct)

        accel = []
        if verifier.coarse_screen:
            accel.append(f"粗筛 1/{verifier.coarse_scale}")
        if verifier._gpu is not None:
            accel.append(f"GPU 距离阵 ({verifier.gpu_desc})")
        if verifier.warm_workers > 1:
            accel.append(f"并行预热 ×{verifier.warm_workers}")
        self._log(f"特征匹配 + 几何验证: {n} 张图, {total} 个图像对"
                  f"{'；加速: ' + ' + '.join(accel) if accel else '；加速: 关闭（全量逐对）'}",
                  percent=8)
        for _nt in verifier.notes:
            self._log(f"⚠ {_nt}", percent=8)
        _t0 = time.perf_counter()
        # P0-2：改用「按需读盘」序列喂匹配。`all_pairs` 只按下标递增顺序访问
        # `images[k]` **恰好一次**（内部预热循环），算完特征即丢引用 ⇒
        # 匹配期常驻源图从 `3·H·W·n`（91 张 = 1.66GB）降到**同一时刻 1 张**。
        # 融合同样复用这个 loader（各自再读一遍盘，反正本来就要按需读）。
        # W1：并行预热时**关掉预读** —— 6 个工作线程各自并发解码，本身就把 I/O
        #     重叠掉了；再挂一个预读线程只会与它抢盘并打断「顺序访问」判定。
        _pf = bool(LAZY_PREFETCH) and verifier.warm_workers <= 1
        lazy_all = _LazyImages(kept_paths, self.max_side, prefetch=_pf)
        edges = verifier.all_pairs(lazy_all, progress=_onpair, notify=self._log)
        _match_s = time.perf_counter() - _t0
        self._log(f"匹配阶段耗时 {_match_s:.1f}s", percent=52)
        meta['accel'] = verifier.accel_report()
        meta['accel']['match_seconds'] = round(_match_s, 1)

        accepted = [e for e in edges if e.accepted]
        rejected = [e for e in edges if not e.accepted]
        meta['matches'] = [
            {'i': e.i, 'j': e.j, 'accepted': bool(e.accepted), 'reason': e.reason,
             'inliers': int(e.n_inliers), 'good': int(e.n_good),
             'shift': round(float(e.shift), 1),
             'hull_ratio': round(float(e.hull_ratio), 4),
            'hull_norm': round(float(e.hull_norm), 4),
             'inlier_ratio': round(float(e.inlier_ratio), 3),
             'trans_inlier_ratio': round(float(e.trans_inlier_ratio), 3),
             'model': e.model or '-',
             'angle_deg': round(float(e.angle_deg), 2),
             'scale': round(float(e.scale), 4)}
            for e in edges
        ]
        self._log(f"几何验证: 接受 {len(accepted)} 条可靠匹配, "
                  f"剔除 {len(rejected)} 条疑似误匹配", percent=52)

        # ── 连通分量：哪些图属于同一视野 ──
        groups = connected_components(n, accepted)
        meta['groups'] = [[int(x) for x in g] for g in groups]
        main = groups[0] if groups else []

        if not accepted or len(main) < 2:
            raise RuntimeError(
                "未找到任何相互重叠的图像对 —— 这些照片可能拍摄的不是同一视野，"
                "无法拼接。\n\n" + self._reject_report(rejected) +
                "\n\n建议：只上传同一视野内连续平移拍摄的照片；"
                "若确有需要，请确认相邻照片之间至少有 20% 的重叠区域。")

        if len(groups) > 1:
            extra = sum(len(g) for g in groups[1:])
            meta['warnings'].append(
                f"检测到 {len(groups)} 组互不重叠的图像：主组 {len(main)} 张已拼接，"
                f"另有 {extra} 张与主组无重叠（不属于同一视野），已排除。")
            self._log(f"警告: 图像分为 {len(groups)} 组, 仅拼接最大组 {len(main)} 张", percent=54)
        else:
            dropped = n - len(main)
            if dropped > 0:
                meta['warnings'].append(f"{dropped} 张图与主组无重叠，已排除。")

        # ── 选引擎 ──
        # 默认 'fallback'（本机实测的可靠路径）；'auto' 也用 fallback，
        # 因为 OpenStitching 在本机数据上会退化（见模块 docstring）。
        engine = (self.engine or "auto").lower()
        if engine in ("auto", ""):
            engine = "fallback"

        result = None
        main_images = None
        if engine == "openstitching" and _HAS_OST:
            main_images = [lazy_all[i] for i in main]
            try:
                result, ost_meta = self._run_openstitching(main_images)
                if self._looks_degenerate(result):
                    raise RuntimeError(
                        "OpenStitching 输出呈放射状拖影（相机参数退化），已弃用该结果")
                meta['method'] = 'openstitching'
                meta['engine_meta'] = ost_meta
            except Exception as e:
                self._log(f"OpenStitching 失败 ({type(e).__name__}: {e}), 切换可靠引擎",
                          percent=60)
                meta['warnings'].append(
                    f"OpenStitching 不可用，已切换内置引擎: {type(e).__name__}: {e}")
                result = None
        elif engine == "openstitching" and not _HAS_OST:
            meta['warnings'].append("未安装 stitching 包, 使用内置引擎")

        if result is None:
            # ── 释放匹配阶段的驻留，融合改为按需读盘 ──
            # 匹配结束后：SIFT 描述子（8000×128×4B/张，91 张约 0.37GB）不再需要，
            # 让给画布以提高可用分辨率。源图本来就已不常驻（P0-2，匹配期只留 1 张）。
            h_px, w_px = sizes[0]
            frame_pixels = int(h_px) * int(w_px)
            lazy = _LazyImages([kept_paths[i] for i in main], self.max_side,
                               prefetch=LAZY_PREFETCH)

            del lazy_all              # 匹配用的 loader（只持路径，占内存可忽略）
            del main_images          # OpenStitching 试过的话，这里还挂着整批引用
            try:
                verifier.release()          # 丢描述子缓存
            except AttributeError:
                pass
            import gc
            gc.collect()

            budget = canvas_pixel_budget(len(main), frame_pixels,
                                         holding_frames=False)
            self._log(f"画布像素预算 {budget / 1e6:.0f}MP "
                      f"(按可用内存推算, 源图已改为按需读盘)", percent=58)

            # 用户图幅档位要换算到**画布坐标**。
            # 历史实现会把融合结果 `×1/s0` 还原回原图分辨率，所以画布上限得写成
            # `out_side × s0`；现在**不做向上采样**（见 `fallback.UPSCALE_OUTPUT`），
            # 画布与成品 1:1，故画布上限就是 `out_side` 本身。
            s0_pre = scales[main[0]]
            out_side_canvas = int(self.out_side) if self.out_side else 0
            if self.out_side:
                _note = (f"（源图已按 max_side 预缩 ×{s0_pre:.3f}，成品不再放大回去）"
                         if s0_pre != 1.0 else "")
                self._log(f"输出图幅档位: 最长边 ≤ {self.out_side}px{_note}", percent=59)

            result, fb_meta = self._run_fallback(lazy, edges, main,
                                                frame_pixels=frame_pixels,
                                                budget=budget,
                                                out_side_canvas=out_side_canvas)
            meta['method'] = 'fallback_opencv'
            meta['engine_meta'] = fb_meta

            # 内存比用户档位更紧时如实告知，不静默降质。
            # ★ 自动档（out_side=0）下不能照抄用户档位文案 —— 否则会输出
            #   「所选图幅 0px 超出当前可用内存」这种自相矛盾的提示（用户并没选 0px）。
            if 'memory' in (fb_meta.get('capped_by') or []):
                _cw, _ch = fb_meta['canvas'][0], fb_meta['canvas'][1]
                _nom = fb_meta.get('nominal_canvas') or [0, 0]
                if self.out_side:
                    meta['warnings'].append(
                        f"所选图幅 {self.out_side}px 超出当前可用内存，"
                        f"已按内存上限缩至 {_cw}×{_ch}。"
                        f"想拿到所选图幅请先释放内存，或选更小的档位。")
                else:
                    meta['warnings'].append(
                        f"自动档按当前可用内存定上限：名义画布 {_nom[0]}×{_nom[1]}，"
                        f"成品已缩至 {_cw}×{_ch}（×{fb_meta.get('out_scale', 0):.3f}）。"
                        f"想拿到更大图幅请先释放内存。")

        # ── 成品只向下采，不向上采（用户口径，见 `fallback.UPSCALE_OUTPUT`）──
        # 源图若在匹配阶段被 `max_side` 预缩过（s0 < 1），整条链路就是在低分辨率
        # 上跑的；把结果 `×1/s0` 拉回原分辨率只是插值，不产生新信息 ⇒ **不做**。
        # 输出因此停在真实可用的分辨率上，等效像素尺度由 `meta['output_scale']`
        # 如实报出（此时 = out_scale × s0）。
        s0 = scales[main[0]]
        import fallback as _fb_mod
        if _fb_mod.UPSCALE_OUTPUT and s0 != 1.0:
            f = 1.0 / s0
            result = cv2.resize(result, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
            self._log(f"上采样回原分辨率 (×{f:.3f})", percent=94)

        ph, pw = result.shape[:2]
        meta['output_size'] = [int(pw), int(ph)]
        # 输出图幅：`output_side_cap` 是用户所选档位（0=自动）；
        # `output_scale` 是输出相对**源图名义尺寸**的线性比（1.0=未缩）。
        # 历史实现把结果 `×1/s0` 还原回原分辨率，两者恰好抵消，所以 `output_scale`
        # 等于 fallback 的 `out_scale`；现在**不做向上采样**，输出停在画布尺度上，
        # 故还要再乘上匹配阶段的预缩放 `s0`。
        meta['output_side_cap'] = int(self.out_side or 0)
        _os_scale = float((meta.get('engine_meta') or {}).get('out_scale', 1.0) or 1.0)
        if not _fb_mod.UPSCALE_OUTPUT:
            _os_scale *= float(s0 or 1.0)
        meta['output_scale'] = round(_os_scale, 4)
        meta['used_indices'] = [int(x) for x in main]
        meta['coverage_stats'] = {
            'used_images': len(main),
            'output_pixels': int(pw) * int(ph),
        }

        self._log(f"完成: {pw}×{ph} (引擎 {meta['method']}, 用了 {len(main)}/{n} 张)",
                  percent=100)
        return result, meta
    # ── 放射状拖影检测（相机参数退化的特征） ──
    DEGENERATE_RATIO = 1.05

    @staticmethod
    def _radiance_ratio(pano: np.ndarray) -> float:
        """最强 30% 梯度像素上，|∂/∂x| / |∂/∂y| 的均值比。

        相机参数退化（焦距 f≈1px）时每张图被当成极端透视投影，融合结果呈
        **从中心向外的方向性拉丝**：沿拉丝方向的梯度被抹成连续斜坡（大但
        缓），垂直于拉丝方向的梯度被压成窄脊（小），于是某一个轴向的分量
        系统性偏大，比值偏离 1。

        与「梯度方向直方图」的区别：直方图只统计方向分布，而拉丝方向本身
        在整幅图里是**连续变化**的（放射状），各方向都会出现，因此直方图
        法对本症无效——实测正常与退化在 p90/p95/p99 三个分位上完全重叠
        （正常 0.093~0.223 vs 退化 0.123~0.223）。改成轴向幅值比后分离度
        显著改善。
        """
        try:
            g = cv2.cvtColor(pano, cv2.COLOR_BGR2GRAY) if pano.ndim == 3 else pano
            h, w = g.shape[:2]
            s = 1200.0 / max(h, w)          # 固定尺度，避免分辨率影响
            if s < 1.0:
                g = cv2.resize(g, (max(1, int(w * s)), max(1, int(h * s))),
                               interpolation=cv2.INTER_AREA)
            g = cv2.GaussianBlur(g, (5, 5), 0)
            gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3).ravel()
            gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3).ravel()
            mag = np.hypot(gx, gy)
            if mag.size < 1000:
                return 1.0
            k = mag >= np.percentile(mag, 70)
            ax = float(np.abs(gx[k]).mean())
            ay = float(np.abs(gy[k]).mean())
            if ax < 1e-6 or ay < 1e-6:
                return 1.0
            return ax / ay
        except Exception:
            return 1.0

    @classmethod
    def _looks_degenerate(cls, pano: np.ndarray) -> bool:
        """判断全景是否呈「放射状拖影」。

        实测（ZK203-81.5-T2）：正常拼接 5 例比值 0.872~0.982；退化拖影中
        OST18=1.156 / noWC=1.102 / affine=1.737。阈值 1.05 可全部拦下且对
        正常图零误报。

        已知盲区：**灰度、弱纹理**的退化图（ost_test，PPL 单偏光）比值为
        0.939，落在正常区间内——该图纹理过弱，拉丝方向性不足以被任何像素
        统计量捕获。因此本函数只作**最后一道兜底**，主判据是
        `_run_openstitching` 里对相机焦距的直接检查（焦段退化是确定性的
        内部状态，不依赖图像统计）。
        """
        return cls._radiance_ratio(pano) > cls.DEGENERATE_RATIO

    # ── 剔除原因汇总 ──
    @staticmethod
    def _reject_report(rejected: Sequence[MatchEvidence]) -> str:
        if not rejected:
            return "（未检测到任何可用匹配）"
        from collections import Counter
        c = Counter()
        for e in rejected:
            key = (e.reason or '未知').split('(')[0].strip() or '未知'
            c[key] += 1
        return "剔除原因统计：\n" + "\n".join(f"  · {k}: {v} 个图像对"
                                              for k, v in c.most_common(6))

    # ════════════════════════════════════════════════════════
    # 引擎 1: OpenStitching
    # ════════════════════════════════════════════════════════
    def _run_openstitching(self, images: Sequence[np.ndarray]):
        # warper 用 affine —— 显微切片是平面，球面/柱面投影会引入畸变。
        # crop=False 保留完整（不规则）边缘，显微大图通常要留全部视野。
        cfg = dict(
            detector='sift',
            nfeatures=StitchConfig.nfeatures,
            match_conf=0.35,
            matcher_type='flann',
            estimator='homography',
            adjuster='ray',
            warper_type='affine',
            wave_correct_kind='no',
            compensator=StitchConfig.compensator,
            blender_type='multiband',
            blend_strength=StitchConfig.blend_strength,
            crop=False,
            confidence_threshold=0.3,
            medium_megapix=0.6,
            low_megapix=0.1,
            final_megapix=-1,        # 保留原图分辨率
        )
        self._log(f"OpenStitching 拼接 {len(images)} 张 (affine + multiband)...", percent=60)
        st = ost.Stitcher(**cfg)
        out = st.stitch(list(images))
        pano = out[0] if isinstance(out, tuple) else out
        if pano is None or getattr(pano, 'size', 0) == 0:
            raise RuntimeError("OpenStitching 返回空图")

        # ── 焦距体检：相机参数退化的确定性判据 ──
        # 退化时 estimateFocal 会把 f 压到 ≈1px，随后 warp 变成从中心炸开的
        # 透视投影，融合出放射状拖影。直接读内部状态，比事后看图像统计可靠
        # 得多。这里只**记录**不抛错——哪怕焦距可疑，也先让结果出来后由
        # `_looks_degenerate` 和上层共同决定，避免误杀。
        fdiag = ''
        try:
            cams = getattr(st, 'cameras_', None) or getattr(st, 'cameras', None)
            fs = []
            for c in (cams or []):
                f0 = getattr(c, 'focal', None)
                if f0 is not None:
                    fv = float(np.asarray(f0).reshape(-1)[0])
                    fs.append(fv)
            if fs:
                med = float(np.median(fs))
                fdiag = f"焦距中位数 f={med:.1f}px"
                # 以图像尺寸为尺度：正常显微图 f 与图像短边同量级或更大
                short = float(min(pano.shape[:2]))
                if med < 0.05 * short:
                    self._log(
                        f"⚠ OpenStitching 相机焦距退化 ({fdiag}, 图像短边 {short:.0f}px)，"
                        f"结果可能为放射状拖影", percent=88)
        except Exception as e:
            fdiag = f"(焦距读取失败: {e})"

        self._log(f"OpenStitching 完成: {pano.shape[1]}×{pano.shape[0]} {fdiag}", percent=92)
        return pano, {'engine': 'openstitching', 'config': cfg, 'focal': fdiag}

    # ════════════════════════════════════════════════════════
    # 引擎 2: 纯 OpenCV 降级（真 warp + 多频带融合）
    # ════════════════════════════════════════════════════════
    def _run_fallback(self, main_images, edges, main,
                      frame_pixels: int = 0, budget: int = 0,
                      out_side_canvas: int = 0):
        import fallback as fb

        local = {g: k for k, g in enumerate(main)}
        sub = [MatchEvidence(i=local[e.i], j=local[e.j], accepted=True, matrix=e.matrix,
                             n_inliers=e.n_inliers, shift=e.shift,
                             hull_ratio=e.hull_ratio, inlier_ratio=e.inlier_ratio,
                             angle_deg=e.angle_deg, scale=e.scale)
               for e in edges if e.accepted and e.i in local and e.j in local]
        if not sub:
            raise RuntimeError("主组内没有可用的匹配边")

        h, w = main_images[0].shape[:2]
        # 注意：这里**不要**再跑一次 `fb.solve_poses`。
        # 旧实现为了打印一行"旋转角范围"而独立求解一次位姿，而
        # `fb.stitch_fallback` 内部又会完整求解一遍 —— 两次输入相同、
        # 结果逐位相同，91 张时纯浪费 ~107s（位姿求解是全流程第三大热点）。
        # 旋转范围现在由 `stitch_fallback` 内部的 progress 回调报出，
        # 且报的是**最大连通分量**的范围，比旧日志更贴切。
        # 分组本身仍然要算（传给融合引擎），但那是 O(边数) 的并查集，
        # 与位姿求解的代价不在一个量级上。
        fb_groups = connected_components(len(main_images), sub)

        # 比例尺白框：跨帧共识定位一次，避免逐帧检测（LazyImages 每取一次
        # 就要重解码整张图）并利用叠加物的位置恒定性抗单帧误检。
        sb_arg = None
        if self.detect_scale_bar:
            box = self._scale_bar_box(main_images)
            if box is not None:
                self._log(f"比例尺白框 {box[2]}×{box[3]} @ ({box[0]},{box[1]}) "
                          f"跨帧共识；内容 inpaint + 权重压 0", percent=60)
                sb_arg = (lambda k, _b=box, _s=(h, w):
                          self._scale_bar_mask_from_box(_b, _s, pad=8))
            else:
                self._log("比例尺白框: 未检出（本批无叠加物或干扰过大，不遮）",
                          percent=60)

        result, m = fb.stitch_fallback(
            main_images, sub, fb_groups,
            feather=self.feather_size,
            scale_bar_mask=sb_arg,
            progress=lambda s: self._log(s),
            max_pix=(budget or None),
            out_side=out_side_canvas)
        m['engine'] = 'fallback_opencv'
        return result, m


def save_image(img: np.ndarray, out_dir: str, ext: str = 'jpg') -> str:
    os.makedirs(out_dir, exist_ok=True)
    fname = f"pano_{uuid.uuid4().hex[:12]}.{ext}"
    fpath = os.path.join(out_dir, fname)
    if ext.lower() in ('jpg', 'jpeg'):
        cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tofile(fpath)
    elif ext.lower() == 'png':
        cv2.imencode('.png', img, [cv2.IMWRITE_PNG_COMPRESSION, 3])[1].tofile(fpath)
    else:
        cv2.imwrite(fpath, img)
    return fpath
