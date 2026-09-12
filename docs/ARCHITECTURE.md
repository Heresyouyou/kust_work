# GeoTranslate · 地学论文离线翻译系统 — 架构文档

> 版本: v4 (2026-09-12) | 作者: 内部
> 目标 GPU: RTX 4060 Laptop (8GB VRAM) | 实测速度: \~27 tok/s decode, PDF ≈ 7MB/min

***

## 1. 系统总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                    GeoTranslate 系统架构                              │
│                                                                     │
│  ┌──────────┐    ┌──────────────────┐    ┌───────────────────────┐  │
│  │ 浏览器   │───▶│  Flask Web UI    │───▶│  run_babeldoc.py       │  │
│  │ index.html│   │  web_app.py      │    │  (BabelDOC v0.2.33)    │  │
│  │ :5555    │    │  :5555           │    │                       │  │
│  └──────────┘    └──────────────────┘    │  ┌─────────────────┐  │  │
│                                           │  │GeoBabelTranslator│  │  │
│                                           │  │(babeldoc_trans.)│  │  │
│                                           │  └────────┬────────┘  │  │
│                                           │           │           │  │
│                                           │  ┌────────▼────────┐  │  │
│                                           │  │LlamaBatchEngine │  │  │
│                                           │  │(llama_http_eng.)│  │  │
│                                           │  │_GATE=Semaphore(4)│ │  │
│                                           │  └────────┬────────┘  │  │
│                                           └───────────┼───────────┘  │
│                                                       │ HTTP         │
│                                                       ▼              │
│                                          ┌───────────────────────┐   │
│                                          │  llama-server         │   │
│                                          │  :8090                │   │
│                                          │  Qwen3.5-4B-Q4_K_M    │   │
│                                          │  parallel=4           │   │
│                                          │  ctx=16384 (unified)   │   │
│                                          └───────────────────────┘   │
│                                                                     │
│  数据流:  PDF → BabelDOC 解析 → 分段 → LLM 翻译 → BabelDOC 排版 → PDF  │
└─────────────────────────────────────────────────────────────────────┘
```

***

## 2. 目录结构（迁移时需要复制的）

```
Translate/
├── web_app.py              # Flask 后端 (API + 任务管理)
├── index.html              # 前端 SPA (内嵌 Flask, render_template_string) → /translate
├── portal/                 # ★ 门户层 (2026-09-12 新增): / 欢迎主页 + 入口注册表
│   ├── registry.py         #   功能入口注册表 —— 加入口只改这个文件
│   ├── routes.py           #   GET / · GET /api/portal/health · GET /theme.js
│   ├── assets.py           #   静态资产白名单 + mtime 缓存 (2026-09-13 新增, 见 §14)
│   ├── assets/theme.js     #   ★ 主题引擎: 浅色/深色, 按昆明日出日落自动切换 (见 §14)
│   ├── templating.py       #   静态模板工具 (不用 Jinja2, 见 §13)
│   └── templates/portal.html
├── modules/                # ★ 功能模块 (每个模块一个 Blueprint, 独立 try/except 注册)
│   ├── __init__.py         #   register_all() 统一注册
│   ├── news/               #   今日早报 → /today · /daily  (2026-09-12 新增)
│   ├── journals/           #   期刊列表 → /journals      (2026-09-12 新增)
│   ├── palette/            #   科研配色 → /palette       (2026-09-12 新增)
│   └── archive/            #   译文归档 → /archive（近 48h，按原文标题聚合，版本按钮直达 PDF）
├── _gen_palettes.py        # 配色数据生成脚本 → modules/palette/palettes.json
├── _apply_theme_vars.py    # (一次性) 6 个页面硬编码色 → CSS 变量, 已执行完毕勿重跑
├── _make_theme_previews.py # 生成 output/*_theme_preview.html 离线主题预览
├── theme_dom_test.js       # theme.js 的 DOM 垫片测试 (73 项断言, node 直接跑)
├── run_babeldoc.py         # BabelDOC 翻译主入口
├── babeldoc_translator.py  # GeoBabelTranslator (BabelDOC 自定义 Translator)
├── llama_http_engine.py    # LlamaBatchEngine (HTTP 调用 llama-server)
├── geogpt_terms.json       # 地质术语词典 (2168 en→zh)
├── llama-cpp/
│   └── llama-server.exe    # llama.cpp HTTP 服务端
├── models-gguf/
│   └── Qwen3.5-4B-Q4_K_M.gguf  # 主模型 (2.55GB, Q4_K_M)
├── uploads/                # (运行时) 上传的原 PDF
└── translated/             # (运行时) 译文 PDF 输出
```

## 3. 启动流程

### 3.1 手动方式（推荐调试用）

```powershell
# 终端 1: 启动 llama-server (常驻后台)
cd D:\PostNews\Translate
$env:PATH = "D:\Python 3.12.4\Lib\site-packages\torch\lib;" + $env:PATH
.\llama-cpp\llama-server.exe -m models-gguf\Qwen3.5-4B-Q4_K_M.gguf -ngl -1 -c 16384 -t 4 -b 2048 -ub 512 -cb --parallel 4 --kv-unified --cache-prompt --reasoning off --host 127.0.0.1 --port 8090

# 终端 2: 启动 Flask Web UI
cd D:\PostNews\Translate
py -3.12 web_app.py
# → 打开 http://127.0.0.1:5555
```

### 3.2 自动方式（生产用）

`web_app.py` 会在首次翻译时自动检测 llama-server 状态：

- `ensure_server_ready()` → 调 `/health` 端点

- 未运行则 `start_server()` → `subprocess.Popen(llama-server.exe ...)`

- watchdog 守护：每 60s 检查一次，挂了自动重启

### 3.3 一键脚本

`start_all.bat` 或手动依次执行两行。

***

## 4. 核心参数速查表

### 4.1 llama-server（性能核心）

| 参数               | 值                                    | 理由                                     |
| ---------------- | ------------------------------------ | -------------------------------------- |
| `-m`             | `models-gguf/Qwen3.5-4B-Q4_K_M.gguf` | 2.55GB, Q4\_K\_M, 地质领域表现好              |
| `-ngl`           | `-1`                                 | 全层 GPU offload (RTX 4060 8GB 够)        |
| `-c`             | `16384`                              | 全局 KV pool（unified-KV 模式，不是 per-slot）  |
| `-t`             | `4`                                  | CPU 线程数，跟随 parallel                    |
| `-b`             | `2048`                               | prefill batch，对 decode tps 无影响         |
| `--parallel`     | `4`                                  | **Sweet spot**（实测：N=2→4 吞吐翻倍，N→8 仅+3%） |
| `--kv-unified`   | on                                   | 所有 slot 共享 1 个 KV pool，显存不翻倍           |
| `--cache-prompt` | on                                   | 缓存 system prompt prefill（多请求节省 \~50ms） |
| `--reasoning`    | `off`                                | **关键**：禁用 CoT，decode tps 翻倍            |
| `--host`         | `127.0.0.1`                          | 仅本地监听                                  |
| `--port`         | `8090`                               | <br />                                 |

### 4.2 LlamaBatchEngine（Python 引擎层）

| 参数                     | 值                         | 代码位置                       |
| ---------------------- | ------------------------- | -------------------------- |
| `SERVER_URL`           | `http://127.0.0.1:8090`   | L35                        |
| `N_PARALLEL`           | `4`                       | L42                        |
| `N_CTX`                | `16384`                   | L41                        |
| `_GATE`                | `Semaphore(4)`            | L239 — **和 parallel 严格对齐** |
| `max_tokens cap`       | `1500`                    | L326                       |
| 自动切分阈值                 | `src > 2500 chars`        | `_split_text()`            |
| 自动切分递归深度               | `≤ 3`                     | <br />                     |
| auto-split max\_tokens | `512`                     | 递归段更保守                     |
| HTTP 超时                | `180s`                    | L356                       |
| Connection pool        | `10 conn, keepalive=True` | L246-248                   |

### 4.3 run\_babeldoc.py（BabelDOC 配置）

| 参数                            | 值                   | 代码位置                        |
| ----------------------------- | ------------------- | --------------------------- |
| `set_translate_rate_limiter`  | `4`                 | L117 — **关键：和 \_GATE 严格对齐** |
| `qps`                         | `8`                 | L212 — BabelDOC 派活节奏        |
| `disable_rich_text_translate` | `True`              | L213 — 不拆超链接，减少段数           |
| `skip_scanned_detection`      | `True`              | L209 — 论文都是数字 PDF，省 44s     |
| `table_model`                 | `None`              | L210 — 显式关 rapidocr         |
| `watermark_output_mode`       | `NoWatermark`       | L211 — 省 \~1min             |
| `use_side_by_side_dual`       | `False`             | L207 — dual 版由 PyMuPDF 拼接   |
| `enhance_compatibility`       | `False`             | L212                        |
| `cancel_event`                | `threading.Event()` | L100 — 用户取消信号（新增）           |

### 4.4 web\_app.py（Flask 后端）

| 参数        | 值                                                                           | 说明                    |
| --------- | --------------------------------------------------------------------------- | --------------------- |
| 端口        | `5555`                                                                      | <br />                |
| Worker 模型 | **单线程串行队列**                                                                 | 防止多任务竞争 GPU           |
| QUEUE 大小  | `64`                                                                        | <br />                |
| MAX\_JOBS | `12`                                                                        | 超过清理旧 job             |
| Job 字段    | `status/progress/message/queue_position/cancel_event/mono_url/dual_url/...` | `cancel_event` 不返回给前端 |

***

## 5. 数据流详解

### 5.1 完整翻译管线

```
用户上传 PDF
    │
    ▼
web_app.py → api_upload()
    ├─ 保存到 uploads/ (带 hash 后缀)
    ├─ 创建 job (status='queued', cancel_event=Event())
    └─ QUEUE.put(job_id)
    │
    ▼
_queue_worker() (单线程, daemon)
    ├─ job.status = 'translating'
    └─ _run_translate(job_id)
        │
        ├─ 绑定 _on_progress 回调
        ├─ babel_translate(src_path, output_dir,
        │        progress_callback=_on_progress,
        │        cancel_event=cancel_event)
        │
        ▼
run_babeldoc.translate_pdf()
    ├─ set_translate_rate_limiter(4)  ← 节流
    ├─ GeoBabelTranslator (自定义 BaseTranslator)
    ├─ TranslationConfig(qps=8, disable_rich_text=True, ...)
    ├─ ProgressMonitor + _babel_callback (桥接 BabelDOC v2)
    │     └─ ⚠️ 每次回调都检查 cancel_event → 触发 CancelError
    ├─ hl.do_translate(pm, config)  ← BabelDOC 主流程
    │     ├─ [Parse] PDF → IR (DocLayout-YOLO onnxruntime)
    │     ├─ [Translate] 分段 → 多线程派发 → GeoBabelTranslator.do_translate()
    │     │     └─ LlamaBatchEngine.translate(text)
    │     │           ├─ 术语匹配 → 注入 SYSTEM prompt
    │     │           ├─ auto-split (src>2500 → 递归 ≤3 层)
    │     │           ├─ _GATE.acquire() ← 关键闸门 (4 slot)
    │     │           ├─ POST http://127.0.0.1:8090/v1/chat/completions
    │     │           ├─ 错误重试 (HTTP 500 → 自动切分重试)
    │     │           └─ return 译文
    │     ├─ [Typeset] 按排版恢复译文到 PDF
    │     └─ [Output] Save PDF → translated/*.zh.mono.pdf
    │
    ├─ 捕获 CancelError → job.status='cancelled', message='已取消'
    └─ PyMuPDF 拼接 dual 版 (if mono 存在)
```

### 5.2 取消链路（新增）

```
前端 "✕ 取消" 按钮 → confirm('确定取消?')
    │
    ▼
POST /api/cancel/<job_id>
    │
    ├─ cancel_event.set()  ← 只这一句!
    │
    ├─ 如果 job.status == 'queued':
    │     直接 job.status = 'cancelled'  ← 瞬间完成
    │     (worker.get() 后会检查 status, 跳过)
    │
    └─ 如果 job.status == 'translating':
          job.message = '正在取消...'
          ↓ 等待下次 _babel_callback
          cancel_event.is_set() == True
          raise CancelError('用户取消')
          ↓ 冒泡
          run_babeldoc.py → 外层 except → 一路到 web_app._run_translate
          ↓ 捕获 CancelError
          job.status = 'cancelled'
          job.message = '已取消'
          ↓
          前端 poll 检测到 status='cancelled'
          显示 "已取消", 停止轮询, 隐藏 loadingWrap
```

### 5.3 分段-翻译-排版链路

```
BabelDOC IR 段 → GeoBabelTranslator.do_translate(text)
    │
    ▼
LlamaBatchEngine.translate(text, max_tokens=None, _depth=0)
    │
    ├─ 术语动态匹配: geogpt_terms.json.match(text)
    │     → 注入到 SYSTEM message (TERM_INJECT_PREFIX + hits)
    │
    ├─ auto-split 判断:
    │     src_len ≤ 2500 → 直接翻
    │     src_len > 2500 → _split_text() (段落→句子→硬切, 递归≤3)
    │
    ├─ ctx overflow 重试:
    │     POST → HTTP 500 (ctx exceeded) → 自动切分 → 递归重试
    │
    ├─ _GATE.acquire() ← Semaphore(4), 控制真正并发
    │
    ├─ POST llama-server:
    │     SYSTEM: SYSTEM_PROMPT + 动态术语表
    │     USER:   text.strip() (纯净, 只有正文!)
    │     max_tokens: min(1500, safe_cap)
    │     temperature: 0.0
    │
    └─ 返回译文 (BabelDOC 回收 → 排版)
```

***

## 6. API 文档（Web UI 后端）

| Method | Path                   | 说明                                                              |
| ------ | ---------------------- | --------------------------------------------------------------- |
| `GET`  | `/`                    | **门户欢迎主页**（portal 包，9 个入口 / 5 个可用）                              |
| `GET`  | `/translate`           | 翻译页（内嵌 index.html）※原先挂在 `/`                                     |
| `GET`  | `/today`               | 今日早报跳转：302 → 当日飞书文档；无链接时 302 → `/daily`                         |
| `GET`  | `/daily`               | 今日早报详情页（当日新闻 + 海报）                                              |
| `GET`  | `/daily/poster`        | 输出最新一张日报海报 PNG（文件名白名单校验）                                        |
| `GET`  | `/journals`            | 期刊列表页（14 种目标期刊 + 本地抓取的最新文章）                                     |
| `GET`  | `/palette`             | 科研配色页（21 组 / 199 色，点击复制色号）                                      |
| `GET`  | `/archive`             | 译文归档页（modules/archive）· 近 48h · 一篇论文一行                          |
| `GET`  | `/api/portal/health`   | 门户状态：`{llama: ok/loading/down, active: N}`                      |
| `GET`  | `/api/news/latest`     | 早报状态：`{date, is_today, url, has_url, news_count}`               |
| `GET`  | `/api/news/list`       | 当日新闻条目（读 PostNews/news.json）                                    |
| `GET`  | `/api/journals/list`   | 期刊聚合列表（合并 4 个数据源，去重）                                            |
| `GET`  | `/api/palette/list`    | 色板数据（读 modules/palette/palettes.json）                           |
| `GET`  | `/api/archive/list`    | 论文分组视图（默认）· `?kind=dual/mono/original/translated/upload` 兼容扁平视图 |
| `GET`  | `/api/check`           | 健康检查：llama-server + BabelDOC 状态 + 译版缓存                          |
| `POST` | `/api/check`           | `{name: "xxx.pdf"}` → 查译版缓存                                     |
| `POST` | `/api/upload`          | 上传 PDF → 返回 `{job_id, status, name}`                            |
| `GET`  | `/api/status/<job_id>` | 任务状态 + 进度 + 排队位置                                                |
| `POST` | `/api/cancel/<job_id>` | 取消任务 → queued 直接跳过, translating 发送信号                            |
| `GET`  | `/api/_debug`          | 内部：所有 jobs + engine 状态                                          |
| `GET`  | `/uploads/<f>`         | 静态文件：原 PDF                                                      |
| `GET`  | `/translated/<f>`      | 静态文件：译文 PDF                                                     |

### Job Status 枚举

| 状态            | 说明   | 前端表现                         |
| ------------- | ---- | ---------------------------- |
| `queued`      | 排队中  | 显示队列位置，`cancelBtn` 显示 "取消排队" |
| `translating` | 翻译中  | 显示进度条，`cancelBtn` 显示 "取消翻译"  |
| `done`        | 完成   | 显示 PDF，下载按钮                  |
| `cancelled`   | 用户取消 | 显示 "已取消"                     |
| `failed`      | 错误   | 显示 "错误" + 错误信息               |

***

## 7. Prompt 架构

### 7.1 SYSTEM\_PROMPT（固定）

```
你是专业地质学术论文翻译。规则:
1. 完整翻译为简体中文, 数字/化学式/同位素比值保持原样
2. 禁止 XML/HTML 标签, 翻译完整不中断

核心术语:
矿物: chalcopyrite→黄铜矿, pyrite→黄铁矿, quartz→石英...
岩石: granite→花岗岩, basalt→玄武岩...
矿床: porphyry→斑岩型, hydrothermal→热液...
蚀变: alteration→蚀变, silicification→硅化...
时代: Archean→太古宙, Proterozoic→元古宙...
术语: isotope→同位素, mineralization→矿化
```

### 7.2 动态术语注入（运行时）

```python
_dyn_system = SYSTEM_PROMPT
if _gloss:  # text 里命中的 geogpt_terms.json 术语
    _dyn_system = SYSTEM_PROMPT + "\n\n" + TERM_INJECT_PREFIX + hits
# TERM_INJECT_PREFIX = "术语对照(必须采用; 原文中的数字与单位必须照抄, 禁止换算): "
```

**关键：术语表放 SYSTEM message，绝不 prepend 到 USER message！**（之前放 USER 导致术语表被 LLM 当成正文输出）

### 7.3 USER message

```python
# 短段 (< 100 chars) → 明确指令
USER: "Translate this English text to concise Chinese. Keep it short but complete.
       Output ONLY the Chinese translation:\n{text.strip()}"

# 长段 (≥ 100 chars) → 纯净正文, BabelDOC 已分段
USER: "{text.strip()}"
```

### 7.4 模型参数

| 参数            | 值                     | 理由                                              |
| ------------- | --------------------- | ----------------------------------------------- |
| `temperature` | `0.0`                 | 确定性输出，学术翻译不发散                                   |
| `max_tokens`  | `min(1500, safe_cap)` | 保守不截断，safe\_cap = CTX - prompt\_est - 500       |
| 禁用 CoT        | `--reasoning off`     | 必须！否则 decode tps 减半且输出被 `<think>...</think>` 污染 |

***

## 8. 性能模型

### 8.1 GPU SM 饱和度（裸测，RTX 4060 3072 CUDA cores）

```
parallel=1: 55.2 tok/s per req  总吞吐 55 tok/s    基准
parallel=2: 53.9 tok/s per req  总吞吐 102 tok/s   +85% (几乎没损失)
parallel=4: 41.0 tok/s per req  总吞吐 159 tok/s   +189% ★ SWEET SPOT
parallel=6: 33.0 tok/s per req  总吞吐 189 tok/s   +243% (per req 崩)
parallel=8: 25.5 tok/s per req  总吞吐 194 tok/s   +253% (收益趋零)
```

### 8.2 完整管线（端到端）

```
BabelDOC ThreadPoolExecutor(固定 64 线程)
    ↓ rate_limiter=4 (每秒 4 令牌)
    ↓ 不排队地派活 → LlamaBatchEngine._GATE (Semaphore 4)
    4 个立即进入, 其余排队
    ↓ llama-server --parallel 4
    4 prompt 同时处理, GPU 饱和
    ↓ 每个 segment avg 5s
    gate_wait(row N) ≈ (N-4)/4 × 5s

Skarn Epidote 27MB/28pg/284segs 实测:
  gate/comm = 330% (gate_wait 是 LLM 推理时间的 3.3 倍)
  总 wall ≈ 220s, avg 0.77s/segment
```

### 8.3 理论吞吐公式

```
总耗时 = 排队时间 + LLM 推理时间 + BabelDOC 排版/输出

排队时间 = (总段数 - _GATE) / _GATE × avg_segment_llm_time
         = (N - 4) / 4 × 5s

例: 200 段, avg 5s/段
    排队 = 196/4 × 5 = 245s
    LLM  = 200 × 5 = 1000s (但并行, 实际是 200/4 × 5 = 250s)
    实际 ≈ 245 + 250 = 495s → 独立脚本实测 San Albino 254s (更优)
```

### 8.4 为什么不能把 parallel 开更大？

```
parallel=8: 总吞吐 +3% (GPU SM 饱和了, 几乎没增益)
但每个请求 tps 从 41 降到 26
→ BabelDOC 是串行管线 (翻译完一段才排版)
→ 单 segment wall time 翻倍 → 总时长反而略增
→ RTX 4060 8GB 的 sweet spot 就是 parallel=4
```

***

## 9. 术语泄露防护（踩过的坑）

| Bug                        | 现象                            | 根因                                  | 修复                                 |
| -------------------------- | ----------------------------- | ----------------------------------- | ---------------------------------- |
| 术语表放 USER                  | 译文里出现 "术语对照(必须采用): gold→金..." | LLM 把 USER 里的所有内容都当正文               | 术语表全部放 SYSTEM message              |
| 动态术语表前缀                    | 译版段落开头有 "矿床→造山型金矿床"           | TERM\_INJECT\_PREFIX prepend 到 USER | 同上 → SYSTEM                        |
| auto-split cap=512         | References 大段被截断半译            | 递归切分 max\_tokens 太小                 | 改成 `min(1500, safe_cap)`           |
| rich\_text\_translate=True | 段数暴增 (BabelDOC 拆超链接)          | 蓝色超链接被拆成独立 segment                  | `disable_rich_text_translate=True` |
| rate\_limiter > \_GATE     | gate\_wait 雪崩 (25s+)          | BabelDOC 64 线程同时抢 \_GATE=4          | `rate_limiter ≤ _GATE`, 严格对齐       |
| parallel > 4               | 总时长略增                         | GPU SM 饱和, 单请求 tps 崩                | parallel=4 不变                      |
| Flask 不 reload             | 改了参数没生效                       | Python 模块加载后不自动重新 import            | 改完代码必须重启 Flask                     |

***

## 10. 迁移清单

### 10.1 硬件要求

| 项目       | 最低   | 推荐      | 说明                              |
| -------- | ---- | ------- | ------------------------------- |
| GPU VRAM | 4GB  | **8GB** | 4B-Q4 模型 2.55GB + KV pool 3.5GB |
| 存储       | 10GB | 20GB    | 模型 + 依赖 + 运行时缓存                 |
| CPU      | 4 核  | 8 核     | DocLayout-YOLO + BabelDOC 线程池   |
| 内存       | 8GB  | 16GB    | BabelDOC 解析大 PDF 时峰值 2GB+       |

### 10.2 软件依赖

```powershell
# Python 3.12
py -3.12 -m pip install flask requests PyMuPDF onnxruntime
py -3.12 -m pip install babeldoc==0.2.33   # ⚠️ 版本锁定!

# llama.cpp (独立下载, 编译或用预编译包)
# https://github.com/ggerganov/llama.cpp/releases
# 需要支持 --parallel 和 --kv-unified
```

### 10.3 迁移步骤

```
1. 复制代码: web_app.py, index.html, run_babeldoc.py,
             babeldoc_translator.py, llama_http_engine.py,
             geogpt_terms.json, llama-cpp/, models-gguf/

2. 安装依赖 (见 10.2)

3. 修改 paths (如需):
   - llama_http_engine.py L35: SERVER_URL
   - llama_http_engine.py L155: model_path 默认位置
   - web_app.py L16-18: UPLOAD_DIR / TRANSLATED_DIR

4. GPU 驱动确认 nvidia-smi 正常

5. 启动 llama-server (参数见 3.1)

6. 启动 Flask (py -3.12 web_app.py)

7. 访问 http://127.0.0.1:5555 → 上传测试 PDF

8. 冒烟:
   curl -X GET  http://127.0.0.1:8090/health
   curl -X POST http://127.0.0.1:5555/api/upload (选 PDF)
   curl -X POST http://127.0.0.1:5555/api/cancel/<job_id>
```

### 10.4 可选：换模型

| 模型                      | 大小     | 适用               |
| ----------------------- | ------ | ---------------- |
| Qwen3.5-4B-Q4\_K\_M (主) | 2.55GB | 地质论文，速度/质量平衡     |
| Qwen3.5-0.8B-Q4\_K\_M   | 0.5GB  | 极低端 GPU fallback |
| Hunyuan-MT-7B-Q4\_K\_M  | 4.31GB | 质量优先，需 ≥6GB VRAM |

模型参数只改 llama-server 的 `-m` 路径，其他不用动（prompt 兼容）。

***

## 11. 已知限制

1. **BabelDOC 0.2.33 版本锁死** — PyPI 最新 0.6.4，跨越巨大更新但 API 变化大暂不升级
2. **Flask 单线程串行队列** — 防止多 PDF 同时跑抢 GPU
3. **取消延迟** — translating 状态下等下次 BabelDOC progress callback（最快 \~1 segment，最慢几秒）
4. **RTX 4060 8GB 硬瓶颈** — parallel=4 是理论极限，想更快必须换硬件或换推理后端（TensorRT-LLM/vLLM）
5. **Windows only** — DocLayout-YOLO onnxruntime DML + llama-server.exe 原生 Windows
6. **geo 术语表 2168 条** — 运行时动态匹配，如果目标领域不同需要换 geogpt\_terms.json

***

## 12. 快速故障排查

| 现象                         | 原因                              | 诊断                                                                               |
| -------------------------- | ------------------------------- | -------------------------------------------------------------------------------- |
| Flask 启动 crash             | `ModuleNotFoundError: babeldoc` | `pip install babeldoc==0.2.33`                                                   |
| `/api/check` 返回 llama=down | llama-server 没启动                | 手动启动或 `py -3.12 -c "from llama_http_engine import start_server; start_server()"` |
| 译文有术语表文字                   | 术语被 prepend 到 USER              | 检查 `_gloss` 是否只在 SYSTEM message                                                  |
| 段速很慢 (30-40s)              | gate\_wait 雪崩                   | 看 `_probe_data.jsonl` 里 gate\_ms 是否 >> comm\_ms → `rate_limiter` 太大              |
| ctx exceeded (HTTP 500)    | auto-split 失败                   | 看 `_split_text()` 深度 ≥ 3 还是没切够                                                   |
| 取消按钮点了没反应                  | cancel\_event 没传递               | 确认 run\_babeldoc.translate\_pdf 收到 cancel\_event 参数                              |
| Flask 改了代码没生效              | Python 模块不 reload               | kill Flask 进程 → 重启                                                               |
| RTX 4060 VRAM 不够           | parallel=4 也 OOM                | 检查 `--kv-unified` 是否启用；或换 Qwen3.5-0.8B                                           |

***

## 13. 门户层与功能模块（2026-09-12 新增）

kust.work 根路径已改为**门户工作台**，翻译页退到 `/translate`。完整说明见 `PORTAL_README.md`。

**架构（方案 B：模块化单体）**

```
GET /            门户页        portal/routes.py     ← 9 个入口，3 个分组，5 个可用
GET /translate   翻译页        web_app.py index     ← 原 / 的内容，一字未改
GET /today       今日早报      modules/news/        ← 302 跳当日飞书文档
GET /daily       早报详情      modules/news/        ← news.json + 最新海报
GET /journals    期刊列表      modules/journals/    ← 近 60 天「获取窗口」（5 源 + 获取台账）
GET /palette     科研配色      modules/palette/     ← palettes.json 21 组 199 色
GET /archive     归档页        modules/archive/     ← 近 48h 论文分组，版本按钮直达 PDF
/api/*  /uploads/*  /translated/*   全部留在根路径 ← 关键
```

**铁律：翻译页的 API 与静态资源不能带前缀。**
`index.html` 的 4 处 `fetch`（`/api/check` `/api/upload` `/api/status/` `/api/cancel/`）
和后端产出的 PDF URL（`/uploads/*` `/translated/*`）都是根绝对路径 ——
所以页面可以搬到 `/translate`，但 API 必须留在根。这条约束让翻译模块**前端 0 行改动**。

**为什么门户页不用 Jinja2**
`render_template_string` 会把 `{` `}` 交给模板引擎，而门户页 CSS/JS 大括号密集。
`portal/templating.py` 改用纯字符串替换 + mtime 热重载（改 HTML 免重启；
改 `.py` 或 `palettes.json` 仍必须重启，见 §9 最后一行）。

**加新入口**：`portal/registry.py` 追加一条 + `modules/` 下建 Blueprint + 在
`modules/__init__.py` 的 `_MODULES` 里加一行，三步搞定，门户卡片自动出现。

**动态卡片机制（今日早报 / 期刊列表用）**
卡片渲染时带 `data-mod="<id>"`，角标 `<span>` 带 `data-tag="<id>"`。
`portal.html` 底部的第二个 IIFE 按 id 取数并回写 `href` / 角标 / 描述 ——
**天天变的内容（飞书链接、新文数量）不进 HTML 模板，模板保持恒定**。
JS 即便完全失效，`/today` 的服务端 302 仍能兜住跳转，可用性不依赖 JS。

**早报链接的落盘来源**
`daily_run.py` 的 `_record_latest_daily()` 在拿到飞书 `doc_url` 后，
增量合并写入 `PostNews/daily_push_state.json` 的 `latest_daily` 键。
该函数整体 try/except —— 门户是附加功能，任何情况下不拖累推送主流程。
注意：门户侧（`modules/news`）**只读** PostNews 的数据文件，绝不写入。

**容错**：门户、以及每个功能模块的注册都各自包在 try/except 里 ——
门户挂了 `/` 自动回落成翻译页；某个模块写坏只影响它自己的入口。
不让任何一层变成翻译服务的单点故障。

**译文归档的聚合方式（modules/archive）**
以「清洗后的文件名」为分组键，把**同一篇论文的三个版本聚成一行**：

| 原始文件名                                 | 清洗后标题 |
| ------------------------------------- | ----- |
| `X_a775d2f5.pdf`                      | `X`   |
| `X_a775d2f5.no_watermark.zh.dual.pdf` | `X`   |
| `X_a775d2f5.no_watermark.zh.mono.pdf` | `X`   |

清洗：去 `.zh.dual.pdf` / `.zh.mono.pdf` → 去 `.no_watermark` → 去 `_xxxxxxxx` 哈希。
标题优先取原文的。行内三个版本按钮是普通 `<a target="_blank">`，
直接指向既有的 `/uploads/*` 与 `/translated/*` 静态路由 ——
它们返回 `Content-Disposition: inline`，由**浏览器原生 PDF 阅读器**负责查看与下载。
服务端不做任何下载封装，因此没有附件流 / 内存打包 / 临时文件带来的报错面。

**⚠️ 48 小时窗口 ↔ 文件保留期，两者必须一致**

| 常量                              | 位置                          | 含义                   |
| ------------------------------- | --------------------------- | -------------------- |
| `ARCHIVE_WINDOW_HOURS = 48`     | `modules/archive/routes.py` | **展示**窗口，只决定"显示什么"   |
| `RETENTION_SECONDS = 48 * 3600` | `web_app.py`                | **实际**删除阈值，决定"文件活多久" |

改造前 `RETENTION_SECONDS` 是 `7200`（2 小时），会让 48 小时窗口永远是空的。
调小任何一个之前先想清楚两者的关系。

***

## 14. 全局主题（浅色 / 深色，2026-09-13 新增）

**需求**：全局可切浅色/深色；日间（昆明日出后、日落前）默认浅色，夜间默认深色；
右上角可手动覆盖。

### 14.1 三层机制

| 层    | 位置                                                      | 作用                | 失效后果      |
| ---- | ------------------------------------------------------- | ----------------- | --------- |
| 深色变量 | 6 个页面各自的 `<style>:root`                                 | 默认值 + 兜底          | —         |
| 浅色覆盖 | `portal/assets/theme.js` 注入 `:root[data-theme="light"]` | 浅色配色 + 按钮样式       | 退化为「只有深色」 |
| 主题决策 | `theme.js` 顶部同步执行 + 右上角按钮                               | 判定并写 `data-theme` | 退化为「只有深色」 |

浅色用属性选择器 `:root[data-theme="light"]`（权重 0,2,0）盖住各页面的 `:root`（0,1,0），
**因此不依赖** **`<link>`/`<style>`** **的先后顺序，也不怕页面样式表反向覆盖**。
深色则始终留在页面内联 —— JS 全挂页面也完整可读。

### 14.2 接入方式（加新页面时照抄）

```html
</style>
<script src="/theme.js"></script>   <!-- 必须同步！加 defer/async 会闪一下深色 -->
</head>
```

页面 `<head>` 里**只有这一行**。按钮由 theme.js 自己 `appendChild` 到 `<header>` 末尾，
不需要在 HTML 里预置任何元素。

### 14.3 三态模式

`localStorage['kust.theme.mode']`：`auto`（默认）/ `light` / `dark`。
右上角按钮点击循环 `自动 → 浅色 → 深色 → 自动`，同域所有页面、所有标签页共用；
通过 `storage` 事件做跨标签同步。`auto` 下每 60 秒 + `visibilitychange` 时重判，
跨过日出/日落会自己翻转。

### 14.4 昆明日出日落算法（纯本地，不联网）

`GEO = { lat: 25.0389, lon: 102.7183, tz: 8 }`，NOAA 简化式（太阳视高度 −0.833°）：

```
n      = ceil(JD(当地日) − 2451545.0 + 0.0008)      ← ★ 必须取整
J*     = n − lon/360
M      = 357.5291 + 0.98560028·J*
C      = 1.9148·sinM + 0.02·sin2M + 0.0003·sin3M
λ      = M + C + 180 + 102.9372                      ← 太阳黄经
Jtrans = 2451545.0 + J* + 0.0053·sinM − 0.0069·sin2λ
δ      = asin(sinλ · sin23.44°)
cosω   = (sin(−0.833°) − sinφ·sinδ) / (cosφ·cosδ)
日出/日落 = Jtrans ∓ ω/360   → 转成昆明当地分钟数
```

> **踩过的坑**：`n` 漏了 `Math.ceil` 会把「当地日内的偏移」重复计入 `J*`，
> 使 `Jtransit` 整体偏移 **恰好等于时区的时数**（昆明偏 8 小时）。
> 症状极具迷惑性：日出算成 22:55、日落算成 11:18，rise/set 颠倒但日长看起来正常。

精度：±1\~2 分钟（受 `n` 的整日量化影响），判断日夜绰绰有余。

### 14.5 变量表（21 行 / 48 个变量）

变量表在 6 个页面的 `:root` 里**逐字一致**，新增颜色一律走变量。
一致性由 `theme_dom_test.js` 自动断言：它从 `portal.html` 抽出深色变量名清单，
再检查浅色块是否**一个不漏**地覆盖 —— 漏一个就会让某个组件「半深半浅」，
这是本机制最容易出错的地方，所以做成自动检查而不是靠人眼。

命名分组：面（`--bg/--surface/--surface-2/--surface-3`）、线（`--border/--border-hover`）、
字（`--text/--muted/--dim/--link`）、主色（`--accent` 系列）、语义（`--warn/--err`）、
分档徽章（`--tier-{strong,mid,low}-{bg,fg,bd}`）、组件（`--kw-*` `--sw-*` `--scroll-*` …）。

### 14.6 验证

```bash
node theme_dom_test.js        # 73 项：算法 / 模式 / 按钮 / 注入样式 / 降级，全绿
py -3.12 _make_theme_previews.py   # 生成 output/*_theme_preview.html 离线预览
```

`theme_dom_test.js` 用自建 DOM 垫片（**不引入 jsdom**，省几百 MB），
可注入「假 Date」来测任意时刻的主题判定 —— 这是纯静态检查做不到的。

### 14.7 刻意不随主题变的颜色

PDF 白纸底（`.scroll canvas` / `.pdf-frame` 的 `#fff`）、悬浮在 PDF 上的页码条与
进度卡（半透明黑）、断线提示（红）、完成态绿。这些浮在白色 PDF 之上，
换成浅色反而不可读。

### 14.8 缓存

`/theme.js` 走 `no-cache`（见 `web_app.py` 的 `after_request`），
其余 `.js/.css/.pdf` 仍是 1 小时强缓存。主题引擎属配置型资源，改完要立刻生效。
服务端另有 mtime 缓存（`portal/assets.py`），改 JS 不用重启 Flask。

***

## 15. 期刊列表「近 60 天获取窗口」（2026-09-13 新增）

卡片里展示什么，口径从「本轮抓取到的」改成「**本地近 60 天获取到的**」——
判据是**本地日期时间**，而不是期刊方的发布日期。

### 15.1 为什么必须新建获取台账

既有数据源全都没有「获取时间」这一个维度：

| 数据源                           | 时间信息        | 能不能当获取时间            |
| ----------------------------- | ----------- | ------------------- |
| `journal_pushed.json`         | `pushed_at` | ✅ 秒级，但只覆盖"已推送"的少数文章 |
| `journal_batch_pending.json`  | 顶层 `date`   | ⚠️ 只有天粒度            |
| `journal_updates.json`        | 无           | ❌ **每次运行被整体重写**     |
| `journal_article_buffer.json` | 无           | ❌ 同上                |

**致命陷阱**：拿文件 mtime 当获取时间 → 文件每次重写都把时间"刷新"成今天
→ 文章永远老化不出窗口 → 60 天窗口退化成「显示全部」。
所以 `journal_ledger.py` 建了 `journal_ledger.json`：
以**标题指纹**为键，首次写入 `first_seen` 后**永不改写**，窗口才能随时间自然老化。

```
D:/PostNews/journal_ledger.json
{
  "_meta": {"updated_at": "...", "retention_days": 180, "count": 314},
  "journals": {"Geology": [{"title": "...", "url": "...",
                            "first_seen": "2026-09-09T07:57:36",
                            "source": "pushed|fetch|backfill", ...}]}
}
```

打点时机（两处，缺一不可）：

- `journal_checker.check_and_fetch()` 每轮**全量抓取**后 → `record_seen()`
  （只登记"新增"文章会让台账漏掉首次运行时的存量）

- `journal_checker.record_pushed()` → `record_pushed_seen()`，用它各自的
  `pushed_at` 作为 `first_seen`（保真，不改写已有记录）

保留期 180 天（>60 天窗口，留足核查余量），每轮检测后自动 `prune()`。

### 15.2 时间解析优先级（`_collect_recent`）

```
台账 first_seen  >  pushed_at  >  批次 date  >  文件 mtime  >  视为近期（计入 undated）
```

前 4 个都能随时间长旧，会正常老化出窗口。最后那个兜底只针对"解析不出时间"的
存量条目 —— 这些是当前列表里的东西，**没法证明它超窗**，保守保留并在
`window.undated_total` 里报出来，便于事后核查（当前实测 = 0）。

### 15.3 接口契约（`GET /api/journals/list`）

```jsonc
{
  "window": {"days": 60, "from": "2026-07-15", "to": "2026-09-13",
             "now": "2026-09-13 00:47", "ledger_available": true,
             "ledger_updated_at": "...", "undated_total": 0, "expired_total": 0},
  "max_per_card": 6, "max_expand_per_card": 80,
  "groups": [{"region": "国际", "journals": [{
     "article_total": 70,                       // 近 60 天总数
     "articles": [ /* 最多 80 条，按获取时间倒序 */ ],
     "article_newest": "09-13",
     "article_undated": 0,
     "topic_total": 15, "topic_articles": [...] // 主题归档视图不受影响
  }]}]
}
```

每篇文章带 `obtained_at`（ISO）/ `obtained_label`（同年 `MM-DD`，跨年 `YYYY-MM-DD`）。

### 15.4 前端行为

- 视图名「近 60 天」，徽章「60 天内 N 篇 / 60 天内无」

- 单卡默认 6 条折叠，超出给「展开其余 N 篇 ↓ / 收起 ↑」
  （**事件委托挂在** **`#body`** **上** —— 卡片整体会被重渲染，直接绑在卡片上会失效）

- 视图切换栏另给一个「**全部展开 ↓ / 收起全部 ↑**」总开关（`#allbtn`）。
  只在「近 60 天」视图出现：该视图条目**全部随载荷下发**，能真铺开；
  「主题归档」的单卡上限是**服务端裁剪**的（`_MAX_TOPIC_PER_CARD = 8`），
  前端铺开也拿不到更多数据，故不给按钮（可见性由 `syncAllBtn()` 按 `VIEW` / `REGION` 决定）。
  单卡下发上限 `_MAX_EXPAND_PER_CARD = 200`（当前最大单刊 70 篇，留足余量不截断）；
  真超了前端会明示「另有 N 篇超出单卡展示上限」，**不假装已全量**。

- 条目尾部显示本地获取日期，悬浮显示完整 `obtained_at`

- 汇总行 / 右上角展示窗口边界与台账状态

### 15.5 验证

- 边界：59/60 天前保留、61 天前剔除、正好 60 天保留（含边界）

- 去重：台账（官网 URL）vs updates（DOI URL）同标题 → 合成 1 条

- 排序：按获取时间倒序；无时间戳 → 保留并计入 `undated`

- `portal_journals_dom_test.js` 在线模板 **59/59**、离线快照 **54/54**
  （快照页数据内嵌，5 条合成载荷断言自动跳过）

- 13 个端点全部 200（`/today` 的 302 是跳转当日文档，属预期），主题归档视图 119 篇 / 13 刊未受影响

### 15.6 上线前必做：重启 Flask

**改过** **`.py`** **之后不重启，线上跑的还是旧代码**，症状是「页面能打开、不报错、就是数据少」——
2026-09-13 实际踩到：`/api/journals/list` 只返回 **13 条**（只剩 `journal_updates.json` 的内容），
而台账里明明有 **314 条**。根因是 Flask 进程启动于 23:23，早于 `modules/journals/routes.py`
的改动（00:47），**旧代码根本不读** **`journal_ledger.json`**。

`portal/templating.py` 的 mtime 热重载**只覆盖 HTML**，`.py` 不在内。

```bash
curl -s http://127.0.0.1:5555/api/journals/list | grep -c '"window"'   # 0 = 旧代码，1 = 新代码
MSYS_NO_PATHCONV=1 taskkill /PID <pid> /T /F                          # 先杀（PID 锁会拒绝重复启动）
py -3.12 web_app.py                                                   # 再起
```

### 15.7 首次部署怎么做

```bash
py -3.12 journal_ledger_backfill.py --fetch   # 历史回填 + 全量抓取采集（一次性）
py -3.12 journal_ledger.py                    # 查看台账摘要
py -3.12 journal_ledger.py --prune            # 只做保留期裁剪
```

`--fetch` 会真实联网抓 14 个期刊（约 2 分钟）。它做的事和 `journal_checker`
每轮运行一样，只是单独跑一次，好让门户第一天就有内容。

***

*文档自动生成于 2026-09-12，§14–§15 于 2026-09-13 增补，所有数据来自实测。*
