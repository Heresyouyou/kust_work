# GeoTranslate · Web 集成变化梳理

> **范围**：从「Web 最初创立」（Flask `:5555` + 单个 `index.html`，打开即翻译页）
> 到「门户化集成」（kust.work 门户工作台 + 模块化单体）之后发生的变化。
>
> **依据**：`README.md`（最初形态）、`GEO_TRANSLATE_ARCHITECTURE.md` §1–§6 / §13–§15、
> `PORTAL_README.md`、`portal/registry.py`、`modules/*` 路由、`web_app.py`、`index.html` 实测。
> 整理时间：2026-09-13

---

## 0. 一句话结论

**翻译核心链路一行未动**，变的是外面套了一层门户与模块体系：
翻译页从 `/` 让位到 `/translate`，`web_app.py` 从「翻译服务」升级为「宿主进程 + 翻译服务之一」。

---

## 1. 时间线

| 阶段 | 形态 | 标志 |
| --- | --- | --- |
| **Web 最初**（Geo-Mine-Translator） | Flask `:5555` + 单个 `index.html`，打开就是翻译页；`llama-server :8090` 单独起 | `README.md`；架构文档 §1–§12 |
| **2026-09-12 门户化** | `/` 变门户工作台，翻译页退到 `/translate`；新增 `portal/` 与 `modules/` | 架构文档 §13 |
| **2026-09-13 扩展** | 全局主题、期刊 60 天窗口、翻译队列、内网共享、队列卡死防护 | 架构文档 §14–§15 + 当日工作 |

---

## 2. 变化清单

### 2.1 入口与路由（最直观）

| 路径 | 最初 | 现在 |
| --- | --- | --- |
| `/` | 翻译页 | **门户工作台**（`portal/routes.py`） |
| 翻译页 | `/` | **`/translate`**（原内容一字未改） |
| `/today` `/daily` `/daily/archive` `/daily/poster/<date8>` | 无 | 今日早报 / 详情页 / 归档 / 海报（`modules/news`） |
| `/journals` | 无 | 期刊列表（`modules/journals`） |
| `/palette` | 无 | 科研配色（`modules/palette`） |
| `/archive` | 无 | 译文归档（`modules/archive`） |
| `/translate/queue` | 无 | 翻译队列（`modules/queue`） |
| `/share` | 无 | 内网共享（`modules/share`） |

新增 API：`/api/portal/health` `/api/news/latest` `/api/news/list` `/api/daily/archive`
`/api/daily/snapshot` `/api/journals/list` `/api/palette/list` `/api/archive/list`
`/api/translate/queue` `/api/share/*`（含 `.ps1` `.bat` `nas.exe` `ping` `info`）

**未变**：`/api/check` `/api/upload` `/api/status/<job_id>` `/api/cancel/<job_id>`
`/api/_debug` `/uploads/*` `/translated/*`

> **铁律**：页面路径可带前缀，**API 与静态资源必须留在根路径**。
> 这条约束是「翻译页前端 0 行改动」的前提。

### 2.2 目录结构

新增：

```
portal/                 # 门户层（2026-09-12 新增）
├── registry.py         # 入口注册表 —— 加入口只改这里
├── routes.py           # GET / · /api/portal/health · /theme.js
├── templating.py       # 纯字符串替换 + mtime 热重载（不用 Jinja2）
├── assets.py           # 静态资产白名单 + mtime 缓存
├── assets/theme.js     # 主题引擎（浅/深色，按昆明日出日落自动切换）
└── templates/portal.html
modules/                # 功能模块（每个模块一个 Blueprint）
├── __init__.py         # register_all() 统一注册
├── archive/ journals/ news/ palette/   # 2026-09-12 首批
└── queue/ share/                       # 2026-09-13 新增
```

新增工具脚本：`theme_dom_test.js` `queue_dom_test.js` `portal_journals_dom_test.js`
`tools/verify_queue_timeout.py` `tools/nas_tool.py` `tools/build_nas_exe.py`

### 2.3 `web_app.py` 的角色变化

| | 最初 | 现在 |
| --- | --- | --- |
| 定位 | 翻译服务 + 唯一页面 | **宿主进程**，翻译只是其中一个模块 |
| 页面 | 提供 `/` | 提供 `/translate`，并把 `/` 交给门户 |
| 注册 | — | `portal.init_portal(app, get_active=...)` + `modules.register_all(app, data_dir=..., get_queue=...)`（各自 try/except） |
| 兜底 | — | 门户注册失败时 `/` 自动回落成翻译页 |
| 新增能力 | — | 队列快照 `_queue_snapshot()`、活跃任务数 `_active_jobs()`、PID 单实例锁 |

### 2.4 前端：翻译页 0 行改动（关键设计）

实测 `index.html` 的 4 处 fetch 全是**根绝对路径**：

```
L830  fetch('/api/check', ...)
L844  fetch('/api/upload', ...)
L880  fetch('/api/status/' + jobId, ...)
L977  fetch('/api/cancel/' + S.currentJobId, ...)
```

所以页面从 `/` 搬到 `/translate` **不需要改任何前端代码**；
后端产出的 PDF URL（`/uploads/*` `/translated/*`）同理。

### 2.5 模板与渲染

| 页面 | 渲染方式 | 原因 |
| --- | --- | --- |
| 翻译页 | `render_template_string(INDEX_HTML)` | 沿用最初做法 |
| 门户与新模块 | `portal/templating.py` 纯字符串替换 | `render_template_string` 会把 `{` `}` 交给 Jinja2，而门户页 CSS/JS 大括号密集 |

`templating.py` 带 **mtime 热重载**：改 HTML 免重启（改 `.py` 仍必须重启）。

### 2.6 模块注册与容错

- 每个模块是独立 Blueprint，`modules/__init__.py` 里**逐个 try/except 注册**
  → 某个模块写坏只影响它自己的入口，不拖累翻译主服务
- 门户注册失败 → `/` 回落翻译页
- **加一个功能入口三步**：`registry.py` 追加一条 → `modules/` 下建 Blueprint
  → `modules/__init__.py` 的 `_MODULES` 加一行

### 2.7 依赖注入（集成带来的架构约束）

**问题**：队列数据在 `web_app.py` 内存里，模块需要它，但
`modules` **不能反向 `import web_app`**（`web_app → modules → web_app` 循环导入）。

**解法**：回调注入。

```python
portal.init_portal(app, get_active=_active_jobs)
modules.register_all(app, upload_dir=..., translated_dir=...,
                     data_dir=BASE.parent,          # 指向 PostNews 根目录
                     get_queue=_queue_snapshot)     # 文献队列只读快照
```

**方向约定**：门户侧**只读** PostNews 的数据文件，绝不写入。
唯一反向写入是 `daily_run.py` 增量写 `daily_push_state.json` 的 `latest_daily`。

### 2.8 任务队列的能力扩展

| 能力 | 最初 | 现在 |
| --- | --- | --- |
| 数据结构 | `jobs` dict + `QUEUE` + 单 worker | 同左，但**调度线程与工作线程分离** |
| 取消 | 无 | `cancel_event`，排队直接跳过、翻译中发信号 |
| 进度 | 简单百分比 | 阶段权重（解析 20% / 翻译 70% / 排版 8% / 输出 2%）+ 排队位次 |
| 对外可见 | `/api/status/<id>` | 新增 `/api/translate/queue` 快照 + 翻译队列页 |
| 卡死防护 | 无 | 软超时 10 min 发取消 + 硬超时 30 min 跳过 + 看门狗线程 |

> 快照只挑**可序列化白名单字段** —— `jobs` 里混着 `threading.Event`
> （`cancel_event`），直接 `jsonify(jobs)` 会抛 `TypeError`。

### 2.9 配置与常量

| 常量 | 位置 | 变化 |
| --- | --- | --- |
| `RETENTION_SECONDS` | `web_app.py` | **7200（2h）→ 48×3600（48h）** |
| `ARCHIVE_WINDOW_HOURS = 48` | `modules/archive/routes.py` | 新增（**展示**窗口） |
| `TRANSLATE_HARD_TIMEOUT = 30min` | `web_app.py` | 新增（硬超时） |
| `TRANSLATE_STALL_TIMEOUT = 10min` | `web_app.py` | 新增（无进度软超时） |

> **两者必须一致**：文件保留期必须 ≥ 归档窗口，否则 48h 窗口永远是空的
> （改造前 `RETENTION=2h` 就是这个 bug）。

### 2.10 主题

全局浅/深色：**日间（昆明日出后~日落前）浅色，夜间深色**，右上角三态按钮可手动覆盖。
`portal/assets/theme.js` 引擎（本地算日出日落，不联网）+ 6 个页面各 21 行 48 个变量。
接入只有一行 `<script src="/theme.js">`，**必须同步加载**（defer/async 会闪一下深色）。

### 2.11 启动与运维

- **启动顺序不变**：`llama-server :8090` + `Flask :5555`
- 新增：`_ensure_worker()`（队列 worker + 看门狗）、PID 单实例锁 `.flask_5555.pid`、
  waitress 生产 WSGI、mtime 热重载
- **规则**：改 `.py` 必须重启 Flask；改 HTML 走 mtime 热重载无需重启

### 2.12 测试体系（从无到有）

| 脚本 | 断言 | 覆盖 |
| --- | --- | --- |
| `theme_dom_test.js` | 73 | 主题变量与浅色漏配 |
| `queue_dom_test.js` | 35 | 翻译队列五种状态徽章、位次、进度、转义 |
| `portal_journals_dom_test.js` | 59 | 期刊双视图、60 天窗口、展开收起 |
| `tools/verify_queue_timeout.py` | 3 | 队列卡死防护（假任务秒级复现） |

---

## 3. 没变的部分（同样重要）

1. **翻译链路**：浏览器 → Flask → `run_babeldoc.py` → `babeldoc_translator.py`
   → `llama_http_engine.py`（`_GATE=Semaphore(4)`）→ `llama-server :8090`
   → BabelDOC 还原排版 → `mono.pdf` + `dual.pdf`
2. **4 个翻译 API** 与 2 个静态目录（`/uploads/*` `/translated/*`）路径与语义
3. **`index.html` 前端逻辑**（0 行改动）
4. **核心参数**：`qps=16`、`RateLimiter(8)`、`N_CTX=16384`、`N_PARALLEL=4`、
   `max_tokens cap=1024`、auto-split 阈值 2500 chars、SYSTEM_PROMPT 与术语注入
5. **启动方式**：`llama-server` + `py -3.12 web_app.py`，或 `start_translate.bat`

---

## 4. 当前全景

### 门户入口（12 个，可用 8 个）

| 分组 | 入口 | 路径 | 状态 |
| --- | --- | --- | --- |
| 资讯与推送 | 今日早报 | `/daily` | online（主推） |
| | 早报归档 | `/daily/archive` | online |
| | 推送日志 | — | 规划中 |
| 科研工具 | **文献翻译** | `/translate` | online（主推） |
| | 期刊列表 | `/journals` | online |
| | 科研配色 | `/palette` | online |
| | 翻译队列 | `/translate/queue` | online |
| | 译文归档 | `/archive` | online |
| | 地学术语库 | — | 规划中 |
| 系统 | 内网共享 | `/share` | online（主推） |
| | 运行监控 | — | 规划中 |
| | 文件柜 | — | 规划中 |

### 路由归属

```
web_app.py   /translate  /api/check  /api/upload  /api/status/<id>
             /api/cancel/<id>  /api/_debug  /uploads/*  /translated/*
portal/      /  /api/portal/health  /theme.js
modules/news     /today /daily /daily/archive /daily/poster[/<date8>]
                 /api/news/latest /api/news/list /api/daily/archive /api/daily/snapshot
modules/journals /journals            /api/journals/list
modules/palette  /palette             /api/palette/list
modules/archive  /archive             /api/archive/list
modules/queue    /translate/queue     /api/translate/queue（+ 旧入口 302 兼容）
modules/share    /share               /api/share/*
```

---

## 5. 关键铁律（集成过程中踩出来的）

1. **页面路径可带前缀，API 与静态资源必须留根路径** —— 翻译页前端 0 改动的前提。
2. **门户页不用 Jinja2** —— CSS/JS 大括号密集，走纯字符串替换。
3. **模块不能反向 `import web_app`** —— 循环导入，一律用回调注入。
4. **门户侧只读 PostNews 数据，绝不写入。**
5. **每个模块独立 try/except 注册** —— 不让任何一层成为翻译服务的单点故障。
6. **改 `.py` 必须重启 Flask**；改 HTML 走 mtime 热重载。
7. **归档窗口与文件保留期必须一致。**
8. **队列快照只取可序列化白名单字段**（`threading.Event` 不可序列化）。
9. **同文件并行发多个 Edit 会互相覆盖** —— 批量改同一文件必须串行。

---

## 6. 一句话给后来人

翻译系统是一颗**稳定的内核**，门户是后来长出来的**外壳**。
改外壳时记住：只要不动 `/api/check`、`/api/upload`、`/api/status/`、`/api/cancel/`
这四条根路径和 `index.html`，内核就不会被你改坏。
