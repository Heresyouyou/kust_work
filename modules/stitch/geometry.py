# -*- coding: utf-8 -*-
"""显微图像拼接的几何验证层

本模块只做一件事：**判断两张图是否真的拍到了同一块区域**，并在是的情况下
给出可靠的相对位姿。

## 核心领域约束：载物台只有平移

显微拍照是**载物台平移**取图（FreeModeAcquisition 之类的自动拼图台），
载物台**不会旋转、也不会缩放**。所以两张图之间的真实几何关系只有
**平移 (tx, ty)** —— 不是旋转、不是相似变换、更不是单应。

**旋转自由度必须被钉死，不能"顺手拟合"。** 理由是可证伪性：模型越松，
假重叠越容易凑出残差极小的"完美解"。一个含旋转的相似变换，可以用一个
子虚乌有的 θ 去迁就一堆错配，于是 RANSAC 的残差判据整体失效。
本模块曾在实测里被这个漏洞骗过：**真重叠对**被解出 19.2°、55° 的旋转
（`ZK203-81.5-T2` 的 4-1&4-6），在平移载物台上这是**物理上不可能**的。

## 平移优先（translation-first）

估计顺序固定为两级，且第一级能成立就绝不进入第二级：

1. **平移优先**：把模型钉成 `[[1,0,tx],[0,1,ty]]`，用平移 RANSAC 求 t。
   显微切片是**同一定焦平面**，实测闭环极佳——连续三帧
   `#28→#29→#30→#28` 的合成平移与直接测量之差仅 **0.3px**（见下方实测节）。
   成立条件：内点足够 + 判据 1~4 全过。
2. **旋转兜底**：仅当平移模型**一个精确匹配像素都拿不到**
   （即平移路径在粗匹配上就无解）时，才放开旋转重估一次。若兜底也解出
   大角度，**直接剔除** —— 载物台不会转，解出旋转就等于承认那对匹配是错的。

## 判据（基于「全局布局」标签的实测标定）

设图像短边为 S（默认按 2208px 标定），相对位移 t = |平移|：

1. **最小位移**  t >= MIN_SHIFT_RATIO * S （默认 0.04*S ≈ 88px）
   真重叠实测 396~1770px；假匹配（不同视野）实测 0.3~28px。
   分界放在 88px 极安全。
2. **内点空间散布**（几何证据）：内点必须铺在重叠区，而不是聚成一簇。
   两个口径满足**任一**即可：
     · `hull_ratio` = 内点凸包 / **图像面积** >= MIN_HULL_RATIO（绝对口径）
     · `hull_norm`  = 内点凸包 / **理论重叠区** >= MIN_HULL_NORM（尺度无关）
   大位移（小重叠区）时只有后者可用 —— 见下方 MIN_HULL_NORM 注释。
3. **重叠证据**（两个独立证据取「或」，缺一不可的是"至少有一个"）：
     · A · 匹配自洽：内点率 >= MIN_TRANS_INLIER_RATIO（平移路径）/ 0.70（兜底）
     · B · 几何散布：`hull_norm` >= MIN_HULL_NORM（**仅平移路径**）
   ★ 为什么 A 单独不够：大位移对的 `n_good` 会被**非重叠区**里的周期性重复
   纹理错配稀释，内点率天然偏低（真重叠实测可低至 **0.28**）。B 与位移解耦，
   是独立证据；而相似变换的自由度大、散布可由旋转"造"出来，故 B 不用于兜底路径。
4. **内点绝对数** >= MIN_INLIERS（40；兜底路径要求 >= 120）

实测标定（91 张 / 4095 对；真值取自 1039 条高置信边解出的**全局布局**，
自身残差中位 0.15px ⇒ 可信。应重叠 1285 对、不应重叠 2810 对）：

    hull_norm    真重叠 p5=0.714 中位=0.926   |  假匹配 max=0.0028  → 间隔 255×
    A∨B 规则     真召回 85.6% → 98.7%（补回 159 条）  |  假接受 0 → 0
    对抗压测     150 远离对 + 50 边界对：hull_norm max 0.0039，0 条 >= 0.30

实测判别结果（ZK203-81.5-T2 的 4-*-A 七图）：

    真重叠对  4-1&4-6 / 4-1&4-2 / 4-6&4-7 / 4-2&4-3 / 4-3&4-4   → 全部 ACCEPT
    假匹配对  4-1&4-3 / 4-3&4-6 / 4-4&4-5 / 4-5&4-7 / …          → 全部 REJECT
"""

from __future__ import annotations

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

# ── 默认阈值（按 2208px 短边标定） ──
MIN_SHIFT_RATIO = 0.04      # t >= 4% 短边
MIN_HULL_RATIO = 0.03       # 内点凸包 >= 3% 图像面积（**绝对**口径，见 MIN_HULL_NORM）
MIN_INLIER_RATIO = 0.50     # 内点率 >= 50%
MIN_TRANS_INLIER_RATIO = 0.60   # 平移模型内点率下限（主力判据）
MIN_INLIERS = 40            # 内点绝对下限
MIN_GOOD = 25               # 粗匹配绝对下限

# ★ 归一化内点散布阈值（2026-09-26 加入，与 MIN_HULL_RATIO 是 **OR** 关系）
#
# 为什么需要它：`MIN_HULL_RATIO` 是**绝对**面积比，而内点凸包大小天然与
# 「重叠区大小」成正比。位移 t 越大重叠区越小 —— 位移 2000~2600px 时理论重叠
# 区只剩 8~11%，hull_ratio 必然贴着 0.03 阈线。实测 91 张里被判「内点聚集」
# 的 98 对中，**64 对经对齐验证是真重叠（corr 0.6~0.98）**；只上传其中两张
# （位移 2360px、corr 0.983）时会直接报「未找到任何相互重叠的图像对」——
# 即小样本 + 大步长场景下的功能性失败。
#
# 新判据把凸包面积除以「平移模型下的理论重叠区」：
#     hull_norm = hull_area / max(0, W-|tx|) / max(0, H-|ty|)
# 于是它与位移无关（尺度无关）。3771 对采样实测：
#     真重叠 (corr>=0.6, n=805): min 0.554  中位 0.917
#     假匹配 (corr<0.35, n= 24): p75 0.0026
# 差 3 个数量级 ⇒ 阈值在 0.05~0.50 区间内结论完全一致（极不敏感），取 0.30。
#
# ★★ 2026-09-26 第二用：hull_norm 还兼任**平移一致性判据的"或"分支**。
# 用 1039 条高置信边解出的**全局布局**（非循环标签，残差中位 0.15px）标定：
#     应重叠 1285 对 / 不应重叠 2810 对
#     hull_norm:  真重叠 p5=0.714 中位=0.926  |  假匹配 max=0.0028（间隔 255×）
#     规则 (内点率>=0.60) ∨ (hull_norm>=0.30): 真召回 85.6%→98.7%，假接受 0→0
#     补回的 159 条位移全 >=2011px（小重叠区，内点率被稀释），hull_norm min 0.510
# 对抗压测（150 远离对 + 50 边界对，放宽全部阈值强行推到凸包计算）：
#     hull_norm max 0.0039，**0 条** >= 0.30 ⇒ 阈值有 77× 裕度。
MIN_HULL_NORM = 0.30

# 旋转兜底的收紧阈值：载物台不会转，解出超过这个角度就直接判错
FALLBACK_ANGLE_DEG = 5.0
FALLBACK_MIN_INLIERS = 120      # 兜底路径要求更多内点，避免"少内点凑旋转"
FALLBACK_MIN_INLIER_RATIO = 0.70

# RANSAC 参数
RANSAC_THRESH = 4.0
RATIO_TEST = 0.80
# 平移 RANSAC：迭代次数可以少很多（只有 2 个自由度），但上限给足
TRANS_MAX_ITERS = 3000

# ── 提速参数（2026-09-24 实测） ──
# 1/8 尺度粗筛：62 张实测 2.93ms/对，`good >= 20` 保留 44.5%，
# **363 条真重叠 0 漏**（实测），故当候选过滤器用；仍带连通性兜底。
COARSE_SCALE = 8
COARSE_NFEATURES = 1000
COARSE_GOOD_MIN = 20
COARSE_MIN_IMAGES = 4        # 少于这么多张时不值得粗筛（对太少）

# GPU 距离阵：torch.cdist 分块算 8000×8000×128 的 L2 距离矩阵，
# 实测 14.2ms/对（71 对/s，BFMatcher 107ms/对），top-1 一致率 99.9946%。
GPU_CHUNK = 1000             # query 分块（8GB 显存：1000×8000 fp32 ≈ 32MB）
GPU_MIN_FEATURES = 2000      # 特征少于此数 GPU 启动开销不划算，走 CPU
GPU_RECHECK_MARGIN = 5       # good 数落在 [min_good, min_good+margin] 时用 CPU 复核

# ── 预热并行度（2026-09-28 实测） ──
# 预热是「逐帧解码 → 细 SIFT → 粗 SIFT」，**帧与帧之间没有任何依赖**，天然可并行。
# 91 张实测（T2-C-origin）：串行 46.2s（细 SIFT 独占 44.5s / 96%），
#   W=2 → 39.3s · W=4 → 34.7s · **W=6 → 32.8s（最优，1.41×）** · W=8 → 38.6s · W=12 → 56.6s。
# W 过大反而慢：OpenCV 自身已按 CPU 数在 `detectAndCompute` 内部并行，
# 再叠工作线程 = 过订阅（W=6 已是 6×16 线程）。
# ★ 每线程必须用**自己的**检测器对象：共享 `self._det` 并发调用不保证线程安全。
# 1（或 0）= 退回串行，行为与旧版逐位一致。
WARM_WORKERS = 6


@dataclass
class MatchEvidence:
    """一对图像之间的匹配证据（含判据原始值，便于调试与前端展示）。"""
    i: int
    j: int
    accepted: bool
    reason: str = ""

    # 原始统计
    n_kp_i: int = 0
    n_kp_j: int = 0
    n_good: int = 0
    n_inliers: int = 0

    # 判据值
    shift: float = 0.0          # |t| 像素
    hull_ratio: float = 0.0     # 内点凸包 / 面积（绝对口径）
    hull_norm: float = 0.0      # 内点凸包 / 理论重叠区（尺度无关口径，见 MIN_HULL_NORM）
    inlier_ratio: float = 0.0   # 内点 / good
    trans_inlier_ratio: float = 0.0  # 平移模型内点 / good（主力判据）

    # 位姿（i -> j 的相似变换 2x3）
    matrix: Optional[np.ndarray] = None
    angle_deg: float = 0.0
    scale: float = 1.0
    model: str = ""             # 'translation' | 'similarity'（用了哪条路径）

    def reject(self, reason: str) -> "MatchEvidence":
        self.accepted = False
        self.reason = reason
        return self


def _to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _clahe(gray: np.ndarray, clip: float = 2.0) -> np.ndarray:
    """显微图照明不均，做一次 CLAHE 提升特征稳定性（检测用，不改原图）。"""
    try:
        return cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8)).apply(gray)
    except Exception:
        return gray


# ── kNN 距离：CPU（BFMatcher）与 GPU（torch）两条实现，语义必须一致 ──
#
# `pair()` 只依赖 (距离, 索引) 两个数组做 ratio test，所以把匹配后端抽象成
# 「返回 N×2 的 L2 距离与 des_b 行号」即可。判据本身（ratio test、平移 RANSAC、
# 四条判据）**完全不动** —— 这是"换后端不改判定"的结构保证。

def _knn2_cpu(bf, a: np.ndarray, b: np.ndarray, k: int = 2):
    """BFMatcher.knnMatch → (dist[N,k] float32, idx[N,k] int64)。

    dist 取 OpenCV 给的 L2 距离（float32），保证与历史结果逐位可比；
    缺失的第 k 近邻填 inf（ratio test 会自然通过）。
    """
    knn = bf.knnMatch(a, b, k=k)
    n = len(knn)
    ds = np.full((n, k), np.inf, np.float32)
    ix = np.zeros((n, k), np.int64)
    for row, ms in enumerate(knn):
        for t, m in enumerate(ms):
            ds[row, t] = m.distance
            ix[row, t] = m.trainIdx
    return ds, ix


def _knn2_torch(a: np.ndarray, b: np.ndarray, device, chunk: int = GPU_CHUNK,
                k: int = 2):
    """torch.cdist 分块求 kNN → (dist[N,k] float32, idx[N,k] int64)。

    显存只与 chunk×|b| 有关，与 |a| 无关，故可处理 8000×8000 的大矩阵。
    **注意**：GPU 计时必须 synchronize，否则测出的是「入队时间」而非真实耗时
    （实测会得到 0.2ms 的假值）。
    """
    import torch
    ta = torch.from_numpy(np.ascontiguousarray(a, dtype=np.float32)).to(device)
    tb = torch.from_numpy(np.ascontiguousarray(b, dtype=np.float32)).to(device)
    n = int(ta.shape[0])
    ds = np.empty((n, k), np.float32)
    ix = np.empty((n, k), np.int64)
    for s in range(0, n, chunk):
        e = min(n, s + chunk)
        d = torch.cdist(ta[s:e], tb, p=2)
        v, i = torch.topk(d, k, dim=1, largest=False, sorted=True)
        v = torch.nan_to_num(v, nan=float('inf'), posinf=float('inf'))
        ds[s:e] = v.detach().cpu().numpy()
        ix[s:e] = i.detach().cpu().numpy()
    if device.type == 'cuda':
        torch.cuda.synchronize(device)
    return ds, ix


def _resolve_torch_device(use_gpu) -> Tuple[Optional[object], str]:
    """决定是否用 GPU 做距离阵。返回 (torch.device | None, 说明文字)。

    `use_gpu` 语义：True=必须用（不可用则记录原因并回落）、False=禁用、
    None/"auto"=可用就用。**任何异常都只降级、不抛出**。
    """
    if use_gpu is False:
        return None, "已禁用 (use_gpu=False)"
    try:
        import torch
    except Exception as e:
        return None, f"torch 不可用 ({type(e).__name__})"
    if not torch.cuda.is_available():
        return None, "CUDA 不可用，走 CPU"
    try:
        dev = torch.device("cuda")
        _ = torch.zeros(1, device=dev)      # 真分配一次，确认驱动/显存可用
        torch.cuda.synchronize(dev)
        del _
        try:
            name = torch.cuda.get_device_name(0)
        except Exception:
            name = "CUDA"
        return dev, f"torch {torch.__version__} · {name}"
    except Exception as e:
        return None, f"CUDA 初始化失败 ({type(e).__name__}: {e})"


def _estimate_translation(src: np.ndarray, dst: np.ndarray,
                          thresh: float, max_iters: int = TRANS_MAX_ITERS):
    """**纯平移** RANSAC：求 t 使 dst ≈ src + t。

    实现说明：模型只有 2 个自由度，最小样本为 1 对点，用 1 点 RANSAC 会退化
    （单点必完美满足，无法定位）。故采用 **LMedS 风格的确定性搜索 + 局部精化**：

    1. 以「所有配对位移的中位数」作为初值。中位数对错配天然鲁棒
       （点集中任一簇的占比都 < 50%）。
    2. 迭代重加权：按残差保留内点，用内点均值更新 t，直到内点集合稳定。
    3. 再做一次坐标下降：在 (tx, ty) 各自的 ±几个像素范围内细扫，
       取内点数最大的取值（消除中位数初值残留的偏置）。

    返回 (M 2x3 或 None, mask bool[n])。M 的旋转部分恒为单位阵。
    """
    n = len(src)
    if n < 2:
        return None, None

    d = dst - src                      # 每对的位移观测
    t = np.median(d, axis=0)           # LMedS 初值

    for _ in range(8):
        r = np.hypot(d[:, 0] - t[0], d[:, 1] - t[1])
        keep = r <= thresh
        if keep.sum() < 2:
            return None, None
        t_new = d[keep].mean(axis=0)
        if np.hypot(*(t_new - t)) < 1e-3:
            t = t_new
            break
        t = t_new

    # 坐标下降精化：平移的两个分量可分离地细扫
    step = max(0.5, thresh / 4.0)
    for _ in range(3):
        improved = False
        for axis in (0, 1):
            best_t, best_cnt = t[axis], int((np.hypot(d[:, 0] - t[0],
                                                      d[:, 1] - t[1]) <= thresh).sum())
            for delta in (-step, step):
                cand = t.copy()
                cand[axis] = t[axis] + delta
                cnt = int((np.hypot(d[:, 0] - cand[0], d[:, 1] - cand[1]) <= thresh).sum())
                if cnt > best_cnt:
                    best_cnt, best_t = cnt, cand[axis]
            if best_t != t[axis]:
                t[axis] = best_t
                improved = True
        if not improved:
            break
        step *= 0.5

    r = np.hypot(d[:, 0] - t[0], d[:, 1] - t[1])
    mask = r <= thresh
    if mask.sum() < 2:
        return None, None

    # 用最终内点做一次最小二乘
    t = d[mask].mean(axis=0)
    r = np.hypot(d[:, 0] - t[0], d[:, 1] - t[1])
    mask = r <= thresh

    M = np.array([[1.0, 0.0, t[0]], [0.0, 1.0, t[1]]], np.float64)
    return M, mask


class OverlapVerifier:
    """特征检测 + 匹配 + 几何验证。

    内部对每张图只检测一次特征并缓存，避免 O(n²) 次重复检测。
    """

    def __init__(self,
                 nfeatures: int = 8000,
                 detector: str = "sift",
                 min_shift_ratio: float = MIN_SHIFT_RATIO,
                 min_hull_ratio: float = MIN_HULL_RATIO,
                 min_hull_norm: float = MIN_HULL_NORM,
                 min_inlier_ratio: float = MIN_INLIER_RATIO,
                 min_trans_inlier_ratio: float = MIN_TRANS_INLIER_RATIO,
                 min_inliers: int = MIN_INLIERS,
                 min_good: int = MIN_GOOD,
                 ratio_test: float = RATIO_TEST,
                 ransac_thresh: float = RANSAC_THRESH,
                 fallback_angle_deg: float = FALLBACK_ANGLE_DEG,
                 use_clahe: bool = True,
                 # ── 提速（可选，全部可关；关掉即回到全量逐对匹配） ──
                 use_gpu=None,
                 coarse_screen: bool = True,
                 coarse_scale: int = COARSE_SCALE,
                 coarse_nfeatures: int = COARSE_NFEATURES,
                 coarse_good_min: int = COARSE_GOOD_MIN,
                 coarse_min_images: int = COARSE_MIN_IMAGES,
                 gpu_recheck_margin: int = GPU_RECHECK_MARGIN,
                 warm_workers: int = WARM_WORKERS):
        self.nfeatures = int(nfeatures)
        self.min_shift_ratio = float(min_shift_ratio)
        self.min_hull_ratio = float(min_hull_ratio)
        self.min_hull_norm = float(min_hull_norm)
        self.min_inlier_ratio = float(min_inlier_ratio)
        self.min_trans_inlier_ratio = float(min_trans_inlier_ratio)
        self.min_inliers = int(min_inliers)
        self.min_good = int(min_good)
        self.ratio_test = float(ratio_test)
        self.ransac_thresh = float(ransac_thresh)
        self.fallback_angle_deg = float(fallback_angle_deg)
        self.use_clahe = bool(use_clahe)

        # 粗筛（1/N 尺度候选过滤）
        self.coarse_screen = bool(coarse_screen)
        self.coarse_scale = max(1, int(coarse_scale))
        self.coarse_nfeatures = int(coarse_nfeatures)
        self.coarse_good_min = int(coarse_good_min)
        self.coarse_min_images = int(coarse_min_images)

        # GPU 距离阵
        self.gpu_recheck_margin = int(gpu_recheck_margin)
        # 预热并行度（见 WARM_WORKERS；<=1 即串行）
        self.warm_workers = max(0, int(warm_workers))
        self.detector = detector.lower()
        self._gpu, self.gpu_desc = _resolve_torch_device(
            None if use_gpu in (None, "auto") else bool(use_gpu))
        if use_gpu is True and self._gpu is None:
            self.notes = [f"要求 GPU 但不可用：{self.gpu_desc}"]
        else:
            self.notes = []

        if detector.lower() == "orb":
            self._det = cv2.ORB_create(nfeatures=self.nfeatures)
            self._coarse_det = cv2.ORB_create(nfeatures=self.coarse_nfeatures)
            self._norm = cv2.NORM_HAMMING
        else:
            self._det = cv2.SIFT_create(nfeatures=self.nfeatures)
            self._coarse_det = cv2.SIFT_create(nfeatures=self.coarse_nfeatures)
            self._norm = cv2.NORM_L2
        self._bf = cv2.BFMatcher(self._norm)

        self._cache: dict = {}
        self._coarse: dict = {}
        # ── P0-2：匹配期不再常驻整批解码图（2026-09-27）──
        # `features()/coarse_features()` 的缓存键 `_fp()` 会**抽样像素**算内容指纹，
        # 所以「算键」本身就要求有图。若配对时临时按需解码，n 张图会被解码
        # 2(n-1) 次（4095 对 × 2 = 8190 次），完全不可接受。
        # 因此这里额外维护 idx → 缓存键 的索引表：**预热阶段**逐张喂图、算完特征
        # 即释放像素；之后所有配对只走 `*_by_idx()` 从缓存取，不再需要任何像素。
        # 效果：匹配期常驻源图从 3·H·W·n 降到「同一时刻 1 张」。
        self._key_by_idx: Dict[int, tuple] = {}
        self._ckey_by_idx: Dict[int, tuple] = {}
        self._shapes: Dict[int, Tuple[int, int]] = {}
        self.screen_stats: Optional[dict] = None
        self.fallback_triggered: bool = False

    def release(self):
        """释放特征缓存。

        匹配结束后描述子就没用了（8000×128×4B ≈ 4.1MB/张，91 张约 0.37GB）。
        融合阶段要的是解码图而不是特征，调用方可在大画布融合前主动回收。
        粗筛缓存与 GPU 显存一并归还（融合阶段还要用显存做别的）。
        """
        self._cache.clear()
        self._coarse.clear()
        self._key_by_idx.clear()
        self._ckey_by_idx.clear()
        self._shapes.clear()
        if self._gpu is not None:
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:
                pass

    # ── 加速器自述（写入 meta，便于前端/日志交代用了什么） ──
    def accel_report(self) -> dict:
        return {
            'gpu': self.gpu_desc if self._gpu is not None else None,
            'coarse_screen': bool(self.coarse_screen),
            'coarse': self.screen_stats,
            'coarse_fallback_full': bool(self.fallback_triggered),
            'warm_workers': int(self.warm_workers),
            'notes': list(self.notes),
        }

    # ── 特征缓存 ──
    # 用「图像内容的指纹」做 key，而不是调用方给的 idx。
    # master 代码按 idx 取用是稳定的，但若换了图却复用同一 idx，
    # 按 idx 缓存会**静默**返回另一张图的特征（测试里踩到过）。
    @staticmethod
    def _fp(idx, img: np.ndarray):
        return (int(idx), img.shape, img.strides,
                hash(img[::max(1, img.shape[0] // 32),
                         ::max(1, img.shape[1] // 32)].tobytes()))

    def _make_detectors(self):
        """新建一對检测器（细 / 粗），参数与 `__init__` 里那对**逐字相同**。

        并行预热时每个工作线程各持一对 —— 共享 `self._det` 被并发调用不保证安全。
        参数完全一致 ⇒ 同一张图算出的关键点/描述子与串行路径逐位相同。
        """
        if self.detector == "orb":
            return (cv2.ORB_create(nfeatures=self.nfeatures),
                    cv2.ORB_create(nfeatures=self.coarse_nfeatures))
        return (cv2.SIFT_create(nfeatures=self.nfeatures),
                cv2.SIFT_create(nfeatures=self.coarse_nfeatures))

    def features(self, idx, img: np.ndarray, det=None):
        key = self._fp(idx, img)
        if key not in self._cache:
            g = _to_gray(img)
            if self.use_clahe:
                g = _clahe(g)
            kp, des = (det or self._det).detectAndCompute(g, None)
            pts = np.float32([k.pt for k in kp]).reshape(-1, 2) if kp else np.zeros((0, 2), np.float32)
            self._cache[key] = (pts, des)
        self._key_by_idx[idx] = key        # P0-2：建 idx → 键索引
        return self._cache[key]

    def features_by_idx(self, idx: int):
        """P0-2：从缓存按索引取特征，**不需要图**（预热阶段已建好索引）。"""
        key = self._key_by_idx.get(idx)
        if key is None:
            raise KeyError(f"特征未预热: idx={idx}")
        return self._cache[key]

    # ── 粗筛特征（1/N 尺度，少量特征） ──
    def coarse_features(self, idx, img: np.ndarray, det=None):
        """低分辨率特征，只用于"这一对值不值得细算"。

        实测（62 张真数据）：1/8 尺度 + nfeatures=1000，单对 2.93ms，
        用 `good >= 20` 保留 44.5% 的候选，**363 条真重叠一条不漏**。
        """
        key = self._fp(idx, img)
        if key not in self._coarse:
            g = _to_gray(img)
            s = self.coarse_scale
            if s > 1:
                nh, nw = max(1, g.shape[0] // s), max(1, g.shape[1] // s)
                g = cv2.resize(g, (nw, nh), interpolation=cv2.INTER_AREA)
            if self.use_clahe:
                g = _clahe(g)
            kp, des = (det or self._coarse_det).detectAndCompute(g, None)
            pts = np.float32([k.pt for k in kp]).reshape(-1, 2) if kp else np.zeros((0, 2), np.float32)
            self._coarse[key] = (pts, des)
        self._ckey_by_idx[idx] = key       # P0-2：建 idx → 键索引
        return self._coarse[key]

    def coarse_by_idx(self, idx: int):
        """P0-2：从缓存按索引取粗筛特征，**不需要图**。"""
        key = self._ckey_by_idx.get(idx)
        if key is None:
            raise KeyError(f"粗筛特征未预热: idx={idx}")
        return self._coarse[key]

    def coarse_good(self, i: int, j: int, img_i=None, img_j=None) -> int:
        """粗尺度下通过 ratio test 的匹配数。**只当筛子，不作判据。**

        注意：粗尺度下真/假的 `good` 分不开（拒绝对也能有 562 个 good），
        真伪要在**细尺度**由**内点率**分开（真 61~77% / 假 26~55%）。

        `img_i/img_j` 可省略（P0-2）：省略时走 `coarse_by_idx()` 从缓存取。
        """
        if img_i is None:
            _pa, da = self.coarse_by_idx(i)
        else:
            _pa, da = self.coarse_features(i, img_i)
        if img_j is None:
            _pb, db = self.coarse_by_idx(j)
        else:
            _pb, db = self.coarse_features(j, img_j)
        if da is None or db is None or len(da) < 2 or len(db) < 2:
            return 0
        try:
            ds, _ix = _knn2_cpu(self._bf, da, db)
        except cv2.error:
            return 0
        return int(np.count_nonzero(ds[:, 0] < self.ratio_test * ds[:, 1]))

    # ── kNN 后端选择（CPU/GPU 语义一致，失败即永久回落 CPU） ──
    def _knn2(self, des_a: np.ndarray, des_b: np.ndarray, force_cpu: bool = False):
        """返回 (dist[N,2], idx[N,2], via_gpu)。"""
        if (self._gpu is not None and not force_cpu
                and des_a.shape[0] >= GPU_MIN_FEATURES
                and des_b.shape[0] >= GPU_MIN_FEATURES):
            try:
                ds, ix = _knn2_torch(des_a, des_b, self._gpu)
                return ds, ix, True
            except Exception as e:          # 显存/驱动/版本 → 只降级，不抛出
                self._gpu = None
                self.gpu_desc = f"运行中失败已回落 CPU ({type(e).__name__})"
                self.notes.append(f"GPU 匹配失败回落 CPU: {type(e).__name__}: {e}")
        ds, ix = _knn2_cpu(self._bf, des_a, des_b)
        return ds, ix, False

    # ── 单对匹配 ──
    def pair(self, i: int, j: int, img_i: np.ndarray = None,
             img_j: np.ndarray = None, short_side: float = None,
             *, shape_i=None, feat_i=None, feat_j=None) -> MatchEvidence:
        """判定 i、j 是否真重叠；是则给出 i -> j 的相对位姿。

        两级估计（**平移优先**，详见模块 docstring）：
          A. 纯平移 RANSAC —— 载物台不旋转，这是唯一物理上成立的模型；
          B. 旋转兜底 —— 仅当 A 完全拿不到精确匹配像素时才尝试，
             且兜底解出的大角度直接被判为"匹配错误"。

        **P0-2 起 `img_i/img_j` 可省略**：本函数对原始图像的依赖只有
        ①`features()` 取特征、②`img_i.shape[:2]` 取尺寸；两者在预热阶段都已缓存
        （`features_by_idx()` / `_shapes`）。省掉图像参数后，调用方无需再让整批
        解码图常驻内存（91 张 = 1.66GB）。
        """
        ev = MatchEvidence(i=i, j=j, accepted=False)

        if feat_i is not None:
            pts_a, des_a = feat_i
        else:
            try:
                pts_a, des_a = (self.features(i, img_i) if img_i is not None
                                else self.features_by_idx(i))
            except KeyError:
                return ev.reject("特征未就绪（该图读取失败）")
        if feat_j is not None:
            pts_b, des_b = feat_j
        else:
            try:
                pts_b, des_b = (self.features(j, img_j) if img_j is not None
                                else self.features_by_idx(j))
            except KeyError:
                return ev.reject("特征未就绪（该图读取失败）")
        ev.n_kp_i, ev.n_kp_j = len(pts_a), len(pts_b)
        if des_a is None or des_b is None or len(des_a) < 4 or len(des_b) < 4:
            return ev.reject("特征点不足")

        try:
            ds, ix, via_gpu = self._knn2(des_a, des_b)
        except cv2.error:
            return ev.reject("匹配失败")

        # ratio test 与 OpenCV 同式：L2 距离直接比较（**不换成平方距离**，
        # 那会把 `d1 < r*d2` 变成 `d1² < r²d2²`，阈值语义不同，实测会引入 60/60 的偏差）。
        # GPU 侧只是 fp32 ULP 层面的差异，仅可能翻转**擦边 query** 的判定，故：
        # 用了 GPU 且 good 数贴近接受阈值时，用 CPU 逐位复核一次。
        # （good 数远离阈值时，十几个 query 的差异不可能改变接受/拒绝结论。）
        if via_gpu and self.gpu_recheck_margin > 0:
            n_quick = int(np.count_nonzero(ds[:, 0] < self.ratio_test * ds[:, 1]))
            if n_quick <= self.min_good + self.gpu_recheck_margin:
                try:
                    ds, ix, _ = self._knn2(des_a, des_b, force_cpu=True)
                except cv2.error:
                    pass

        ok = ds[:, 0] < self.ratio_test * ds[:, 1]
        ev.n_good = int(np.count_nonzero(ok))
        if ev.n_good < self.min_good:
            return ev.reject(f"粗匹配不足 ({ev.n_good})")
        # queryIdx 就是 des_a 的行号；trainIdx（ix[:,0]）是 des_b 的行号。
        # 注意第 0 列才是最近邻，别误用第 1 列（次近邻）当目标。
        src = pts_a[np.flatnonzero(ok)]
        dst = pts_b[ix[ok, 0]]

        # ── A. 平移优先 ──
        M, mask = _estimate_translation(src, dst, self.ransac_thresh)
        used_fallback = False
        if M is None or int(mask.sum()) < self.min_inliers:
            # 平移模型拿不到足够精确匹配像素 → 退一步试旋转（放宽内点下限）
            M, mask, used_fallback = self._estimate_similarity(src, dst)
            if M is None:
                return ev.reject("位移估计失败（平移与旋转兜底均无解）")
            ev.model = "similarity"
        else:
            ev.model = "translation"

        mask = np.asarray(mask).ravel().astype(bool)
        ev.n_inliers = int(mask.sum())
        ev.matrix = M

        if ev.n_inliers < self.min_inliers:
            return ev.reject(f"内点过少 ({ev.n_inliers})")

        ev.shift = float(math.hypot(M[0, 2], M[1, 2]))
        ev.inlier_ratio = ev.n_inliers / max(ev.n_good, 1)
        ev.angle_deg = float(math.degrees(math.atan2(M[1, 0], M[0, 0])))
        ev.scale = float(math.hypot(M[0, 0], M[1, 0]))

        # ── 旋转判据：载物台不会转，解出旋转就是匹配错了 ──
        # 平移路径恒为 0°；兜底路径若解出可观角度，直接剔除（可证伪性）。
        if abs(ev.angle_deg) > self.fallback_angle_deg:
            return ev.reject(
                f"旋转异常 ({ev.angle_deg:.1f}° > {self.fallback_angle_deg:.0f}°)"
                f"，载物台只平移不旋转，该匹配不可信")

        # 内点凸包占比（重叠区大小）
        in_pts = src[mask]
        hull_area = 0.0
        if len(in_pts) >= 3:
            hull = cv2.convexHull(in_pts.astype(np.float32))
            hull_area = float(cv2.contourArea(hull))
            ev.hull_ratio = hull_area / float(short_side * short_side * 3.0)
        else:
            ev.hull_ratio = 0.0

        # ★ 归一化散布（尺度无关）：凸包 / 平移模型下的**理论重叠区**
        # 重叠区 = (W-|tx|) × (H-|ty|)（平移模型下的精确结论）。位移越大重叠区
        # 越小，而内点凸包也随之变小 —— 除以它之后判据与位移解耦。
        if shape_i is not None:                 # P0-2：优先用显式尺寸
            _h_i, _w_i = shape_i
        elif img_i is not None:
            _h_i, _w_i = img_i.shape[:2]
        else:
            _h_i, _w_i = self._shapes[i]
        _ov_w = max(0.0, float(_w_i) - abs(float(M[0, 2])))
        _ov_h = max(0.0, float(_h_i) - abs(float(M[1, 2])))
        _a_exp = _ov_w * _ov_h
        ev.hull_norm = (hull_area / _a_exp) if _a_exp > 1.0 else 0.0

        # ★ 几何散布证据（尺度无关，与「匹配自洽」相互独立）。
        #   仅平移路径可用：相似变换的自由度大，散布可由旋转"造"出来，不能当证据。
        spread_ok = (not used_fallback) and (ev.hull_norm >= self.min_hull_norm)

        # ── 平移一致性（主力判据 A）──
        # 只有平移模型的内点率才判；兜底路径的相似变换本就自由度高，
        # 用内点率判它会误杀（旋转自由度能吃掉残差）。
        #
        # ★ 低一致性**不足以单独否定**：大位移对（小重叠区）的 n_good 里混进了
        #   大量落在**非重叠区**的周期性重复纹理错配，内点率被稀释 —— 实测真
        #   重叠的内点率可低至 0.28。此时若内点凸包已铺满理论重叠区（spread_ok），
        #   几何证据独立成立 ⇒ 放行给后续判据（判据 1/2/4 仍会各自把关）。
        #   标定见文件头 MIN_HULL_NORM 注释（真召回 85.6%→98.7%，假接受 0）。
        if not used_fallback:
            ev.trans_inlier_ratio = ev.inlier_ratio
            if (ev.trans_inlier_ratio < self.min_trans_inlier_ratio
                    and not spread_ok):
                return ev.reject(
                    f"平移一致性低 ({ev.trans_inlier_ratio:.0%} < "
                    f"{self.min_trans_inlier_ratio:.0%}) 且内点未铺满重叠区 "
                    f"(归一化 {ev.hull_norm:.3f} < {self.min_hull_norm})，"
                    f"疑似重复纹理错配")

        # 尺度必须接近 1（同一显微镜同一放大倍率）。
        # 只对兜底路径检查：平移路径的 scale 恒为 1.0，不必查。
        if used_fallback and not (0.85 <= ev.scale <= 1.18):
            return ev.reject(f"尺度异常 ({ev.scale:.3f})")

        # 判据 1：最小位移
        min_shift = self.min_shift_ratio * short_side
        if ev.shift < min_shift:
            return ev.reject(f"位移过小 ({ev.shift:.0f}px < {min_shift:.0f}px)，疑似误匹配")

        # 判据 2：内点空间散布
        # 两个口径满足**任一**即可：
        #   · hull_ratio 绝对面积比 —— 旧口径，小位移大重叠时主导
        #   · hull_norm  归一化覆盖比 —— 尺度无关，大位移小重叠时救回真重叠
        #   （hull_norm 高时本判据必然通过；故此处实际等价于「hull_ratio 或
        #     hull_norm」，与判据 3 的 spread_ok 是同一证据，不重复计。）
        if (ev.hull_ratio < self.min_hull_ratio
                and ev.hull_norm < self.min_hull_norm):
            return ev.reject(
                f"内点聚集 (凸包 {ev.hull_ratio:.1%} < {self.min_hull_ratio:.1%}，"
                f"归一化 {ev.hull_norm:.3f} < {self.min_hull_norm})，重叠区不可信")

        # 判据 3：内点率（兜底路径用更严的阈值；平移路径可由 spread_ok 替代 ——
        #         与上面的「平移一致性」是同一证据，避免同一把尺子量两次）
        need_ratio = FALLBACK_MIN_INLIER_RATIO if used_fallback else self.min_inlier_ratio
        need_inl = FALLBACK_MIN_INLIERS if used_fallback else self.min_inliers
        if ev.n_inliers < need_inl:
            return ev.reject(f"内点过少 ({ev.n_inliers} < {need_inl}，旋转兜底路径)")
        if ev.inlier_ratio < need_ratio and not spread_ok:
            return ev.reject(f"内点率低 ({ev.inlier_ratio:.0%} < {need_ratio:.0%})")

        ev.accepted = True
        ev.reason = "ok" if not used_fallback else "ok(旋转兜底)"
        return ev

    def _estimate_similarity(self, src: np.ndarray, dst: np.ndarray):
        """旋转兜底：放开旋转的相似变换估计。返回 (M, mask, True) 或 (None, None, True)。"""
        try:
            M, mask = cv2.estimateAffinePartial2D(
                src.reshape(-1, 1, 2), dst.reshape(-1, 1, 2),
                method=cv2.RANSAC, ransacReprojThreshold=self.ransac_thresh,
                maxIters=5000, confidence=0.9995,
            )
        except cv2.error:
            return None, None, True
        if M is None or mask is None:
            return None, None, True
        return M, mask, True

    # ── 粗筛 ──
    def _screen(self, pairs: List[Tuple[int, int]], notify=None) -> set:
        """1/N 尺度粗筛，返回候选对集合。

        先用 `good >= coarse_good_min` 把明显不重叠的对（真重叠只占 3~6% 的大批
        数据里，这就是 95% 的算力）挡在细匹配之外。判据只用 `good` 计数——
        粗尺度下真/假的 good 分不开，**内点率这个主力判据必须留到细尺度**。

        P0-2 起**不再收 `images`**：粗筛特征由 `all_pairs` 的预热阶段一并算好并
        建索引，这里只从缓存取（`coarse_by_idx`），因此不需要任何像素数组。
        """
        t0 = time.perf_counter()
        cand = set()
        n_neg = 0
        for i, j in pairs:
            try:
                g = self.coarse_good(i, j)
            except Exception:
                g = 0
            if g >= self.coarse_good_min:
                cand.add((i, j))
            else:
                n_neg += 1
        dt = time.perf_counter() - t0
        self.screen_stats = {
            'scale': self.coarse_scale, 'nfeatures': self.coarse_nfeatures,
            'good_min': self.coarse_good_min,
            'n_pairs': len(pairs), 'n_candidates': len(cand),
            'keep_ratio': round(len(cand) / max(len(pairs), 1), 4),
            'seconds': round(dt, 2),
        }
        if notify:
            notify(f"粗筛 1/{self.coarse_scale} (nf={self.coarse_nfeatures}, "
                   f"good>={self.coarse_good_min}): {len(pairs)} 对 → "
                   f"候选 {len(cand)} 对 ({self.screen_stats['keep_ratio']:.0%})，"
                   f"剔除 {n_neg} 对，耗时 {dt:.1f}s")
        return cand

    # ── 预热（可并行） ──
    def _warm_parallel(self, images, n: int, do_coarse: bool):
        """W1（2026-09-28）：并行预热。逐帧「解码 → 细特征 → 粗特征」。

        **等价性论证**：与串行版相比，输入像素、检测参数、每帧内部的调用顺序
        （先 `features` 后 `coarse_features`）、写入的缓存键（`_fp` 只依赖 idx+像素）
        **全部逐字相同**，唯一差别是「由哪个线程算」。每线程持有一对**自己的**检测器
        （`_make_detectors()`，参数与 `self._det` 逐字相同）⇒ 同一张图算出的
        关键点坐标与描述子逐位相同（已由 `_crit_warm_equiv.py` 逐张 `np.array_equal` 验证）。
        之后所有配对仍**串行**执行、判据一行未改 ⇒ 接受边与串行版逐条相同。

        `images[k]` 由各工作线程各自访问（`_LazyImages` 的 `__getitem__` 只在取
        预读槽/更新预读状态时短暂持锁）⇒ 并发取图安全；并行下**不开预读**
        （调用方应传 `prefetch=False`），因为并发解码本身就重叠了 I/O。

        `notes` 的追加在锁内（串行版天然有序，并行需保证 list.append 语义安全）。
        """
        workers = min(int(self.warm_workers), max(1, n))
        tls = threading.local()
        note_lock = threading.Lock()

        def work(k):
            try:
                img = images[k]
            except Exception as exc:
                self._shapes[k] = (0, 0)
                with note_lock:
                    self.notes.append(
                        f"图像 #{k} 读取失败，已按「与任何图都不重叠」处理："
                        f"{type(exc).__name__}: {exc}")
                return
            io = img.shape[:2]
            self._shapes[k] = (int(io[0]), int(io[1]))
            dets = getattr(tls, 'dets', None)
            if dets is None:
                dets = tls.dets = self._make_detectors()
            det, cdet = dets
            try:
                self.features(k, img, det=det)
            except Exception:
                pass
            if do_coarse:
                try:
                    self.coarse_features(k, img, det=cdet)
                except Exception:
                    pass
            del img

        ex = ThreadPoolExecutor(max_workers=workers)
        try:
            # 用 map 消费全部结果：异常在 work 内已被吞掉（与串行版一致）
            list(ex.map(work, range(n)))
        finally:
            ex.shutdown()

    # ── 全对遍历 ──
    def all_pairs(self, images: Sequence[np.ndarray], progress=None,
                  notify=None) -> List[MatchEvidence]:
        """遍历所有图像对，返回每对的 MatchEvidence（**长度恒为 n(n-1)/2**）。

        两级加速（都可关，关掉即回到全量逐对）：
          1. **粗筛**：1/8 尺度先把明显不重叠的对剔掉（实测 62 张保留 44.5% 候选，
             真重叠 0 漏）。被筛掉的对仍以一条 `accepted=False` 的证据占位，
             保证 `meta['matches']` 的长度与含义不变。
          2. **GPU 距离阵**：细匹配的 kNN 改由 torch.cdist 算（见 `_knn2`）。

        **连通性兜底**：粗筛后若接受边构成的图不连通，说明筛子可能漏掉了桥接边，
        于是把剩下所有对被筛掉的对**补做全量细匹配**。最坏情况退化为"多花一次
        粗筛的时间"，结果与全量逐对**逐条相同** —— 只会更慢，不会更差。

        **P0-2（2026-09-27）：预热一次、之后完全不碰像素。**
        `images` 可以传「按需读盘」序列（`stitcher._LazyImages`）：本函数只顺序
        访问 `images[k]` 恰好一次（下标递增，不回头），算完 SIFT/粗筛特征并记下
        尺寸就丢引用 ⇒ 匹配期常驻源图从 `3·H·W·n`（91 张 = 1.66GB）降到
        **同一时刻 1 张**。传普通 `list[np.ndarray]` 也照常工作（只是省不到内存，
        那取决于调用方是否还持有整批引用）。
        """
        n = len(images)
        if n < 2:
            return []

        # ── 预热：逐张取图 → 算特征 → 记尺寸 → 释放 ──
        # `_shapes` 与两个缓存索引建好后，下面所有配对都只走缓存，
        # 这一点是 P0-2 的关键：`pair()` 与 `coarse_good()` 都不再需要图。
        do_coarse = bool(self.coarse_screen and n >= self.coarse_min_images)
        if self.warm_workers > 1:
            # W1（2026-09-28）：帧间无依赖 ⇒ 并行预热（实测 1.41×）。
            # `short_side` 沿用串行版口径 = 第 0 张的短边（此处从 `_shapes[0]` 取回，
            # 与串行在同一位置赋同一个值；第 0 张读不到时同样不会赋值 → NameError，
            # 行为与串行一致）。
            self._warm_parallel(images, n, do_coarse)
            h0, w0 = self._shapes.get(0, (0, 0))
            if h0 and w0:
                short_side = float(min(h0, w0))
        else:
            for k in range(n):
                try:
                    img = images[k]
                except Exception as exc:
                    # 按需读盘（P0-2）时解码可能失败 —— 不抛，按「与任何图都不重叠」
                    # 处理，并把原因记进 `notes`（stitcher 会把 notes 逐条打成告警）。
                    self._shapes[k] = (0, 0)
                    self.notes.append(
                        f"图像 #{k} 读取失败，已按「与任何图都不重叠」处理："
                        f"{type(exc).__name__}: {exc}")
                    continue
                io = img.shape[:2]
                self._shapes[k] = (int(io[0]), int(io[1]))
                if k == 0:
                    short_side = float(min(io))
                try:
                    self.features(k, img)
                except Exception:
                    pass
                if do_coarse:
                    try:
                        self.coarse_features(k, img)
                    except Exception:
                        pass
                del img

        total = n * (n - 1) // 2
        pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]

        cand = None
        if do_coarse:
            cand = self._screen(pairs, notify=notify)

        out: List[MatchEvidence] = []
        done = 0

        def _run(i, j):
            nonlocal done
            # 不传图：pair() 走 features_by_idx() + self._shapes[i]
            ev = self.pair(i, j, short_side=short_side)
            out.append(ev)
            done += 1
            if progress:
                progress(done, total, ev)

        if cand is None:
            for i, j in pairs:
                _run(i, j)
            return out

        skipped: List[Tuple[int, int]] = []
        for i, j in pairs:
            if (i, j) in cand:
                _run(i, j)
            else:
                skipped.append((i, j))

        if skipped:
            acc = [e for e in out if e.accepted]
            groups = connected_components(n, acc)
            if len(groups) > 1:
                # 候选图不连通 → 粗筛漏了桥接边，全量补齐（零风险兜底）
                msg = (f"粗筛后接受边分为 {len(groups)} 组（不连通）→ "
                       f"对未筛的 {len(skipped)} 对补做全量精匹配，"
                       f"保证结果与全量逐对一致")
                self.fallback_triggered = True
                self.notes.append(msg)
                if notify:
                    notify(msg)
                for i, j in skipped:
                    _run(i, j)
            else:
                # 连通 → 被筛掉的对就是非重叠，仍写一条占位证据保持报告完整
                for i, j in skipped:
                    ev = MatchEvidence(i=i, j=j, accepted=False)
                    ev.reject(f"粗筛未命中（1/{self.coarse_scale} 尺度 good < "
                              f"{self.coarse_good_min}，判为非重叠）")
                    out.append(ev)
                    done += 1
                    if progress:
                        progress(done, total, ev)

        out.sort(key=lambda e: (e.i, e.j))
        return out


# ── 匹配图 → 连通分量 ──

def connected_components(n: int, edges: Sequence[MatchEvidence]) -> List[List[int]]:
    """根据已接受的边，把图分成若干互不重叠的组。"""
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for e in edges:
        if e.accepted:
            union(e.i, e.j)

    groups: dict = {}
    for k in range(n):
        groups.setdefault(find(k), []).append(k)
    return sorted(groups.values(), key=lambda g: (-len(g), g[0]))


def build_adjacency(n: int, edges: Sequence[MatchEvidence]) -> List[List[Tuple[int, np.ndarray, float]]]:
    """构建邻接表：adj[i] = [(j, M_ij, inliers), ...]，并补齐反向边。"""
    adj: List[List[Tuple[int, np.ndarray, float]]] = [[] for _ in range(n)]
    for e in edges:
        if not e.accepted or e.matrix is None:
            continue
        M = e.matrix
        # i -> j
        adj[e.i].append((e.j, M.copy(), float(e.n_inliers)))
        # j -> i：相似变换求逆
        R = M[:, :2]
        t = M[:, 2]
        try:
            Rinv = np.linalg.inv(R)
        except np.linalg.LinAlgError:
            continue
        Minv = np.hstack([Rinv, (-Rinv @ t).reshape(2, 1)])
        adj[e.j].append((e.i, Minv, float(e.n_inliers)))
    return adj
