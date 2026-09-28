# -*- coding: utf-8 -*-
"""拼图模块回归测试（离线，不需要浏览器，不碰线上 Flask）

用法:
    D:\\Python 3.12.4\\python.exe modules/stitch/_tests/regress.py

判据来源：`HANDOVER.md` 第六节「成功标准」+ 实拍数据的真值标定
（ZK203-81.5-T2 的 4-*-A 序列，已知哪几对真重叠）。
"""
import os
import sys
import time
import glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import stitcher as S
import geometry as G

SRC = os.path.join("d:\\science", "2\u7530\u575d\u5fc3\u94a8\u77ff", "\u955c\u4e0b",
                   "ZK203-81.5-T2")

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))


def f(name):
    return os.path.join(SRC, name)


def run(files, engine="auto", **kw):
    st = S.Stitcher(max_side=0, engine=engine, **kw)
    return st, st.stitch(files)


def accepted_pairs(meta):
    return {(m["i"], m["j"]) for m in meta["matches"] if m["accepted"]}


# ══════════════════════════════════════════════════════════════════
print("\n═══ 1. 几何验证层：真重叠必须接受（纯平移，θ 恒为 0）═══")
# ★ 2026-09-23 重新标定（用户纠正：载物台只平移不旋转）
#   T2-C-origin 是**真正连续平移**的序列（#28→#29→#30 闭环残差仅 0.3px），
#   是本模块最可靠的真值来源。ZK203 的 4-1&4-6 已证伪（见 §2）。
SRC_T2C = os.path.join("d:\\science", "2\u7530\u575d\u5fc3\u94a8\u77ff", "\u955c\u4e0b",
                       "\u62fc\u56fe", "T2-C-origin")


def f2c(name):
    return os.path.join(SRC_T2C, name)


def load2(p):
    return S.cv2.imdecode(np.fromfile(p, dtype=np.uint8), S.cv2.IMREAD_COLOR)


_f2c = sorted(glob.glob(os.path.join(SRC_T2C, "*.jpg")))
truth_overlap = [(_f2c[27], _f2c[28]), (_f2c[32], _f2c[33]), (_f2c[45], _f2c[46])]
ver = G.OverlapVerifier(nfeatures=8000)

for k, (pa, pb) in enumerate(truth_overlap):
    ia, ib = load2(pa), load2(pb)
    ev = ver.pair(2 * k, 2 * k + 1, ia, ib, float(min(ia.shape[:2])))
    name = f"{os.path.basename(pa)[-6:]}→{os.path.basename(pb)[-6:]}"
    check(f"接受真重叠 {name}", ev.accepted,
          f"inl={ev.n_inliers} shift={ev.shift:.0f} hull={ev.hull_ratio:.3f} "
          f"θ={ev.angle_deg:.2f}° model={ev.model}")

# 平移优先的硬断言：真重叠必须走平移路径，且解出的旋转恒为 0
_tx = [ver.pair(10 + 2 * k, 11 + 2 * k, load2(pa), load2(pb),
                float(min(load2(pa).shape[:2])))
       for k, (pa, pb) in enumerate(truth_overlap)]
check("真重叠走【平移优先】路径", all(e.model == "translation" for e in _tx),
      f"models={[e.model for e in _tx]}")
check("真重叠解出的旋转为 0", all(abs(e.angle_deg) < 0.01 for e in _tx),
      f"θ={[round(e.angle_deg, 3) for e in _tx]}")

print("\n═══ 2. 几何验证层：非重叠必须剔除（核心 Bug 防线）═══")
# ★ 4-1&4-6 从「真重叠」改判为「非重叠」的证据链（2026-09-23）：
#   ① 它只能解出 19.2° 旋转 —— 载物台平移不可能产生旋转，是模型在迁就错配；
#   ② 穷举 ±1800px 全部平移，最小 MAD=17.9 vs 随机基线 22.8，仅 1.27x
#      （真重叠对照组 1.68x）—— 几乎与「两张无关的图」无区别；
#   ③ 该组 7 张两两之间**从未**解出过一致的平移关系，连通分量恒为孤立点。
#   旧版把它当"真重叠"，是因为旋转自由度掩盖了错配。
truth_fake = [("4-1-A.jpg", "4-6-A.jpg"),          # ← 已证伪，不再是真值
              ("4-1-A.jpg", "4-3-A.jpg"), ("4-3-A.jpg", "4-6-A.jpg"),
              ("4-1-A.jpg", "4-4-A.jpg"), ("4-5-A.jpg", "4-7-A.jpg"),
              ("1-0-A.jpg", "1-0-B.jpg")]
ver2 = G.OverlapVerifier(nfeatures=8000)
for k, (a, b) in enumerate(truth_fake):
    ia = S.cv2.imdecode(np.fromfile(f(a), dtype=np.uint8), S.cv2.IMREAD_COLOR)
    ib = S.cv2.imdecode(np.fromfile(f(b), dtype=np.uint8), S.cv2.IMREAD_COLOR)
    ev = ver2.pair(2 * k, 2 * k + 1, ia, ib, float(min(ia.shape[:2])))
    check(f"剔除非重叠 {a}-{b}", not ev.accepted,
          f"inl={ev.n_inliers} shift={ev.shift:.1f} → {ev.reason}")

print("\n═══ 3. 非重叠输入必须明确报错，而不是输出糊图 ═══")
try:
    run([f(x) for x in ("1-0-A.jpg", "1-0-B.jpg", "1-0-C.jpg")])
    check("非重叠组拒绝拼接", False, "居然成功了 —— 会输出糊图")
except RuntimeError as e:
    ok = "不是同一视野" in str(e) and "剔除原因" in str(e)
    check("非重叠组拒绝拼接", ok, f"({len(str(e))} 字说明)")

print("\n═══ 4. 真重叠组：两个引擎都能拼出合理尺寸 ═══")
for engine in ("openstitching", "fallback"):
    try:
        t0 = time.time()
        st, (img, meta) = run([f("4-6-A.jpg"), f("4-7-A.jpg")], engine=engine)
        pw, ph = img.shape[1], img.shape[0]
        # 两张 2752x2208 若完全重叠 = 1 张面积；真重叠应 >1.5 张
        ratio = (pw * ph) / (2752.0 * 2208.0)
        check(f"{engine}: 尺寸合理", 1.3 < ratio < 4.0,
              f"{pw}x{ph} = {ratio:.2f} 张  ({time.time()-t0:.1f}s)")
        check(f"{engine}: 不是整幅糊图", img.std() > 8.0,
              f"std={img.std():.1f}")
    except Exception as e:
        check(f"{engine}: 尺寸合理", False, f"{type(e).__name__}: {e}")

print("\n═══ 5. ★ 平移优先：旋转自由度必须被钉死（本轮核心修正）═══")
# 领域约束：显微拍照是载物台**平移**取图，载物台不旋转、不缩放。
# 故 ① 真重叠必须走纯平移路径；② 解出大角度的匹配必须被判错。
_ok_model, _ok_angle, _bad_rot = [], [], []
for k, (pa, pb) in enumerate(truth_overlap[:2]):
    ev = ver.pair(30 + 2 * k, 31 + 2 * k, load2(pa), load2(pb),
                  float(min(load2(pa).shape[:2])))
    _ok_model.append(ev.model)
    _ok_angle.append(abs(ev.angle_deg))
check("真重叠：估计路径为纯平移", all(m == "translation" for m in _ok_model),
      f"models={_ok_model}")
check("真重叠：旋转恒为 0°", all(a < 0.01 for a in _ok_angle),
      f"|θ|={[round(a, 4) for a in _ok_angle]}")

# 4-1&4-6 只能解出 19.2° 旋转 —— 必须被「旋转异常」判据拦下，
# 而不是像旧版那样把旋转当真、再绕开尺度判据放行。
_ev46 = ver.pair(40, 41, S.cv2.imdecode(np.fromfile(f("4-1-A.jpg"), dtype=np.uint8),
                                        S.cv2.IMREAD_COLOR),
                 S.cv2.imdecode(np.fromfile(f("4-6-A.jpg"), dtype=np.uint8),
                                S.cv2.IMREAD_COLOR), 2208.0)
check("解出大旋转的组合被剔除", not _ev46.accepted,
      f"θ={_ev46.angle_deg:.2f}° → {_ev46.reason}")
check("剔除理由指明旋转异常", "旋转异常" in _ev46.reason,
      f"{_ev46.reason}")
check("旋转判据阈值存在且为小角", 0 < G.FALLBACK_ANGLE_DEG <= 10.0,
      f"FALLBACK_ANGLE_DEG={G.FALLBACK_ANGLE_DEG}")

print("\n═══ 6. 混合输入：只拼主组，孤立图排除并告警 ═══")
files7 = sorted(glob.glob(os.path.join(SRC, "4-*-A.jpg")))
st, (img, meta) = run(files7)
check("多组被拆分报告", len(meta["groups"]) >= 2,
      f"groups={meta['groups']}")
check("排除项有告警", any("互不重叠" in w or "无重叠" in w for w in meta["warnings"]),
      f"{meta['warnings']}")

# ★ 2026-09-23：4-*-A 经复核**只有 4-6&4-7 一对真重叠**（其余为孤立图），
#   故这里不再断言「主组 >=3 张」。该组不再是拼接成功与否的判据。
st2, (img2, meta2) = run([_f2c[27], _f2c[28], _f2c[29], _f2c[30]], engine="fallback")
check("T2-C 连续四帧：单组连通", len(meta2["groups"]) == 1
      and len(meta2["groups"][0]) == 4, f"groups={meta2['groups']}")
check("T2-C 连续四帧：输出不是整幅黑图",
      np.count_nonzero(img2) > img2.size * 0.02,
      f"非零像素 {100*np.count_nonzero(img2)/img2.size:.1f}%")

print("\n═══ 7. 内存与裁剪修正（2026-09-23）═══")
import inspect
import fallback as FB

# ① 裁剪必须与图片数 n 无关，且只裁「整行/整列全黑」的空边。
#    旧规则 `cov >= max(1.0, n*0.30)` 的阈值随 n 线性增长，而画布平均覆盖率只有
#    O(1) —— T2-C 8 张实测丢掉了内容包围盒的 48.5%，而原始帧 99.9% 是非黑像素。
_arr = np.zeros((40, 50, 3), np.uint8)
_arr[10:30, 5:45] = 128
_arr[12:14, 8:10] = 0                       # 内部黑洞，不得被当作「空边」裁掉
_out = FB._trim_black_border(_arr)
check("裁剪只裁整行整列全黑（内部黑洞保留）",
      _out.shape[0] == 36 and _out.shape[1] == 50,
      f"{_arr.shape[:2]} → {_out.shape[:2]}  (期望 (36, 50))")
check("裁剪不引入 n 相关阈值",
      "len(masks) * 0.30" not in inspect.getsource(FB.stitch_fallback))

# ② 融合必须只保留一份画布级缓冲，且支持迭代器输入（不再要求调用方先攒成列表）
_fbsrc = inspect.getsource(FB.stitch_fallback)
check("融合按需取图（不再一次性展开主组）", "images[comp[k]]" in _fbsrc)
_imgs = [np.zeros((8, 8, 3), np.uint8) for _ in range(2)]
_mks = [np.full((8, 8), 255, np.uint8) for _ in range(2)]
_gen = ((im, mk) for im, mk in zip(_imgs, _mks))
_r = FB.multiband_blend(_gen, None, (8, 8))
check("multiband_blend 支持迭代器输入",
      isinstance(_r, np.ndarray) and _r.shape == (8, 8, 3), f"{_r.shape}")

# ③ 画布上限必须由可用内存推算，而不是写死的魔数
_B_FREE = S.canvas_pixel_budget(91, 2752 * 2208, holding_frames=False)
_B_HOLD = S.canvas_pixel_budget(91, 2752 * 2208, holding_frames=True)
check("画布预算存在且不超过绝对上限",
      4_000_000 <= _B_FREE <= S.StitchConfig.max_canvas_pixels,
      f"{_B_FREE/1e6:.0f}MP (上限 {S.StitchConfig.max_canvas_pixels/1e6:.0f}MP)")
check("画布预算随「持有源图」下降（按需读盘能换到更大画布）",
      _B_HOLD < _B_FREE, f"持有 {_B_HOLD/1e6:.0f}MP vs 按需 {_B_FREE/1e6:.0f}MP")

# ④ 融合阶段改为按需读盘
_li = S._LazyImages([_f2c[27]], 0)
_lim = _li[0]
check("_LazyImages 按需读图且尺寸正确",
      _lim.shape[:2] == (2208, 2752), f"{_lim.shape}")
del _lim

# ⑤ 裁剪修正后：画布里的内容不得再被丢弃（out_scale==1 时输出应≈画布）
_em2 = meta2["engine_meta"]
_cv = _em2["canvas"]
if _em2["out_scale"] == 1.0:
    _keep = (meta2["output_size"][0] * meta2["output_size"][1]) / float(_cv[0] * _cv[1])
    check("裁剪不再丢内容（保留 >=99% 画布）", _keep >= 0.99,
          f"输出 {meta2['output_size']} vs 画布 {_cv} = {_keep:.3f}")
else:
    check("裁剪不再丢内容（保留 >=99% 画布）", True,
          f"out_scale={_em2['out_scale']:.3f}，跳过该断言")

print("\n═══ 8. 内存安全检查保留（HANDOVER Phase 3）═══")
_src = inspect.getsource(S.Stitcher._check_memory)
check("保留 MemoryError 明确报错", "MemoryError" in _src and "内存不足" in _src)

print("\n═══ 9. 元数据结构稳定（前端依赖）═══")
need = {"method", "matches", "groups", "used_indices", "warnings", "output_size"}
check("meta 字段齐全", need <= set(meta.keys()),
      f"缺 {need - set(meta.keys())}" if not need <= set(meta.keys()) else "")
check("meta.matches 带 model 字段", all("model" in m for m in meta["matches"]),
      f"示例={meta['matches'][0] if meta['matches'] else None}")

print("\n═══ 10. 输出图幅档位（全景最长边上限）═══")
import fallback as FB10

check("StitchConfig 暴露图幅档位表",
      hasattr(S.StitchConfig, "out_side_tiers")
      and {0, 16384, 8192, 4096} <= set(S.StitchConfig.out_side_tiers),
      f"{getattr(S.StitchConfig, 'out_side_tiers', None)}")

# 档位细分后容易写错：必须严格递减、都落在后端夹紧区间 [1024, 65536]，
# 且相邻档位比 ≤ 1.4（否则"细分"等于白加，跨度比原来还疏）。
_tiers = [t for t in S.StitchConfig.out_side_tiers if t]
check("档位表严格递减且都在后端夹紧区间内",
      _tiers == sorted(_tiers, reverse=True) and all(1024 <= t <= 65536 for t in _tiers),
      f"{_tiers}")
_ratios = [a / b for a, b in zip(_tiers, _tiers[1:])]
check("相邻档位跨度 ≤ 1.4 倍（确实细分了）", max(_ratios) <= 1.4,
      f"max={max(_ratios):.3f} min={min(_ratios):.3f}")
check("前后端档位区间一致（routes 夹紧 1024~65536）",
      min(_tiers) == 1024 and max(_tiers) == 16384,
      f"min={min(_tiers)} max={max(_tiers)}")

# ① 尺度决策是纯函数，单独测（跑一次完整拼接才发现"档位没生效"太慢）
_s, _why = FB10.plan_out_scale(10000, 5000, out_side=2048)
check("档位小于画布 → 按档位缩", abs(_s - 0.2048) < 1e-6 and _why == ["user"],
      f"scale={_s:.4f} why={_why}")
_s, _why = FB10.plan_out_scale(10000, 5000, out_side=16384)
check("档位大于画布 → 不缩", _s == 1.0 and _why == [], f"scale={_s} why={_why}")
_s, _why = FB10.plan_out_scale(10000, 5000, out_side=0, max_pix=10_000_000)
check("未选档位 → 只受内存约束", abs(_s - (10_000_000 / 50_000_000) ** 0.5) < 1e-6
      and _why == ["memory"], f"scale={_s:.4f} why={_why}")
_s, _why = FB10.plan_out_scale(10000, 5000, out_side=2048, max_pix=1_000_000)
check("档位比内存还大 → 取更严的内存，且两条原因都记下",
      abs(_s - (1_000_000 / 50_000_000) ** 0.5) < 1e-6 and _why == ["user", "memory"],
      f"scale={_s:.4f} why={_why}")
_s, _why = FB10.plan_out_scale(100, 100, out_side=65536, max_pix=0)
check("极小画布不受 90MP 默认上限影响", _s == 1.0 and _why == [], f"scale={_s}")

# ⑤ 画布预算的契约：前端「推荐」档位靠 health 的 canvas_budget_mp 收口，
#    所以这个值必须真的等于「可用内存 − 预留」除以实测的 65B/px（取 75 含余量）。
#    哪天有人改了 BLEND_BYTES_PER_PIX 或 MEM_RESERVE_BYTES 而没同步口径，这里会红。
import psutil as _ps
_avail = _ps.virtual_memory().available
_b1 = S.canvas_pixel_budget(1, 0, holding_frames=False)
_expect = int((_avail - S.MEM_RESERVE_BYTES) / S.BLEND_BYTES_PER_PIX)
_expect = max(4_000_000, min(_expect, S.StitchConfig.max_canvas_pixels))
check("canvas_pixel_budget(按需读盘) 与解析式一致（health 依赖它）",
      abs(_b1 - _expect) < 2_000_000, f"{_b1/1e6:.1f}MP vs {_expect/1e6:.1f}MP")
check("画布预算不超过绝对上限", _b1 <= S.StitchConfig.max_canvas_pixels,
      f"{_b1/1e6:.0f}MP ≤ {S.StitchConfig.max_canvas_pixels/1e6:.0f}MP")
check("画布预算至少 4MP（极端内存下也出得来图）", _b1 >= 4_000_000, f"{_b1/1e6:.1f}MP")

# ② 端到端：真跑一次档位=2048（比全分辨率更快，因为融合本身就在缩小后的画布上做）
_nom_side = max(meta2["engine_meta"]["nominal_canvas"])
st10, (img10, meta10) = run([_f2c[27], _f2c[28], _f2c[29], _f2c[30]],
                            engine="fallback", out_side=2048)
_w10, _h10 = meta10["output_size"]
check("指定档位后输出最长边不超档位", max(_w10, _h10) <= 2048, f"{_w10}×{_h10} ≤ 2048")
check("指定档位 → 真缩小，不放大回名义尺寸",
      abs(meta10["output_scale"] - 2048.0 / _nom_side) < 0.01,
      f"scale={meta10['output_scale']} 期望≈{2048.0 / _nom_side:.4f}")
check("指定档位 → 长宽比与名义画布一致",
      abs((_w10 / _h10) - (meta2["engine_meta"]["nominal_canvas"][0]
                           / meta2["engine_meta"]["nominal_canvas"][1])) < 0.02,
      f"{_w10/_h10:.4f}")
# ★ 回归护栏：旧代码在 out_scale<1 时把缩放做了两遍（src 预缩 + 仿射矩阵里再缩），
#   内容只占画布的 out_scale 倍，裁边后输出骤减、长宽比也歪。
#   该路径此前从未被触发（内存预算远大于实际画布），本用例是它唯一的守卫。
_c10 = meta10["engine_meta"]["canvas"]
_keep10 = (_w10 * _h10) / float(_c10[0] * _c10[1])
check("档位缩小时内容仍铺满画布（缩放只做一次）", _keep10 >= 0.95,
      f"输出 {_w10}×{_h10} / 画布 {_c10} = {_keep10:.3f}")
check("meta 如实报出图幅档位与缩放",
      meta10["output_side_cap"] == 2048 and "out_scale" in meta10["engine_meta"],
      f"cap={meta10['output_side_cap']} scale={meta10['output_scale']}")
check("档位缩小的输出仍然有效（非整幅黑图）",
      float((img10.max(axis=2) > 8).mean()) > 0.5,
      f"非黑 {float((img10.max(axis=2) > 8).mean())*100:.1f}%")
check("输出图幅字段进入 meta（前端依赖）",
      {"output_size", "output_side_cap", "output_scale"} <= set(meta10.keys()),
      f"缺 {({'output_size','output_side_cap','output_scale'} - set(meta10.keys()))}")
del img10

# ③ 未选档位（自动）时行为不变
check("自动档位下 out_scale 与画布一致（未引入回归）",
      abs(meta2["engine_meta"]["out_scale"] - 1.0) < 1e-9
      or meta2["output_scale"] == meta2["engine_meta"]["out_scale"],
      f"out_scale={meta2['engine_meta']['out_scale']} "
      f"meta={meta2['output_scale']}")

# ══════════════════════════════════════════════════════════════════
print("\n═══ 10b. 成品只向下采、不向上采（用户口径 2026-09-24「不做无效放大」）═══")
check("默认禁止成品向上采样", FB.UPSCALE_OUTPUT is False,
      f"UPSCALE_OUTPUT={FB.UPSCALE_OUTPUT}")
check("默认用连续 3 通道权重做乘加", FB.MULADD_3CH is True,
      f"MULADD_3CH={FB.MULADD_3CH}")
check("垂直窗口默认开启", FB.V_CROP_ENABLED is True, f"V_CROP_ENABLED={FB.V_CROP_ENABLED}")

# 直接打 `stitch_fallback`：用 `max_pix` 逼出 out_scale<1 的画布，再断言输出停在
# **画布**尺度上，而不是被 INTER_CUBIC 拉回名义尺寸（旧行为）。
_imgs = [np.full((600, 800, 3), 60, np.uint8), np.full((600, 800, 3), 130, np.uint8)]
_M = np.array([[1.0, 0.0, 300.0], [0.0, 1.0, 200.0]], np.float64)
_edges = [G.MatchEvidence(i=0, j=1, accepted=True, matrix=_M, n_inliers=500,
                          shift=(300.0, 200.0), hull_ratio=0.9, inlier_ratio=0.5,
                          angle_deg=0.0, scale=1.0)]
_r, _m = FB.stitch_fallback(_imgs, _edges, [[0, 1]], max_pix=200_000, out_side=0)
check("内存缩过画布时输出停在画布尺度（不拉回名义尺寸）",
      max(_r.shape[:2]) <= max(_m["canvas"]) + 2
      and max(_r.shape[:2]) < max(_m["nominal_canvas"]) * 0.9,
      f"输出 {_r.shape[1]}×{_r.shape[0]}, 画布 {_m['canvas']}, "
      f"名义 {_m['nominal_canvas']}, out_scale={_m['out_scale']:.3f}")
del _r, _imgs

# 源图在匹配阶段被 `max_side` 预缩时，同样不上采样回原分辨率。
_img16, _meta16 = S.Stitcher(max_side=1200, engine="fallback").stitch(
    [_f2c[27], _f2c[28], _f2c[29], _f2c[30]])
_ws16, _hs16 = _meta16["output_size"]
_nom16 = max(_meta16["engine_meta"]["nominal_canvas"])
check("源图预缩时输出不上采样（不超预缩画布的名义尺寸）",
      max(_ws16, _hs16) <= _nom16 + 2,
      f"输出 {_ws16}×{_hs16} ≤ 预缩名义画布 {_nom16}"
      f"（旧行为会 ×1/s0 拉回原分辨率）")
del _img16

# 乘加两种实现必须**逐位相同** —— 换的是实现，不是算子。这条锁死 MULADD_3CH 的安全性。
_rng = np.random.RandomState(17)
_ch, _cw, _lv = 320, 384, 3
_items = []
for _i in range(3):
    _c = _rng.randint(0, 256, (_ch, _cw, 3), np.uint8)
    _k = np.zeros((_ch, _cw), np.uint8)
    _k[20 + 60 * _i:260, 30 + 90 * _i:280] = 255
    _items.append((_c, _k))
_ref = FB.multiband_blend(_items, None, (_ch, _cw), levels=_lv)
FB.MULADD_3CH = False
_old = FB.multiband_blend(_items, None, (_ch, _cw), levels=_lv)
FB.MULADD_3CH = True
check("乘加（3ch 连续 vs stride-0 广播）逐位相同", np.array_equal(_ref, _old),
      f"不同像素 {int((_ref != _old).any(axis=2).sum())}")

# 垂直窗口版在「窗口=整画布」时也必须与整画布版逐位相同（V_CROP 的安全性）
_st = [(np.ascontiguousarray(c), np.ascontiguousarray(k), 0, 0) for c, k in _items]
_roi1, _ = FB._multiband_blend_roi(_st, (_ch, _cw), _lv)
check("窗口版（窗口=整画布）与整画布版逐位相同", np.array_equal(_ref, _roi1),
      f"不同像素 {int((_ref != _roi1).any(axis=2).sum())}")
del _ref, _old, _roi1, _items, _st

# ── 10c. W1（2026-09-28）：内容 warp 输出原点 ROI 化 ──
# 内容 `warpAffine`(LANCZOS4) 占融合 ~59%；垂直窗口平均只用到画布高的 ~43%
# ⇒ 只算窗口那一段。等价性靠 `M[1,2] -= ry0`（**正向**变换的输出原点搬运）。
# ⚠️ 掩膜（INTER_NEAREST）**不可**同样处理（实测非逐位），故只锁内容 warp。
check("内容 warp ROI 化默认开启", FB.WARP_ROI is True, f"WARP_ROI={FB.WARP_ROI}")

# 构造「窗口高 < 画布高」的场景（两图沿 y 拉开 900px），确保真的走到 ROI 分支。
_w1imgs = [np.full((400, 600, 3), 40, np.uint8),
           np.full((400, 600, 3), 200, np.uint8)]
_w1M = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 900.0]], np.float64)
_w1edges = [G.MatchEvidence(i=0, j=1, accepted=True, matrix=_w1M, n_inliers=500,
                            shift=(0.0, 900.0), hull_ratio=0.9, inlier_ratio=0.5,
                            angle_deg=0.0, scale=1.0)]
_w1h = []
_cv = FB.cv2                      # regress 未直接 import cv2；fallback 已 import，同一模块对象
_orig_wa = _cv.warpAffine


def _wa_spy(src, M, dsize, *a, **kw):
    if kw.get('flags') == _cv.INTER_LANCZOS4:
        _w1h.append(int(dsize[1]))
    return _orig_wa(src, M, dsize, *a, **kw)


FB.WARP_ROI = True
_cv.warpAffine = _wa_spy
try:
    _ra, _ma = FB.stitch_fallback(_w1imgs, _w1edges, [[0, 1]], max_pix=0, out_side=0)
finally:
    _cv.warpAffine = _orig_wa
FB.WARP_ROI = False
_rb, _mb = FB.stitch_fallback(_w1imgs, _w1edges, [[0, 1]], max_pix=0, out_side=0)
FB.WARP_ROI = True
check("ROI 分支真的被走到（存在 dst 高 < 画布高的内容 warp）",
      len(_w1h) >= 1 and min(_w1h) < max(_w1h),
      f"内容 warp 的 dst 高度集 = {sorted(set(_w1h))}")
check("内容 warp ROI 化 与 整画布 warp 逐位相同",
      _ra.shape == _rb.shape and np.array_equal(_ra, _rb),
      f"{_ra.shape} vs {_rb.shape}，不同像素 "
      + (str(int((_ra != _rb).any(axis=2).sum())) if _ra.shape == _rb.shape else "形状不同"))
del _ra, _rb, _w1imgs, _w1edges, _w1h

# ══════════════════════════════════════════════════════════════════
print("\n═══ 11. 羽化口径（默认 0 = 不羽化）═══")
import dis as _dis
import re as _re

# 口径（用户明确要求 2026-09-27）：**默认不羽化**。羽化是在重叠区把两张
# 亚像素错位的图做加权平均 ⇒ 细节必然发糊，显微图丢矿物颗粒感。
# 消缝已由「内容 BORDER_REPLICATE + 多频带融合」完成，不需要羽化。
# 三处默认值必须同时是 0，否则前端滑杆会把"看似已关"变成"其实开着"。
check("StitchConfig.feather 默认 0",
      int(getattr(S.StitchConfig, "feather", -1)) == 0,
      f"{getattr(S.StitchConfig, 'feather', None)}")

_fp = inspect.signature(FB.stitch_fallback).parameters.get("feather")
check("stitch_fallback(feather=0) 签名默认 0",
      _fp is not None and _fp.default == 0,
      f"{_fp.default if _fp else '缺少该参数'}")

# 当前实现里 feather 是**纯兼容参数**（函数体内一次都没读）。哪天有人把它接进
# 融合，这条会翻红 —— 提醒必须同时补「≤10px 夹紧」与「糊图 A/B 对比」。
# （不要把这条提醒写成 extra：`check` 的 extra 在 PASS 时也会打印，会被误读成警告。）
_fused = any(i.opname == "LOAD_FAST" and i.argval == "feather"
             for i in _dis.get_instructions(FB.stitch_fallback))
check("feather 当前未参与融合（纯兼容参数）", not _fused)

# routes 的夹紧区间必须与前端滑杆量程一致：否则滑杆拖到 800 会被静默夹成 10，
# 用户以为"设了 800"，实际是 10 —— 这种"看不出来"的错配最难发现。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_rsrc = open(os.path.join(_ROOT, "routes.py"), encoding="utf-8", errors="replace").read()
_mr = _re.search(r"feather\s*=\s*_int\(\s*'feather'\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)",
                 _rsrc)
_rt = _mr.groups() if _mr else None
check("routes 夹紧 feather 到 [0,10] 且默认 0", _rt == ("0", "0", "10"),
      f"{_rt}  （匹配到: {_mr.group(0).strip() if _mr else '无'}）")

_hsrc = open(os.path.join(_ROOT, "assets", "index.html"),
             encoding="utf-8", errors="replace").read()
_mi = _re.search(r'<input[^>]*id="feather"[^>]*>', _hsrc)
_tag = _mi.group(0) if _mi else ""
_mv = _re.search(r'value="(\d+)"', _tag)
_mx = _re.search(r'max="(\d+)"', _tag)
check("前端滑杆默认值 = 0", bool(_mv) and _mv.group(1) == "0",
      _tag if _tag else "未找到 #feather 控件")
check("前端滑杆量程上限 ≤10px（与后端口径一致）",
      bool(_mx) and int(_mx.group(1)) <= 10,
      f"max={_mx.group(1) if _mx else '未找到'}")

# ══════════════════════════════════════════════════════════════════
print("\n" + "═" * 62)
print(f"结果: {len(PASS)} 通过 / {len(FAIL)} 失败")
if FAIL:
    for x in FAIL:
        print(f"  ✗ {x}")
    sys.exit(1)
print("全部通过 ✓")
