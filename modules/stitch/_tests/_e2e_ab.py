# -*- coding: utf-8 -*-
"""真实图像端到端 A/B（固定在 `stitch_fallback` 层）：
垂直窗口融合 vs 整画布融合必须**逐位相同**，并测真实加速比。

为什么不用「整条流水线跑两遍」：SIFT/RANSAC 在多线程下不确定（实测两次同参
数跑出的画布宽度都不同：5429 vs 5430），那样比不出结论。这里先用 spy 抓下
一次真实 `stitch_fallback` 的**完整入参**（图序列 + 接受的边 + 连通分量），
再用同一份入参跑两遍 —— 位姿求解、warp、裁边全都确定性，唯一变量就是窗口。
"""
import os
import sys
import glob
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
# ★ 向上查找模块根（含 `fallback.py` 的那一层）—— 脚本归档到别的目录后仍能跑。
ROOT = HERE
while ROOT and not os.path.exists(os.path.join(ROOT, 'fallback.py')):
    ROOT = os.path.dirname(ROOT)
sys.path.insert(0, ROOT)

import stitcher as S          # noqa: E402
import fallback as fb         # noqa: E402

SRC = os.path.join("d:\\science", "2田坝心钨矿", "镜下", "拼图", "T2-C-origin")

_orig = fb.stitch_fallback
CAP = {}


def _spy(images, edges, groups, **kw):
    CAP['args'] = (images, edges, groups)
    CAP['kw'] = kw
    return _orig(images, edges, groups, **kw)


def capture(files):
    fb.stitch_fallback = _spy
    try:
        st = S.Stitcher(max_side=0, engine="auto")
        st.stitch(files)
    finally:
        fb.stitch_fallback = _orig
    return CAP


def run_ab(crop, tag, muladd=True):
    images, edges, groups = CAP['args']
    kw = dict(CAP['kw'])
    fb.V_CROP_ENABLED = crop
    fb.MULADD_3CH = muladd
    msgs = []

    def pg(m):
        msgs.append((time.perf_counter(), m))

    kw['progress'] = pg
    t0 = time.perf_counter()
    img, meta = _orig(images, edges, groups, **kw)
    dt = time.perf_counter() - t0
    tb = None
    for ts, m in msgs:
        if "多频带融合" in m:
            tb = dt - (ts - t0)
            break
    win = [m for _, m in msgs if "垂直窗口" in m]
    print(f'  [{tag}] V_CROP={crop} MULADD_3CH={muladd}: 输出 {img.shape[1]}x{img.shape[0]}  '
          f'融合耗时 {dt:.1f}s{"" if tb is None else f"（含之前 {dt - tb:.1f}s）"}  '
          f'{win[0] if win else ""}')
    return img, dt


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    files = sorted(glob.glob(os.path.join(SRC, "*.jpg")))[:n]
    print('=' * 78)
    print(f'端到端 A/B（固定入参）：{len(files)} 张 T2-C-origin')
    print('=' * 78)
    cap = capture(files)
    images, edges, groups = cap['args']
    print(f'  捕获入参: {len(images)} 张图, {len(edges)} 条接受边, '
          f'组大小 {[len(g) for g in groups]}')
    print(f'  融合画布预算: max_pix={cap["kw"].get("max_pix")} out_side={cap["kw"].get("out_side")}')

    a, ta = run_ab(True, 'A')
    b, tb = run_ab(True, 'B')
    c, tc = run_ab(False, 'C')
    d, td = run_ab(True, 'D', muladd=False)

    print()
    if a.shape != c.shape or a.shape != d.shape:
        print(f'❌ 形状不同 {a.shape} / {c.shape} / {d.shape} —— 说明入参没固定住')
        return 1
    same_det = np.array_equal(a, b)
    same = np.array_equal(a, c)
    same_mul = np.array_equal(a, d)
    dm = np.abs(a.astype(np.int32) - d.astype(np.int32))
    print(f'确定性自检（A vs B）: {"✅ 逐位相同" if same_det else "❌ 不同"}')
    print(f'窗口 vs 整画布（A vs C，S2）: {"✅ 逐位相同" if same else "❌ 不同"}  '
          f'不同像素={int((np.abs(a.astype(np.int32) - c.astype(np.int32)) > 0).sum())}')
    print(f'3ch vs 广播乘加（A vs D，S4）: {"✅ 逐位相同" if same_mul else "❌ 不同"}  '
          f'不同像素={int((dm > 0).sum())}')
    print(f'融合耗时: 整画布 {tc:.1f}s → 窗口 {ta:.1f}s  加速 {tc / ta:.2f}x')
    print(f'          乘加广播 {td:.1f}s → 3ch {ta:.1f}s  加速 {td / ta:.2f}x')
    return 0 if (same and same_det and same_mul) else 1


if __name__ == '__main__':
    sys.exit(main())
