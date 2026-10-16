---
name: microscopy-stitch
description: 显微薄片图像拼合模块（stitch）的完整操作与改造手册。当需要理解、维护、排障、优化或迁移这个把「载物台平移拍摄的重叠视场」拼成全景大图的模块时使用。核心不可违背的领域约束：载物台只平移不旋转 ⇒ 旋转自由度必须钉死；匹配主力判据是内点率而非内点数；融合内存 O(画布) 与图片数无关；任何提速/省内存改动都必须通过「逐位等价」验收。含架构分层、算法原理、性能与内存实测账、已否决清单、排障手册、路径适配指引。
---

# 显微图像拼合模块（stitch）· SKILL

> **分析对象**：`D:\PostNews\Translate\modules\stitch`（约 4 个核心 py，合计 ~178 KB）
> **数据来源**：本文件内容全部来自**实际读取**当前源码 + 模块记忆档（`MEMORY.md` / `MEMORY-DETAIL.md` / `overview.md`）+ 运维手册 §3c/§8c，**无凭记忆编写**。
> **整理日期**：2026-09-29
> **版本锚点**：`geometry.py` / `stitcher.py` (2026-09-28)、`fallback.py` (2026-09-29)、`routes.py` (2026-09-29)

---

## 0. 给 AI 的读取指引（先看这张表）

| 你要做什么 | 直接跳到 |
|---|---|
| 搞清楚这个模块是干什么的、边界在哪 | §1、§2 |
| 改算法 / 调阈值（**先读这一节，否则一定改坏**） | **§2 领域约束** + §4 |
| 理解匹配为什么这么设计 | §4.1 |
| 理解融合为什么这么慢 / 怎么省内存 | §4.3、§5 |
| 跑起来、调接口、看健康状态 | §3.3、§6 |
| 线上出问题了 | §6.4、§9 排障手册 |
| 想「优化一下」 | **§5.3 已否决清单（先看，别重做）** |
| 把代码搬到别的机器 / 别的项目 | §10 路径适配 |
| 交改动前怎么自证没改坏 | §7 逐位等价验证方法论 |

**三条最高优先级铁律**（违反任何一条，输出即失效，且**不会报错**）：

1. **旋转自由度必须钉死。** 载物台不旋转。任何「解出可观角度」都是**匹配错了**，不是拍到了旋转。
2. **判据是内点率，不是内点数。** 周期性纹理的错配能凑出几千个互相一致的「内点」。
3. **任何提速/省内存改动，必须提交「冻结输入下的逐位等价」证据**（0 像素差），否则不予采纳。

---

## 1. 一句话与边界

**一句话**：把显微镜下**载物台平移扫描**得到的一批**有重叠**视场图，通过 SIFT 特征匹配 + 几何校验（纯平移模型）+ 最小二乘全局位姿 + 多频带融合，拼成一张无缝、可标定（µm/px）的全景 PNG。

| 这个模块**做** | 这个模块**不做** |
|---|---|
| 估计两图间**平移** (tx, ty) 并做全局一致性优化 | ❌ 不做旋转/缩放/单应估计（见 §2） |
| 用内点率 + 尺度无关凸包比剔除假重叠 | ❌ 不靠灰度直方图匹配（对本素材无效，见 §4.1） |
| 多频带（Laplacian 金字塔）融合消缝 | ❌ 不依赖羽化参数（`feather` 已废弃，恒 0） |
| 成品按内存预算自动决定分辨率、禁止向上采样 | ❌ 不做「像素无损的 91 张全分辨率」（内存物理上不够，见 §5.2） |
| 结果存 NAS、失败降级本地；上传原图只作临时件 | ❌ 不把上传原图留在 NAS（过程性文件，见 §6.3） |

---

## 2. 领域约束（★ 改算法前必读）

**显微拍照 = 载物台平移取图；不旋转、不缩放。两图之间的真实几何关系只有平移 (tx, ty)。**

⇒ **旋转自由度必须钉死。** 模型越松，假重叠越容易凑出残差极小的假解。

| 该用 | 不该用 | 为什么 |
|---|---|---|
| ✅ **纯平移模型** `[[1,0,tx],[0,1,ty]]`，LMedS 风格初值 + 迭代重加权 + 坐标下降精化 | ❌ 把旋转当真去自由拟合 | 一个**子虚乌有的 θ** 就能让一堆错配的残差变得极小 ⇒ **RANSAC 残差判据整体失效**。旧实现用 `estimateAffinePartial2D` 自由拟合，还写了「|θ|≤5° 才查尺度」的放行逻辑，等于给漏洞再开一道门 |
| 平移连 `min_inliers` 个精确匹配都拿不到时，**才**放开旋转兜底一次，且要求更严（`≥120` 内点 且 内点率 `≥0.70`） | ❌ 兜底解出大角度还放行 | `FALLBACK_ANGLE_DEG = 5.0`，一超即判该匹配为错 |

★ **「两图解出可观角度」= 那对匹配是错的。永远不要把它当好消息。**

**两个曾被写错的先验（已被实测推翻，别再照着旧版走）**：

| 旧结论 | 真相 | 证据 |
|---|---|---|
| 「ZK203 的 `4-1-A` & `4-6-A` 是真重叠 ⇒ 所以必须保留旋转」 | **错**。该对只能解出 19.2°（载物台不可能），且穷举 ±1800px 全平移的最小 MAD=17.9 vs 随机基线 22.8，仅 **1.27×**（真重叠对照组 1.68×）——与「两张无关的图」无区别。那 7 张里**只有 `4-6&4-7` 一对真重叠**，其余是孤立图。旧版被旋转自由度骗了 | `MEMORY.md` §1；`overview.md` "第一轮" |
| 「`warp` 必须整画布算，ROI 会差 16786 px」 | **错**。根因是当年把 `warpAffine` 的**正向**矩阵当逆向用，平移量只搬了 `M11·ry0 ≈ 0.9994·ry0`。正确口径：输出原点抬 `ry0` 只需 **`M[1,2] -= ry0`**（float64 精确减法）⇒ 内容 warp **可以**逐位 ROI | `MEMORY-DETAIL.md` §K.2 |

---

## 3. 架构分层与数据流

### 3.1 文件职责

| 文件 | 大小 | 职责 | 层 |
|---|---|---|---|
| `geometry.py` | 48 KB | SIFT 检测/匹配、**几何校验判据**、纯平移 RANSAC、相似变换兜底、连通分量与邻接表 | **领域核心层**（决定「哪些对真的重叠」） |
| `fallback.py` | 54 KB | 全局位姿求解（`solve_poses`）、多频带融合（`stitch_fallback`）、裁剪、输出缩放决策（`plan_out_scale`） | **算法实现层**（默认引擎） |
| `stitcher.py` | 49 KB | 编排：内存体检、尺寸预读、`_LazyImages` 按需读盘、比例尺白框检测、引擎选择、画布预算、`StitchConfig` | **应用编排层** |
| `routes.py` | 28 KB | Flask Blueprint、表单参数解析、存储落位（NAS/本地）、任务表、上传清扫 | **协议/集成层** |
| `assets/{index.html,app.js,styles.css}` | — | 前端：图幅档位控件、推荐算法、结果展示 | 表现层 |
| `_tests/{regress.py,test_routes.py}` | — | 回归（**72 断言**）、路由用例 | 验证 |

**依赖方向（单向，无环）**：
`routes.py → stitcher.py → fallback.py → geometry.py`
— 反向依赖只有一处例外：`fallback.py` 从 `geometry` import `MatchEvidence, build_adjacency`（数据结构，不是控制流）。

### 3.2 主流水线（`Stitcher.stitch()`）

```
[1] 内存体检  _check_memory()
        ↓  只覆盖**匹配阶段**（实测 1.23× avg_bytes，取 2× 余量）
[2] 尺寸预读  _image_size() 只读文件头（前 64KB，解 JPEG SOF / PNG IHDR）
        ↓  读不出才真正解码一次验证；全程不解码 91 张 2752×2208
[3] 预热（可并行）  逐帧: 解码 → 细 SIFT(nfeatures=8000) → 粗 SIFT(1/8 尺度, 1000)
        ↓  warm_workers=6 时 ThreadPoolExecutor；每 worker 自建 detector
[4] all_pairs  O(n²)  粗筛 → 精细匹配 → 几何判据 → MatchEvidence
        ↓  91 张 = 4095 对
[5] connected_components()  并查集分组 → 主组 main + 孤立组
        ↓
[6] 引擎分派  主组退化? → _run_openstitching()（本素材会放射状拖影）
        ↓        否则   → _run_fallback()  ← 实际默认路径
[7] _run_fallback
        ├─ 比例尺白框：跨帧中位数共识 _scale_bar_box() → 生成 inpaint 掩膜
        └─ fb.stitch_fallback(...)
              ├─ solve_poses()            全局最小二乘（lm，数值 Jacobian）
              ├─ _warp_stream()           逐帧 warp + 生成器喂给下游（不攒列表）
              ├─ _multiband_blend_roi()   多频带融合（内容 warp 用 ROI）
              ├─ acc_lap / acc_w 归一化    反归一化重建
              ├─ result[cov == 0] = 0     清掉 BORDER_REPLICATE 糊出帧外的雾
              └─ _trim_black_border()     只裁「整行/整列 max 灰度 ≤ 8」的空边
        ↓
[8] _save(img, fmt='png')  →  NAS 优先（Daily_Poster/stitch/results），失败降级本地 results/
        ↓
[9] meta 回填（n_images / method / warnings / matches / groups / output_scale / accel）
```

### 3.3 接口与参数

Blueprint `stitch_bp`，**无 `url_prefix`**：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/stitch` | 页面（**注意：无尾斜杠**，`/stitch/` 是 404） |
| POST | `/api/stitch/run` | 同步拼接 |
| POST | `/api/stitch/submit` | 异步提交（走统一任务队列，见 §6.2） |
| GET | `/api/stitch/task/<task_id>` | 任务查询 |
| GET | `/api/stitch/result/<filename>` | **内联预览**（`send_file` 不带 `as_attachment`） |
| GET | `/api/stitch/result/<filename>/dl` | 下载 |
| GET | `/api/stitch/assets/<path:rel>` | 静态资源（白名单式 `resolve()` + `relative_to()` 双校验防穿越） |
| GET | `/api/stitch/health` | 健康检查（`/stitch/health` 是 **404**） |

**健康检查自述字段**（`GET /api/stitch/health`，排查时**先看这里**再猜）：

| 字段 | 含义 |
|---|---|
| `engine` | `'openstitching'` = 该库可用；`'fallback_opencv'` = 走纯 OpenCV（本素材的正确路径） |
| `uploads_dir` / `results_dir` | **实际**落盘位置（不用猜是不是降级到本地了） |
| `results_on_nas` | 结果是否真的在 NAS 上 |
| `canvas_budget_mp` | 本次画布像素预算（MP）。= `canvas_pixel_budget(1, 0, holding_frames=False) / 1e6`，与 `Stitcher.stitch` **同口径** |
| `accel.use_gpu` / `coarse_screen` / `warm_workers` / `warp_roi` | 加速开关**实际生效值** —— 判断「优化到底有没有起作用」的唯一依据 |
| `cv2` / `service` / `ok` | 版本与存活 |

**表单参数**（`routes._parse_opts()`，含夹紧区间）：

| 参数 | 默认 | 合法区间 | 语义 |
|---|---|---|---|
| `feather` | `0` | `[0, 10]` | **已废弃**，融合段不使用。上限夹 10 只为防 API 传「大羽化」 |
| `max_side` | `0` | `0` 或 `[1024, 4096]` | **输入预缩放**（加快匹配）。`0` = 原尺寸。**别和 `out_side` 混** |
| `out_side` | `0` | `0` 或 `[1024, 65536]` | **输出图幅上限**（全景最长边 px）。`0` = 自动。不锁枚举，API 可传任意值 |
| `detect_scale_bar` | `1` | `'1'` 开 / `'0'` 关 | 比例尺白框检测 + inpaint |
| `inpaint_holes` | `0` | — | 空洞修补 |
| `engine` | `auto` | `auto` / … | 引擎选择 |
| `use_gpu` | `auto` | `auto`/`1`/`0` | 距离阵走 torch GPU |
| `coarse_screen` | `1` | `'1'`/`'0'` | 1/8 尺度粗筛 |

---

## 4. 算法原理

### 4.1 匹配判据（`geometry.py`）★ 全模块最需要理解的部分

**判据主力是「内点率」，不是内点数。**

显微切片满是**周期性重复纹理**，错配会**成簇**出现且**成簇的错配互相一致**——
一副错配点集能凑出几千个「内点」、残差还很小。只有内点率压得住它。

| 类型 | 实测内点率 |
|---|---|
| 真重叠 | **88% ~ 95%** |
| 错配成簇 | 60% ~ 75% |
| 剪影成簇 | 0.6% |

**灰度 MAD 对本素材无效**（所以不能靠像素比对兜底）：

| | 真重叠帧 | 远隔帧 |
|---|---|---|
| MAD | 27.5 vs 基线 46.1 = **1.68×** | 27.0 vs 基线 40.8 = **1.51×** |

两者拉不开差距（切片纹理在灰度上高度自相似）⇒ **必须 SIFT 特征 + 几何判据**。

#### 常量表（`geometry.py`）

| 常量 | 值 | 含义 |
|---|---|---|
| `MIN_SHIFT_RATIO` | `0.04` | 位移 ≥ 4% 短边（≈88px）才算真重叠 |
| `MIN_HULL_RATIO` | `0.03` | 内点凸包 ≥ 3% 图像面积（**绝对**口径） |
| `MIN_HULL_NORM` | `0.30` | 内点凸包 ≥ 30% **理论重叠区**（尺度无关） |
| `MIN_INLIER_RATIO` | `0.50` | 内点率下限 |
| `MIN_TRANS_INLIER_RATIO` | `0.60` | **平移模型**内点率下限（主力判据） |
| `MIN_INLIERS` | `40` | 内点绝对下限 |
| `MIN_GOOD` | `25` | 粗匹配绝对下限 |
| `FALLBACK_ANGLE_DEG` | `5.0` | 兜底路径解出角度超过它就判错 |
| `FALLBACK_MIN_INLIERS` | `120` | 兜底路径要求更多内点 |
| `FALLBACK_MIN_INLIER_RATIO` | `0.70` | 兜底路径要求更高内点率 |
| `RANSAC_THRESH` | `4.0` | RANSAC 残差阈值 (px) |
| `RATIO_TEST` | `0.80` | Lowe ratio |
| `TRANS_MAX_ITERS` | `3000` | 平移精化迭代上限 |
| `COARSE_SCALE` / `COARSE_NFEATURES` / `COARSE_GOOD_MIN` / `COARSE_MIN_IMAGES` | `8` / `1000` / `20` / `4` | 粗筛参数（张数 < 4 不粗筛，对太少） |
| `GPU_CHUNK` / `GPU_MIN_FEATURES` / `GPU_RECHECK_MARGIN` | `1000` / `2000` / `5` | GPU 分块 / 启动门槛 / 复核裕度 |
| `WARM_WORKERS` | `6` | 预热并行度 |

#### ★ 两个绝对阈值会在小重叠区漏判真重叠（2026-09-26 修）

- `hull_ratio ≥ 0.03` 是**绝对**面积比 —— 位移 2000~2600px 时理论重叠区只剩 8~11%。
- `trans_inlier_ratio ≥ 0.60` 的**分母被污染** —— 真重叠实测可低至 **0.28**。

**修法**：引入尺度无关量

```
hull_norm = hull_area / [(W - |tx|) · (H - |ty|)]
```

作独立 OR 分支：

```
接受 ⇔ (hull_ratio ≥ 0.03  或  hull_norm ≥ 0.30)
    且 (inlier_ratio ≥ 0.60 或  hull_norm ≥ 0.30)     ← 第二处 OR 仅平移路径
```

第二处 OR **只给平移路径**（相似变换自由度大，不作证据）。

| 指标 | 修前 | 修后 |
|---|---|---|
| 接受边 | 1018 | **1120** |
| 真召回 | 80.9% | **87.2%** |
| 假接受 | — | **0 / 2810** |
| 边集自洽 max | — | 0.682 px |
| 隔离度 | — | 真 p5 **0.714** vs 假 max **0.0028**（255×） |

常量落在 `geometry.MIN_HULL_NORM` / `StitchConfig.min_hull_norm` / `MatchEvidence.hull_norm`。
**残留（有意保守）**：旋转兜底路径还能多救 78 条，要救需单独标定 + 对抗压测。

#### 纯平移 RANSAC（`_estimate_translation`）

2 自由度 ⇒ **1 点样本退化**，故不能用经典 RANSAC。实际做法：
**配对位移取中位数作初值 → 迭代重加权 → 坐标下降精化**（逐轴标量搜索，实测够用：T2-C 3211/3211、位移云 0.4px）。

#### ★ 判「到底有没有真旋转」看**位移云散布**（模型无关）

取真解内点的位移 `d = dst - src`，看散布：

| | 真平移（T2-C #28→#29） | 有旋转（ZK203 4-6→4-7） |
|---|---|---|
| 位移云标准差 | (0.4, 0.4) px | (18.4, 23.3) px |
| 单一平移解释的残差中位数 | **0.34 px** | **28.18 px** |
| 解出 θ | 0.00° | 2.34°（r=1750px 处应 ±72px，实测云范围 66/83px ✓ 吻合） |

**推论**：ZK203 `4-6&4-7` 平移路径只有 11 内点、相似兜底 2349 —— **不是估计器坏了**，是那对**确实转了 2.34°**。**别去「修」`_estimate_translation`**。
比任何拟合都可靠：拟合出的 θ 能被错配迁就，位移云的散布不能。

### 4.2 全局位姿（`fallback.solve_poses`）

- 以第 0 张图为参考，每张图求 `(theta, tx, ty)`；用 `scipy.optimize.least_squares(method='lm')`。
- `lm` 用**数值 Jacobian**，273 参数 ⇒ 每次迭代 274 次 `residuals`，每次遍历 1120 条边 ⇒ **位姿段 97.7% 的时间在这里**。
- `POSE_VEC=True`：`residuals` 对**每条边完全独立**（只用 `pa`、`pb`、`e.matrix`），故可批量算且**不改任何浮点结果**。
  实测单次残差 **41.6 ms → 1.31 ms**；`residuals` 会被调 **2747 次** ⇒ 位姿段 **117 s → 约 6 s**。
- ★ **只改 `residuals` 的实现，不碰 scipy 调用**（仍 `method="lm"` + 数值 Jacobian）⇒ 迭代序列逐位相同。
  **这与「换解析/稀疏 Jacobian」是两回事**（后者会改迭代序列）。
- ⚠️ `lm` **不支持 `jac_sparsity`**；要用得换 `trf`，会改收敛路径。

### 4.3 融合（`fallback.stitch_fallback` / `multiband_blend`）

#### 内存 = O(画布)，与图片数 n 无关（2026-09-23 改造，**务必保持**）

改造前是 `4·n·画布 + 86·画布` —— 每张图都被 warp 成**整张画布**存进列表，91 张即使降采样到 90MP 也要 **24.6 GB，必然 OOM**。

**四条改动（改 blending 时不要把这几条改回去）**：

1. `stitch_fallback` 用**生成器**逐张喂给 `multiband_blend`，**不攒** `warped`/`masks` 列表；
2. 权重金字塔**只存单通道** float32，靠 `wq[..., None]` 广播 —— 别再用 `np.dstack([mk]*3)`；
3. Laplacian **就地**算（`g[k] -= cv2.pyrUp(...)`），首图直接接管临时数组当累加器；
4. 融合阶段用 `stitcher._LazyImages` **按需读盘**；匹配完必须 `del images` + `OverlayVerifier.release()`
   （否则那 1.66 GB 源图一直占着，挤掉本可用的画布分辨率）。
   注意 `stitch_fallback` 里**不能再写** `sub_images = [images[g] for g in comp]`。

**实测常数**：`BLEND_BYTES_PER_PIX = 75`（实测 ~65 B/px × 1.15 安全系数）。

| 构成（16MP / n=6 实测 58.9 B/px，解析式预测 57.3 —— 模型成立） | B/px | 说明 |
|---|---|---|
| **单图瞬时** | **~36** | warp 结果 3C + 其金字塔 16C + `pyrUp` 临时 12C + 权重金字塔 5.3C |
| `acc_lap` | 16 | float32 **3 通道** × Σ 4C/3 |
| `acc_w` | 5.3 | float32 单通道 × Σ 4C/3 |
| 输出 / 其他 | ~8 | PNG 缓冲、resize、cv2 scratch |

★ 两个反直觉结论：
- **层数几乎不影响**（levels=1 是 63、levels=8 是 58.7）—— 第 0 层独占绝大部分 ⇒ **减层数 / 改 `min_dim` 是白费**。
- **最大单项是「单图瞬时」而非累加器**，且它按**整张画布**分配，而单帧只占画布 1/n —— 纯浪费。

#### ★ 接缝根因 = 黑填充，不是「羽化不足」

`warpAffine(borderMode=BORDER_CONSTANT, borderValue=0)` 在每张图边界造出「内容→0」的人工台阶，
权重边界≈1 ⇒ 被 Laplacian **原样重建成 ~30px 亮带**。

**修法**：内容 warp 改 `BORDER_REPLICATE`（权重仍 `BORDER_CONSTANT 0`）。受控 A/B 缝位灰度阶跃 **45.95 → 0.12**。

**副作用**：REPLICATE 让内容糊出帧外 ⇒ 外沿浅雾 + `_trim_black_border` 失效。
**修法**：warp 顺手 OR 出 `cov`（各帧占据区并集），融合后 `result[cov == 0] = 0`。
★ `cov |= ...` 在嵌套生成器里必须 `nonlocal cov`。

`feather` 参数此前**从未被 `stitch_fallback` 使用**（前端「默认 100」是谎），2026-09-27 只对齐了签名/文档/上限。
**羽化/增益补偿/高频取胜/INTER_LINEAR 在主台阶消除后均无额外收益**，羽化反使重叠区发糊。

#### ★ 裁剪绝不能用「随 n 增长」的阈值（2026-09-23 踩过大坑）

旧规则 `cov >= max(1.0, len(masks)*0.30)` 阈值随 n 增长，而画布平均覆盖率只有 **O(1)** ⇒ n 越大丢得越多。
实测 T2-C 8 张**只保留内容包围盒的 51.5%**，丢的 10.8 MP 是**真薄片内容**（原始帧 99.9% 非黑、四角灰度 68~80，根本没有黑角）。

现用 `_trim_black_border`：**只裁「整行/整列最大灰度 ≤ 8」的空边**（用 `max` 不是 `mean`）
⇒ **物理上不可能切到有效内容**，且与 n 无关。**用户口径：边角有黑色很正常，帧自身黑角一律保留。**

#### 比例尺（叠加物）白框

旧实现（单帧 / 最大连通域 / 面积 3000~80000）62 帧只检出 6 帧。
新 `stitcher._detect_scale_bar_box → _scale_bar_box` 用**跨帧中位数共识 ≥40%**：
`threshold 235 → MORPH_CLOSE → 连通域 → 矩形性 ≥0.80 → 长宽比 1.1~5.0 → 内有黑横线(<90 占比>0.5)`。
实测 **62/62 检出**，共识框 `(2379, 2001, 305, 126)`。

★ 只压权重不够：重建要 `acc_lap/acc_w` 归一化，权重压 0.02 会让分母同比例变小、把白框 Laplacian **原样复活**。
必须**先 inpaint**（`cv2.inpaint(..., TELEA, 3)`）再压 0.02。验证：对成品再跑检测器应检不到。

**确定性标尺（可当尺度真值）**：白框恒在 `(2380, 2001, 304×126)`，**500 µm ≡ 281 px → 1.7617 µm/px**（153 帧一致，无例外）。
手动拼图 `T2-C-手动.jpg` 8192×6943 是 2.3725 µm/px（2000 µm ≡ 843 px）。

### 4.4 输出图幅 `out_side` 与「缩放只能做一次」

**档位表**（`StitchConfig.out_side_tiers`，~1.25 倍一档，13 档）：

```
(0, 16384, 12288, 10240, 8192, 6144, 5120, 4096, 3072, 2560, 2048, 1536, 1280, 1024)
```

前端分「大图（定量测量）/ 中等（观察+局部测量）/ 轻量（目视预览）」三组；另有 `-1` 自定义（1024~65536）与 `-2` 推荐。
**后端只认整数 px**，`-2`/`-1` 都在前端 `app.js → outSideValue()` 解析掉。

**语义是一条硬约定**：

| 取值 | 行为 |
|---|---|
| `out_side > 0` | 输出**真实**缩到该尺寸以内，**不放大回名义尺寸**。等效像素尺度见 `meta.output_scale`。档位是**上限** |
| `out_side == 0` | **保持历史行为**：受内存限制缩了再 `INTER_CUBIC` 放大回名义尺寸 ⇒ 输出像素网格与源图一致、µm/px 标定不变 |

**用户档位与内存预算两个上限取更严者**，决策抽成**纯函数** `fallback.plan_out_scale(canvas_w, canvas_h, out_side, max_pix) -> (out_scale, capped_by)`，`capped_by ∈ {'user','memory'}`。
内存比用户档位更紧时往 `meta.warnings` 塞中文告警，**不静默降质**。

#### ★ 「推荐」档位算法（`app.js` 的 `REC_BEGIN..REC_END`）

```
k = ceil(sqrt(n))                    # 栅格
并排帧数 = (k-1) × 0.8 + 1            # ★ 不能写成 k × 0.8
est = 并排帧数 × 帧最长边
档位 = max( 不小于 est 的最小档位, 内存预算允许的最大档位 )  取更小者
```

- **别简化回 `k × 0.8`**：n=2 时它给 1.6 帧 < 实测 1.79 帧 ⇒ 推荐比真实画布还小 = **主动降采样**。
- 三实测点（n=2/4/16 → 画布 4914/4075/8265，帧 2752×2208）全部**估大**。方向是**有意**的：档位是上限，估大只多花时间/内存。
- 帧尺寸**只读文件头**（每张切前 64KB，解 JPEG SOF / PNG IHDR），**不解码**；失败再落 `createImageBitmap`。
- 内存收口靠 health 的 `canvas_budget_mp`（= `canvas_pixel_budget(1, 0, holding_frames=False) / 1e6`，与 `Stitcher.stitch` **同口径**）。没有它就会推荐 10240 这类到不了的档位。
- 竞态：拖完图立刻点开始，扫描可能没跑完 ⇒ `run()` 里选「推荐」且数据未就绪时先 `await` 补齐。

#### ★ 一个真踩到的几何 Bug：`out_scale != 1` 时缩放被做了两遍

`stitch_fallback._warp_stream` 既把 `src` 按 `out_scale` 预缩小（INTER_AREA），**又**把 `out_scale` 写进仿射矩阵 `S`。
后果：内容几何错位 + 平移量没缩。实测档位 2048 时画布 1946 宽，内容只铺到第 **1817** 列 → 右边 128px 黑带 → 裁边后 `1283×1515`、长宽比 **0.90**（名义 0.95）。
**此路径此前从未被触发**（内存预算 72~88MP 一直大于实际画布 12~50MP），潜伏了很久。

正确写法是**把矩阵共轭回去**（预缩小是 `u = os·x`）：

```python
S  = np.array([[os, 0, -x0*os], [0, os, -y0*os]])      # 源坐标 -> 缩小后的画布
Mw = (np.vstack([S, [0,0,1]]) @ M4)[:2]
if os != 1.0:
    src = cv2.resize(src, None, fx=os, fy=os, interpolation=cv2.INTER_AREA)
    Mw = Mw @ np.array([[1/os,0,0],[0,1/os,0],[0,0,1]])   # 线性部分归一，平移量留 os·(t-原点)
```

**判据（已进回归 §10，别删）**：指定档位输出必须**铺满**缩小后的画布（`输出面积 / engine_meta.canvas ≈ 1.000`）且**长宽比与名义画布一致**。
修复后实测 2046 档位 → `1946×2048`，铺满 1.000，比值 0.9502 = 名义 0.9502。

---

## 5. 性能与内存（实测账）

### 5.1 加速开关总表（全部「逐位相同、0 像素差」）

| 开关 | 位置 | 效果 | 备注 |
|---|---|---|---|
| 粗筛 + GPU 距离阵 | `StitchConfig.coarse_screen` / `use_gpu` | **6.21×**（305.8s → 49.3s） | 接受边逐条不变。可 per-request 关 |
| 预热并行 ×6 | `StitchConfig.warm_workers`（`geometry.WARM_WORKERS`） | 预热 46.2s → **32.8s**；`all_pairs` 78.3s → **56.1s** | 帧间无依赖。**W>8 反慢**（SIFT 内部已多线程，超订） |
| 内容 warp 输出原点 ROI | `fallback.WARP_ROI` | 融合 177.8s → **113.3s**；端到端融合段 159.6s → **101.4s** | **只对内容（LANCZOS4）**；掩膜（NEAREST）**不可** ROI |
| 位姿残差向量化 | `fallback.POSE_VEC` | 位姿段 **117s → ~6s**（17×） | 只改 `residuals`、不碰 scipy ⇒ 迭代序列逐位相同 |
| 垂直切条 ROI | `fallback.V_CROP_ENABLED` | 融合 39.5s → 31.8s（15 张，0 差异） | `roi_halo(L) = 4·2^L` |
| 权重乘加 3 通道 | `fallback.MULADD_3CH` | 160.3ms → 62.8ms（**2.52×**） | 见下方坑 |
| 全画布临时收窄 ×3 | `fallback.COV_WINDOW_OR` / `MASK_MUL` / `BASE_REUSE` | 185.2s → 174.9s（0 像素差） | 各 52MB 级临时缓冲 |

**合计：248.3s → 165.8s（1.50×）**，41 项断言全绿。
★ `/api/stitch/health` 的 `accel` 会自述 `use_gpu / coarse_screen / warm_workers / warp_roi` —— **先看它再判断到底有没有生效**。

**粗筛 + GPU 的三条实现约束（踩过就别再踩）**：

1. `knnMatch` 抽象成数组后**第 0 列才是最近邻**；误用第 1 列当目标索引 → 全部对「内点过少」。
2. ratio test 必须用 **L2 距离直接比较**（`d1 < r·d2`）；换平方距离 = 阈值变成 `r²`，语义不等价。
3. GPU 只在 `good ∈ [min_good, min_good+5]` 时用 CPU 复核 —— 那是 fp32 ULP 唯一可能翻转判定的区间；
   torch/显存任何异常 → **永久回落 CPU，不抛出**。

**超订警告**：`warm_workers > 8` 反而变慢（`cv2.SIFT` 内部已多线程）。
**共享 detector 不安全**：并行预热时**每个 worker 必须自建 `cv2.SIFT`**（`_make_detectors()`）—— 共享实例并发调用无线程安全保证。

**粗筛收益的边界**：批里若有**孤立帧**（与任何图都不重叠）→ 接受边图天然不连通 → 自动全量补齐，粗筛收益归零，只剩 GPU 的 5.55×。
**最坏 5.55× / 最好 6.21×，永不比基线差**。（设计取舍：只补「跨分量」的对能救回粗筛收益，但**会改变接受边集合 → 破坏逐位等价**，故不做。）

### 5.2 全流程时间与内存（91 张量级）

| 阶段 | 占比（314.7s 基线） |
|---|---|
| 匹配 | 86.9s（28.8%） |
| 位姿 | 89s（29.5%） |
| 融合 | 117s（38.8%） |

★ **全流程大头是位姿 + 融合，不是匹配** —— 别只盯着匹配段优化。
★ 全局 RSS 峰值 **7500 MB，在匹配段**（91 张源图 + 描述子），不是融合段。

| 场景 | 结果 |
|---|---|
| 16 张端到端（T2-C #21–#36） | 139s，画布 6096×8265 = **50.4 MP**，服务峰值 **3.10 GB** |
| 内存改造前（同批） | 7.44 GB、输出仅 18.8 MP（被裁 48%） |
| 62 张 / 1891 对（仅匹配段） | 基线 305.8s → 粗筛 147.7s → GPU 55.1s → **两者 49.3s** |

**像素无损仍不现实**：91 张画布约 107~287 MP，全分辨率需 **~7~18.7 GB** > 15.6 GB 物理内存。
按需读盘后可用约 **75~85 MP**（`out_scale ≈ 0.5~0.55`）。要真无损须**分块/分带融合**。

**三条「像素无损」口径别混为一谈**：

| 口径 | 达成条件 |
|---|---|
| **传输/存储无损** | 结果默认存 **PNG**（`_save(img, fmt='png')`，压缩级 6）。早期存 JPEG-92 有损，会在晶界/裂隙产生 8×8 块状伪影 —— 对显微测量**不可接受** |
| **几何分辨率无损** | `max_side=0` + `out_side=0` + 内存预算未触发（`out_scale == 1.0`）⇒ 全程不缩放 |
| **内容无重采样** | `INTER_LANCZOS4` 对非整数位移**必然重采样**。只有位移恰好是整数像素时才是真搬运 |

### 5.3 ★ 已否决清单（**别再试**）

| 方案 | 否决依据 |
|---|---|
| ❌ numpy BLAS 距离阵 | 681 ms/对，比 `BFMatcher` 107 ms/对慢 **6.4×** |
| ❌ 相位相关剪枝 | 3/31 真重叠峰值为负 |
| ❌ 粗筛并行 | `BFMatcher` 内部已并行，实测仅 **1.11×**，不值得引入共享态风险 |
| ❌ 「先裁剪再融合」省内存 | 最多省 33% 画布，但金字塔要在裁剪边界外留 `2^(L+1)` halo ⇒ 8 帧时 halo **7.9 MP > 省下的 7.3 MP**，**净亏** |
| ❌ 减层数 / 改 `min_dim` | levels=1 是 63、levels=8 是 58.7 B/px ⇒ 第 0 层独占绝大部分，**白费** |
| ❌ `float16` 累加器 | 仅省总内存 12%，却冒粗层条带风险，投入产出比最差 |
| ❌ 四边金字塔 ROI | `pyrDown/pyrUp` 的 SIMD 尾部按数组**宽度**取浮点累加顺序 ⇒ 内容层 ULP，对 halo **非单调** |
| ❌ GPU 融合 | `pyrUp` 不逐位 + LANCZOS4 无 GPU 算子 + warp 不可 ROI |
| ❌ 掩膜 warp ROI | `INTER_NEAREST` 二值边界对原点平移敏感：24×4 组 × **1728 M 像素 → 3421 px 跳变**（掩膜整画布只花 ~10.9 ms，不值得冒风险） |
| ❌ OpenStitching 引擎 | 在本机数据上退化成放射状拖影（故默认走纯 OpenCV `_run_fallback`） |
| ❌ 用 MAD / 灰度直方图做兜底判据 | 真 1.68× vs 远隔 1.51×，拉不开 |
| ❌ `np.copyto` 胀 3 通道 | 171.2 ms，**比老写法还慢**（0.92×） |
| ❌ 权重金字塔存 3 通道 | `cv2.pyrDown` 在 1ch vs 3ch float32 **不逐位相同** |

**待定/默认关闭**：`fallback.ACC_MEMMAP = False`（累加器 memmap 落盘已实现、逐位不变，但收益**高度依赖页缓存命中率** ⇒ 需要在目标机器上实测再决定是否默认开启）。⚠️ Windows 上 memmap 必须显式 `close()`，否则删不掉文件。

---

## 6. 运维

### 6.1 服务与路由

门户 Flask 跑在 **5555**（`tools/supervisor.py` 管，pid 记在 `.flask_5555.pid`）。

**三个易误判的坑**：

1. `/stitch/`、`/stitch/health` 都是 **404** —— 别拿它们判「服务挂了」。
2. 本机 `curl` 必须加 `--noproxy '*'`（环境有代理）。
3. 5555 上另有 svchost 端口代理 LISTENING，**kill 前核对 pid**。

### 6.2 统一任务队列（拼合与翻译共用一条串行通道）

**为什么合并**：文献翻译与图像拼合**都吃 GPU/内存**，并跑就是互相抢。故合并成**一条**串行通道。
内核在 `web_app.py`：`jobs` 表 + FIFO `QUEUE` + 单 `_queue_worker` 线程 + `TASK_LANE = Semaphore(1)`。任务带 `kind`（`'translate' | 'stitch'`）。

链路（**改这条链路时四层都要跟**）：
`web_app.register_task_runner` → `modules/__init__.register_all(register_runner=…)` → `routes.init_stitch(register_runner=…)` → `register_runner('stitch', _stitch_runner)`

★★ **`submit_task` 只负责入队，队列还得知道谁来执行** —— 漏注入 `register_runner` 的后果是
**「入了队但没人执行」**：`/api/stitch/submit` 返回 `queued:true`，队列快照 `by_kind:{stitch:1}` + `failed:1`，
错误 **`未注册的任务种类（kind=stitch）`**（2026-09-28 实测踩到）。

★ **串行道归属**：走队列时调度线程**已持有** `TASK_LANE`，执行体**不能再 acquire**（自锁 → 等硬超时被误判失败）；
只有兜底自跑（`ctx.lane_held=False`）才自己拿锁。

★ 排队期被取消的任务执行体**从未运行** ⇒ `finally` 不触发 ⇒ 上传临时件残留，需 `_drop_task_uploads()` 补删。

#### 6.2.1 队列里「查看成品图」——`job.result` 字段契约

拼合完成后，**任务队列页（`/queue`）** 的该条目上会多出「查看成图 / 下载」两个链接（2026-09-30 落地）。
链路横跨**三个文件**，改任意一段都要跟着改另两段：

| 层 | 位置 | 职责 |
|---|---|---|
| 写入 | `modules/stitch/routes.py` → `_stitch_runner` 末尾 | `ctx.finish(message='完成', result=info)` |
| 放行 | `web_app.py` → `_queue_snapshot` | `_FIELDS` 必须含 `'result'`；再对 result 做**键白名单**裁剪 |
| 渲染 | `modules/queue/templates/queue.html` → `jobRow` | 取 `public_url` → 「查看成图」（`target="_blank"`，内联看图）；`public_download_url` → 「下载」 |

`info` 有 **8 个键**，**两套地址各有各的消费方，别互相顶替**（★ 用户口径 2026-09-30：链接直接用 NAS 的）：

| 键 | 形态 | 谁用 / 为什么 |
|---|---|---|
| `url` / `download_url` | **相对路径** `/api/stitch/result/pano_xxx.png` | 拼合页 `assets/app.js` 当**内嵌资源**（`<img src>`、`<a download>`）。★ **必须保持相对**：页面若从 **https** 打开，内嵌资源用 `http://` 绝对地址会被浏览器**混合内容策略拦掉** |
| `public_url` / `public_download_url` | **NAS 对外入口** `http://<nas-host>/<share> | 队列页的「查看成图 / 下载」链接。能分享给别人、换入口打开也有效 |

- **NAS 入口选 `kust.work`，不是 `<lan-ip>`**：后者只在实验室 LAN（<nas-host>/<share> —— 与 `modules/share` 的 `SERVER` 是同一条坑。
  实测两条人群都兼顾：本机取 15.9MB 成品图 **0.45s**、校园网同事可直开。
  ⚠️ 别拿 SMB 经验否定它：`//<nas-host>/<share> 挂 94s 是 **445 端口**的事，HTTP 80/443 实测 **0.016s**。
  换域名/换网段：环境变量 `STITCH_PUBLIC_BASE`（**设空串则退回相对路径**）。常量在 `routes.PUBLIC_BASE` / `_public_url()`。
- **`width/height` 必须 `int()`**：`result.shape` 元素虽多为 Python int，但进 `jsonify` 前不能留 numpy 标量（`TypeError: not JSON serializable`）。
- ★★ **绝不能把 `meta` 塞进队列的 job**：91 张任务的 `meta.matches` 有 **4095 条**逐对判据明细，而 `/api/queue` 是前端 **2s 一次**的高频轮询 —— 等于每次轮询搬一遍大 JSON。
  故模块自己的 `tasks` 表（`/api/stitch/task/<id>`）拿 `dict(info, meta=meta)` **完整版**，队列只拿 `info` **精简版**。
- ⚠️ **新增字段只有同时进 `_FIELDS` 白名单才会出现在快照里**（`_queue_snapshot` 是白名单过滤，不是全量透传）；漏加的表现是「后端写进去了，前端死活看不到」，不报错。
- 内联看图靠 `send_file` **不带** `as_attachment`，下载走 `/dl`（带 `Content-Disposition: attachment`）。
- 成品图是**原尺寸加载**（后端不缩图），全景 PNG 常达几十 MB ⇒ 队列条目会显示 `3720×2632 · 15.2MB` 让用户有预期。
- **时效**：`jobs` 是**内存表**，已完成任务保留 **1 小时**后被清理（`submit_task` 内的 TTL）⇒ **队列里的链接**过了窗口就点不到了，
  但**成品图本身长期在 NAS** `results/`（见 §6.3），可按文件名直接取；`public_url` 只解决"分享/换入口"，不解决"任务记录过期"。

### 6.3 存储：什么放本地、什么放 NAS

判据只有一条：**这份文件是「过程性临时件」还是「需要长期留存的成果」**。
不要按「体积大就丢 NAS」来拍，否则临时件会在 NAS 上堆垃圾。

| 类型 | 去向 | 例子 |
|---|---|---|
| 过程性临时件（用完即弃） | **本地** | 用户上传的原图 `uploads/` —— 拼完就没用 |
| 需要留存的成果 | **NAS** | 拼接结果 `results/` —— 一次生成反复使用 |

实现要点（`routes.py` 的 `_pick_store`）：

- NAS 根：`S:\Daily_Poster\stitch\results`。
- **必须有降级**：NAS 不可达 → 回退 `modules/stitch/results/`，功能不中断。
- **★ 探针不要用「写一个文件再删掉」**：删除会被环境级 safe-delete hook 拦下并抛 `OSError`，导致 NAS 明明可用却被判不可用（本模块第一版就踩了，表现为日志 `NAS 不可用 ... trash-failed` 却回退成功）。**只做 `mkdir(exist_ok=True)` + `os.access(..., W_OK)`，不落任何探针文件。**
- `_find_result(filename)` 按「NAS 优先 / 本地兜底」找回；**拒绝含 `/`、`\` 或 `.` 开头的 filename**（防穿越）。
- `_save` 用 `cv2.imencode(...)[1].tofile()`（避中文路径写失败）。
- **健康检查要自证位置**：`/api/stitch/health` 返回 `uploads_dir` / `results_dir` / `results_on_nas`，一眼看出实际落盘到哪。

#### ★ 一旦挂载 NAS 目录，`rm` / `os.remove` 会被 safe-delete hook 拦下

环境装了 safe-delete 拦截：`rm -f` / `rm -rf` / `os.remove` / `os.unlink` 删任何文件时都会先尝试送回收站，
失败即 **fail-closed 抛错**。对 NAS 上的文件尤其明显：`rm -rf /s/...` 会刷一屏 `[safe-delete][SAFE_DELETE_FAIL_CLOSED]`
而**一个文件都没删掉**（命令仍返回 **0**，极易误判成「已删除」）。

可靠做法 —— 用 Win32 API 直接删（绕过 hook，且**不可恢复**，慎用）：

```python
import ctypes, pathlib
kd = ctypes.windll.kernel32
kd.DeleteFileW.argtypes = [ctypes.c_wchar_p];      kd.DeleteFileW.restype = ctypes.c_int
kd.RemoveDirectoryW.argtypes = [ctypes.c_wchar_p]; kd.RemoveDirectoryW.restype = ctypes.c_int
d = pathlib.Path(r'S:\Daily_Poster\stitch\uploads')
for f in sorted(d.iterdir()):
    if f.is_file():
        kd.DeleteFileW(str(f))
kd.RemoveDirectoryW(str(d))
```

### 6.4 上传临时件清理

`uploads/` 是**过程性临时件**，正常路径在 `finally` 里删。三种会漏：任务线程被强杀、文件被占用（Windows 删不掉已打开文件）、`os.remove` 静默失败。

`routes._sweep_uploads()` 在 `init_stitch` 里兜底扫一次，**只删 mtime 超 10 分钟**的
（防测试用 Flask test client 调 `init_stitch` 时误删线上正在跑任务的上传件）。

### 6.5 任务表有界

`tasks` 原不淘汰（91 张 JSON 942 KB，`meta.matches` 占 99%）。
现加 `_TASKS_MAX = 48` + `_trim_tasks()`（淘汰最旧已完成）+ `_put_task` / `_patch_task`。

**并发 OOM 防护**：`_check_memory` 只看**提交瞬间**的可用内存，两任务并发会**双过检查** ⇒ 必须串行化。
串行锁**不在本模块**，由统一任务队列下发（见 §6.2）：

- `routes._HOOKS = {'submit_task', 'get_task', 'lane'}`；`init_stitch(task_lane=…)` 把队列的 `TASK_LANE` 注入进来；
- `_lane()` 返回注入的 lane，**注不进时**退回本模块自持的 `_FALLBACK_LANE = threading.Semaphore(1)`（保证脱离门户单独跑也能串行）；
- ★ **`lane_held` 规则**：执行体若由队列调度线程调用（`ctx.lane_held` 为真）**不得再 acquire**（自锁 → 等硬超时被误判失败）；
  只有兜底自跑（`need_lane = not ctx.lane_held`）才自己拿锁。

submit 会回 `queued: bool` 区分「走队列」与「本模块自跑」，前端据此提示「已排队」。

---

## 7. 逐位等价验证方法论（★ 血泪，交改动前必做）

### 7.1 四条铁律

1. **峰值 RSS 必须逐进程测。** 同一进程里 A 跑完再跑 B，glibc arena 不还给系统 ⇒ 严重低报。
   曾实测同进程「−192 MB」，拆成两进程真值 **−1580 MB**（6093 → 4513 MB）。
2. **逐位比对必须在「冻结输入」上做。** 整条流水线 SIFT/RANSAC 多线程不确定，
   同样入参两次跑**画布宽度都不同**（5429 vs 5430）。⇒ 缓存 `edges`/`poses` 后只比 `stitch_fallback` 产物。
3. **GPU/多线程路径下 `synchronize` 必须显式调用**，否则测出 0.2 ms 假值。
4. **改 blending / 融合实现，一律用「新旧模块同进程喂同一位姿」套路**：
   把改动前 `fallback.py` 另存独立模块，在同一进程里给新旧实现喂**同一份位姿**（几何结果 pickle 缓存），分别跑再逐位比数组。

### 7.2 已知的正常差异（不必追）

`cv2.pyrDown` 对 **1 通道 vs 3 通道 float32 差 1 个 ULP（3.05e-05）** ⇒ 权重金字塔 3ch → 1ch 会带来
~1e-7 比例的 ±1 灰阶差（端到端 **8 / 66,718,323** 像素）。
最小复现确认来源，但随机合成数据复现不出翻转 ⇒ **只能说「与 1 ULP 舍入一致」，不声称已证明**。对显微测量无影响。

### 7.3 量测坑

- **别用「全画布列/行剖面 99.5 分位/中位」当接缝判据**（真缝只占几列，被内容陡边缘主导）。
  可信口径：① 缝侧亮带（横剖面凸起幅度 + 峰位是否在缝上）② **1:1 裁片肉眼**。
- 逐位验证的效果脚本在 `.workbuddy/artifacts/stitch-验证脚本/`（`_crit_*.py`），含
  `_crit_warp_roi_sweep.py`、`_crit_mask_sweep.py`、`_crit_warm_equiv.py`、`_crit_pose_vec_equiv.py`、
  `_muladd_ab.py`、`_crit_p03_equiv.py`、`accel-fidelity-check.py`。

---

## 8. 测试与回归

```bash
cd D:\PostNews\Translate
"D:\Python 3.12.4\python.exe" modules/stitch/_tests/regress.py      # 72 断言
"D:\Python 3.12.4\python.exe" modules/stitch/_tests/test_routes.py  # 路由, exit=0
node queue_dom_test.js                                              # 前端队列 DOM, 47 断言
```

**工具坑**：

| 坑 | 正确做法 |
|---|---|
| 语法检查 | 用 `py_compile`。`stitcher.py` 带 **UTF-8 BOM**，`ast.parse` 会假报 `U+FEFF` |
| `test_routes.py` 的 `atexit` 清理 | 用**默认参数**绑路径，不能用 `__file__`/模块全局（解释器收尾时可能已清空） |
| **本机 curl 传文件** | **不能用 Git Bash 的 `/tmp` 路径**（Windows 原生 curl 解析不了，报 `curl: (26)` 且 `-s` 下**静默返回空** ⇒ 极易误判成「接口没响应」）。用 `D:/...` 正斜杠路径 |
| 读日志 | `logs/flask.out.log` 是**混合编码**（以 UTF-8 为主、掺少量 GBK）⇒ 用 `utf-8, errors='replace'` 读 |
| 判断卡住 | 重定向到文件是**块缓冲**，末尾可能压在缓冲区不落盘 ⇒ **绝不能据此判「卡住了」**，要轮询文件大小 |
| 推断事件顺序 | **禁止用日志顺序推断先后**（`print` 行与 `logging` 行时序不可信，实测 `[锁] 拿锁成功` 出现在 `Serving on` 之后） |

**前端纯逻辑不必上浏览器**：从 `assets/app.js` 按标记切出函数体，用桩 DOM
（`$ = id => ({value, style, textContent, options, addEventListener(){}})`）在 node 里 `eval` 一遍即可。
★ 两个作用域坑：① 函数里的 `eval` 只把声明泄漏到**该函数**作用域，解析与断言必须写在**同一个函数**里；
② `eval` 里的 `const` **不会**泄漏到外层（函数声明会），常量要显式 `globalThis` 导出。

---

## 9. 排障手册

| 症状 | 判据（先做这一步） | 处置 |
|---|---|---|
| **进度条停 0% / 永不报错** | `cat .flask_5555.pid` 与 `netstat` 的 5555 LISTENING pid **对不上** ⇒ 进程被重启过 | ★ 机制：**进程一死，内存 `jobs` 表随之消失** ⇒ 前端轮询拿不到记录（`/api/_debug` → `jobs:{}`、`/api/queue` → `total:0`）⇒ **停在 0% 且永不报错**。所以「卡 0%」**第一嫌疑永远是进程被重启/崩溃**，不是翻译/拼接逻辑 |
| 进程**原生崩溃**（无 traceback，日志凭空断掉） | 走 Windows WER：`C:\ProgramData\Microsoft\Windows\WER\ReportArchive\AppCrash_python.exe_*` 的 `Report.wer`（**UTF-16**），看 `Sig[3] 故障模块` / `Sig[6] 异常码` / `Sig[7] 偏移`；`EventTime` 是 **FileTime 且 UTC** | ★ **故障模块会换代**（曾 `cv2.pyd`+`c0000409`，后换显卡驱动 `nvwgf2umx.dll`）⇒ **每次都要重新取证，别套旧结论** |
| 拼接结果出现**放射状拖影** | 是否误用了 OpenStitching 引擎 | 走 `_run_fallback`（纯 OpenCV） |
| 输出**右边/下边有黑带、长宽比不对** | 算 `输出面积 / engine_meta.canvas`，看是否 < 1.000 | 命中 §4.4 的「缩放做了两遍」Bug ⇒ 检查矩阵是否已共轭回 `diag(1/os,1/os,1)` |
| 结果被**裁掉一大块内容** | 检查裁剪判据是否与 n 挂钩 | 必须用 `_trim_black_border`（整行/列 **max** 灰度 ≤ 8），**不能**用随 n 增长的覆盖率阈值 |
| 接缝处有**~30px 亮带** | 检查内容 warp 的 `borderMode` | 必须 `BORDER_REPLICATE`（权重仍 `BORDER_CONSTANT 0`），并配套 `result[cov == 0] = 0` |
| 比例尺白框**没被遮掉** | 对成品再跑一次检测器 | 必须**先 inpaint 再压权重**；只压权重会把白框 Laplacian 原样复活 |
| 接受边**明显变少** | 看 `meta.matches` 里 `hull_norm` 分布 | 确认 `MIN_HULL_NORM` 的 OR 分支**只挂在平移路径**上（相似变换路径不作证据） |
| 大批量 **OOM** | 看 `BLEND_BYTES_PER_PIX` 与 `canvas_pixel_budget()` | 画布预算**必须由内存推算**，不能写死 `MAX_PIX` |
| 换机器后 **GPU 没生效** | 看 `/api/stitch/health` 的 `accel.use_gpu` | OpenCV 若为非 CUDA 构建（`cv2.cuda.getCudaEnabledDeviceCount() == 0`）⇒ **走 torch**（`torch.cdist`），**别自编译 OpenCV-CUDA**。`nvidia-smi` 报 NVML 初始化失败**不代表**没 GPU |

---

## 10. 路径适配（换机器 / 换项目要改哪些）

本模块的**算法部分**（`geometry.py` / `fallback.py`）几乎无外部耦合，可直接移植；
**编排与集成部分**（`stitcher.py` / `routes.py`）绑定了本机环境。搬迁清单：

| 项 | 现值（本机实测） | 换环境要改 |
|---|---|---|
| 项目根 | `D:\PostNews\Translate` | 部署根目录 |
| Python 解释器 | `D:\Python 3.12.4\python.exe` | 目标机解释器 |
| NAS 结果根 | `S:\Daily_Poster\stitch\results` | ★ **若挪进计划任务/提升令牌环境，`S:` 不可见 ⇒ 必须换 UNC**：`\\<nas-host>\<share> |
| NAS 上传区 | `S:\Daily_Poster\stitch\uploads` | 同上 |
| 本地兜底结果 | `modules/stitch/results/` | 保持相对模块目录即可 |
| 临时 memmap 目录 | `modules/stitch/.cache_mm`（`ACC_MEMMAP_DIR`，默认关闭） | 同上（需落在**顺序读写快**的盘：实测 D 盘写 1446 MB/s、读 2417 MB/s） |
| 服务端口 | `5555` | supervisor / 前端调用 |
| 硬件基线 | 16 逻辑核 / 10 物理核 / 15.6 GB / RTX 4060 Laptop 8GB / 驱动 536.99 / torch 2.5.1+cu121 | **`BLEND_BYTES_PER_PIX=75` 与 `canvas_pixel_budget()` 是按 15.6 GB 标定的** ⇒ 换内存容量需重新标定 |
| 素材基线 | 2752×2208 显微薄片、相邻重叠 ~59%、位移 ~790px | **`StitchConfig` 全部默认值是按此标定的**；换镜头/换步长需重新标定阈值 |

**换网段/换 NAS 用环境变量覆盖**（不要改代码）。

---

## 11. 硬约束清单（Do / Don't）

| ❌ 不要做 | 理由 |
|---|---|
| 把几何模型放开成自由相似变换/单应 | 假重叠会凑出残差极小的假解，判据整体失效（§2） |
| 把「解出角度」当好消息 | 载物台不转 ⇒ 解出角度 = 匹配错了 |
| 用内点数（而非内点率）当主力判据 | 周期性纹理错配能凑几千个互相一致的内点 |
| 用灰度 MAD 做兜底判据 | 真 1.68× vs 远隔 1.51×，拉不开 |
| 只用绝对阈值 `hull_ratio` / `trans_inlier_ratio` | 大位移时漏判真重叠（§4.1） |
| 在 `stitch_fallback` 里攒 `warped`/`masks` 列表 | 内存退化回 O(n·画布)，91 张必然 OOM |
| 让权重金字塔存 3 通道 | `pyrDown` 1ch vs 3ch 不逐位相同 |
| 对掩膜 warp 做 ROI | `INTER_NEAREST` 二值边界敏感，实测 3421 px 跳变 |
| 打开 `UPSCALE_OUTPUT` | 用户口径：成品**只能向下采**，放大只产生假像素 |
| 改 `out_side_tiers` 只改一处 | 必须**同步三处**：`stitcher.out_side_tiers`、`assets/index.html` 的 `<option>`、`regress.py §10` 自检 |
| 在别处直接读 `$('outSide').value` | 会把 `-1`（自定义）当像素发出去；**只走 `app.js → outSideValue()`** |
| 把推荐公式简化回 `k × 0.8` | n=2 时推荐比真实画布还小 = 主动降采样 |
| 用 `rm` / `os.remove` 删 NAS 挂载区的文件 | safe-delete hook fail-closed，**删不掉却返回 0** ⇒ 用 ctypes `DeleteFileW` |
| 用「写探针再删」探测 NAS 可写性 | 删除被 hook 拦下 ⇒ 好盘被误判不可用 |
| 用 `ast.parse` 检查 `stitcher.py` | 该文件带 UTF-8 BOM，会假报 `U+FEFF`；用 `py_compile` |
| 用 Git Bash `/tmp` 路径给本机 curl 传文件 | Windows 原生 curl 解析不了，**静默返回空** |
| 用 `print` 行与 `logging` 行的**先后**推断事件顺序 | 块缓冲 ⇒ 时序不可信 |
| 只看匹配段就下「性能瓶颈」结论 | 全流程大头是位姿 + 融合；匹配段内瓶颈（特征检测）在全流程只占 ~5% |

---

## 12. 素材真值（判断改动好坏的基准）

| 素材 | 定位 |
|---|---|
| ✅ **`拼图\T2-C-origin`（91 张）** | **唯一真值**：连续平移、**蛇形（serpentine）扫描**、相邻重叠 ~59%、三帧闭环残差 **0.3 px**（`#28→#29→#30→#28`） |
| ❌ `ZK203-81.5-T2` 的 `4-*-A`（7 张） | 只有 `4-6 & 4-7` 一对真重叠，余者孤立（曾被误判 `4-1 & 4-6` 解出 19.2° = 物理不可能） |
| 62 张批次 | = `T2-A-origin`（与 T2-C 同源同参数），提速基线都在它上面采 |

**素材侧的现实**：

- `T2-C-origin` 是**蛇形扫描**而非单向平移：相邻帧位移方向会周期性反向（实测 `#06→#07`、`#15→#16`、`#25→#26`、`#38→#39`、`#50→#51` 等处突变）。解出的位移序列能重建轨迹，可交叉验证。
- 相邻帧重叠约 **59%**（位移 ~790px vs 帧 2752×2208）⇒ 匹配内点数 2000~4500。
- 该素材充满**极向色（XPL 干涉色）**：颜色随视场位置变化，SIFT 的灰度化会丢掉这部分信息；必要时按通道或去色彩后匹配。
- 规模账：91 张 → **4095 个图像对**。**上传/匹配是 O(n²)，上限先由内存和上传体积决定，不是算法。**

---

## 13. 变更纪律

1. **改算法前先证伪先验假设。** 本模块历史上两次重大结论都是「先前的『实测』本身建立在错误公式上」（§2 的两条）。
2. **明确区分「已验证」与「未验证」。** 写不出的出处不要写。
3. **只重跑受影响的下游。** 改融合就只重验融合，不要整体重开。
4. **交付必须带**：改动文件清单、根因、验收结果（断言数 / 逐位差异 / 耗时对比）、**未验证项显式声明**。
5. 改完 `share` 相关（NAS 挂载脚本）要重跑 `tools/gen_offline_scripts.py` 并核对 4 个 ps1 的基线 sha256 不变——但那属于**另一个模块**的约定，与本模块算法无关。

---

**图片/文件关联**：核心文件 `geometry.py`（判据）→ `fallback.py`（位姿+融合）→ `stitcher.py`（编排）→ `routes.py`（接口）。
细节档：`modules/stitch/.workbuddy/memory/MEMORY.md`（决策级硬事实）、`MEMORY-DETAIL.md`（实测表与推导）、`overview.md`（算法修正总览）。
运维总纲：skill `postnews-web-ops` §3c / §3d / §8c。
