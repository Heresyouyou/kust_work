# -*- coding: utf-8 -*-
"""纯 OpenCV 降级拼接引擎（不依赖 stitching 包）

交接文档指出的三个致命 Bug 全部在此修复：

* **Bug 2 / 3 — 旋转被丢弃、贴图不做 warp**
  旧代码把 `estimateAffinePartial2D` 拿到的完整相似变换丢掉，只用 (tx, ty)
  做整数像素平移粘贴。本模块用**完整相似变换 (theta, scale, tx, ty)** 做
  bundle adjustment，贴图时真正调用 `cv2.warpAffine`（INTER_LANCZOS4）。

* **Bug 4 — 400px 单频羽化把重叠区糊掉**
  改为 **Laplacian 金字塔多频带融合**（`cv2.pyrDown/pyrUp` 手写，
  避免依赖 stitching 包），保留高频细节；羽化宽度默认降到 100px。

* 附带：每张图先做亮度增益补偿（gain compensation），消除载物台照明不均
  导致的接缝处亮度跳变。
"""

from __future__ import annotations

import math
import os
import uuid
from collections import deque
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from geometry import MatchEvidence, build_adjacency


# ════════════════════════════════════════════════════════════
# 位姿求解：以第 0 张图为参考，每张图 (theta, tx, ty)
# ════════════════════════════════════════════════════════════

# ── 位姿残差向量化（2026-09-27 落地）────────────────────────────
# `solve_poses` 的 `residuals` 对**每条边完全独立**（只用 pa、pb、e.matrix），
# 因此可以批量算而**不改任何浮点结果**。逐位等价已实测证明
# （`.workbuddy/artifacts/stitch-验证脚本/_crit_pose_vec_equiv.py`）：
#   · 批量 `np.linalg.inv`(N,3,3) 与逐个 3x3 求逆 —— 逐位相同；
#   · 批量 `inv(Ab) @ Aa` 与逐个矩阵乘 —— 逐位相同；
#   · 端到端残差在 5 个不同 x（含 5 个尺度扰动，模拟 LM 迭代轨迹）上
#     `np.array_equal` **全部为真**。
# 收益（91 张 / 1120 边实测）：单次残差 41.6 ms → 1.31 ms；而 `residuals`
# 会被调 **2747 次**（lm 无数值 Jacobian ⇒ 每次迭代 273+1 次）⇒ 位姿段
# **117 s → 约 6 s**，占全流程约 30%。
# ★ 只改 `residuals` 的**实现**，**不碰 scipy 调用**（仍 `method="lm"` +
#   数值 Jacobian）⇒ 迭代序列逐位相同 ⇒ 位姿逐位相同 ⇒ 成品逐位相同。
#   这与「换解析/稀疏 Jacobian」（会改迭代序列）是两回事，别混。
# 置 False 可回到逐条版（A/B 对比与回退用）。
POSE_VEC = True


def _pose_matrix(p_global: np.ndarray, pose: np.ndarray) -> np.ndarray:
    """把全局位姿 (theta, tx, ty) 转成从「图 i 坐标 -> 全局坐标」的 2x3 矩阵。"""
    th, tx, ty = float(pose[0]), float(pose[1]), float(pose[2])
    # 先绕图中心旋转，再平移到全局位置
    h, w = p_global
    cx, cy = w / 2.0, h / 2.0
    c, s = math.cos(th), math.sin(th)
    R = np.array([[c, -s], [s, c]], np.float64)
    T1 = np.array([[1, 0, -cx], [0, 1, -cy]], np.float64)
    T2 = np.array([[1, 0, cx], [0, 1, cy]], np.float64)
    return T2 @ np.vstack([R, [0, 0]]) @ T1 + np.array([[0, 0, tx], [0, 0, ty]], np.float64)


def _compose(pose_a: np.ndarray, M_a2b: np.ndarray, p_global) -> np.ndarray:
    """已知 pose_a（图 a -> 全局），求 pose_b（图 b -> 全局）。

    M_a2b 是 a->b 的相似变换 (2x3)。因此 b -> a 为 inv(M_a2b)，
    再经 pose_a 映射到全局。
    """
    R = M_a2b[:, :2]
    t = M_a2b[:, 2]
    Rb_inv = np.linalg.inv(R)
    M_b2a = np.hstack([Rb_inv, (-Rb_inv @ t).reshape(2, 1)])
    A = _pose_matrix(p_global, pose_a)
    A3 = np.vstack([A, [0, 0, 1]])
    B4 = np.vstack([M_b2a, [0, 0, 1]])
    C = A3 @ B4
    th = math.atan2(C[1, 0], C[0, 0])
    return np.array([th, C[0, 2], C[1, 2]], np.float64)


def solve_poses(edges: Sequence[MatchEvidence], n: int,
                image_shape: Tuple[int, int]) -> Tuple[np.ndarray, List[List[int]], List[int]]:
    """BFS 生成初值 → scipy 最小二乘做全局 bundle adjustment。

    返回 (poses[n,3], groups, root_of_each)
    """
    h, w = image_shape
    p_global = (h, w)
    adj = build_adjacency(n, edges)

    poses = np.zeros((n, 3), np.float64)
    seen = [False] * n
    groups: List[List[int]] = []
    roots: List[int] = [-1] * n

    for root in range(n):
        if seen[root]:
            continue
        seen[root] = True
        roots[root] = root
        comp = [root]
        q = deque([root])
        while q:
            cur = q.popleft()
            for nb, M_cur2nb, _inl in adj[cur]:
                if seen[nb]:
                    continue
                seen[nb] = True
                roots[nb] = root
                poses[nb] = _compose(poses[cur], M_cur2nb, p_global)
                comp.append(nb)
                q.append(nb)
        groups.append(sorted(comp))

    # ── 全局 bundle adjustment（每个连通分量独立优化） ──
    try:
        from scipy.optimize import least_squares
    except Exception:
        return poses, groups, roots

    for comp in groups:
        if len(comp) < 3:
            continue
        idx = {g: k for k, g in enumerate(comp)}
        local_edges = [e for e in edges
                       if e.accepted and e.i in idx and e.j in idx and e.matrix is not None]
        if len(local_edges) < 2:
            continue

        x0 = np.concatenate([poses[g] for g in comp])

        # 每条边的「局部索引 / 权重 / 目标矩阵」在循环外抽一次 —— 它们是常量，
        # 放进 residuals 里等于 2747 次重复构造。抽出来不改任何算术。
        _ei = np.array([idx[e.i] for e in local_edges], np.intp)
        _ej = np.array([idx[e.j] for e in local_edges], np.intp)
        _wts = np.array([math.sqrt(max(e.n_inliers, 1) / 50.0)
                         for e in local_edges], np.float64)
        _mats = np.array([e.matrix for e in local_edges], np.float64)   # (m,2,3)

        def residuals(x):
            r = [x[0], x[1], x[2]]  # 固定参考图，消 gauge 自由度
            if not POSE_VEC:
                # ── 逐条版（A/B 与回退用；与历史实现逐字一致）──
                P = {g: x[3 * k:3 * k + 3] for g, k in idx.items()}
                for e in local_edges:
                    pa, pb = P[e.i], P[e.j]
                    Aa = np.vstack([_pose_matrix(p_global, pa), [0, 0, 1]])
                    Ab = np.vstack([_pose_matrix(p_global, pb), [0, 0, 1]])
                    # 期望：inv(Ab) @ Aa  ≈ e.matrix
                    M = np.linalg.inv(Ab) @ Aa
                    E = M[:2] - e.matrix
                    wt = math.sqrt(max(e.n_inliers, 1) / 50.0)
                    r.extend((wt * E).ravel().tolist())
                return np.array(r, np.float64)

            # ── 向量化版（逐位等价，见模块顶部 POSE_VEC 注释）──
            # ① 每个位姿只需算一次 A（旧版每条边重算 2 次；同入参 ⇒ 同结果）
            A_all = np.empty((len(comp), 3, 3), np.float64)
            for k in range(len(comp)):
                A_all[k, :2] = _pose_matrix(p_global, x[3 * k:3 * k + 3])
            A_all[:, 2, 0] = 0.0
            A_all[:, 2, 1] = 0.0
            A_all[:, 2, 2] = 1.0
            # ② 批量求逆 + 批量矩阵乘（已证与逐个 3x3 的 inv/matmul 逐位相同）
            M = np.matmul(np.linalg.inv(A_all[_ej]), A_all[_ei])[:, :2, :]
            # ③ 权重点乘；reshape(-1) 的展平顺序与「逐条 extend」完全一致
            E = M - _mats
            return np.concatenate([x[:3], (E * _wts[:, None, None]).reshape(-1)])

        try:
            res = least_squares(residuals, x0, method="lm", max_nfev=200)
            for g, k in idx.items():
                poses[g] = res.x[3 * k:3 * k + 3]
        except Exception:
            pass  # 优化失败就保留 BFS 初值

    return poses, groups, roots


# ════════════════════════════════════════════════════════════
# 亮度增益补偿
# ════════════════════════════════════════════════════════════

def gain_compensate(images: Sequence[np.ndarray],
                    masks: Sequence[np.ndarray]) -> List[np.ndarray]:
    """按重叠区灰度均值，把每张图拉到一个公共亮度。

    简化版 gain_blocks：以最大灰度图为基准，逐图求重叠区均值比。
    """
    n = len(images)
    if n < 2:
        return [im.astype(np.float32) for im in images]

    f32 = [im.astype(np.float32) for im in images]
    means = []
    for im, mk in zip(f32, masks):
        g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
        m = mk > 0
        means.append(float(g[m].mean()) if m.any() else float(g.mean()))
    target = float(np.median(means))

    out = []
    for im, mn in zip(f32, means):
        gain = target / mn if mn > 1e-3 else 1.0
        gain = float(np.clip(gain, 0.7, 1.4))
        out.append(np.clip(im * gain, 0, 255))
    return out


# ════════════════════════════════════════════════════════════
# 多频带融合（Laplacian pyramid）
# ════════════════════════════════════════════════════════════

def _pyramid_levels(size: Tuple[int, int], min_dim: int = 32) -> int:
    h, w = size
    lv = 0
    while min(h, w) > min_dim and lv < 8:
        h, w = h // 2, w // 2
        lv += 1
    return max(lv, 1)


def multiband_blend(images: Sequence[np.ndarray],
                    masks: Optional[Sequence[np.ndarray]],
                    canvas_size: Tuple[int, int],
                    levels: Optional[int] = None) -> np.ndarray:
    """多频带（Laplacian 金字塔）融合。

    低频用权重做平滑过渡（消除接缝亮度跳变），高频按最大权重取（保留细节，
    避免多张平均把矿物颗粒糊掉）。

    ## 内存（本函数是整条链路的内存瓶颈）

    峰值从 O(n·画布) 压到 O(画布)，三处改动，**数值结果与旧实现逐位相同**：

    1. `images`/`masks` 允许传**迭代器**，逐个消费、用完即释放。旧实现要求调用
       方先把 n 张「全画布中间图」攒成列表，那一步就吃掉 `4·n·画布` 字节
       ——16 张 50MP 画布时是 3.2GB，且随 n 线性膨胀到 91 张不可行。
    2. 权重金字塔只存**单通道** float32，靠广播参与运算。旧版
       `np.dstack([wq]*3)` 把它胀成 3 通道，白占 2/3。
    3. Laplacian **就地**计算（`g[k] -= pyrUp(g[k+1])`），且首图直接接管该临时
       数组当累加器，省掉一次全画布 float32 拷贝。

    `masks=None` 时 `images` 需是 `(img, mask)` 对的迭代器。
    """
    ch, cw = canvas_size
    if levels is None:
        levels = _pyramid_levels((ch, cw))

    # 各层尺寸链（pyrDown 的输出尺寸是 ceil(d/2)）
    dims = [(ch, cw)]
    for _ in range(levels):
        _h, _w = dims[-1]
        dims.append(((_h + 1) // 2, (_w + 1) // 2))

    # ── 预分配并复用每层的中间 buffer ──
    # 旧实现每张图、每层都新分配一次画布级 float32（层 0 在 52MP 画布上
    # 就是 627MB），91 张 × 9 层 ≈ 819 次大块分配。glibc 对 >128KB 的
    # 请求走 mmap，于是每次都是 mmap + 首次触碰的 page fault + munmap，
    # 实测是融合段的主要开销之一。
    # 复用后每个 buffer 只分配一次；`pyrDown/pyrUp` 直接写 `dst`。
    # ⚠️ 这与旧实现**数值逐位相同**：同样的算子、同样的输入、同样的顺序。
    gbuf = [np.empty((h, w, 3), np.float32) for h, w in dims]
    wbuf = [np.empty((h, w), np.float32) for h, w in dims]
    upbuf = [np.empty((h, w, 3), np.float32) for h, w in dims]

    # 累加器也预分配。旧实现用「首图接管」（acc = lap0，之后 += lap1…），
    # 新版从 0 起累加（0 + lap0 == lap0，浮点上逐位相同，仅 -0 会变成 +0）。
    acc_lap = [np.zeros((h, w, 3), np.float32) for h, w in dims]
    acc_w = [np.zeros((h, w), np.float32) for h, w in dims]

    stream = images if masks is None else zip(images, masks)
    n_used = 0

    for im, mk in stream:
        if im is None or mk is None:
            continue
        n_used += 1

        # 第 0 层：就地写入预分配 buffer（等价于 im.astype(np.float32)）
        np.copyto(gbuf[0], im, casting='unsafe')
        np.copyto(wbuf[0], mk, casting='unsafe')

        # 图像/权重金字塔（写入各自的下一层 buffer）
        for k in range(levels):
            cv2.pyrDown(gbuf[k], gbuf[k + 1])
            cv2.pyrDown(wbuf[k], wbuf[k + 1])

        # Laplacian：就地减去上一层上采样，写进 upbuf 复用
        for k in range(levels):
            cv2.pyrUp(gbuf[k + 1], upbuf[k],
                      dstsize=(dims[k][1], dims[k][0]))
            gbuf[k] -= upbuf[k]

        # 加权累加（就地乘，再累加）
        for k in range(levels + 1):
            wq = wbuf[k]
            if wq.shape != gbuf[k].shape[:2]:
                wq = cv2.resize(wq, (gbuf[k].shape[1], gbuf[k].shape[0]))
            if MULADD_3CH:
                # 把单通道权重胀成**连续** 3 通道再乘（缘由见模块级 `MULADD_3CH`）。
                # `upbuf[k]` 在上一段拉普拉斯步之后已经无用了（`k == levels` 时更是
                # 从未写过），正好当这个 3ch scratch ⇒ **零额外内存**；下一张图进来
                # 时拉普拉斯步会重新写它，不存在脏数据。
                cv2.merge([wq, wq, wq], upbuf[k])
                cv2.multiply(gbuf[k], upbuf[k], dst=gbuf[k])
            else:
                gbuf[k] *= wq[..., None]
            acc_lap[k] += gbuf[k]
            acc_w[k] += wq

    if n_used == 0:
        return np.zeros((ch, cw, 3), np.uint8)

    # 重建：逐层上采样累加（upbuf 复用；acc 用完即释放以压低峰值）
    out = acc_lap[levels] / np.maximum(acc_w[levels], 1e-6)[..., None]
    acc_lap[levels] = None
    acc_w[levels] = None
    for k in range(levels - 1, -1, -1):
        cv2.pyrUp(out, upbuf[k], dstsize=(dims[k][1], dims[k][0]))
        out = upbuf[k] + acc_lap[k] / np.maximum(acc_w[k], 1e-6)[..., None]
        acc_lap[k] = None
        acc_w[k] = None
    return np.clip(out, 0, 255).astype(np.uint8)


# 融合阶段是否启用「垂直窗口」加速（只切上下、宽度仍为整画布）。
# 置 False 会退化成整画布窗口 —— 因为窗口版在「窗口=整画布」时与
# `multiband_blend` 逐位相同，所以这个开关可以安全地做 A/B 逐位比对与回退。
V_CROP_ENABLED = True

# 融合加权乘加是否用**连续 3 通道权重**（而非 `w[..., None]` 的 stride-0 广播）。
# 置 False 回到 `gbuf[k] *= wbuf[k][..., None]` 的老写法（供 A/B 与逐位比对）。
#
# 为什么：`w[..., None]` 的最后一维 stride=0，numpy 内层循环因此掉出 SIMD。
# 实测（3200x4000 单层，`.workbuddy/artifacts/stitch-验证脚本/_muladd_ab.py`）：老写法 160.3ms，
# `cv2.merge` 胀成 3ch + `cv2.multiply` 后 62.8ms = **2.52×**；
# 若 3ch 权重已就绪则是 57.2ms（2.77×）—— 即瓶颈全在广播本身。
# ⚠️ 不能用 `np.copyto(w3, w[..., None])` 来胀：171.2ms，反而比老写法慢（0.92×），
#    因为广播填写的写侧虽连续、读侧 stride=0，同样掉 SIMD。
# ⚠️ 也不能让**权重金字塔本身**存 3 通道：`cv2.pyrDown` 在 3ch 与 1ch 上**不逐位相同**
#    （实测见 `.workbuddy/artifacts/stitch-验证脚本/_muladd_ab.py` 的 Q1），金字塔必须保持单通道。
# 逐位：`cv2.multiply` 是逐元素单次舍入的 IEEE 乘法，与 numpy 的 `*` 同值；
#       仅改「怎么乘」，不改算子与顺序 ⇒ 输出逐位相同（`_muladd_ab.py check` 验证）。
MULADD_3CH = True

# 成品是否允许**向上采样**。用户口径（2026-09-24）：**禁止** ——
# 「不做无效放大，成品只能向下采，不能向上采」。放大不产生新信息，只会把插值
# 像素写进科研用图。置 True 会恢复历史行为（`out_side == 0` 且被内存预算缩过时，
# 把结果用 `INTER_CUBIC` 拉回名义画布尺寸），仅供 A/B 与逐位比对，生产不要打开。
# 作同一口径的还有 `stitcher._run_fallback` 里「×1/s0 还原到原图分辨率」那步。
UPSCALE_OUTPUT = False

# ── P0-3 融合段「全画布临时收窄」三条开关（2026-09-27）────────────────────
# 三条都只改「怎么算」不改「算什么」，逐位等价；置 False 即回到改前写法，
# 供 A/B 逐位对拍（`.workbuddy/artifacts/stitch-验证脚本/_crit_p03_equiv.py`）与回退。
#
# COV_WINDOW_OR：`cov` 只 OR 窗口切片，而不是整画布。
#   窗口 = 图像 bbox ± roi_halo ⊇ bbox，而权重掩膜按 BORDER_CONSTANT 0 生成、
#   支撑 ⊆ bbox ⇒ 窗口外 `mk ≡ 0` ⇒ OR 0 不改变结果，逐位相同。
#   省掉每张一次的全画布比较（52MP 画布 = 52MB 临时 bool）。
# MASK_MUL：清非覆盖区改用 elementwise 乘，而不是「建全画布 bool + 花式索引」。
#   `cov ∈ {0,1}`，result 为 uint8 ⇒ `uint8 * 1` 不溢出、逐位相同；
#   省掉 52MB 的 bool 缓冲与一次随机写（花式索引本来就慢）。
# BASE_REUSE：掩膜底图按形状复用，而不是每张 `np.ones(shape)*255`。
#   值恒为 255，且从不就地修改（比例尺分支走 `astype` 生成新数组再重绑名字）。
COV_WINDOW_OR = True
MASK_MUL = True
BASE_REUSE = True

# ── W1 · 内容 warp 输出原点 ROI 化（2026-09-28）────────────────────────────
# ★ 这是融合段最大的时间杠杆：内容 `warpAffine`(LANCZOS4) 占融合 ~59%
#   （91 张 × 整画布 73.5MP ≈ 6.7 Gpix），而垂直窗口平均只用到画布高的 ~43%
#   ⇒ 其余部分算完就扔。改成「只 warp 窗口那一段」实测融合 **177.8s → 113.3s**。
#
# ## 为什么可以（推翻 2026-09-24 的旧结论）
# 旧记录说「warp 必须整画布算，实测 698x564 窗口里 16786 个像素不同，最大差 12」。
# 2026-09-28 复核发现那次 A/B 的**平移量算错了**：把输出原点位移写成了
# `M[0,2] -= M[0,1]*ry0` / `M[1,2] -= M[1,1]*ry0`（把正向变换当成了逆变换），
# 于是实际只平移了 `M11·ry0 ≈ 0.9994·ry0` —— 差 0.06% × ry0 ≈ 1px 量级。
# 用同样错误的公式复现，差异立刻是「30828 px / max 238」，量级吻合。
#
# `warpAffine` 的 M 是**正向**变换 `dst = M·[x_src, y_src, 1]`。窗口第
# `y_local` 行对应绝对第 `y_abs = y_local + ry0` 行，故正确的原点搬运是
#     **`M[1,2] -= ry0`（且只减这一项；x 不动、旋转/尺度块一律不动）**
# 该减法是 float64 上的**精确**减法（|M12|,ry0 ≪ 2^52）。
#
# 实测（`.workbuddy/artifacts/stitch-验证脚本/`）：
#   · `_crit_warp_roi_retest.py`：真实几何 × 6 组 (tx,ty) × 4 个 ry0 ⇒ 全 `array_equal`；
#   · **`_crit_warp_roi_sweep.py`：12 组真实构造 Mw × 5 个 ry0 × 1080 M 像素 ⇒ 0 差异**。
#
# ## ⚠️ 只对**内容**（LANCZOS4）成立；掩膜（NEAREST）**不可** ROI 化
# `_crit_mask_sweep.py` 实测：INTER_NEAREST 在 24 组 × 4 个 ry0 × **1728 M** 掩膜
# 像素上有 **3421 px** 跳变，且全落在掩膜边界（`ry0=0` 时恒 0 差异）
# ⇒ NEAREST 的坐标量化对原点搬运敏感得多（二值图一旦跨界就整块翻 0↔255，
# 会同时改变 `cov` 与第 0 层权重）。端到端复现：如果连掩膜一起 ROI 化，
# 产物会出现 **667 px / max 65** 的差异。
# 掩膜 warp 只占 ~10.9ms/张（内容 1037ms）⇒ **保持整画布**几乎不花时间、零风险。
#
# 置 False 即回到「整画布 warp 再裁窗口」（旧行为），供 A/B 逐位对拍与回退。
WARP_ROI = True

# ── P1-1 融合累加器落盘（2026-09-27）─────────────────────────────────────
# `acc_lap`（float32 3ch）+ `acc_w`（float32 1ch）改 `np.memmap` 落到 D 盘临时文件，
# 省下约 **16 B/画布像素**的常驻匿名内存（各层合计约 5.33 B/px × 1.33 ≈ 7.1 B/px…
# 按层 0 为主计，实测足迹 77~85 → 约 61~69 B/px）。
#
# **逐位不变**：同 dtype、同数值、纯 elementwise 读改写；重建仍在普通 ndarray 上做
# （`acc_lap[k] / np.maximum(acc_w[k], 1e-6)` 的结果是新建数组，不是 memmap）。
#
# ⚠️ **收益高度依赖页缓存命中率**，必须实测后再决定是否保留默认开启：
#    累加器总量 ≈ 画布 5.33 B/px（68MP → 1.1GB），若整块能驻留页缓存则几乎不产生
#    真实磁盘流量（D 盘顺序写 1446 MB/s），此时「省下的」只是把**匿名内存**换成了
#    **可回收的页缓存** —— 进程 RSS 确实下降，但系统层面占用相近。
#    设 False 即回到 `np.zeros`（改前行为）。
ACC_MEMMAP = False
ACC_MEMMAP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              '.cache_mm')


class _AccAlloc:
    """融合累加器分配器：`ACC_MEMMAP=True` 时落盘（memmap），否则普通 `np.zeros`。

    ⚠️ **必须显式 close**：Windows 上 memmap 不关掉就删不掉文件。这里自己持有
    引用（调用方会把 `acc_lap[k]` 置 None 提前放引用），`close()` 时先 `flush`
    再关 `_mmap`，最后删文件；`__del__` 兜底，避免异常路径漏删。
    """

    def __init__(self, on_disk: bool, tmpdir: str):
        self.on_disk = bool(on_disk)
        self.tmpdir = tmpdir
        self._arrs: List[np.memmap] = []
        self._files: List[str] = []

    def alloc(self, shape, tag: str) -> np.ndarray:
        if not self.on_disk:
            return np.zeros(shape, np.float32)
        try:
            os.makedirs(self.tmpdir, exist_ok=True)
            # 唯一文件名：文件必然是新建的 ⇒ 内容为 0（不需要额外清零）。
            fn = os.path.join(self.tmpdir, f"acc_{tag}_{uuid.uuid4().hex}.dat")
            a = np.memmap(fn, dtype=np.float32, mode='w+', shape=shape)
            self._arrs.append(a)
            self._files.append(fn)
            return a
        except Exception:
            # 磁盘/权限任何异常 → 就地回落内存，绝不因此让整单失败
            self.on_disk = False
            return np.zeros(shape, np.float32)

    def close(self):
        for a in self._arrs:
            try:
                a.flush()
                a._mmap.close()
            except Exception:
                pass
        self._arrs.clear()
        for fn in self._files:
            try:
                os.remove(fn)
            except OSError:
                pass
        self._files.clear()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def _purge_mm_dir(tmpdir: str):
    """清掉上次崩溃残留的累加器临时文件（只删自己命名的 `acc_*.dat`）。"""
    try:
        for fn in os.listdir(tmpdir):
            if fn.startswith('acc_') and fn.endswith('.dat'):
                try:
                    os.remove(os.path.join(tmpdir, fn))
                except OSError:
                    pass
    except OSError:
        pass


def roi_halo(levels: int) -> int:
    """**垂直**裁剪所需的外扩量（层 0 像素）：`4·2^L`。

    ## 为什么是 4·2^L

    要保证「窗口内的加权结果整块累加进画布」与「全画布实现」逐位相同，
    窗口（层 0）必须在**垂直方向**同时满足两件事（以窗口上边界为例）：

    1. **权重非零区完整** —— `w⁽ᵏ⁾ = pyrDownᵏ(mask)` 的支撑（层 0 像素）
       是 `2(2ᵏ−1)`，即超出覆盖区约 `1` 个 level-k 像素。窗口外权重严格为 0，
       累加时相当于加 0，浮点值不变。
    2. **权重非零区内 `lap` 精确** —— 窗口边界处的 `REFLECT_101` 补边与
       全画布版读到的是不同数据（真实画布继续、窗口镜像折回），该污染每层
       向内扩张 2 个 level-k 像素、再经 `lap = g − pyrUp(g⁽ᵏ⁺¹⁾)` 传播到
       上一层，合计约 **3 个 level-k 像素**；加上第 1 条所需的 1 个，
       即 `4·2^L`。

    窗口原点会对齐到 `2^L` 的倍数（只多不少），窗口贴到画布上下边时
    边界条件与全画布版**同一条边**、完全不产生污染。所以 `4·2^L` 是余量
    最小的安全值。

    ## 实测验证（`.workbuddy/artifacts/stitch-验证脚本/_vcrop_equiv.py`）

    画布 384x320/L=3 的 6 类贴边用例 ×4 档 halo + 8 个随机四边形，
    以及 3000x2400/L=6 的 20 个随机四边形：`3·2^L` 偶有 1 灰阶差异，
    **`4·2^L` 及以上全部 0 差异，且对 halo 单调**（越大越安全）——
    这正是「纯 halo 阈值型」判据的特征。

    ## ⚠️ 只对「垂直裁剪」有效

    窗口的**宽度必须等于画布宽度**（即只切上下、不切左右）。因为
    `cv2.pyrDown/pyrUp` 的 SIMD 尾部处理会按**数组宽度**取不同的浮点累加
    顺序，宽度一变就出现 ULP 级差异（见 `.workbuddy/artifacts/stitch-验证脚本/_roi_dbg6.py`：内容金字塔
    在层 1+ 有 187 个像素不同，而掩模金字塔因只有 0/255、运算精确而完全一致）。
    这种差异**无法靠放大 halo 消除**，且表现为「halo 大小与成功与否非单调」
    ——实测 `halo=4·2^L` 失败、`6·2^L` 通过、`8·2^L` 又失败。
    因此四边同时裁剪的 ROI 方案已被否决；只有全宽窗口是逐位可靠的。
    """
    return 4 * (1 << int(levels))


def _multiband_blend_roi(stream, canvas_size, levels):
    """窗口版多频带融合（**只用于垂直裁剪**，即 `rx0=0` 且窗口宽度=画布宽度）。

    每张图只在「覆盖区 ± `roi_halo`」的**上下**窗口内建金字塔并加权，然后把
    窗口的各层结果**整块**累加到画布对应行切片。工作量从 O(整画布) 降到
    O(图高 + 2·halo) × 画布宽（52MP 画布 / 91 张实测约 2.2×）。

    ## 为什么与 `multiband_blend` 逐位相同（可证 + 已实测）

    设画布像素 `p`（层 0）。要让窗口版结果与全画布版逐位相同，只需：
    **对每个 `w_i,k[p_k] ≠ 0` 的像素，`lap_i,k[p_k]` 与 `w_i,k[p_k]` 必须精确**。

    1. **窗口外 `w ≡ 0`** —— `w⁽ᵏ⁾ = pyrDownᵏ(mask)` 的支撑超出覆盖区约
       `1` 个 level-k 像素，窗口含「覆盖区 ± `roi_halo`」，故窗口外权重
       严格为 0。累加窗口外等于加 0，浮点值不变；更关键的是 `acc_w = 0`
       处 `acc_lap/acc_w` 恒为 `0/1e-6 = 0`（**精确 0**，不是"接近 0"），
       重建时 `pyrUp(0)=0`，所以窗口边缘的边框效应**不会**顺着重建渗进有效区。
    2. **垂直边界只污染约 3 个 level-k 像素** —— `pyrDown`/`pyrUp` 是 5 抽头
       可分离滤波，窗口边界处的 `REFLECT_101` 与全画布版读到不同数据，污染
       每层向内扩张 2 个 level-k 像素、再经 `lap = g − pyrUp(g⁽ᵏ⁺¹⁾)` 传播，
       合计约 3 个；加上第 1 条所需的 1 个，即 `roi_halo = 4·2^L`（见 `roi_halo`）。
    3. **宽度不动** —— 窗口宽度必须**等于**画布宽度。这样每一行的水平滤波
       输入、数组宽度、SIMD 尾部结构都与全画布版完全一致，逐位同源。
       （宽度一旦改变，`cv2.pyrDown/pyrUp` 的 SIMD 尾部会取不同的浮点累加
       顺序 → ULP 级差异，且无法用 halo 消除。实测见 `.workbuddy/artifacts/stitch-验证脚本/_roi_dbg6.py`。）
    4. **取整对齐** —— 窗口原点 `rx0/ry0` 必须是 `2^L` 的倍数，这样
       level-k 切片 `[ry0>>k, ry0>>k + dims[k])` 与画布的 level-k 网格
       严格同格（`ry0>>k` 为整数，且 `ry0>>k + ceil(rh/2^k) = ceil((ry0+rh)/2^k)`）。
       窗口只需**落在画布内**（`ry0+rh ≤ CH`），尺寸不必是 `2^L` 的倍数 ——
       画布侧 `dims_c[k] = ceil(CH/2^k) ≥ ceil((ry0+rh)/2^k)`。
       窗口边贴到画布上下边时边界条件与全画布版**同一条边**，无污染。

    `stream` 每项：
      `(img_roi, mask_roi, rx0, ry0)` —— 窗口内容与窗口左上角在画布上的
      坐标。`rx0` 必须为 0（或与画布同宽），`ry0` 必须是 `2^levels` 的倍数。

    ⚠️ **前提：`img_roi` 必须逐位等于「该窗口在整画布坐标系下的 warp 结果」。**
    W1（2026-09-28）起 `_warp_stream` 直接用 `M[1,2] -= ry0` 把输出原点搬到窗口
    上沿、**只算窗口那几行**；这与「整画布 warp 再裁」逐位相同（论证见模块级
    `WARP_ROI`，实测见 `_crit_warp_roi_retest.py` / `_crit_warp_roi_e2e.py`）。
    ⚠️ 旧注释（2026-09-24）曾断言「换原点会改像素，warp 必须整画布」——那条
    结论源于平移量算错（把正向矩阵当逆矩阵），已于 2026-09-28 推翻。
    """
    ch, cw = canvas_size
    dims_c = [(ch, cw)]
    for _ in range(levels):
        _h, _w = dims_c[-1]
        dims_c.append(((_h + 1) // 2, (_w + 1) // 2))

    # P1-1：累加器可按需落盘（见模块级 `ACC_MEMMAP`）。`close()` 在下方每个
    # 出口处调用（`_AccAlloc.__del__` 兜底异常路径），确保 Windows 上文件能删掉。
    _acc = _AccAlloc(ACC_MEMMAP, ACC_MEMMAP_DIR)
    if _acc.on_disk:
        _purge_mm_dir(ACC_MEMMAP_DIR)

    acc_lap = [_acc.alloc((h, w, 3), f"lap{k}") for k, (h, w) in enumerate(dims_c)]
    acc_w = [_acc.alloc((h, w), f"w{k}") for k, (h, w) in enumerate(dims_c)]

    # 窗口尺寸在同一批图里高度重复（都是 2^L 的倍数），故按尺寸复用 buffer。
    # ⚠️ key 必须带 `tag`（g / w / up 三套互不同源）：`gbuf[k]` 与 `upb[k]`
    # 形状完全相同，若只按形状缓存，两者会拿到**同一个 ndarray** ——
    # `cv2.pyrUp(gbuf[k+1], upb[k])` 就会原地覆盖掉高斯层，
    # 接着 `gbuf[k] -= upb[k]` 变成 `x -= x`，Laplacian 恒等于 0（融合直接失效）。
    pool: Dict[tuple, np.ndarray] = {}

    def _b(tag, hh, ww, ch3):
        key = (tag, hh, ww, ch3)
        b = pool.get(key)
        if b is None:
            b = np.empty((hh, ww, 3) if ch3 else (hh, ww), np.float32)
            if len(pool) > 24:         # 尺寸种类极有限，防意外膨胀
                pool.clear()
            pool[key] = b
        return b

    n_used = 0
    for img, mk, rx0, ry0 in stream:
        if img is None or mk is None:
            continue
        n_used += 1
        rh, rw = img.shape[:2]
        # 层尺寸链必须与 `cv2.pyrDown` 一致用 **ceil**（输出尺寸是 ceil(d/2)），
        # 用 floor 会在奇数尺寸时错一格。
        dims = [(rh, rw)]
        for _ in range(levels):
            dims.append(((dims[-1][0] + 1) // 2, (dims[-1][1] + 1) // 2))

        gbuf = [_b('g', hh, ww, True) for hh, ww in dims]
        wbuf = [_b('w', hh, ww, False) for hh, ww in dims]
        upb = [_b('u', hh, ww, True) for hh, ww in dims]

        np.copyto(gbuf[0], img, casting='unsafe')
        np.copyto(wbuf[0], mk, casting='unsafe')
        for k in range(levels):
            cv2.pyrDown(gbuf[k], gbuf[k + 1])
            cv2.pyrDown(wbuf[k], wbuf[k + 1])
        for k in range(levels):
            cv2.pyrUp(gbuf[k + 1], upb[k], dstsize=(dims[k][1], dims[k][0]))
            gbuf[k] -= upb[k]

        for k in range(levels + 1):
            if MULADD_3CH:
                # 连续 3 通道权重（缘由见模块级 `MULADD_3CH`）：`wbuf[k][..., None]`
                # 的 stride-0 广播会让 numpy 掉出 SIMD。`upb[k]` 在上一段拉普拉斯
                # 步之后已无用（`k == levels` 时从未写过），借来当 3ch scratch
                # ⇒ **零额外内存**；下一张图的拉普拉斯步会重新写它。
                cv2.merge([wbuf[k], wbuf[k], wbuf[k]], upb[k])
                cv2.multiply(gbuf[k], upb[k], dst=gbuf[k])
            else:
                gbuf[k] *= wbuf[k][..., None]
            ty0, tx0 = ry0 >> k, rx0 >> k
            hh, ww = dims[k]
            # 窗口在画布内时可证 hh/ww 恰好落在画布层 k 内；这里仍防御性裁一下
            # （``ry0+rh > CH`` 的调用方错误不该变成形状错乱）。
            hh = min(hh, dims_c[k][0] - ty0)
            ww = min(ww, dims_c[k][1] - tx0)
            if hh <= 0 or ww <= 0:
                continue
            acc_lap[k][ty0:ty0 + hh, tx0:tx0 + ww] += gbuf[k][:hh, :ww]
            acc_w[k][ty0:ty0 + hh, tx0:tx0 + ww] += wbuf[k][:hh, :ww]

    if n_used == 0:
        _acc.close()
        return np.zeros((ch, cw, 3), np.uint8), 0

    # 重建仍在整画布上做（只做一次，成本远低于逐图）
    upb = [np.empty((h, w, 3), np.float32) for h, w in dims_c]
    out = acc_lap[levels] / np.maximum(acc_w[levels], 1e-6)[..., None]
    acc_lap[levels] = None
    acc_w[levels] = None
    for k in range(levels - 1, -1, -1):
        cv2.pyrUp(out, upb[k], dstsize=(dims_c[k][1], dims_c[k][0]))
        out = upb[k] + acc_lap[k] / np.maximum(acc_w[k], 1e-6)[..., None]
        acc_lap[k] = None
        acc_w[k] = None
    _acc.close()
    return np.clip(out, 0, 255).astype(np.uint8), n_used


def _trim_black_border(result: np.ndarray, margin: int = 8,
                       black_eps: int = 8) -> np.ndarray:
    """裁掉四周「整行/整列全黑」的空白边。

    旧规则是 `cov >= max(1.0, n * 0.30)` —— 阈值随图片数 n 线性增长，而画布的
    平均覆盖率只有 O(1)。n 一大多阈值就远高于均值，等于只留下多条扫描线叠回去
    的那一小块。实测 `T2-C` 8 张：画布 4941x4501，内容占比 79.5%，却只输出
    3669x3118 —— **丢掉了内容包围盒的 48.5%，而原始帧 99.9% 是非黑像素，
    四角灰度 68~80（根本没有黑角），被丢的是实实在在的薄片内容。**

    改为与 n 无关的保守判据：只有**整行/整列的最大灰度都 <= black_eps** 才裁。
    用 max 而不是 mean，保证只要一行里有任何一个非黑像素就整行保留 ——
    因此**不可能切到有效内容**，最多只是把纯黑的外沿修干净。

    注：若要把斜向扫描留下的黑色边缘也去掉，需要输出不规则 alpha 蒙版，
    那是矩形输出之外的改动，暂不做（手动拼图 `T2-C-手动.jpg` 同样保留了
    这些黑色边缘，作为对照是一致的）。
    """
    g = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY) if result.ndim == 3 else result
    col_has = np.where(g.max(axis=0) > black_eps)[0]
    row_has = np.where(g.max(axis=1) > black_eps)[0]
    if not len(col_has) or not len(row_has):
        return result
    h, w = g.shape[:2]
    x0 = max(0, int(col_has[0]) - margin)
    x1 = min(w, int(col_has[-1]) + 1 + margin)
    y0 = max(0, int(row_has[0]) - margin)
    y1 = min(h, int(row_has[-1]) + 1 + margin)
    if x0 == 0 and y0 == 0 and x1 == w and y1 == h:
        return result
    return result[y0:y1, x0:x1]


# ════════════════════════════════════════════════════════════
# 降级引擎主流程
# ════════════════════════════════════════════════════════════

def plan_out_scale(canvas_w: int, canvas_h: int,
                   out_side: int = 0, max_pix: int = 0) -> Tuple[float, List[str]]:
    """决定输出缩放系数与受限原因。

    两个约束都是**上限**，故取更严者：

    * `out_side` —— 用户选的图幅档位：画布最长边上限（画布坐标，px）。
    * `max_pix`  —— 内存安全阀：画布像素上限。

    返回 `(out_scale, capped_by)`，`capped_by` 是 `'user'` / `'memory'` 的子集
    （可能两者都有：用户档位本身就超了内存）。提出来单测，避免"档位没生效"
    这类问题只能靠跑一次完整拼接才能发现。
    """
    cw, ch = int(canvas_w), int(canvas_h)
    side = max(cw, ch)
    side_scale = 1.0
    if int(out_side or 0) > 0 and side > int(out_side):
        side_scale = float(out_side) / float(side)

    mem_scale = 1.0
    pix_cap = int(max_pix) if max_pix else 90_000_000
    if cw * ch > pix_cap:
        mem_scale = math.sqrt(pix_cap / float(cw * ch))

    capped_by: List[str] = []
    if side_scale < 1.0:
        capped_by.append('user')
    if mem_scale < side_scale:
        capped_by.append('memory')
    return min(side_scale, mem_scale), capped_by


def stitch_fallback(images: Sequence[np.ndarray],
                    edges: Sequence[MatchEvidence],
                    groups: List[List[int]],
                    feather: int = 0,
                    scale_bar_mask: Optional[object] = None,
                    progress=None,
                    max_pix: Optional[int] = None,
                    out_side: int = 0) -> Tuple[np.ndarray, Dict]:
    """在**已接受的边**上做真正的 warp + 多频带融合。

    入参 `images` 与 `edges` 的索引已经是同一坐标系（调用方负责重映射），
    `groups` 是这些索引上的连通分量。

    只拼接最大的连通分量；其余分量作为"独立视野"在 meta 里报告。

    ## `feather`：已废弃，**本函数不使用羽化**

    保留参数只为兼容旧调用方。消缝靠的是
    「内容 `BORDER_REPLICATE`（不给金字塔造人工台阶）+ 多频带融合」，
    不是羽化：羽化会在重叠区平均两张亚像素错位的图 → **细节发糊**。
    实测羽化 100px / 8px 在主台阶消除后都没有额外收益。

    ★ **口径（用户明确要求）**：默认不羽化；若将来确实要启用，
    羽化带必须 **≤ 10px**，以免稀释重叠区细节、把矿物颗粒糊掉。

    ## 输出尺寸的两个约束

    * `out_side` —— **用户选的图幅档位**：画布最长边上限（画布坐标，px）。
      >0 时输出**真实**缩到该尺寸以内，不回落放大。
    * `max_pix` —— **内存安全阀**：画布像素上限，由调用方按可用内存推算。

    两者取更严的一个（`out_scale = min(用户档位, 内存预算)`）。若内存比用户要求
    更紧，会在 meta 的 `capped_by` 里标出 `memory`，让调用方可以告警而不是
    静默降质。
    """
    if not groups:
        raise RuntimeError("没有可用的重叠匹配")

    comp = groups[0]
    if len(comp) < 2:
        raise RuntimeError("未找到任何相互重叠的图像对：这些照片可能拍的不是同一视野")

    local = {g: k for k, g in enumerate(comp)}
    sub_edges_all = [e for e in edges if e.accepted and e.i in local and e.j in local]
    if not sub_edges_all:
        raise RuntimeError("最大重叠组内没有可用的匹配边")

    h, w = images[0].shape[:2]
    # 注意：**不要**在这里写 `sub_images = [images[g] for g in comp]`。
    # 调用方现在传的是「按需读盘」序列，这一步会把 n 张图一次性拉进内存。
    # 下面改为在生成器里逐张取、用完即释放。

    remapped: List[MatchEvidence] = []
    for e in sub_edges_all:
        remapped.append(MatchEvidence(
            i=local[e.i], j=local[e.j], accepted=True, matrix=e.matrix,
            n_inliers=e.n_inliers, shift=e.shift, hull_ratio=e.hull_ratio,
            inlier_ratio=e.inlier_ratio, angle_deg=e.angle_deg, scale=e.scale))

    poses, _g, _r = solve_poses(remapped, len(comp), (h, w))
    if progress:
        # 旋转范围在这里报，而不是让调用方自己再算一遍。
        # `stitcher._run_fallback` 原先在调用本函数**之前**独立跑了一次
        # `solve_poses`，仅为了打印这一行 —— 同样的输入、逐位相同的结果，
        # 91 张时白白多花 ~107s（位姿求解本身就是全流程的第三大热点）。
        _th = np.degrees(poses[:, 0])
        progress(f"→ fallback: 位姿优化完成, 旋转 {_th.min():.2f}° ~ {_th.max():.2f}°")

    # ── 计算全局画布 ──
    corners = []
    for k in range(len(comp)):
        M = _pose_matrix((h, w), poses[k])
        pts = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
        corners.append(cv2.transform(pts, M).reshape(-1, 2))
    allc = np.vstack(corners)
    x0, y0 = np.floor(allc.min(axis=0)).astype(int)
    x1, y1 = np.ceil(allc.max(axis=0)).astype(int)
    CW, CH = int(x1 - x0), int(y1 - y0)

    if progress:
        progress(f"→ fallback: 画布 {CW}x{CH}")

    # ── 输出尺寸：用户档位与内存预算取更严的一个 ──
    # `max_pix` 由调用方按可用内存下传（None 时用历史默认值）；`out_side` 是
    # 用户选的"最长边上限"。两者都是**上限**，故尺度取 min。
    nominal_w, nominal_h = CW, CH
    out_scale, capped_by = plan_out_scale(CW, CH, out_side=out_side,
                                         max_pix=max_pix or 0)

    if out_scale < 1.0:
        CW = max(1, int(round(CW * out_scale)))
        CH = max(1, int(round(CH * out_scale)))
        if progress:
            why = []
            if 'user' in capped_by:
                why.append("用户档位")
            if 'memory' in capped_by:
                why.append("内存预算")
            progress(f"→ fallback: 输出缩至 {CW}x{CH} "
                     f"(×{out_scale:.3f}, 受限自{'/'.join(why)})")

    # 各帧占据区的并集（1 B/画布像素）。用途见融合后的最终裁剪。
    cov = np.zeros((CH, CW), np.uint8)

    # ── 垂直融合窗口（每张图只在「自身上下范围 ± halo」内建金字塔）──
    # 依据见 `roi_halo` / `_multiband_blend_roi`：**只切上下、不切左右**，
    # 既能把每图工作量从整画布降到 ~(图高 + 2·halo) × 画布宽（52MP 画布、
    # 91 张实测约 2.2×），又保持与全画布融合**逐位相同**。
    _lv = _pyramid_levels((CH, CW))
    _hl = roi_halo(_lv)
    _A = 1 << _lv
    _cy = np.stack([c[:, 1] for c in corners])
    win_y = []
    for k in range(len(comp)):
        if not V_CROP_ENABLED:               # A/B 与回退：整画布窗口（= 旧行为）
            win_y.append((0, CH))
            continue
        by0 = int(np.floor((_cy[k].min() - y0) * out_scale))
        by1 = int(np.ceil((_cy[k].max() - y0) * out_scale))
        ry0 = max(0, (by0 - _hl) // _A * _A)
        ry1 = min(CH, by1 + _hl)
        if ry1 <= ry0:                       # 退化保护（正常不会发生）
            ry0, ry1 = 0, CH
        win_y.append((ry0, ry1))

    if progress:
        _cov_h = float(np.mean([(b - a) / CH for a, b in win_y]))
        progress(f"→ fallback: 垂直窗口 {_hl}px halo, 平均占画布高 {_cov_h * 100:.0f}%")

    # ── warp 每张图到画布（流式：不保留 n 张全画布中间图）──
    # 旧实现把每张 warp 结果都攒进列表再交给融合，峰值因此含 4·n·画布 字节：
    # 16 张 50MP 画布 = 3.2GB，且随 n 线性膨胀（91 张必然 OOM）。
    # 改成生成器逐个喂给融合，峰值只与画布大小有关。
    def _warp_stream():
        # cov 在外层 stitch_fallback 作用域，本生成器要就地 OR 累积它，
        # 必须显式 nonlocal；否则 `cov |= ...` 会被判定为局部赋值 -> UnboundLocalError。
        nonlocal cov
        # 掩膜底图按形状复用（P0-3，见模块级 `BASE_REUSE`）。原来每张都
        # `np.ones(shape)*255` 现分配 + 清零，91 张就是 91 次 6MB 的分配抖动。
        # 值恒为 255，且下方**从不就地改它**（比例尺分支走 `astype` 生成新数组
        # 再重绑名字）⇒ 复用逐位安全。
        _base_cache: Dict[tuple, np.ndarray] = {}
        for k in range(len(comp)):
            M = _pose_matrix((h, w), poses[k])
            M4 = np.vstack([M, [0, 0, 1]])
            # 源坐标 -> 缩小后的画布（含整体缩放与画布平移）
            S = np.array([[out_scale, 0, -x0 * out_scale],
                          [0, out_scale, -y0 * out_scale]], np.float64)
            Mw = (np.vstack([S, [0, 0, 1]]) @ M4)[:2]

            src = images[comp[k]]
            if out_scale != 1.0:
                src = cv2.resize(src, None, fx=out_scale, fy=out_scale,
                                 interpolation=cv2.INTER_AREA)
                # ★ 补偿「src 已经被预缩小」这一步。
                # 旧代码把 Mw 直接用在预缩小后的 src 上，等于缩放做了两遍：
                # 线性部分多乘一个 out_scale（内容几何错位），平移量又没缩
                # ——实测档位 2048 时画布 1946 宽，内容只铺到第 1817 列，
                # 右边留 128px 黑带，裁边后长宽比被带歪（0.90 vs 名义 0.95）。
                # 该路径此前从未被触发（内存预算一直大于实际画布），故一直没暴露。
                #
                # 正确做法：把矩阵共轭回去 —— 预缩小是 u = os·x，故
                #   Mw_预缩小坐标 = Mw_源坐标 · diag(1/os, 1/os, 1)
                # 于是线性部分归一（只留旋转），平移量保持 os·(t - 画布原点)。
                Mw = Mw @ np.array([[1.0 / out_scale, 0.0, 0.0],
                                    [0.0, 1.0 / out_scale, 0.0],
                                    [0.0, 0.0, 1.0]], np.float64)

            # 尺度条抑制：内容与权重都要处理。
            # ★ 只压权重是不够的：重建时那一层要做 acc_lap/acc_w 归一化，
            #   权重压到 0.02 会让分母同比例变小，白框的 Laplacian 被**原样复活**
            #   （实测残留在拼图外沿，因为那里只有一张图覆盖、没有邻居来补）。
            #   所以这里先把内容 inpaint 掉，权重再压到 0.02：有邻居时邻居胜出，
            #   没邻居时留下一块平滑填充而不是一个"500 µm"白标签。
            if scale_bar_mask is not None:
                sb = scale_bar_mask(k)
                if sb is not None:
                    if sb.shape[:2] != src.shape[:2]:
                        sb = cv2.resize(sb, (src.shape[1], src.shape[0]),
                                        interpolation=cv2.INTER_NEAREST)
                    m8 = (sb > 0.5).astype(np.uint8)
                    if m8.any():
                        src = cv2.inpaint(src, m8, 3, cv2.INPAINT_TELEA)

            # ★★ 内容必须用 BORDER_REPLICATE，绝不能用黑填充。
            # 黑填充会在每张图的边界造出一个「内容 -> 0」的人工台阶；这个台阶
            # 在 Laplacian 金字塔里是巨大的响应，而权重在边界处还≈1，于是被
            # 原样重建出来。两张真重叠帧（3-4，θ=0.000°，3043 内点）实测：
            #   黑填充 → 缝两侧 阶跃 +45.95 / -40.04 灰度（肉眼即"明显边界"）
            #   REPLICATE → +0.12 / -1.43 灰度（降到内容自身噪声地板）
            # 对比过：羽化 100px、窄羽化 8px、高频取胜、逐帧增益补偿、INTER_LINEAR
            # 在主台阶消除后**都没有额外收益**；羽化反而会在重叠区平均两张
            # 亚像素错位的图 → 细节发糊。所以这里不引入羽化。
            # 权重侧仍用 BORDER_CONSTANT 0：帧外必须是被排除的。
            #
            # ── W1：把**内容** warp 的输出原点搬到窗口上沿（ry0）──
            # 窗口第 y_local 行 == 整画布第 y_local+ry0 行，故正向矩阵只需
            # `M[1,2] -= ry0`（float64 精确减法）。dst 高度从 CH 降到 rh
            # ⇒ 内容 warp 只算窗口那一段，结果与「整画布 warp 再裁」**逐位相同**
            #（`_crit_warp_roi_sweep.py`：1080 M 像素，0 差异）。
            #
            # ★★ 掩膜 warp **不做** ROI 化（下方保持整画布）：
            #   `_crit_mask_sweep.py` 实测 INTER_NEAREST 在 1728 M 掩膜像素上
            #   有 **3421 px** 跳变（全落在掩膜边界；ry0=0 时恒 0）——NEAREST 的
            #   坐标量化比 LANCZOS4 敏感得多。而掩膜 warp 只 ~10.9ms/张
            #   （内容 1037ms，占融合 59%）⇒ 保持整画布几乎不花时间、却零风险。
            ry0, ry1 = win_y[k]
            rh = ry1 - ry0
            if WARP_ROI and 0 < rh < CH:
                Mw_c = Mw.copy()          # Mw 是 2x3 float64，只改 (1,2)
                Mw_c[1, 2] -= ry0
                img = cv2.warpAffine(src, Mw_c, (CW, rh),
                                     flags=cv2.INTER_LANCZOS4,
                                     borderMode=cv2.BORDER_REPLICATE)
            else:
                img = cv2.warpAffine(src, Mw, (CW, CH),
                                     flags=cv2.INTER_LANCZOS4,
                                     borderMode=cv2.BORDER_REPLICATE)[ry0:ry1]
            if BASE_REUSE:
                base = _base_cache.get(src.shape[:2])
                if base is None:
                    base = np.full(src.shape[:2], 255, np.uint8)
                    _base_cache[src.shape[:2]] = base
            else:
                base = np.ones(src.shape[:2], np.uint8) * 255
            if scale_bar_mask is not None:
                sb = scale_bar_mask(k)
                if sb is not None:
                    if sb.shape[:2] != base.shape[:2]:
                        sb = cv2.resize(sb, (base.shape[1], base.shape[0]),
                                        interpolation=cv2.INTER_NEAREST)
                    base = (base.astype(np.float32) * (1.0 - sb * 0.98)).astype(np.uint8)
            # 掩膜**恒整画布**再裁窗口（见上方 ★★）。
            mk = cv2.warpAffine(base, Mw, (CW, CH),
                                flags=cv2.INTER_NEAREST,
                                borderMode=cv2.BORDER_CONSTANT,
                                borderValue=0)[ry0:ry1]
            # 只把「自身上下范围 ± halo」这一段交给融合。
            # P0-3 `COV_WINDOW_OR`：只 OR 窗口切片即可（窗口外 `mk ≡ 0`，见模块级注释）。
            if COV_WINDOW_OR:
                cov[ry0:ry1] |= (mk > 0)
            else:
                cov |= (mk > 0)      # 改前写法：整画布 OR
            yield img, mk, 0, ry0

    # 多频带融合（窗口版；窗口宽度=画布宽度，与全画布版逐位相同）
    if progress:
        progress("→ fallback: 多频带融合")
    result, _n_used = _multiband_blend_roi(_warp_stream(), (CH, CW), _lv)

    # ★ 用「各帧占据区的并集」做最终裁剪。
    # BORDER_REPLICATE 让内容在帧外仍有连续值 —— 这正是消除接缝的关键
    # （金字塔在边界看不到人工台阶），但拼图**外沿**不能让它糊出去：
    # 那里没有别的帧覆盖，只会在四周留一圈浅色雾边，既难看又会让
    # `_trim_black_border` 失效（外沿不再全黑，于是空边裁不掉）。
    # 只按占据区裁，内部重叠区不受影响（那里本就 100% 被覆盖）。
    # 注意判据用 `> 0` 而不是 `> 127`：比例尺区权重被压到 0.02（非 0），
    # 仍算占据，从而显示 inpaint 后的填充而不是挖出一个黑洞。
    if cov.any():
        if MASK_MUL:
            # P0-3 `MASK_MUL`：`cov ∈ {0,1}`、result 为 uint8 ⇒ uint8 乘不溢出，
            # 逐位相同；省掉「全画布 bool（52MB）+ 花式索引随机写」这一趟。
            np.multiply(result, cov[..., None], out=result)
        else:
            result[cov == 0] = 0      # 改前写法
    del cov

    # 只裁「整行/整列全黑」的空边。帧自身的黑角按采集现实保留
    # （用户口径：边角有黑很正常），绝不因覆盖率低就丢掉单图外沿的有效内容。
    result = _trim_black_border(result)

    # ── 不做无效放大（用户口径，见模块级 `UPSCALE_OUTPUT`）──
    # 历史行为：`out_side == 0`（自动模式）且画布被内存预算缩过时，把结果用
    # `INTER_CUBIC` 拉回名义画布尺寸 —— 好处是输出像素网格与源图一致（µm/px 不变），
    # 但**像素是插出来的**。现在一律不放大：成品停在真实可用的分辨率上，
    # 等效像素尺度由 `meta['out_scale']` 如实报出。
    # `out_side > 0`（用户点了图幅档位）本来就不放大（选 8192 就该拿到 ≤8192 的真图），
    # 这条历史行为被保留。
    if UPSCALE_OUTPUT and out_scale != 1.0 and not (int(out_side or 0) > 0):
        fx = 1.0 / out_scale
        result = cv2.resize(result, None, fx=fx, fy=fx, interpolation=cv2.INTER_CUBIC)

    meta = {
        "used_indices": comp,
        "out_scale": out_scale,
        "out_side": int(out_side or 0),
        "canvas": [CW, CH],
        "nominal_canvas": [nominal_w, nominal_h],
        "capped_by": capped_by,
        "poses": [{"theta_deg": round(float(np.degrees(p[0])), 3),
                   "tx": round(float(p[1]), 1), "ty": round(float(p[2]), 1)} for p in poses],
    }
    return result, meta
