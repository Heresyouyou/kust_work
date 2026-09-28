# kust_work · 地学科研工作台（Web 站）

> **本仓库是 Web 站的源码镜像，用于版本管理**，不是部署源。
> 运行时工作区在 `D:\PostNews\Translate`（Flask 必须从那里启动，导入路径才对得齐），
> 两边靠 `tools/sync_from_workspace.py` **单向同步**，避免手工拷贝漂移。
>
> ⚠️ **本仓库是 public**。同步脚本在写盘前会自动脱敏（内网主机名 / 共享名 / 私网 IP /
> 内网账号 → `<nas-host>` / `<share>` / `<lan-ip>` / `<nas-user>`）。
> 并**硬排除** `modules/share/`（含内网明文用户名与口令）—— 它永远不会出现在这里。

---

## 结论先行

| 你关心的 | 结论 |
|---|---|
| 这是什么 | 一个自托管的**单进程 Flask 门户**：1 套门户框架 + **11 个业务模块**，跑在 `0.0.0.0:5555`，数据全部留本地 |
| 架构核心 | **门户只做注册表与主题**；模块各自是 Blueprint，`modules/__init__.register_all()` 统一挂载；**重任务走同一条串行通道**（统一任务队列） |
| 能不能 clone 就跑 | **不能**。本仓库只收**文本源码 + 文档**（见 §1），vendor 与二进制资产需另行获取 |
| 改代码的正确姿势 | 改**工作区** → 跑 `sync_from_workspace.py` → 回本仓库 `git diff`/commit（见 §9） |

---

## 1. 收录边界（★ 先看这里）

本仓库是**源码基线**，不是可运行发行版。收录策略由 `tools/sync_from_workspace.py`
的 `ITEMS` / `SKIP_*` 常量定义，每次同步都会打印审计摘要（跳过多少项、为什么）。

| | 内容 | 说明 |
|---|---|---|
| ✅ **收录** | 门户框架、11 个模块的 `.py` / 模板 / 前端源码、运维脚本、文档、测试 | 即「Web 架构」本身 |
| 🚫 **永不收录** | `modules/share/` | 含内网 NAS 明文用户名/口令（`__init__.py` 里直接拼 `net use /user:…`） |
| 🚫 **运行时数据** | `*/uploads/`、`*/results/`、`*/data/`、`modules/stitch/.workbuddy/` | 上传原件、拼接结果、打卡记录、开发产物 |
| 🚫 **生成物 / 第三方 / 二进制** | `modules/story/docs/`（汇报页图）、`modules/reader/assets/vendor/`（pdf.js 9.2 MB）、各类 png/exe/ttf/wasm | 体积大、可再生 |

> **因此克隆后：** PDF 阅读器缺 `vendor/pdf.js`、story 缺页图与品牌图，都需自行补。
> 这是有意的取舍 —— 换来一个干净、可 diff、能长期维护的源码基线。
> 需要「完整可跑」的镜像时，改 `ITEMS` 并把 `SKIP_SUFFIX` / `SKIP_DIRS` 放宽即可。

`uploads/`、`translated/`、`*.pem`、`*.pdf` 等另在 `.gitignore` 里做了**纵深防御**
（即使有人手工拷贝也不会被误提交）。

---

## 2. 架构总览

```
                    ┌──────────────── web_app.py（唯一进程，0.0.0.0:5555）────────────────┐
浏览器 ──▶          │                                                                      │
                    │  portal/          门户框架：registry（卡片）/ routes / templating       │
                    │     ├ registry.py  ← 加功能入口只改这里（MODULES 列表）                  │
                    │     ├ routes.py    / 与 /theme.js 与 /api/portal/health                 │
                    │     └ assets/theme.js  主题引擎（日出日落本地算，不联网）                │
                    │                                                                      │
                    │  modules/__init__.py  register_all(app, submit_task=…, register_runner=…)│
                    │     ├─ 11 个业务模块（各自 Blueprint，独立注册互不拖累）                  │
                    │     └─ 注入 4 个句柄：submit_task / get_task / task_lane / register_runner│
                    │                                                                      │
                    │  ★ 统一任务队列（内核就在 web_app.py，不在 modules/queue）                │
                    │     jobs{} + QUEUE(FIFO) + _queue_worker(单线程) + TASK_LANE=Sem(1)     │
                    │     文献翻译 & 图像拼合 → 同一条串行通道（都吃 GPU/内存，并跑会互抢）      │
                    └──────────────────────────────────────────────────────────────────────┘
                                              │
                        ┌─────────────────────┴─────────────────────┐
                        ▼                                           ▼
              D:\PostNews（只读数据源）                     NAS：\\<nas-host>\<share>\
              期刊台账 / 早报状态 / news.json                译文归档 · 拼图结果 · 早报海报
```

三条设计主线：

1. **门户不做业务**，只做注册表 + 主题 + 静态资源；每个模块自带 `routes.py` + `templates/`。
2. **模块不反向 import `web_app`**（会循环导入）。需要队列能力时，由 `register_all(...)`
   **单向下发句柄**注入。
3. **重任务单行道**：翻译与拼合都吃 GPU/内存，必须排进同一条串行通道，否则互相抢资源。

---

## 3. 站点地图

页面（`online` 状态）：

| 路径 | 说明 | 归属 |
|---|---|---|
| `/` | 门户工作台：功能卡片墙 + 今日早报动态绑定 | `portal` |
| `/translate` | 文献翻译：上传英文地学 PDF → 中文对照译文 | `web_app.py` + `index.html` |
| `/journals` | 期刊列表：双视图（近 60 天 / 主题归档）+ 页顶「今日更新」 | `modules/journals` |
| `/archive` | 译文归档：近 48 h，按论文分组 | `modules/archive` |
| `/today` → `/daily` | 今日早报（`/today` 是 302 跳转，**预期行为**） | `modules/news` |
| `/daily/archive` | 早报归档（按日期回看） | `modules/news` |
| `/queue` | **任务队列**：翻译 + 拼合共用一条串行通道 | `modules/queue` |
| `/reader` | 自研 canvas PDF 阅读器（原文/译文归一化锚点同步滚动） | `modules/reader` |
| `/palette` | 科研配色：37 套权威色板 | `modules/palette` |
| `/triangle` | 三角计算器（纯前端无依赖） | `modules/triangle` |
| `/story` → `/story/<sid>` | 汇报说明书（`STORY.md` 的可视化网页版）；无 sid 时 302 到第一份 | `modules/story` |
| `/story/deck` → `/story/<sid>/deck` | 网页放映版 PPT（页图是 PNG，不是 HTML 排版） | `modules/story` |
| `/story/admin` | 汇报管理后台（发布 / 编辑 / 重建页图 / 移除） | `modules/story` |
| `/stitch` | 显微图像拼合（**无尾斜杠**，`/stitch/` 会 404） | `modules/stitch` |
| `/tasks` | 任务打卡（证书/赛事看板，多角色 + 联网共享） | `modules/tasks` |
| `/share` | 内网 NAS 挂载脚本 | `modules/share`（**不在本仓库**） |

常用接口：

| 接口 | 用途 |
|---|---|
| `GET /api/portal/health` | 门户健康（`active==0` 才是安全重启窗口） |
| `GET /api/queue` | 统一任务队列快照（`doc.stats.by_kind` / `queue_size` / 工单列表） |
| `POST /api/cancel/<job_id>` | 取消任务（翻译与拼合共用；排队期取消需补删上传临时件，见 §5） |
| `GET /api/journals/list` | 期刊页两个视图数据 |
| `GET /api/archive/list` | 归档列表 |
| `GET /api/news/latest` · `/api/news/list` | 当日早报 / 历史早报 |
| `GET /api/palette/list` | 色板数据 |
| `POST /api/upload` · `GET /api/status/<job_id>` · `POST /api/cancel/<job_id>` | 翻译任务上传 / 轮询 / 取消 |
| `GET /api/stitch/health` | 拼合加速栈自述（`accel`：`use_gpu`/`coarse_screen`/`warm_workers`/`warp_roi`） |
| `POST /api/stitch/submit` · `GET /api/stitch/task/<id>` | 拼合异步提交 / 查询 |

---

## 4. 目录结构

```
kust_work/
├── web_app.py               ★ Flask 入口：路由、PID 单实例锁、缓存头、统一任务队列内核
├── index.html               翻译页（上传 + 进度 + 双语预览）
├── bilingual.py             双语对照版：lr / tb / alt 三形态拼接，原子写出
├── nas_archive.py           NAS 文献仓库：三版本归档、JSON 索引、检索回捞、本地回收
├── portal/                  门户框架
│   ├── registry.py          ★ 功能模块注册表（加卡片只改这里）
│   ├── routes.py            / 与 /theme.js 与 /api/portal/health
│   ├── templating.py        极简模板渲染 + mtime 热重载（刻意不用 Jinja2）
│   ├── assets.py            静态资源响应（mtime 缓存、Content-Type 修正）
│   ├── assets/theme.js      ★ 主题引擎（日出日落本地计算）
│   └── templates/portal.html
├── modules/                 业务模块（各自 Blueprint）
│   ├── __init__.py          register_all()：挂载全部模块 + 注入队列句柄
│   ├── journals/  archive/  news/  palette/  queue/      资讯 · 文献类
│   ├── reader/  triangle/  palette/                      科研工具类
│   ├── stitch/  story/  tasks/                           专业模块
│   └── (share/ 不入库)
├── docs/                    文档
│   ├── ARCHITECTURE.md      翻译系统架构（硬件/依赖/迁移）
│   ├── PORTAL.md            门户交付说明
│   ├── PORTAL_DESIGN.md     门户化改造设计
│   ├── CODE_REFERENCE.md    ★★ 全量 API + 数据流 + 铁律（唯一权威参考）
│   └── WEB_INTEGRATION_CHANGES.md
├── tools/
│   ├── sync_from_workspace.py  ★ 工作区 → 本仓库单向同步（含发布脱敏与收录清单）
│   ├── supervisor.py           服务守护
│   ├── restart_web.py          安全重启（等 active==0 窗口）
│   ├── nas_reaper.py           本地回收：超 48h 的三版本搬到 NAS 后删本地
│   ├── schedule/               ★ 本地定时任务（Windows 任务计划程序）
│   ├── reader_dev_server.py    reader 模块调试服务
│   ├── tasks_build/            tasks 前端手写件的构建源
│   └── npg_check/              tasks 页面视觉巡检
├── requirements-lock.txt    依赖锁定 + 不可升级红线
├── theme_dom_test.js        主题引擎 DOM 测试（72/73）
├── queue_dom_test.js        任务队列页 DOM 测试（39/39）
├── portal_journals_dom_test.js  期刊页 DOM 冒烟（需页面快照与接口 JSON）
└── news_daily_test.py       早报海报取图回归（Flask test_client，无需浏览器）
```

---

## 5. ★ 统一任务队列（翻译 + 拼合共用一条串行通道）

**为什么要合并**：文献翻译（llama.cpp，GPU/CPU 重载）与图像拼合（SIFT + 多频带融合，
GPU/内存重载）**都吃重资源**。两条互不相识的通道让两个重活并跑，就是互相抢显存与内存、
双双变慢。故合并成**一条**。

**内核在 `web_app.py`**（不在 `modules/queue/` —— 那个模块只是**展示页**）：

| 组件 | 作用 |
|---|---|
| `jobs` 表 | 工单状态（`queued` / `translating` / `done` / `failed` / `cancelled`） |
| `QUEUE` | FIFO 队列 |
| `_queue_worker` | **单**消费线程 |
| `TASK_LANE = Semaphore(1)` | 串行道；同步入口（如 `/api/stitch/run`）也要拿它 |
| `kind` | `'translate'` / `'stitch'`，决定由哪个执行体跑 |

### 注入约定（★ 加新任务种类时最容易漏的一步）

模块**不反向 import `web_app`**（循环导入），由 `register_all(...)` 单向下发四个句柄：

| 句柄 | 作用 |
|---|---|
| `submit_task(kind, name, payload=…, job_id=…)` | **把工单塞进队列** |
| `get_task(job_id)` | 查状态 / 位次 |
| `task_lane` | `Semaphore(1)`，同步入口与队列消费线程共用 |
| **`register_runner(kind, fn)`** | **把「谁来执行」交给队列** |

> 🐞 **`submit_task` 只管入队，队列还得知道谁来跑。** 漏了 `register_runner` 的症状是
> **「入了队但没人执行」**：`/api/stitch/submit` 明明返回 `queued:true`，队列却立刻把任务标
> `failed`，错误是 `未注册的任务种类（kind=stitch）`。
>
> 加一个 kind 要串**四层**：
> `web_app.register_task_runner` → `modules/__init__.register_all(register_runner=…)`
> → 模块 `init_xxx(register_runner=…)` → `register_runner('kind', fn)`。

### 执行体的两条硬约定

1. **串行道归属**：走队列时调度线程在 join 本任务期间**已经持着** `TASK_LANE`，
   执行体**不能再 acquire**（自锁 ⇒ 只能等硬超时被判失败）。
   只有兜底自跑（`ctx.lane_held == False`，即未接入队列时）才自己拿锁。
2. **排队期被取消**的任务执行体**从未运行** ⇒ `finally` 不触发 ⇒ 上传临时件残留，
   需 `_drop_task_uploads(task_id)` 补删。

---

## 6. 模块清单

门户注册表共 **18 条**（含 4 条 `coming` 占位），其中**有代码的 11 个**：

| 模块 | 入口 | 要点 |
|---|---|---|
| `journals` | `/journals` | 双视图；**获取时间的唯一权威是 `journal_ledger.json` 的 `first_seen`**，别拿文件 mtime（会被整体重写刷新成今天，文章永不老化） |
| `archive` | `/archive` | 近 `ARCHIVE_WINDOW_HOURS=48` 小时译文，按论文分组；不做服务端下载封装 |
| `news` | `/today` `/daily` | 数据源 `daily_push_state.json` 的 `latest_daily`；海报主存 NAS、本地兜底 |
| `palette` | `/palette` | 37 套色板 / 350 色值，**全部有出处**；扩充必须跑 `_build_palettes.py`，**绝不手写 hex** |
| `queue` | `/queue` | 统一任务队列**展示页**；内核在 `web_app.py`（见 §5）；旧 `/translate/queue` 保留重定向 |
| `reader` | `/reader` | 自研 canvas PDF 内核（`assets/kpdf/`），原文/译文按归一化锚点同步滚动；**依赖 vendor/pdf.js（未收录）** |
| `stitch` | `/stitch` | 显微图像拼合。**载物台只有平移 ⇒ 旋转自由度必须钉死**；SIFT + 几何判据 + 最小二乘位姿 + 多频带融合。提速开关见下表 |
| `story` | `/story` `/story/deck` `/story/admin` | 汇报说明书 + 网页放映版 + 管理后台；页图是 PNG（未收录），提词稿在 `docs/<sid>/`（未收录） |
| `tasks` | `/tasks` | 证书/赛事打卡看板，多角色 + 联网共享，数据落 `data/store.json`（未收录，`ensure_demo()` 会自建示例角色） |
| `triangle` | `/triangle` | 三角函数计算器，纯前端无依赖（新模块的**最简样板**） |
| `share` | `/share` | 内网 NAS 挂载脚本。**含明文凭据，永不入库** |

### `stitch` 的提速开关（均为「逐位相同、0 像素差」优化）

| 开关 | 位置 | 效果 |
|---|---|---|
| 粗筛 + GPU 距离阵 | `StitchConfig.coarse_screen` / `use_gpu` | 6.21×（305.8s→49.3s），接受边逐条不变 |
| 预热并行 ×6 | `StitchConfig.warm_workers` | 预热 46.2s→32.8s；**W>8 反慢**（SIFT 内部已多线程） |
| 内容 warp 输出原点 ROI | `fallback.WARP_ROI` | 融合 159.6s→101.4s；**只对内容（LANCZOS4）**，掩膜（NEAREST）**不可** ROI |
| 位姿残差向量化 | `fallback.POSE_VEC` | 107.6s→6.3s（17×），只改 residuals、不碰 scipy ⇒ 迭代序列逐位相同 |

合计 **248.3s → 165.8s（1.50×）**。判据：`/api/stitch/health` 的 `accel` 会自述
`use_gpu / coarse_screen / warm_workers / warp_roi` —— **先看它，再判断到底有没有生效**。

---

## 7. 门户框架与主题引擎

**`registry.py`** —— 加一个新功能入口 = 往 `MODULES` 追加一条 dict，不用动 `portal.html`，
也不用动 `routes.py`：

| 字段 | 含义 |
|---|---|
| `id` | 唯一标识；前端靠 `data-mod="<id>"` 定位卡片 |
| `group` | 分组名，同组聚合，按首次出现顺序排列 |
| `name` / `desc` | 卡片标题 / 一句话说明（desc 建议 ≤ 26 字） |
| `url` | 入口路径；`status='coming'` 时可留 `None` |
| `tag` | 右上角角标（可被前端 JS 动态改写） |
| `status` | `online` 可点击 / `beta` 可点击（琥珀角标）/ `coming` 置灰 |
| `accent` | `True` = 主推卡片（绿色描边高亮） |
| `live` | `True` = 卡片内容由前端 JS 动态绑定（如今日早报的飞书链接） |

**`templating.py`** —— 刻意**不用 Jinja2**。门户页 CSS/JS 里大括号密集，走 Jinja2 会到处冲突，
所以是纯字符串替换 + **mtime 热重载**（改 HTML 刷新即生效，**改 `.py` 必须重启**）。

**`assets/theme.js`** —— 主题引擎三个要点：

1. **日间浅色 / 夜间深色，按昆明真实日出日落自动切换**（NOAA 简化式本地计算，
   `lat 25.0389 / lon 102.7183 / tz +8`，**不联网、不调 API**）。
2. 右上角三态按钮 `auto / light / dark`，选择存 `localStorage['kust.theme.mode']`。
3. 浅色变量由 JS 注入 `:root[data-theme="light"]`（权重高于各页面的深色 `:root` 兜底），
   接入只需一行 `<script src="/theme.js"></script>` —— **必须同步加载**，
   加 `defer`/`async` 会闪一下深色。

> ★ **模块 UI 一律走 theme.js + CSS 变量，禁止硬编码颜色**（否则浅色模式会半深半浅，
> `theme_dom_test.js` 就是为抓这类问题写的）。

---

## 8. 数据契约（只读）

门户**只读** `D:\PostNews` 下的数据文件，绝不写入。唯一反向写入：
`daily_run.py` 增量写 `daily_push_state.json` 的 `latest_daily`。

| 文件 | 用途 |
|---|---|
| `journals.json` | 期刊元数据（国内 6 + 国际 8） |
| `journal_ledger.json` | ★ 获取台账：每篇**首次**被本地获取的时间（**删了会让获取时间全部重置为今天**） |
| `journal_updates.json` | 最近一轮更新的文章 |
| `journal_article_buffer.json` | 历史累积文章缓存 |
| `journal_topic_archive.json` | 主题相关文献回溯归档 |
| `journal_title_zh.json` | 英文标题的中文小字翻译缓存（门户只读） |
| `daily_push_state.json` · `news.json` | 今日早报 |

---

## 9. ★ 同步工作流（本仓库的日常）

```bash
cd D:\PostNews\kust_work

python tools/sync_from_workspace.py --check   # ① 先看差异，不写盘
python tools/sync_from_workspace.py           # ② 正式同步（含脱敏）
python tools/sync_from_workspace.py --show-skip  # ③ 逐条核对被跳过的文件

git add -A && git commit -m "..."             # ④ 提交
git push origin main
```

同步会打印审计摘要（`已同步 N 项 · 无变化 N 项 · 脱敏 N 项 · 跳过 N 项（分类计数）`）。

**三条纪律：**

1. **改代码只改工作区**，本仓库不手工编辑 —— 任何手改都会在下次同步被覆盖，或制造漂移。
   唯一例外是 `README.md` / `.gitignore` / `sync_from_workspace.py` 这类**只属于本仓库**的文件。
2. **提交前必须 `git status` 复核**，确认没有 `modules/share/`、`*.pem`、`__pycache__`、
   运行时数据混进暂存区。
3. **新增模块要同时加进 `ITEMS`**（目录级条目，之后模块内新文件会自动收录）。

---

## 10. 运行

**前置**：Python 3.12（本机 `D:\Python 3.12.4\python.exe`）+ `flask` / `waitress`。
门户、归档、期刊、色卡、早报、阅读器、三角、任务打卡 **不需要额外依赖**。
`/translate` 的完整功能另需 llama.cpp + GGUF 模型（约 7 GB）+ BabelDOC（**不在本仓库**）。

```bash
cd D:\PostNews\Translate
"D:\Python 3.12.4\python.exe" web_app.py
```

| 事项 | 说明 |
|---|---|
| 启动方式 | 必须**从 `D:\PostNews\Translate` 启动**，导入路径才对得齐 |
| 单实例锁 | `.flask_5555.pid`：PID 活着就拒绝重复启动；PID 死了锁还在会自动清理 |
| 重启 | **优先用 `python tools/restart_web.py`**（自带安全检查 + 等守护拉起） |
| 生效规则 | 改 `.py` **必须重启**；改 HTML/JS 直接刷新浏览器即可 |
| 安全重启窗口 | `GET /api/portal/health` 返回 `active==0` 时才是安全窗口 |

### 环境变量

| 变量 | 用途 | 缺省 |
|---|---|---|
| `NAS_PDF_ROOT` | NAS 文献仓库根目录（UNC 或盘符） | 依次探测 `S:/Translator_PDFs` → UNC |
| `NAS_TTL_HOURS` | 本地 PDF 保留时长，超过后回收到 NAS | `48` |
| `POSTNEWS_PYTHON` | 定时任务用的 `python.exe` 绝对路径 | 自动探测 |
| `NO_PROXY` | **必须含** `127.0.0.1,localhost`，否则本机回环请求走代理 → 翻译 404 | 建议显式设置 |

> 代码里的 `<nas-host>` / `<share>` / `<lan-ip>` / `<nas-user>` 都是**同步时自动替换的占位符**，
> clone 后必须自己配 `NAS_PDF_ROOT` 才能用 NAS 相关功能。

### 依赖红线（`requirements-lock.txt`）

| 包 | 版本 | 为什么钉死 |
|---|---|---|
| `pdfminer.six` | `==20250416` | **必须 < 20250506**。BabelDOC 0.2.33 直接读写解释器上的 `self.ncs`，而 pdfminer 自 20250506 把它搬到 `graphicstate` 且不再初始化 ⇒ 解析阶段直接 `AttributeError`，任务连翻译都没开始 |
| `requests` | `==2.34.2` | 对**本机回环**必须 `trust_env=False`（`llama_http_engine._make_session()` 已统一处理，**改代码时勿删**），否则代理会把 `127.0.0.1:8090` 的请求转成绝对 URI 请求行 → llama.cpp 按 path 匹配 → 404 → 静默把原文当译文 |

---

## 11. 测试

自建轻量 DOM 垫片，**不装 jsdom**，纯 Node 跑；Python 侧用 Flask `test_client`。

```bash
node theme_dom_test.js                       # 主题引擎：72/73（见 §13 已知问题）
node queue_dom_test.js                       # 任务队列页：39/39
node portal_journals_dom_test.js <page.html> <api.json>   # 期刊页冒烟（需先取页面与接口）
"D:\Python 3.12.4\python.exe" news_daily_test.py          # 早报海报取图回归（含 NAS 降级）
```

> ★ 前两个脚本按 `__dirname/portal/...`、`__dirname/modules/...` 解析被测文件，
> 所以它们**必须在仓库根**（与工作区同构）——这就是它们没被放进 `tests/` 的原因。

`stitch` 模块另有生产入口测试（在工作区 `modules/stitch/_tests/`）：
`regress.py`（72 断言，几何/融合逐位回归）与 `test_routes.py`（路由，exit=0）。

---

## 12. 安全边界（public 仓库）

| 项 | 处理 |
|---|---|
| 内网主机名 / 共享名 / 私网 IP / 内网账号 | 同步时按 `REDACTIONS` **模式匹配**替换为占位符（规则本身也只写通配符，不写真实值） |
| `modules/share/` | **硬排除** + `.gitignore` 双保险 |
| `*.pem` / `*.key` / `*_token.txt` / `*_basicauth.txt` | `.gitignore` 已忽略 |
| 运行时用户数据（打卡记录 / 汇报内容 / 上传件 / 拼图结果） | 同步跳过 + `.gitignore` 忽略 |

> ⚠️ 脱敏是**模式匹配**，不是万能的。新增内网地址写法时，请同步更新 `REDACTIONS`
> 并跑一次 `--check` + `git diff` 人工复核。

---

## 13. 已知问题

| 项 | 现象 | 根因 / 建议 |
|---|---|---|
| `theme_dom_test.js` 1 项红 | `浅色覆盖了深色的全部 49 个变量` 失败 | `--info`（深色 `#4a90d9`）在 `portal.html :root` 有定义，但 `theme.js` 的浅色块没覆盖它（`--radius`/`--topbar-h` 是几何量，已被 `GEOMETRIC` 正确豁免）。修法：给浅色块补一个对比度足够的 `--info`。**这是 UI 取值决策，留待确认** |
| 本仓库不可直接运行 | 缺 `reader/assets/vendor/pdf.js`、story 页图、品牌图 | 见 §1 收录边界，属有意取舍 |
| `stitch` 旋转兜底仍有 78 条可救的真重叠边未救 | 召回未到顶 | 有意保守（旋转路径自由度大，需单独标定 + 对抗压测） |

---

## 14. 与其它仓库的关系

| 仓库 | 职责 |
|---|---|
| **kust_work**（本仓库） | Web 站源码：门户框架 + 11 个模块 + 双语版 / NAS 归档 + 运维脚本 + 测试 + 文档 |
| `Geo-Mine-Translator` | PDF 翻译引擎：llama.cpp 接入、BabelDOC、术语表 |
| `D:\PostNews`（非 git） | 期刊抓取管线 + 数据文件（台账、归档、早报状态） |

---

## 15. 常见问题

| 症状 | 排查 |
|---|---|
| 页面能打开但数据少 / 不对 | 大概率**进程跑的旧代码** → 看 §10 生效规则，重启 Flask |
| 刷新后改的 JS 不生效 | `after_request` 覆盖了 `Cache-Control`，查 `portal/assets.py` |
| 浅色模式下有元素发黑 | 漏配变量 → `node theme_dom_test.js` |
| 期刊窗口里文章永远不老化 | 别用文件 mtime 当获取时间，必须用 ledger 的 `first_seen`（§6） |
| 复制色值去画图画出来不一样 | 色值必须从权威源取，见 §6 的 palette 铁律 |
| 拼合任务入队后立刻 failed | 十有八九是**漏注入 `register_runner`**（§5） |
| `/stitch/` 返回 404 | 该页**无尾斜杠**，用 `/stitch` |
