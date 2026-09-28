# PostNews / GeoTranslate · 本地架构代码工作参考

> **定位**：这是本机部署形态的**唯一权威代码参考** —— 进程拓扑、目录地图、**全量 API**、
> **数据传输流**、以及改代码前必须遵守的铁律。
>
> **与其他文档的分工**（避免各写一份互相矛盾）：
>
> | 文档 | 管什么 |
> |---|---|
> | **本文 CODE_REFERENCE.md** | 代码怎么组织、接口长什么样、数据怎么流、**改动纪律** |
> | `GEO_TRANSLATE_ARCHITECTURE.md` | 翻译管线的深度专题（性能模型、Prompt、迁移清单） |
> | `PORTAL_README.md` | 门户层交付说明与运维（怎么访问、怎么加入口） |
> | `WEB_INTEGRATION_CHANGES.md` | "从单独 translate 到 Web 集成"的**历史变化**梳理 |
> | `KUSTWORK_PORTAL_DESIGN.md` | 门户化改造的**方案选型**（含未采用的方案 C/D） |
> | `PROXY_404_ROOTCAUSE.md` | 2026-09-13 事故复盘（代理 404 + pdfminer 版本） |
> | `requirements-lock.txt` | 依赖版本锁定与**不可升级**红线 |
>
> **基准**：2026-09-13 23:10 实盘代码（已核对路由/常量/行数）。
> 全站 **42 条路由**（门户 3 + 翻译核心 9 + 六个模块 30）。

---

## 目录

1. [进程拓扑与访问链路](#1-进程拓扑与访问链路)
2. [目录与文件地图](#2-目录与文件地图)
3. [全量 API 清单](#3-全量-api-清单)
4. [数据传输流](#4-数据传输流)
5. [数据文件与读写方向](#5-数据文件与读写方向)
6. [铁律（改代码前必读）](#6-铁律改代码前必读)
7. [本次修复的两颗地雷（重点）](#7-本次修复的两颗地雷重点)
8. [故障排查手册](#8-故障排查手册)
9. [回归测试与验证工具](#9-回归测试与验证工具)
10. [常见变更操作指南](#10-常见变更操作指南)
11. [环境与依赖](#11-环境与依赖)

---

## 1. 进程拓扑与访问链路

### 1.1 三个进程 + 一条转发

```
                        ┌──────────────────────────────────────┐
   同事的浏览器  ──────▶ │  kust.work : 80                      │
   本机浏览器           │  （NAS 域名解析 → 本机，再经 Windows │
                        │   端口代理 svchost 转发到 5555）     │
                        └───────────────┬──────────────────────┘
                                        ▼
                        ┌──────────────────────────────────────┐
                        │  Flask + waitress   PID 见 .flask_5555.pid │
                        │  0.0.0.0 : 5555   threads=8          │
                        │  web_app.py                          │
                        │   ├─ portal/      → 门户 + /theme.js │
                        │   └─ modules/     → 6 个功能模块     │
                        └───────┬───────────────────┬──────────┘
                                │                   │
                    只读读取    │                   │ HTTP（trust_env=False！）
                   D:\PostNews\*│                   ▼
                                │      ┌────────────────────────────┐
                                │      │ llama-server.exe           │
                                │      │ 127.0.0.1 : 8090           │
                                │      │ Qwen3.5-4B-Q4_K_M (CUDA)   │
                                │      │ --parallel 8 --kv-unified  │
                                │      └────────────────────────────┘
                                ▼
                     journal_ledger.json / news.json / output/*.png …
```

**要点**

- **Flask 不是翻译服务本身**，它同时是门户、是模块宿主、也是**任务队列**（文献翻译 + 图像拼合共用一条串行通道）的宿主。
- **llama-server 是独立进程**，Flask 通过 HTTP 调它；它挂掉由引擎的 watchdog 负责拉起。
- **门户与模块都可能失败但服务不垮**：`web_app.py` 用两个独立 `try/except` 包住
  `portal.init_portal()` 与 `modules.register_all()`，任一抛异常只降级自身。
- **`kust.work:80` 不是 Flask 直接监听的端口**。链路是 `kust.work` →（端口代理
  `svchost.exe`，PID 会变）→ `127.0.0.1:5555`。所以 `netstat` 里看到 `:5555`
  有两个监听（`0.0.0.0:5555` = Flask，`<lan-ip>:5555` = 系统代理）是正常的。

### 1.2 进程启动方式与约束

| 进程 | 启动命令 | 备注 |
|---|---|---|
| llama-server | 由 `llama_http_engine.start_server()` 拉起，或用户跑 `start_translate.bat` | 参数见 §1.3 |
| Flask | `python web_app.py`（waitress `0.0.0.0:5555`，threads=8） | 有 PID 锁，重复启动会被拒 |
| 端口代理 | Windows 系统行为，无需手工启动 | 提供 `kust.work:80 → 5555` |

**⚠️ 在托管会话里启动常驻服务**：必须用 `run_in_background` 方式启动（受管后台任务）。
用 `(cmd &)`、`nohup` 之类把进程丢到 shell 之外**不可行** —— 进程会随那次工具调用的
shell 退出被回收（实测 6 秒后还活着，下一次调用就已经 502 了）。

**⚠️ 改 `.py` 必须重启 Flask**（`PID 锁 .flask_5555.pid` 会拦住重复启动）。
改 `.html` / `.js` **不用**重启（模板与资产都按 mtime 缓存热重载）。

### 1.3 llama-server 参数（必须与引擎常量一致）

引擎 `start_server()` 实际下发的参数：

```
llama-server.exe
  -m  models-gguf/Qwen3.5-4B-Q4_K_M.gguf
  -ngl -1                       # 全部层上 GPU
  -c  16384                     # ★ unified-KV 下是**全局** KV pool，不是 per-slot
  -t  8
  -b  2048  -ub 512  -cb
  --parallel 8                  # ★ 并发 slot 数，必须 == 引擎 N_PARALLEL
  --kv-unified                  # 所有 slot 共享上面那个 pool
  --cache-prompt --reasoning off --cache-idle-slots
  --host 127.0.0.1 --port 8090
```

引擎侧对应常量（`llama_http_engine.py`）：

```python
N_CTX       = 16384     # 全局 pool
N_PARALLEL  = 8         # 并发 slot（2026-09-13 由 4 调到 8）
CTX_PER_SLOT = N_CTX    # unified-KV：共享，所以这里不是 16384/8
```

> **旁注（一处已知不一致）**：用户自己的 `start_translate.bat` 里写的是
> `-c 4096 -t 8 -rea off --port 8090`（旧参数、无 `--parallel`）。
> 两种启动方式都能跑，但**并发档位不同**。若发现吞吐与文档不符，
> 先确认 llama-server 是谁拉起来的：`Get-CimInstance Win32_Process` 看命令行。

---

## 2. 目录与文件地图

```
D:\PostNews\                     ← 数据根（门户只读它，绝不写入）
│   journals.json                期刊元数据（国内6 + 国际8）
│   journal_ledger.json          ★ 获取台账（first_seen 唯一权威时间源）
│   journal_updates.json         最近一轮更新的文章
│   journal_batch_pending.json   待批量推送的一轮（带批次 date）
│   journal_article_buffer.json  历史累积缓存（字段简）
│   journal_doc_urls.json        期刊飞书速递文档链接
│   journal_topic_archive.json   近 N 月主题回溯归档
│   journal_title_zh.json        英文标题→中文小字 缓存（离线批产出）
│   journal_title_zh_status.json 标题翻译状态（队列页读它）
│   daily_push_state.json        latest_daily 键（daily_run.py 落盘）
│   news.json                    当日新闻条目
│   output/daily_poster_*.png    日报海报
│   daily_archive/YYYYMMDD.json  早报日期快照（访问 /daily 时落盘）
│   daily_run.py                 每日抓取+推送主脚本（写上面若干文件）
│   journal_*.py                 期刊抓取/过滤/回溯/基线
│   journal_title_zh.py          标题中文翻译**离线**批（写 title_zh*.json）
│   _build_palettes.py           色卡构建（写 Translate/modules/palette/palettes.json）
│   _palette_src/                色卡权威源存档
│   .workbuddy/memory/           工作记忆（YYYY-MM-DD.md / MEMORY.md）
│
└── Translate\                   ← 服务根（Flask 在这里跑，cwd 也是这里）
    │   web_app.py               ★ Flask 主入口：翻译核心 + 门户/模块装配（881 行）
    │   index.html               翻译页模板（含全部 4 处 fetch）
    │   llama_http_engine.py     ★ llama-server HTTP 引擎（605 行）
    │   babeldoc_translator.py   ★ BabelDOC 翻译器适配（175 行）
    │   run_babeldoc.py          ★ 翻译入口/配置（287 行，CLI 与 web 共用）
    │   bilingual.py             ★ 双语拼接版生成（lr/tb/alt 三形态，原子写出）
    │   llama_cli_engine.py      降级引擎（HTTP 不可用时用）
    │   start_translate.bat      用户侧一键启动（llama-server + Flask）
    │   start_all.bat            仅拉起 Flask 的薄壳
    │   requirements-lock.txt    ★ 依赖锁定与不可升级红线
    │   .flask_5555.pid          Flask 单实例锁
    │   _probe_data.jsonl        引擎埋点（每次 HTTP 一行 JSON）
    │   _babel_callback.log      BabelDOC 进度回调流水
    │   uploads/                 上传原件（保留 48h）
    │   translated/              BabelDOC 产物（保留 48h）
    │      └── _backup_pre_fix/  事故前的坏样本（分析对照用）
    │   portal/                  ★ 门户层（无业务逻辑）
    │   │   __init__.py          对外只导出 registry + routes
    │   │   registry.py          ★ 功能入口注册表（12 条，8 条在线）
    │   │   routes.py            门户 3 条路由 + 卡片渲染
    │   │   templating.py        极简模板（读文件 + 占位符替换，mtime 缓存）
    │   │   assets.py            静态资产白名单（只有 theme.js）
    │   │   templates/portal.html
    │   │   assets/theme.js      ★ 主题引擎（319 行）
    │   modules/                 ★ 功能模块（每模块一个 Blueprint）
    │   │   __init__.py          register_all()：逐个 try/except 注册
    │   │   archive/  译文归档
    │   │   journals/ 期刊列表
    │   │   news/     今日早报 + 归档
    │   │   palette/  科研配色（含 palettes.json）
    │   │   queue/    任务队列（统一队列的展示页）
    │   │   share/    内网共享（含 assets/KustNasTool.exe）
    │   tools/                   可复用工具（见 §9）
    │   *_dom_test.js            DOM 回归测试（node 跑）
    │   *_backup_*/              历史备份目录 —— **非活动代码，勿引用勿 import**
```

> `Translate/_queue_backup_*`、`_share_backup_*`、`_theme_backup_*` 是改造过程中的
> 快照目录。它们的 `.py` 不会被加载，但**会被 `grep` 命中** —— 排查时注意排除。

---

## 3. 全量 API 清单

> 统一约定：**页面路径可带前缀**（`/translate`、`/archive`、`/daily` …），
> **API 与静态资源留在根路径**（`/api/...`、`/uploads/...`、`/translated/...`）。
> 这条约定是硬约束，理由见 §6 铁律 1。

### 3.1 门户层（`portal/routes.py`）

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| GET | `/` | — | HTML（卡片墙，服务端渲染，占位符 `<!--PORTAL_CARDS-->`） |
| GET | `/theme.js` | — | `text/javascript`，`Cache-Control: no-cache`（刻意不走 1 小时缓存） |
| GET | `/api/portal/health` | — | `{"llama":"ok\|loading\|down","active":int\|null}` |

`active` 由 `web_app` 注入的 `get_active()` 提供（`jobs_lock` 内取数）。
`llama` 判定：200 且 `status==ok` → `ok`；503 → `loading`；其余 → `down`。

### 3.2 翻译核心（`web_app.py`）

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| GET | `/translate` | — | HTML（`index.html`，经 Jinja `render_template_string`） |
| GET | `/api/check` | — | `{"ok":true,"llama":{...}}` / `503 {"ok":false,"error":"..."}` |
| POST | `/api/check` | `{"name":"x.pdf"}` | `{"exists":true,"translated_url":..,"dual_url":..,"stem":..,"cache_hit":true}` 或 `{"exists":false,"cache_hit":false}` |
| POST | `/api/upload` | `multipart/form-data`，字段 `file` | 命中缓存：`{"status":"done","original_url","mono_url","dual_url","name","cache_hit":true}`<br>未命中：`{"job_id","status":"queued","original_url","name"}`<br>校验失败：`400 {"error":"only .pdf"\|"too large (>200MB)"}` |
| GET | `/api/status/<job_id>` | — | job 快照（见下）+ `queue_position` + `elapsed`；不存在 → `404 {"error":"not found"}` |
| POST | `/api/cancel/<job_id>` | — | `{"ok":true,"action":"cancelled_queued"}`（排队中）<br>`{"ok":true,"action":"cancel_signal_sent"}`（翻译中）<br>`400 {"error":"already done/failed/cancelled"}` |
| GET | `/api/_debug` | — | `{"jobs":{...},"engine":{"server_url","gate_size"},"queue_size":int}` |
| GET | `/uploads/<path:f>` | — | 原件 PDF（`send_from_directory`） |
| GET | `/translated/<path:f>` | — | 译版 PDF |
| GET | `/` | — | **仅当门户注册失败时**才存在的降级路由，直接渲染翻译页 |

**Job 对象字段**（`/api/status` 返回，已剔除 `path` 与 `cancel_event`）：

```jsonc
{
  "job_id": "…", "name": "s00126-025-01356-w_4d18f4c8.pdf",
  "status": "queued|translating|done|failed|cancelled",
  "progress": 0..100,          // 加权总进度，只增不减
  "message": "解析PDF结构 (0/0)",
  "current_stage": "Translate Paragraphs",
  "stage_progresses": { "Translate Paragraphs": 42.0, ... },
  "created_at": 0.0, "started_at": 0.0, "ended_at": 0.0, "elapsed": 0.0,
  "last_progress_at": 0.0,     // 看门狗据此判「无进度」
  "error": null,
  "original_url": "/uploads/…", "mono_url": "/translated/…", "dual_url": "/translated/…",
  "queue_position": 0          // 仅 status=queued 时非 0
}
```

**Job 状态机**

```
queued ──(worker 取出)──▶ translating ──┬──▶ done        (产物齐 + status 守卫通过)
                                        ├──▶ failed      (异常 / 硬超时)
                                        └──▶ cancelled   (cancel_event / 软超时后取消)
queued ──(取消 API)──────────────────────────────────────▶ cancelled
```

### 3.3 `modules/archive` 译文归档

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| GET | `/archive` | — | HTML |
| GET | `/api/archive/list` | `?kind=all`（默认） | 论文分组视图（下方） |
| GET | `/api/archive/list` | `?kind=dual\|mono\|original` | 单版本扁平列表 |
| GET | `/api/archive/list` | `?kind=translated\|upload` | 旧别名（translated=双语+中文合并） |
| GET | `/api/archive/list` | 非法 kind | `400 {"error":"kind must be one of: …"}` |

分组视图：

```jsonc
{
  "window_hours": 48, "total_papers": 3, "total_files": 7,
  "papers": [{
    "title": "s00126-025-01356-w",       // 用「原文」条目清洗出的标题
    "mtime": 1.7e9, "file_count": 3, "total_size": 1234567,
    "files": [                            // 固定顺序：双语 / 中文 / 原文
      {"kind":"dual","label":"双语","available":true,"name":"…","size":…,"mtime":…,"url":"/translated/…"},
      {"kind":"mono","label":"中文",…},
      {"kind":"original","label":"原文",…}
    ]
  }]
}
```

- 窗口 **48h**，单次上限 300 篇；按钮是原生 `<a target="_blank">`，**服务端不做下载封装**。
- ⚠️ 该窗口依赖 `web_app.RETENTION_SECONDS`（也是 48h）。**两者必须一起调**（§6 铁律 6）。

### 3.4 `modules/journals` 期刊列表

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| GET | `/journals` | — | HTML |
| GET | `/api/journals/list` | — | 两视图一次性返回（下方） |

```jsonc
{
  "ok": true,
  "total_journals": 14, "total_articles": 96, "updated_journals": 9,
  "max_per_card": 6, "max_expand_per_card": 200, "max_topic_per_card": 8,
  "window": {                      // ★ 60 天窗口元信息，前端展示窗口边界
    "days": 60, "from": "2026-07-15", "to": "2026-09-13",
    "now": "2026-09-13 23:10",
    "ledger_available": true, "ledger_updated_at": "…",
    "undated_total": 0, "expired_total": 41
  },
  "tiers":  [{"key":"strong","label":"强相关","floor":19},
             {"key":"mid","label":"相关","floor":12},
             {"key":"low","label":"一般","floor":8}],
  "topic":  {"total":…, "journals_with_hits":…, "window_from":…, "window_to":…,
             "generated_at":…, "min_score":…, "candidate_count":…,
             "date_exact":…, "date_estimated":…, "date_missing":…, "available":true},
  "groups": [{"region":"国际","count":8,"journals":[{
      "name":"Geology", "name_en":…, "name_cn":…, "category":…,
      "website":…, "site_host":"…", "status":…,
      "article_total":12,
      "articles":[{"title":…,"title_zh":…,"url":…,"source":…,"volume":…,"issue":…,
                   "obtained_at":"2026-09-12T07:36:14","obtained_label":"09-12",
                   "obtained_ts":1.75e9}],
      "article_newest":"09-12", "article_undated":0, "doc_url":…,
      "topic_total":5, "topic_tiers":{"strong":2,"mid":3},
      "topic_articles":[{"title":…,"url":…,"doi":…,"date":…,"date_label":…,
                         "score":21,"tier":"strong","keywords":[…],"region":…}]
  }]}, {"region":"国内", …}]
}
```

### 3.5 `modules/news` 今日早报

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| GET | `/today` | — | `302 → /daily` |
| GET | `/daily` | — | HTML；**访问即落盘当天快照**（幂等） |
| GET | `/daily/archive` | — | HTML（归档页） |
| GET | `/daily/poster` | — | 最新海报 PNG；无 → `404 {"error":"no poster"}` |
| GET | `/daily/poster/<date8>` | `date8`=8 位数字 | 该日海报 PNG；格式错 → 400，无 → 404 |
| GET | `/api/news/latest` | — | 首页卡片 + 详情页共用的聚合状态 |
| GET | `/api/news/list` | — | 当日新闻条目 |
| GET | `/api/daily/archive` | — | `{"ok":true,"entries":[{"date8","date","poster","news_count","has_snapshot","doc_url"}]}` |
| GET | `/api/daily/snapshot` | `?date=YYYY-MM-DD`（或 `?d=YYYYMMDD`） | 该日聚合负载；无 → `404 {"ok":false,"error":"no snapshot"}` |

`/api/news/latest`：

```jsonc
{
  "ok": true, "date": "2026-09-13", "is_today": true, "age_days": 0,
  "url": "https://…飞书文档", "has_url": true, "doc_title": "…",
  "news_count": 12, "feishu_sent": "2026-09-13 07:34", "poster_ok": true,
  "poster_url": "/daily/poster", "news_date_text": "2026-09-13", "news_total": 12
}
```

`/api/daily/snapshot` 有快照时额外带 `items`、`historical:true`；
仅有海报无快照时带 `note:"该日仅保留海报，新闻条目未存档"`。

### 3.6 `modules/palette` 科研配色

| 方法 | 路径 | 出参 |
|---|---|---|
| GET | `/palette` | HTML |
| GET | `/api/palette/list` | `{"ok":true,"palettes":[…],"palette_count":n,"color_count":m,"groups":[…]}`；读盘失败 → `500 {"ok":false,"error":"palettes.json 读取失败"}` |

色板数据来自 `modules/palette/palettes.json`（定值快照，不依赖运行时绘图库）。
扩充必须跑 `_build_palettes.py`（§10.3）。

### 3.7 `modules/queue` **任务队列**（2026-09-28 由「翻译队列」改名）

> 该模块本身只是**展示页**；真正的队列内核在 `web_app.py`
> （`jobs` 表 / FIFO `QUEUE` / 单 `_queue_worker` 线程 / `TASK_LANE = Semaphore(1)`）。
> 文献翻译与图像拼合**共用同一条串行通道**（任务带 `kind`）—— 两者都吃 GPU/内存，并跑会互抢。
> ★ 给队列新增一个 `kind` 时，`submit_task` **必须**配 `register_runner(kind, fn)`：
> 只注入前半段 = **入了队但没人执行**，接口照样返回 `queued:true`，队列随后标
> `failed`「未注册的任务种类（kind=xxx）」。

| 方法 | 路径 | 出参 |
|---|---|---|
| GET | `/queue` | HTML（**规范入口**） |
| GET | `/api/queue` | `{"ok":true,"doc":{…},"title":{…},"active":bool}` |
| GET | `/translate/queue` | `302 → /queue`（旧入口兼容） |
| GET | `/api/translate/queue` | 旧接口兼容 |
| GET | `/translate/title-status` | 旧入口兼容 |
| GET | `/api/translate/title-status` | 仅标题队列负载（旧接口兼容） |

```jsonc
{
  "ok": true,
  "doc": {                       // ← web_app 注入的 _queue_snapshot()，模块侧只读
    "ok": true, "queue_size": 0,
    "stats": {"queued":0,"translating":1,"done":3,"failed":0,"cancelled":0,"total":4,"active":1},
    "items": [ /* job 快照（白名单字段）+ queue_position */ ]
  },
  "title": {                     // ← journal_title_zh_status.json
    "ok": true, "state": "idle|running|never|error", "running": false,
    "progress": 100, "coverage_pct": 96.4, "cached": 1204,
    "pending": 45, "translated_this_run": 300, "failed_this_run": 0, "recent_failed": […]
  },
  "active": true
}
```

**⚠️ 序列化陷阱**：`jobs` 里混着 `threading.Event`（`cancel_event`）等活对象，
直接 `jsonify(jobs)` 会抛 `TypeError`。`_queue_snapshot()` 只挑**可序列化白名单**字段，
且必须在 `jobs_lock` **内**取数（jobs 由 worker 线程并发改写）。

### 3.8 `modules/share` 内网共享

| 方法 | 路径 | 出参 |
|---|---|---|
| GET | `/share` | HTML（含一行命令 + 各分步命令的复制按钮 + exe 下载） |
| GET | `/api/share/nas.exe` | `KustNasTool.exe` 附件；未构建 → `404` 文本提示 |
| GET | `/api/share/ping` | `{"ok":true,"service":"kust-portal-share"}`（给 exe 探测地址用） |
| GET | `/api/share/info` | `{"unc","drive","user","server"}`（**不含密码**） |
| GET | `/api/share/all.ps1` | 一键脚本（挂载+体检+修复 内联拼接） |
| GET | `/api/share/mount.ps1` | 仅挂载 |
| GET | `/api/share/diag.ps1` | 只读体检 |
| GET | `/api/share/fix.ps1` | 对症修复 |
| GET | `/api/share/all.bat` | `nas_onekey.bat`（GBK + CRLF 薄壳，内部 `iex (irm …)`） |
| GET | `/api/share/mount.bat` | `mount_s.bat` |
| GET | `/api/share/fix.bat` | `fix_nas.bat` |

**关键性质**

- 所有 ps1 里的地址由 `request.url_root` **动态派生** —— 用户从 `127.0.0.1` 还是
  `kust.work` 打开，复制到的命令都是对的。
- ps1 响应头必须显式 `Content-Type: text/plain; charset=utf-8`（`irm` 靠它解码中文）；
  只给 `mimetype` 会被 Werkzeug 追加成 `…; charset=utf-8; charset=utf-8` 的畸形头。
- ps1 文件是 **UTF-8 无 BOM**（BOM 会被 `iex` 当非法字符）；`.bat` 是 **GBK + CRLF**
  （cmd 代码页 936）。
- 挂载动作在**用户本机**执行（`net use`），服务端做不了，页面只提供脚本/命令/排错。
- ⚠️ 页面与脚本含**明文内网凭据**，仅限内网使用。

---

### 3.9 `modules/tasks` 任务打卡（多角色 · 团队共享）

★ 本模块的 `assets/` 是**生成物**（由 `tools/tasks_build/` 从上游复刻页面生成，**不要手改**），
深度文档见 `tools/tasks_build/NOTES.md`。

**命名约定（2026-09-21）**：对外一律称 **RPG 时间轴**（上游原件标题里的字样是 NPC）。
重命名做在 `build_module.py` 里（§2e 脚本 / §4b+§4e 模板），所以重新下载 `extract/` 也不会退回。
视图内部名随之为 `rpg`（`data-view="rpg"` / `#view-rpg` / `renderRPGView()`）；
`LS_VIEW` 存的是视图名且是**本机态不同步**，所以 `app.js` 里用
`VIEWS = ['rpg','stage','focus','pet']` 做白名单校验，旧值自动回落默认视图（否则内容区会整块空掉）。

| 方法 | 路径 | 出参 |
|---|---|---|
| GET | `/tasks` | HTML 工作台（角色条 + 四视图：**RPG 时间轴** / 阶段打卡 / 番茄钟 / 桌宠） |
| GET | `/api/tasks/assets/<path:rel>` | 白名单式静态资源（`tasks.css` / `app.js` / `roles.js`） |
| GET | `/api/tasks/roles` | `{ok, roles:[{id,name,avatar,demo,createdAt,updatedAt,rev,nodes,checkins}], active}` —— `active` 由 cookie 决定，为空 = 需要弹「创建角色」；`demo:true` 即示例角色 |
| POST | `/api/tasks/roles` | 建角色 `{name, avatar}` → `{ok, role, roles}`，并 `Set-Cookie: kust_tasks_role` |
| POST | `/api/tasks/roles/<id>/active` | 记住当前角色（写 cookie） |
| PATCH | `/api/tasks/roles/<id>` | 改名 / 换头像 `{name?, avatar?}` |
| DELETE | `/api/tasks/roles/<id>` | 删角色（连带删数据）；若删的是本机当前角色则顺手清 cookie |
| POST | `/api/tasks/demo` | 恢复 / 重置示例角色 `{force?}` → `{ok, created, reset, role, roles}`。**不写 cookie**（恢复完还要用户自己去角色条点） |
| GET | `/api/tasks/state?role=&rev=` | `{ok, changed, rev, data}`；`rev` 与库一致时只回 `changed:false` |
| PUT | `/api/tasks/state` | 整份快照回写 `{role, rev, data, removed}` → `{ok, rev, merged, data}` |
| GET | `/api/tasks/health` | `{ok, roles, demo, store, exists, bytes}` |

**关键性质**

- 角色清单**是全局的**（kust.work 内网免登录）：同一团队任何人打开 `/tasks` 都能看到全部角色并切过去看同一份数据。
- `cookie: kust_tasks_role` 只决定「这台浏览器在哪个角色」；cookie 空 / 指向已删角色 →
  前端弹创建角色引导，**看板脚本 `app.js` 不注入**（它是被 `roles.js` 动态注入的，
  静态引入会先渲染一版错数据）。
- 同步的只有 5 个键 `nodes / checkins / badges / focus / pet`；`tab / view / form_draft`
  是「本机视图状态」，**故意不同步**。
- 推送靠**拦截 `localStorage.setItem`** + 700ms 防抖；拉取靠 15s 轮询 + 切回前台补一次；
  离线时降级为纯本地缓存，`dirty` 标记留着，恢复后自动补传。
- 冲突合并：`rev` 一致直接采纳；`rev` 落后 → 按 `_id` 求并集（同 id 客户端优先）；
  `removed` 里的 id 落进**墓碑**（防另一端旧快照复活已删条目，30 天回收）。
- **示例角色 `id='demo'`**（「示例角色 · 参考」）：库里常驻一份填满内容的工作台供参考，
  但**不写 cookie、不参与任何自动跳转** —— cookie 为空时照旧弹创建引导，它只列在弹窗的
  「或进入已有角色」里（带「示例」小标 + 「看样例 →」）与角色条最右边（虚线弱化）。
  被删后下次启动会由 `store.ensure_demo()` 补回；被改乱可在管理弹窗点「恢复示例角色」重置。
  `list_roles()` 把它固定排在最后。它的 `rev` 从 **1** 起（若为 0，客户端首拉会命中
  `changed:false` 快路径而拿到空数据）。
- 数据落 `modules/tasks/data/store.json`（与 `modules/story/stories.json` 同规矩：
  `RLock` + 临时文件 + `os.replace`）。想全员清空 → 置成 `{"version":2,"roles":[]}`
  （重启后示例角色会自动补回；不留它就在 `init_tasks` 里跳过 `ensure_demo()`）。

### 3.10 `modules/stitch` 显微图像拼合（★ 与文献翻译共用任务队列）

> 算法细节（几何约束 / 判据 / 提速开关 / 逐位验证）见 skill `postnews-web-ops` §3c，
> 以及 `modules/stitch/.workbuddy/memory/MEMORY.md`。本节只列 API 与数据流。

| 方法 | 路径 | 出参 |
|---|---|---|
| GET | `/stitch` | HTML（**注意：无尾斜杠**，`/stitch/` 会 404） |
| POST | `/api/stitch/run` | **同步**拼接（不进队列，但走同一个 `TASK_LANE`） |
| POST | `/api/stitch/submit` | **异步**提交 → `{"ok":true,"queued":true,"task_id":"…"}`；入**统一任务队列**（`kind='stitch'`） |
| GET | `/api/stitch/task/<id>` | 任务查询。状态以**队列**为准，结果 payload 留在本模块有界的 `tasks` 表 |
| GET | `/api/stitch/result/<fname>` | 预览 |
| GET | `/api/stitch/result/<fname>/dl` | 下载 |
| GET | `/api/stitch/assets/<path:rel>` | 静态资源（白名单式） |
| GET | `/api/stitch/health` | 健康 + **加速栈自述**（`accel`：`use_gpu` / `coarse_screen` / `warm_workers` / `warp_roi`）+ `results_on_nas` |

**存储**：上传原图 → 本地 `modules/stitch/uploads/`（过程性临时件，`finally` 删 + 启动兜底清扫）；
拼接结果 → **NAS 优先**（UNC `\\<nas-host>\<share>
失败降级本地 `results/`），默认 **PNG 无损**。

**数据流**：
```
POST /api/stitch/submit ─▶ 落 uploads/ ─▶ submit_task('stitch', …, job_id=task_id)
   │                                          │
   │  ← ─ ─ ─ 未注入 submit_task 时退回自跑 ─ ─ ┘
   ▼
web_app._queue_worker（与文献翻译**同一条串行通道** TASK_LANE）─▶ _stitch_runner(job_id, ctx)
   └─ Stitcher.stitch(paths) ─▶ 匹配(SIFT+几何验证) → 位姿(最小二乘) → 融合(多频带) → _save()
```

★ **`register_runner('stitch', _stitch_runner)` 必须被注入**（经 `modules/__init__.register_all`
→ `routes.init_stitch`），否则任务入队后立刻 `failed`「未注册的任务种类（kind=stitch）」。

---

## 4. 数据传输流

### 流 A · 文献翻译端到端（主干）

```
浏览器 ─POST /api/check {name} ─▶ _find_cache()  ──命中──▶ 直接返回译版 URL，不入队
   │
   └─POST /api/upload (file) ─▶ _validate_pdf() ─▶ 存 uploads/<stem>_<8hex>.pdf
                                   │
                                   ├─ _find_cache(fname) 命中 ─▶ 秒回 status=done
                                   └─ 未命中 → jobs[job_id] = {status:queued, cancel_event:Event()}
                                                QUEUE.put(job_id) → _ensure_worker()
   │
   ▼  调度线程 _queue_worker（常驻 1 个）
       QUEUE.get() → status 守卫(queued/translating) → translating
                   → started_at / last_progress_at / progress=0 / message="初始化..."
       → 起**独立工作线程** _run_job_safely(job_id) → t.join(HARD_TIMEOUT=30min)
   │
   ▼  工作线程 _run_translate(job_id)
       run_babeldoc.translate_pdf(src, TRANSLATED_DIR, progress_callback=_on_progress,
                                  doc_layout_model=DOC_LAYOUT_MODEL, cancel_event=…)
   │
   ▼  run_babeldoc.translate_pdf
       ① patch onnxruntime providers（DML 优先 / CUDA→CPU）
       ② patch BabelDOC 字体族（TimesNewRoman-Serif + 思源宋/黑）
       ③ set_translate_rate_limiter(4)
       ④ GeoBabelTranslator(...)  → 懒加载引擎（HTTP 优先，CLI 降级）
       ⑤ ProgressMonitor(hl.TRANSLATE_STAGES, progress_change_callback=_babel_callback)
       ⑥ 清理同 stem 旧译版（glob 删）
       ⑦ TranslationConfig(no_dual=True, use_side_by_side_dual=False,
                            skip_scanned_detection=True, table_model=None,
                            watermark_output_mode=NoWatermark,
                            disable_rich_text_translate=True, qps=8, …)
       ⑧ hl.do_translate(pm, config)
   │
   ▼  BabelDOC 内部四阶段（翻译阶段会按段回调 do_translate）
       Parse(20%) ─▶ Translate Paragraphs(70%) ─▶ Typesetting(8%) ─▶ Save PDF(2%)
   │
   ▼  每段：GeoBabelTranslator.do_translate(text)
       ① _HAS_LATIN 短路：无 [A-Za-z] ⇒ 原样返回（不占 slot）
       ② engine.translate(text)，最多 4 次尝试，退避 1s/3s/5s/7s
          （命中 404/503/ConnectionError/timeout/Loading model 才重试）
       ③ 仍空 → 返回 "⚠️⚠️⚠️ " + 原文（**不静默返原文**，让用户一眼看到）
   │
   ▼  LlamaBatchEngine.translate(text)
       > 2500 字符 且 depth<3 → _split_text() 切块（**在 GATE 之外**）→ 递归
       → _translate_one(text)：
           · 术语注入 TermBank.match → 拼到 SYSTEM（绝不污染 USER）
           · 动态 max_tokens = min(1500, max(128, min(len*0.8+80, CTX_PER_SLOT-est-500)))
           · with _GATE(信号量=N_PARALLEL=8)：      ← GATE 范围仅一次 HTTP
               POST http://127.0.0.1:8090/v1/chat/completions  (timeout 180)
           · 写 _probe_data.jsonl 一行
       HTTP 层错误（404/503）→ 抛给上游；**不在此处切分**（否则 1 次失败放大成 8 次）
   │
   ▼  llama-server → Qwen3.5-4B → 译文（中文）
   │
   ▼  进度回流
       BabelDOC ProgressMonitor ─▶ _babel_callback(**kwargs) ─▶ _on_progress(pct, stage, cur, tot, stage_prog)
        · 取消检查：cancel_event.is_set() → raise CancelError
        · 写 _babel_callback.log
        · stage_progresses[stage]；progress = max(旧, 加权总进度)
        · 更新 last_progress_at（看门狗判活依据）与 message
   │
   ▼  收尾（仍在 _run_translate 内）
       ① mono = _translated_path(stem,'mono')   ← **glob 匹配**，不是硬拼
          BabelDOC 在 NoWatermark 模式下产物名带 `.no_watermark`，
          实际形如 <stem>.no_watermark.zh.mono.pdf
       ② 自动生成双语版 bilingual.ensure_dual(原件, mono, mode=BILINGUAL_MODE)
          左原文 / 右译文，页宽 ×2；链接按 mode 平移；原子写出（.tmp → os.replace）
          实测 22~26 页 0.4~1.0s，同步做不影响响应；失败只告警，绝不拖垮主流程
       ③ 兜底遍历 {stem}*.zh.mono.pdf / .dual.pdf / .zh.pdf
       ④ 状态守卫 j.status == 'translating' 才写 done + mono_url/dual_url
   │
   ▼  前端轮询 GET /api/status/<job_id>
       间隔 1.5s，失败 ×1.3 退避、上限 8s；done/cancelled/failed 即停
```

**为什么翻译阶段要 4 次重试 + `⚠️` 标记**：这是针对"用户看不到失败"的直接对策。
失败被静默吞成原文，表现为"大量段落没翻译"（正是 2026-09-13 事故的症状）；
现在失败段落带 `⚠️⚠️⚠️` 前缀，PDF 里一眼可辨。

### 流 B · 队列自愈（防卡死）

```
                        ┌────────────────────────────────────────────┐
                        │ 调度线程 _queue_worker（常驻，绝不退出）    │
                        │  QUEUE.get() → 起工作线程 → join(30min)    │
                        └──────┬─────────────────────────┬───────────┘
                               │ join 超时               │ join 正常返回
                               ▼                         ▼
                    _mark_timeout(status=failed)   常态流转
                    cancel_event.set()
                        │
   ┌────────────────────┴──────────────────────────────────────────────┐
   │ 看门狗 _stall_watchdog（每 30s 一轮，独立 daemon）                  │
   │  now - last_progress_at >= 10min（软超时）→ cancel_event.set()      │
   │  → BabelDOC 在进度回调间隙抛 CancelError → 工作线程正常结束         │
   │  若它不响应，则由调度线程的 30min 硬超时兜底                        │
   └───────────────────────────────────────────────────────────────────┘

   取消 API：queued → 直接置 cancelled（worker 取出时会 skip）
            translating → 只 set cancel_event（由上述链路优雅退出）
```

**状态守卫（关键）**：写 `done` / `cancelled` / `failed` 前一律检查
`j.status == 'translating'`。否则"被硬超时接管的孤儿线程"晚一步跑完时，
会把「已超时跳过」刷回「完成」，队列状态自相矛盾。

**为什么调度线程要吞异常**：它一旦死掉，`_worker_started` 已是 `True` 不会重启，
队列就**永久停摆**。所以 `except` 里记录日志后必须继续循环。

常量（`web_app.py`）：`TRANSLATE_HARD_TIMEOUT = 30*60`、
`TRANSLATE_STALL_TIMEOUT = 10*60`、`WATCHDOG_INTERVAL = 30`、`MAX_JOBS = 12`、
`QUEUE = Queue(maxsize=64)`。

### 流 C · 页面渲染（门户与模块）

```
GET /journals
   │
   ▼  module routes.py ── render_html(_TEMPLATE)
        │
        ▼  portal/templating.load_html(path)
             · 按 st_mtime 缓存 → **改 HTML 不用重启**
             · 文件缺失/读失败 → _fallback() 降级页（仍是可读的深色）
             · 不经过 Jinja2！
        ▼  page(title, body) 注入最小 :root（深色）+ <script src="/theme.js">
        ▼  Response(text/html)
```

门户首页更进一步：`render_html(_TEMPLATE, {<!--PORTAL_CARDS-->: _render_cards()})`，
卡片 HTML 由 `registry.get_groups()` + `_card()` 生成后**字符串替换**进模板。
每张卡片带 `data-mod="<id>"`，供 `portal.html` 的 JS 按 id 动态改写角标/描述
（今日早报的当天文案就是这么做进去的，模板本身保持恒定）。

> **⚠️ 为什么刻意不用 Jinja2**：门户/归档等页面 CSS 与 JS 密集，`{` `}` 满天飞，
> 走 `render_template_string` 有把大括号误当模板语法的风险。
> **例外**：翻译页 `web_app.index()` 仍用 `render_template_string(INDEX_HTML)`（历史沿革）。
> 新增页面一律走 `portal.templating`。

### 流 D · 主题（浅色 / 深色 / 自动）

```
每个页面 <head>：  <script src="/theme.js"></script>     ← 必须**同步**，不加 defer/async

theme.js 执行顺序（IIFE）：
 ① injectStyle()            注入 <style>：:root[data-theme="light"]{ LIGHT_VARS } + 按钮样式
 ② readMode()               localStorage['kust.theme.mode'] ∈ {auto, light, dark}，默认 auto
 ③ apply(resolve(mode))     立刻写 documentElement[data-theme]，并设 colorScheme
                            —— 在任何像素画出来之前完成 ⇒ 零闪烁
 ④ DOMContentLoaded → mountBtn()   往 <header> 末尾挂三态按钮（自动→浅色→深色）
 ⑤ setInterval(reevaluate, 60s)    自动模式下跨过日出/日落时自动翻转
 ⑥ visibilitychange                后台挂久了回来立刻校正（浏览器会节流定时器）
 ⑦ storage 事件                    多标签页同步
 ⑧ window.kustTheme               只读调试接口：{mode, theme, sun(), isDaytime(), set()}

auto 判据：NOAA 简化式算昆明（25.0389N, 102.7183E, tz+8）日出/日落，
          太阳视高度角 -0.833°；纯本地计算，不联网。
```

**主题的分工铁律**：深色是**各页面自己 `:root` 的兜底默认**；浅色由 `theme.js` 注入
`:root[data-theme="light"]` 覆盖。这样 theme.js 404/被拦/JS 禁用时页面仍可读。
CSS 变量按语义命名，页面里**不出现硬编码 hex**。

**刻意不随主题变**：PDF 白纸底、浮在 PDF 上的页码条/进度卡、断线红、完成绿。

### 流 E · 期刊列表（多源合并 + 60 天窗口）

```
读盘（全部只读，来自 data_dir = D:\PostNews）
  journals.json              期刊元数据
  journal_ledger.json        ★ 台账（每篇 first_seen）
  journal_batch_pending.json 待推送批次（有批次 date）
  journal_updates.json       最近一轮更新（无时间戳 → 用文件 mtime）
  journal_article_buffer.json 历史累积
  journal_doc_urls.json      飞书文档链接
  journal_topic_archive.json 主题回溯归档
  journal_title_zh.json      标题中文小字（mtime 进程内缓存）
   │
   ▼ 每刊 _collect_recent(name, ledger, updates, pending, buffer_, ts_map, cutoff, now)
       来源优先级（先到先得）：台账 > 待推送批次 > 最近更新 > 历史缓存
       去重键 _norm_key(title, url)：标题指纹（去非字母数字、小写、截 72 字）
                                    → 空则用 URL 指纹
       时间解析：条目 first_seen / pushed_at  >  批次 date  >  文件 mtime
       cutoff = now - 60d
         · dt < cutoff        → expired（丢弃，计数）
         · dt 解析不出        → undated（**保留**并计数，无法证明超窗口）
       排序：obtained_ts 倒序
   │
   ▼ _collect_topic(journal_topic_archive.json) 按期刊名分组，保持相关度降序
       分档：strong(≥19) / mid(≥12) / low(≥8)
   │
   ▼ 组装 groups（国际 → 国内），每卡截断：
       articles[:200]（「全部展开」上限）· 前端默认显示 6 条
       topic_articles[:8]
```

> **为什么窗口按「获取时间」而不是「发布时间」**：用户要的是"我手上能点开的链接"。
> 时间源必须用 `journal_ledger.json` 的 `first_seen`（首次写入后**永不改写**），
> 窗口才会随时间自然老化。若改用文件 mtime，`journal_updates.json` 每次运行被整体
> 重写会把时间"刷新"成今天，窗口退化成"显示全部"（§6 铁律 5）。

前端另有「全部展开 / 收起全部」总开关，靠 `_MAX_EXPAND_PER_CARD = 200` 保证
下发量够铺开；超过 200 时前端明确提示"另有 N 篇超出单卡展示上限"，不假装已全量。

### 流 F · 今日早报与归档快照

```
写入侧（不在服务内）： daily_run.py 抓取+推送 → 写
    daily_push_state.json  {latest_daily:{date, doc_url, doc_title, feishu_sent, poster_ok, news_count}}
    news.json              {date, publisher, news:[{num,cat,title,summary,source,url,pub_date}]}
    output/daily_poster_YYYYMMDD(_N)?.png

读取侧（门户，只读）：
  GET /daily
     ├─ _write_snapshot()  ← 幂等落盘：daily_archive/YYYYMMDD.json
     │     内容 = latest_daily + 当日新闻 items + 海报文件名 + generated_at
     └─ render_html(daily.html)  ← 页面 JS 再拉 /api/news/latest + /api/news/list + 海报

  GET /api/daily/archive
     · 扫描 output/ 的历史海报  ┐
     · 叠加 daily_archive/*.json ┘ 合并去重 → 按 date8 倒序
     （即便某天没落盘快照，也能在归档里看到那天海报）

  GET /api/daily/snapshot?date=
     有快照 → 回快照（items + historical:true）
     无快照但有海报 → 只回海报 URL + note
```

海报文件名白名单：`^daily_poster_\d{8}(_\d+)?\.png$`，杜绝路径穿越。

### 流 G · 内网共享（服务端生成脚本，用户本机执行）

```
GET /share
  base = request.url_root                      ← 动态派生，127.0.0.1 与 kust.work 都对
  命令 = iex (irm '<base>api/share/all.ps1')
  页面占位符：{{UNC}} {{DRIVE}} {{CMD_ALL_ATTR}} {{CMD_ALL_HTML}} …

GET /api/share/all.ps1
  build_all(base)：
     _ALL_HEAD
     ├─ 段1 挂载  _strip_exit(_fill(_MOUNT, base))   ← 剥掉顶层 exit
     ├─ _ALL_GATE：if (-not $MOUNTED) { … exit 1 }   ← 挂载失败就不跑体检与修复
     ├─ 段2 体检  _strip_exit(_fill(_DIAG, base))
     └─ 段3 修复  _strip_exit(_fill(_FIX, base))
     _ALL_TAIL
  _fill() 替换：__UNC__ __DRV__ __SVR__ __USER__ __PASS__ __HOSTS__
                __MOUNTURL__ __DIAGURL__ __FIXURL__
  _ps() → UTF-8 无 BOM + CRLF

用户本机（PowerShell）： iex (irm 'http://…/api/share/all.ps1')
   net use S: \\<nas-host>\<share> /user:<nas-user> **** /persistent:yes
   → 清残留（含同服务器其它盘符，防 1219 多重连接）
   → 失败按错误码提示（1219/85/67/53/5）+ 顺手 ping 判内网
   → 成功后 explorer 打开 S:

GET /api/share/nas.exe
  KustNasTool.exe（PyInstaller --noconsole --onefile 打包）
  ⚠️ exe 里**不含脚本逻辑**：按钮只是让 PowerShell 拉最新 .ps1
     ⇒ 改脚本逻辑不用重新下载 exe
GET /api/share/{ping,info}  exe 探测地址、显示共享信息（info 不含密码）
```

`_strip_exit` 的作用：各段单独跑时需要 `exit`，被内联进 `all.ps1` 后会**连整条命令一起终止**，
所以拼接前统一剥掉，流程改由 `_ALL_GATE` 闸门控制。

### 流 H · 英文标题中文小字（离线批 → 门户只读）

```
D:\PostNews\journal_title_zh.py（离线跑批，**不在服务内**）
   · 批量排队，5 条/批 → 术语批内一致
   · 缓存按标题指纹增量续跑，可中断
   · 走本机 llama（同样必须 trust_env=False）
   ↓ 落盘
journal_title_zh.json          {titles: {"<指纹>": {zh: "…"}}}
journal_title_zh_status.json   {state, progress, coverage_pct, cached, pending,
                                translated_this_run, failed_this_run, recent_failed}
   ↓ 门户只读
modules/journals  _title_zh_map()（按 mtime 进程内缓存）→ 卡片 title_zh
modules/queue     _read_title_status() → 队列页标题翻译面板

键规则（两处必须一致）：
   key = re.sub(r'[^a-z0-9]', '', title.lower())
   → journal_title_zh.py::_key  ／  journals/routes.py::_zh_of
缓存缺失 ⇒ 该条没有 title_zh，前端照常显示英文原文，**不报错不空白**。
```

---

### 流 I · 双语拼接版（翻译完成自动生成）

```
BabelDOC 产出的 mono（纯中文）
        │
        ▼  _translated_path(stem,'mono')      ← glob 匹配（铁律 11）
        │  命中 <stem>.no_watermark.zh.mono.pdf（多个取最新 mtime）
        ▼  bilingual.ensure_dual(原件, mono, mode=BILINGUAL_MODE)
           ├─ 已存在且非 force → 直接复用（省 IO）
           └─ 否则 make_bilingual()：
                fitz 打开 原件 + mono
                mode=lr  → new_page(w*2, h)：左原件 @(0,0)，右译文 @(w,0)  ← 默认
                mode=tb  → new_page(w, h*2)：上原件 @(0,0)，下译文 @(0,h)
                mode=alt → 页数 ×2：原1、译1、原2、译2 …（只保留 URI 外链）
                链接迁移：URI 外链 + 内部跳转，矩形按 mode 平移 dx/dy
                原子写出：save(.tmp) → os.replace()   ← 用户不会下载到半截文件
        │
        ▼  dual_url = /translated/<stem>.zh.dual.pdf
           → job['dual_url'] → /api/status → 前端「双语对照」下载按钮
```

**三个产出链路都会给双语版**（缺一不可）：

| 链路 | 位置 | 行为 |
|---|---|---|
| 翻译完成 | `_run_translate` 收尾 | 定位 mono 后立刻 `ensure_dual()`；期间刷新 `last_progress_at` 防看门狗误判 |
| 缓存命中 | `api_upload` | 历史译文若没有双语版，用**本次上传的原件**现场补拼（0.4s 级，同步无感） |
| 缓存查询 | `_find_cache` | 只返回**已存在**的 dual，不现场生成（`/api/check` 可能被高频调用） |
| 离线补生成 | `tools/make_dual.py` | 本功能上线前的历史译文批量补 |

**切换形态**：改 `web_app.py` 顶部 `BILINGUAL_MODE`（`lr`/`tb`/`alt`），
或启动时用环境变量 `BILINGUAL_MODE=alt`，无需改其它代码。

> ⚠️ **缓存命中时用的是「本次上传的原件」而不是历史原件**：两者内容相同、hash 不同，
> 拼出来的双语版左右两半依然严格对应，但文件名以历史译文 stem 为准。

---

### 流 J · NAS 归档与本地回收（文献仓库）

```
翻译完成 ─▶ nas_archive.archive_versions(原件, mono, dual)
              ├─ root() 探测：环境变量 NAS_PDF_ROOT → S:/Translator_PDFs → UNC 兜底（结果缓存 60s）
              ├─ 原件 → <nas>/uploads/    mono → <nas>/translated/    dual → <nas>/duo/
              └─ 写 data/nas_index.json（原子写 + 索引锁），供「命中缓存直出 NAS」用

上传时   ─▶ _find_cache → nas_archive.lookup(name) 命中 ─▶ restore() 从 NAS 取回本地
                            （任务队列页面 / 译文归档依旧只读本地，规则不变）

定时回收 ─▶ 任务计划程序 PostNews-NASReaper（每日 09:00 / 21:00，详见 10.7）
              └─ tools/nas_reaper.py
                   ├─ 运行锁 data/nas_reaper.run.lock（1 小时陈旧保护，防重入）
                   ├─ NAS 不可达 → 直接跳过，本地一个文件都不删
                   ├─ 逐组：copy 到 NAS → 逐个校验 exists && size 相同 → 才删本地
                   ├─ 顺带清理「同一篇文献被重复上传」留下的孤儿原件
                   └─ 写 logs/nas_reaper.log（2 MB 轮转，保留 3 份）
```

**回收只删本地、NAS 永远保留**；校验不过的文件留在本地并记进日志的 `kept`，
绝不为了腾空间冒险。整个流程幂等：重复执行无害。

---

## 5. 数据文件与读写方向

**核心原则：门户与模块对 PostNews 的数据文件是「只读」的。写入方向只有一处反向**
（`daily_run.py` 增量写 `daily_push_state.json` 的 `latest_daily`，供门户读）。

| 文件（`D:\PostNews\`） | 写入者 | 读取者 | 备注 |
|---|---|---|---|
| `journals.json` | 人工/抓取脚本 | `modules/journals` | 14 刊元数据 |
| `journal_ledger.json` | 抓取器/`journal_baseline.py` | `modules/journals` | ★ `first_seen` **写入后永不改写，绝不能删** |
| `journal_updates.json` | `daily_run.py`（**整体重写**） | `modules/journals` | 每次运行重写 → 不能拿 mtime 当获取时间 |
| `journal_batch_pending.json` | 批量检测 | `modules/journals` | 带批次 date |
| `journal_article_buffer.json` | 抓取器 | `modules/journals` | 历史累积，字段简 |
| `journal_doc_urls.json` | 推送脚本 | `modules/journals` | 飞书速递链接 |
| `journal_topic_archive.json` | `journal_backfill.py` | `modules/journals` | 主题回溯归档 |
| `journal_title_zh.json` | `journal_title_zh.py` | `modules/journals` | 标题中文缓存 |
| `journal_title_zh_status.json` | `journal_title_zh.py` | `modules/queue` | 标题翻译状态 |
| `daily_push_state.json` | `daily_run.py` | `modules/news` | 只读其 `latest_daily` |
| `news.json` | `daily_run.py` | `modules/news` | 当日新闻 |
| `output/daily_poster_*.png` | `daily_run.py` | `modules/news` | 海报（白名单命名） |
| `daily_archive/YYYYMMDD.json` | **`modules/news`**（访问 `/daily` 时落盘） | `modules/news` | 门户**唯一会写的目录** |
| `Translate/uploads/*` | `web_app` `/api/upload` | `/uploads/<f>` | 保留 48h |
| `Translate/translated/*` | BabelDOC + `_make_dual_pdf` | `/translated/<f>` | 保留 48h |
| `Translate/modules/palette/palettes.json` | `_build_palettes.py` | `modules/palette` | 扩充色卡唯一入口 |
| `Translate/_probe_data.jsonl` | 引擎每次 HTTP | 分析脚本 | 埋点，行式 JSON |
| `Translate/_babel_callback.log` | `run_babeldoc._babel_callback` | 人工排查 | 每次翻译**覆盖写** |
| `Translate/modules/tasks/data/store.json` | **`modules/tasks`**（PUT `/api/tasks/state` 时落盘） | `modules/tasks` | 任务打卡的角色与工作台数据（多角色共享）。`RLock` + 临时文件 + `os.replace`；含常驻示例角色 `demo`（`ensure_demo()` 启动时补齐）；置成 `{"version":2,"roles":[]}` = 清空用户角色（示例角色重启后自动回来） |

---

## 6. 铁律（改代码前必读）

> 前 5 条是架构级约定，破坏会导致大范围 404/数据错乱；6~10 条是踩过的坑。

### 铁律 1 · 页面路径可带前缀，API 与静态资源必须留根路径
`index.html` 的 **4 处 `fetch`**（`/api/check`、`/api/upload`、`/api/status/…`、
`/api/cancel/…`）与后端产出的 **PDF URL**（`/uploads/…`、`/translated/…`）都是
**根绝对路径**。这条约定让门户改造做到「翻译页前端 0 行改动」。
改路由时把 API 一起挪到前缀下 ⇒ 全站 404。

### 铁律 2 · 访问本机端口一律 `trust_env=False`
任何 `requests` 请求打到 `127.0.0.1:8090` / `:5555` 前，必须走 `_make_session()`
或显式 `session.trust_env = False`。**这是 2026-09-13 事故的根因，详见 §7.1。**

### 铁律 3 · 改 `.py` 必须重启 Flask
`.py` 改动不会被热加载。重启前先确认 `.flask_5555.pid`：
`taskkill` / `Stop-Process` 可能被拦，可用 `ctypes` 的
`OpenProcess(0x0001) + TerminateProcess` 兜底。`.html` / `.js` 按 mtime 热重载，不用重启。

### 铁律 4 · 模块只读 + 依赖注入，**禁止反向 `import web_app`**
`web_app → modules → web_app` 会循环导入。凡是需要 `web_app` 内部数据（队列快照、
活跃任务数）的，一律走 `init_xxx(app, get_queue=…/get_active=…)` 回调注入。
同理，模块**只读** `data_dir`，不写业务文件。

### 铁律 5 · `journal_ledger.json` 的 `first_seen` 不可改写、文件不可删
它是「获取时间」的**唯一权威来源**，门户 `/journals` 的 60 天窗口靠它。
删掉会把所有文章的获取时间重置为今天，窗口永久失效。
也不能拿 `journal_updates.json` 的 mtime 当获取时间 —— 它每次运行被整体重写。

### 铁律 6 · `RETENTION_SECONDS >= ARCHIVE_WINDOW_HOURS`
`web_app.RETENTION_SECONDS = 48h`，`archive.ARCHIVE_WINDOW_HOURS = 48`。
清理 > 窗口 ⇒ 归档页永远空白。**两者必须一起调。**

### 铁律 7 · 并发闸门 `_GATE` 只包一次 HTTP，绝不嵌套
`translate()` 的切分递归**必须在 `_GATE` 之外**做；`_translate_one()` 内只做
"取闸 → 一次 HTTP → 释放"。嵌套 acquire = 经典死锁（§7.3）。

### 铁律 8 · 同一文件不要并行发多个 `Edit`
多个 `Edit` 都基于同一份旧内容写回 ⇒ 互相覆盖（本轮被吃掉 4 处）。
批量改同一文件要**串行**，或改用**断言式脚本**一次性替换（每个锚点要求恰好命中 1 次）。

### 铁律 9 · 模块注册逐个 `try/except`
`portal.init_portal()` 与 `modules.register_all()` 各自独立兜底；
`register_all` 内部每个模块再单独 `try/except`。任何一个写坏只影响自己的入口。

### 铁律 10 · 依赖不许随手升级
本项目**没有虚拟环境**，直连系统 Python 的**用户级 site-packages**
（`C:\Users\youyo\Python\Python312\site-packages`）。
一次 `pip install -U` 就能把生产跑挂。升级前先看 `requirements-lock.txt`（§7.2）。

### 铁律 11 · 产物路径必须 glob 匹配，**绝不硬拼**
BabelDOC 在 `watermark_output_mode=NoWatermark` 下，产物名会多一段 `.no_watermark`：

```
硬拼（错）：  {stem}.zh.mono.pdf                        → exists() 恒 False
实际产物：    {stem}.no_watermark.zh.mono.pdf
```

后果有多隐蔽：`mono_url` 还能靠兜底 glob 拿到，**唯独双语版生成被
`if mono_path.exists()` 挡住** → dual 长期为 0，而前端默认勾选的正是"双语对照"
→ 用户点下载必失败（2026-09-13 修复）。

一律用 `bilingual.find_translated(dir, stem, kind)`，多个匹配取最新 mtime。
改 BabelDOC 的 `watermark_output_mode` 或升级 BabelDOC 时，**这条要重新验一遍**。

---

## 7. 本次修复的两颗地雷（重点）

### 7.1 地雷一：`HTTP_PROXY` 把本机请求改写成绝对 URI → `404 File Not Found`

**症状**：大量段落没有被翻译，直接被返回原文（未翻译率 8%，正常约 3%）。

**链路**

```
环境有 HTTP_PROXY=http://127.0.0.1:53038（沙箱注入）
  → Flask 进程继承该变量
  → requests 默认 trust_env=True，把发往 127.0.0.1:8090 的请求也交给代理
  → urllib3 走代理时用「绝对 URI」请求行：
        正常： POST /v1/chat/completions HTTP/1.1
        异常： POST http://127.0.0.1:8090/v1/chat/completions HTTP/1.1
  → llama.cpp 路由按 path 匹配，绝对 URI 匹配不上 → 404 {"error":{"message":"File Not Found"}}
  → BabelDOC 把异常当「译文」返回原文 → 用户看到大量段落没翻译
```

**判据（可复现）**

- 同一份 `src_len=1907` 的请求，同一秒内有的成功（2400ms）、有的瞬时 404（`comm_ms=0`）。
- 失败率稳定 **44~50%**，与 `_probe_data.jsonl` 里 ok / 404 计数吻合。
- 单连接 keep-alive **串行**请求 → `200/404/200/404` **严格交替**。
- **每次新建 TCP 连接 → 0 失败**；裸 socket 与 stdlib `http.client` → 全 200。
  ⇒ 只有 `requests`/urllib3 中招，服务端无辜。

**附带危害**：`_is_server_alive()` 要求 `/v1/models` 也返 200。被代理污染时它**恒 404**
⇒ 恒判宕机 ⇒ watchdog 把健康的 llama-server 反复杀掉重启（也是"推理服务离线"误报来源）。

**修复**（4 个文件，改动前均已备份）

| 文件 | 改动 |
|---|---|
| `llama_http_engine.py` | 新增 `_make_session()`（`trust_env=False`）+ `_HEALTH_SESSION`；404 时告警并自愈重建 Session 重试一次；HTTP 层错误不再触发切分 |
| `babeldoc_translator.py` | 失败**不静默返原文**，改返回 `⚠️⚠️⚠️ ` 前缀原文；404/503/超时 3~4 次指数退避重试 |
| `journal_title_zh.py` | 直连 8090 处同样免疫 |
| `web_app.py` `/api/check`、`portal/routes.py` 健康探测 | 同样 `trust_env=False` |

启动时另加 `NO_PROXY=127.0.0.1,localhost,<lan-ip>` 作双保险。

**验收数字**

| 项 | 结果 |
|---|---|
| `tools/verify_proxy_fix.py`（**故意保留 HTTP_PROXY**） | 40 段 / 8 线程 → `{'OK': 40}`，404 = 0 |
| 端到端 PDF（26 页，205 秒） | 未翻译率 **8.0% → 3.6%**，`⚠️` 失败标记 = **0** |
| 未翻译率三点对照 | 健康期 **2.9%** / 故障期 **8.0%** / 修复后 **3.6%** |
| 修复前 vs 修复后（HTTP 层，32 请求 × 8 线程） | **44% 失败** → **0% 失败** |

### 7.2 地雷二：`pdfminer.six` 被静默升级 → Parse 阶段硬失败

**症状**：任务 `status=failed, progress=0`，日志报
`AttributeError: 'PDFPageInterpreterEx' object has no attribute 'ncs'`
—— 连翻译都没开始（**Parse PDF** 阶段就崩了）。

**根因**

```
BabelDOC 0.2.33 的 PDFPageInterpreterEx 直接读写解释器上的 self.ncs
     babeldoc/pdfinterp.py:  self.ncs = self.csmap[...]  /  if self.ncs:
pdfminer.six 自 20250506 起重构：颜色空间状态搬到 self.graphicstate.ncs，
     且 init_state() 不再初始化 self.ncs（20250416 仍会设 self.ncs = None）
⇒ pdfminer>=20250506 + BabelDOC 0.2.33 → Parse 阶段 AttributeError
```

**实测**：`20250416` ✅ 可用；`20250506 / 20251107 / 20251227 / 20251230 / 20260107` ❌ 全崩。

**为什么"之前看着正常"**：22:05 升级的模块只有**新进程**才加载；已加载旧版的老进程不受影响，
所以 22:11 前还能翻，**重启后才暴露** —— 这也是 22:36 那次上传也失败的原因。

**修复**：降级并**锁定** `pdfminer.six==20250416`（写进 `requirements-lock.txt`，
含 `babeldoc==0.2.33` / `requests==2.34.2` / `urllib3==2.3.0` / `flask==3.1.1` /
`waitress==3.0.2` / `pymupdf==1.25.2` / `torch==2.5.1+cu121`）。

> `pip uninstall` 时若文件被占用会失败并让 pdfminer 处于**缺失**状态 ——
> 先停 Flask，再 `pip install --force-reinstall --no-deps pdfminer.six==20250416`。

### 7.3 地雷三（更早）：`_GATE` 嵌套 acquire → 12% 卡死

`BabelDOC` 的 `ThreadPoolExecutor` 把多段并发塞进 `translate()`；若每段又切 5 段，
递归各自 `with _GATE` → 外层 permit 永不释放 → 全部阻塞在递归 acquire。
py-spy 抓栈实锤 5 个 worker 卡在 `Semaphore.acquire`。

**修复**：拆成 `translate()`（切分/降级，**不进 GATE**）与 `_translate_one()`
（GATE 内一次 HTTP），见 §4 流 A。修复后 260 段 150s 完成。

### 7.4 地雷四（更早）：队列卡死（5 个缺陷叠加）

`_run_translate()` → `babel_translate()` 是同步阻塞且无法外部打断。
一旦 BabelDOC/llama-server 挂起，调度线程永久卡死 ⇒ 队列再也不消费。
另加 `wait_ready` 只判 `/health`（漏判 chat 端点未挂）导致 404 误判。

**修复**：调度线程 + 独立工作线程 + 双层超时（软 10min / 硬 30min）+ 看门狗 + 状态守卫；
`wait_ready` 改为严格判活（`/health` 200 **且** `/v1/models` 200）。
详见 §4 流 B。

---

## 8. 故障排查手册

| 症状 | 首要怀疑 | 定位手段 | 处置 |
|---|---|---|---|
| 大量段落没翻译 / 直接返回原文 | 代理污染 → llama 404 | `_probe_data.jsonl` 里 `status=http_error` 且 `comm_ms=0`；`session.trust_env` | 确认 §7.1 四个文件未被改回默认；重启 Flask |
| 前端"推理服务离线" | llama-server 真没跑，或 `/v1/models` 被代理污染 | `curl --noproxy '*' http://127.0.0.1:8090/v1/models`；`GET /api/portal/health` | 用 `run_in_background` 受管启动 llama-server |
| 任务 `failed, progress=0`，报 `no attribute 'ncs'` | pdfminer 版本被升级 | `python -c "import pdfminer;print(pdfminer.__version__)"` | 降级 `pdfminer.six==20250416`（先停 Flask） |
| 卡在某阶段不动（历史上 ~12%） | 引擎侧死锁 / server 挂起 | `py-spy dump --pid <flask_pid>`；看 `_GATE` 是否嵌套 | 确认 `translate()`/`_translate_one()` 拆分未被回退 |
| 队列"等待中"永不开始 | 调度线程已死 或 前置任务卡住 | `GET /api/_debug` 看 jobs 与 queue_size | 重启 Flask；确认 `_queue_worker` 的 `except` 未被删 |
| 归档页空白 | `RETENTION_SECONDS` < 窗口 | 对比两个常量；看 `translated/` 是否有近 48h 文件 | 调大保留期（§6 铁律 6） |
| `/journals` 窗口显示全部文章 | 时间源退化成 mtime | `window.ledger_available`；`journal_ledger.json` 是否存在 | 恢复台账；勿删 ledger（§6 铁律 5） |
| 卡片标题没有中文小字 | 标题缓存缺失/键规则不一致 | 对比 `journals/routes.py::_zh_of` 与 `journal_title_zh.py::_key` | 跑一次 `journal_title_zh.py`；缺缓存**不报错**是预期行为 |
| 改了 `.py` 没生效 | 未重启 | 看进程启动时间 vs 文件 mtime | 重启 Flask（§6 铁律 3） |
| 重复启动被拒 | `.flask_5555.pid` 锁 | `netstat` 看 5555 是否已监听 | 先终止旧进程再清 PID 锁 |
| 门户/某模块 500，翻译页正常 | 该模块注册失败被隔离 | 启动日志 `[模块] 注册完成：成功 N 个` | 按日志定位单模块；其余模块不受影响 |
| NAS 挂载失败 | 残留连接 / 盘符占用 / 不在内网 | 页面上的体检报告；`net use` 输出错误码 | 按 1219/85/67/53/5 提示处置；或跑 `all.ps1` |
| exe 默认地址是 `127.0.0.1` | 探测顺序 | `tools/nas_tool.py` 的 `CANDIDATES` | `kust.work`（80）应排最前 |

---

## 9. 回归测试与验证工具

### 9.1 DOM 回归（node，无需浏览器）

```bash
NODE="C:/Users/youyo/.workbuddy/binaries/node/versions/22.22.2-2/node.exe"
cd /d/PostNews/Translate
$NODE theme_dom_test.js                 # 主题：73 断言
$NODE queue_dom_test.js                 # 任务队列页：39 断言

# 期刊页：先取回真实页面与接口，再喂给测试
curl -s --noproxy '*' http://127.0.0.1:5555/journals            -o _j.html
curl -s --noproxy '*' http://127.0.0.1:5555/api/journals/list   -o _j.json
$NODE portal_journals_dom_test.js _j.html _j.json               # 59 断言
rm -f _j.html _j.json
```

> 改配色后**必跑** `theme_dom_test.js` —— 它会揪出「浅色漏配变量」。
> 前端逻辑改动后跑队列页测试 —— 它曾抓出 `r.ok === false` 误判（35 → 8 断言的回归）。

### 9.2 Python 工具（`Translate/tools/`）

| 工具 | 用途 |
|---|---|
| `verify_proxy_fix.py` | **故意保留 `HTTP_PROXY`** 的敌意环境验证：40 段 / 8 线程，要求全 OK、404=0。改网络代码后跑它 |
| `analyze_pdf_zh.py` | 量化译版 PDF 的**未翻译块占比**（对比原文块、统计 `⚠️` 标记）。`python tools/analyze_pdf_zh.py <pdf> [pdf2…]` |
| `make_dual.py` | 双语版离线补生成。`--list` 只列不动、`--force` 重拼、`--mode lr/tb/alt`、`--stem <串>` 过滤。译文 stem 去 `.no_watermark` 后到 `uploads/` 反查原文 |
| `clear_pdf_cache.py` | **清理本地 PDF 缓存**。默认只清 `translated/` 顶层译文；`--backup` 连修复前样本、`--uploads` 连上传原件、`--older N` 按 mtime、`--dry-run` 只看不删。**一律送回收站，绝不硬删** |
| `recycle.py` | 底层：把文件送进 Windows 回收站（`SHFileOperationW` + `FOF_ALLOWUNDO`），供上面的清理脚本调用 |
| `verify_queue_timeout.py` | 队列超时/看门狗行为验证 |
| `nas_tool.py` | NAS exe 的 GUI 源码与地址探测（`CANDIDATES`） |
| `build_nas_exe.py` | PyInstaller 打包 → `modules/share/assets/KustNasTool.exe`（`--noconsole --onefile`） |
| `verify_tasks_module.py` | 任务打卡**离线断言 147 条**：路由 / 静态资源 MIME+no-cache / 目录穿越防护 / 脚本关键点 / CSS 主题 / **RPG 改名（页面与脚本零 `NPC`、视图名白名单）** / **示例角色（常驻、重置清墓碑、不写 cookie、排序）** / 同步接口全流程。接口段把 `store.STORE` 重定向到临时目录，跑完对比确认真实库没被动过 |
| `verify_tasks_visual.py` | 任务打卡**浏览器验收 100 条**（独立实例 @5601 + 临时库）：无 cookie 弹窗 / 空角色 / 旧数据清理 / 双向同步 / 多角色隔离 / 绿浅深主题 / 375px 窄屏 / 零 JS 报错 / **示例角色闭环（不被自动选中 → 点进去有内容 → 恢复重置 → 退出不被缓存拉回）** / 导航与视图标题为「RPG 时间轴」且整页零 `NPC` |
| `check_tasks_live.py` | 任务打卡**线上端到端 31 条**（真写 5555 的库；开头与结尾都清掉所有非示例角色，只留 `demo`，可反复跑） |

### 9.3 接口巡检（curl）

```bash
for p in / /translate /daily /journals /palette /archive /share \
         /api/check /api/news/latest /api/queue /api/portal/health; do
  printf "%s  %s\n" "$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' \
                          "http://127.0.0.1:5555$p" --max-time 10)" "$p"
done
# 期望：除 /today 为 302 外，其余 200
```

> `--noproxy '*'` 在排查时很关键 —— 否则 curl 自己也会被环境代理干扰，得到假的失败。

### 9.4 埋点分析（翻译质量）

```bash
# 状态分布 / 错误类型 / 时间桶 / comm_ms 分布
python - <<'PY'
import json, collections, datetime
rows=[json.loads(l) for l in open('_probe_data.jsonl',encoding='utf-8') if l.strip()]
print('状态分布:', dict(collections.Counter(r.get('status') for r in rows)))
cm=[r.get('comm_ms',0) for r in rows if r.get('status')=='http_error']
print('404 comm_ms（应为 0，若 >0 则另有原因）:', cm[:5])
PY
```

**读法**：`status=http_error` + `comm_ms=0` + 时间上成簇 ⇒ 代理污染类问题；
`status=ok` 但 `completion_tokens` 很小 ⇒ 可能是零成本短路或被截断。

---

## 10. 常见变更操作指南

### 10.1 加一个新功能入口（3 步）

```
1. modules/my_feature/ 下建 routes.py，暴露 myfeature_bp 与 init_myfeature(app, **deps)
   页面路径带前缀（/myfeature），API 留根路径（/api/myfeature/...）
   ⚠️ 不要 import web_app；需要内部数据就加回调参数（见铁律 4）
2. modules/__init__.py 的 _MODULES 追加 ('我的功能', init_myfeature, False)
   （第三项 True = 需要注入 data_dir）
3. portal/registry.py 的 MODULES 追加一条 dict
   （id / group / name / desc / url / tag / status / accent / live）
```

注册输出会在启动日志里看到：`[模块] 注册完成：成功 N 个（…）`。

### 10.2 改路由 / 加 API

- 先读 §6 铁律 1，确认没有把 API 挪进前缀。
- 新增页面 → 用 `portal.templating.render_html()`，**不要** Jinja2。
- 需要新静态资产 → 加进 `portal/assets.py` 的 `_ASSETS` 白名单 + 建文件；
  `/theme.js` 已由 `web_app.after_request` 强制 `no-cache`。

### 10.3 清理本地 PDF 缓存

```bash
python tools/clear_pdf_cache.py --dry-run              # 先看清单（默认只清译文列表）
python tools/clear_pdf_cache.py                        # 清 translated/ 顶层（中文版+双语版）
python tools/clear_pdf_cache.py --backup --uploads     # 连修复前样本与上传原件一起清
python tools/clear_pdf_cache.py --older 48             # 只清 48 小时前的
```

**四条纪律**：

1. **绝不 `rm` / `del`**。全部走 `tools/recycle.py`（`SHFileOperationW` + `FOF_ALLOWUNDO`）
   送**回收站**，误删可还原。译文是可再生但代价不低的产物（一篇 2~3 分钟）。
2. **`SHFileOperationW` 返回码 2 ≠ 失败**。它常在实际成功时仍返回 `FILE_NOT_FOUND`。
   判断成功与否要**看文件是否还在原位**，不要看返回码（曾被这个误导以为数据丢了）。
   验证是否真的进了回收站：扫 `D:\$RECYCLE.BIN\<SID>\$I*`，偏移 28 起 520 字节是
   UTF-16LE 原始路径。
3. **删完重启 Flask**。内存里的 `jobs` 仍指向已删文件，前端会显示失效链接
   （不重启则要等 3600s 自动清理）。
4. **范围默认最小**。只清 `translated/` 顶层；`--backup` / `--uploads` 必须显式加。

> 清理后 `/api/archive/list?kind=dual|mono` 应返回 0 条，`/api/queue` 的
> `items` 应为 0（重启后）。

### 10.3 扩充色卡

```bash
python D:/PostNews/_build_palettes.py     # 色值一律从权威源抓取并存档到 _palette_src/
```
源：ggsci `R/palettes.R`、cmcrameri `cmaps/*.txt`、cmocean `rgb/*-rgb.txt`、matplotlib 内置。
**绝不凭记忆手写 hex**；抓不到就跳过并告警，不拿近似色凑数。
分组是运行时从 `palettes.json` 派生的，加新分组不用改前端。

### 10.4 改配色 / 加主题变量

1. 浅色变量只改 `portal/assets/theme.js` 的 `LIGHT_VARS`（唯一维护点）。
2. 深色兜底在各页面自己的 `:root` 里。
3. 新增页面接入只有一行 `<script src="/theme.js"></script>`，**必须同步加载**。
4. 跑 `node theme_dom_test.js`（73 断言）。

### 10.5 升级依赖

1. 先看 `requirements-lock.txt` 的红线：**`pdfminer.six` 必须 `< 20250506`**。
2. 升级后**必须重启 Flask**（新进程才加载新模块），并跑一次端到端 PDF 翻译。
3. 用 `tools/analyze_pdf_zh.py` 对比未翻译率（健康基线 ≈ 3%）。

### 10.6 重建 NAS exe

```bash
python D:/PostNews/Translate/tools/build_nas_exe.py
# → modules/share/assets/KustNasTool.exe
```
exe 不含脚本逻辑，改 `.ps1` 内容**不需要**重新打包。

### 10.7 本地定时回收（Windows 任务计划程序）

本地 PDF 的定期清理由**系统任务计划程序**驱动，不依赖 WorkBuddy、不依赖常驻进程。
全部脚本位于 `tools/schedule/`：

| 文件 | 作用 |
|---|---|
| `install_task.ps1` | 核心：注册 / 卸载 / 查状态 / 立刻触发（`-Uninstall` `-Status` `-RunNow` `-RunAlways`） |
| `install_task.bat` | 双击安装（管理员身份运行时自动切静默模式） |
| `uninstall_task.bat` | 双击卸载 |
| `status_task.bat` | 看任务状态 + 日志尾部 |
| `reap_daily.bat` | 手动跑一次（参数原样透传给 `nas_reaper.py`） |
| `README.md` | 使用说明与排错 |

任务名 **`PostNews-NASReaper`**，每日 09:00 / 21:00 触发，实际执行：

```
powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -Command
  "$env:PYTHONIOENCODING='utf-8'; $env:NAS_TTL_HOURS='48'; & '<python>' '<root>\tools\nas_reaper.py'"
```

改时间或阈值：`install_task.bat -Times 08:00,20:00 -Hours 24`，改完需重新注册才生效。

**三个实测踩过的坑（改这里之前必读）**：

1. **内层路径只能用单引号**。任务的参数是二次解析的，内层再套双引号会被 `powershell.exe`
   拆开，带空格的 `D:\Python 3.12.4\python.exe` 立刻失效 —— 表现为任务 LastResult=1。
2. **必须注入 `PYTHONIOENCODING=utf-8`**。定时任务的控制台代码页是 cp936，脚本一打印
   中文/emoji 就 `UnicodeEncodeError` 崩掉；脚本内另有 `sys.stdout.reconfigure` 兜底。
3. **路径一律基于项目根**（`nas_reaper.py` 的 `BASE` 常量），不依赖任务的工作目录。

脚本自带：运行锁 `data/nas_reaper.run.lock`（1 小时陈旧保护）、
日志 `logs/nas_reaper.log`（2 MB 轮转、保留 3 份）。

排查顺序：`reap_daily.bat` 复现 → `status_task.bat` 看 LastResult（必须为 `0`/`0x0`）。

---

## 11. 环境与依赖

| 项 | 值 |
|---|---|
| 系统 Python | `D:\Python 3.12.4\python.exe`（**项目脚本统一用它**） |
| 包安装位置 | `C:\Users\youyo\Python\Python312\site-packages`（**用户级，无虚拟环境**） |
| Node（跑 DOM 测试） | `C:\Users\youyo\.workbuddy\binaries\node\versions\22.22.2-2\node.exe` |
| llama.cpp | `Translate/llama-cpp/llama-server.exe`（CUDA 构建） |
| 模型 | `Translate/models-gguf/Qwen3.5-4B-Q4_K_M.gguf`（2.55 GB 主力）<br>`Qwen3.5-0.8B-Q4_K_M.gguf`（降级） |
| GPU | RTX 4060 8 GB |
| Flask | waitress，`0.0.0.0:5555`，threads=8，`static_folder=None` |
| 上传上限 | 200 MB（`MAX_CONTENT_LENGTH`） |
| 缓存策略 | `.pdf` / `.js` / `.css` → `max-age=3600, must-revalidate`；其余 `no-cache`；`/theme.js` 强制 `no-cache` |
| 保留期 | `uploads/` 与 `translated/` 各 48h（后台线程每 300s 清理一次） |
| 网络限制 | `github.com:443` 不通；`api.github.com` / `raw.githubusercontent.com` 通；SSH 22 通 |

**版本锁定（务必遵守）**

```
pdfminer.six==20250416    # ⚠️ 必须 < 20250506，否则 BabelDOC 0.2.33 解析崩
babeldoc==0.2.33
requests==2.34.2
urllib3==2.3.0
flask==3.1.3
waitress==3.0.2
pymupdf==1.25.2
torch==2.5.1+cu121
```

**端口占用**

| 端口 | 归属 |
|---|---|
| 8090 | llama-server（`127.0.0.1`） |
| 5555 | Flask（`0.0.0.0`，waitress） |
| 5555（`<lan-ip>`） | Windows 端口代理（`svchost.exe`），即 `kust.work` 转发入口 |

---

*本文档由 2026-09-13 实盘代码核对生成（42 条路由 / 常量 / 行数均已对照源码）。
改动架构或修 bug 后请同步更新本文档对应小节，并追加 `memory/YYYY-MM-DD.md` 日志。*
