# kust_work · 地学科研工作台（Web 站）

一个自托管的地学工作台：**期刊追踪 + 文献翻译 + 译文归档 + 今日早报 + 科研配色**，
单一 Flask 进程跑在 `0.0.0.0:5555`，全部数据留在本地，不依赖任何外部 SaaS。

> 本仓库只做**版本管理**。运行时工作区是 `D:\PostNews\Translate`（Flask 必须从那里启动，
> 导入路径才对得齐），两边靠 `tools/sync_from_workspace.py` 同步，避免手工拷贝漂移。
>
> ⚠️ **本仓库是公开的**。同步脚本在写入前会自动脱敏：内网主机名、共享名、私网 IP、
> 内网账号一律替换成 `<nas-host>` / `<lan-ip>` / `<nas-user>` 占位符。
> 因此 clone 下来**必须自己配**环境变量（见 §5.3），代码本身不含任何真实凭据。

---

## 1. 站点地图

| 路径 | 说明 | 归属模块 |
|---|---|---|
| `/` | 门户工作台：功能卡片墙 + 今日早报动态绑定 | `portal` |
| `/translate` | 文献翻译：上传英文地学 PDF → 中文对照译文 | `web_app.py` + `index.html` |
| `/journals` | 期刊列表：双视图；页顶「今日更新」板块 = 当天获取到的文章跨刊汇总 | `modules/journals` |
| `/archive` | 译文归档：近 48 小时译文，按论文分组 | `modules/archive` |
| `/today` | 今日早报：302 跳转到当日飞书文档 | `modules/news` |
| `/daily` | 早报详情页（海报 + 条目） | `modules/news` |
| `/palette` | 科研配色：37 套权威色板 | `modules/palette` |
| `/translate/queue` | 翻译队列：文献翻译 + 标题翻译双队列 | `modules/queue` |
| `/theme.js` | 主题引擎（同步加载，必须早于渲染） | `portal/assets` |

配套接口：

| 接口 | 用途 |
|---|---|
| `GET /api/portal/health` | 门户健康检查 |
| `GET /api/journals/list` | 期刊页两个视图的数据（一次性返回） |
| `GET /api/archive/list` | 归档列表 |
| `GET /api/news/latest` · `/api/news/list` | 当日早报 / 历史早报 |
| `GET /api/palette/list` | 色板数据 |
| `POST /api/upload` · `GET /api/status/<job_id>` · `POST /api/cancel/<job_id>` | 翻译任务的上传 / 轮询 / 取消 |

---

## 2. 目录结构

```
kust_work/
├── web_app.py                 Flask 入口：路由、PID 单实例锁、缓存头、翻译任务队列
├── index.html                 翻译页（上传 + 进度 + 双语预览）
├── bilingual.py               ★ 双语对照版：lr / tb / alt 三形态拼接，原子写出
├── nas_archive.py             ★ NAS 文献仓库：三版本归档、JSON 索引、检索回捞、本地回收
├── portal/                    门户框架
│   ├── registry.py            功能模块注册表（加卡片只改这里）
│   ├── routes.py              / 与 /theme.js、/api/portal/health
│   ├── templating.py          极简模板渲染 + mtime 热重载
│   ├── assets.py              静态资源响应（mtime 缓存、Content-Type 修正）
│   ├── assets/theme.js        ★ 主题引擎（日出日落本地计算）
│   └── templates/portal.html  门户页
├── modules/                   业务模块（各自 Blueprint，独立注册互不拖累）
│   ├── journals/              期刊列表（近 60 天 / 主题归档 + 页顶「今日更新」）
│   ├── archive/               译文归档
│   ├── news/                  今日早报
│   ├── palette/               科研配色（含 palettes.json 数据）
│   └── queue/                 翻译队列（文献 + 标题）
├── docs/                      文档
│   ├── ARCHITECTURE.md        翻译系统架构（硬件/依赖/迁移）
│   ├── PORTAL.md              门户交付说明
│   ├── PORTAL_DESIGN.md       门户化改造设计
│   └── CODE_REFERENCE.md      ★★ 本地架构代码工作参考（全量 API + 数据流 + 铁律）
├── requirements-lock.txt      依赖锁定 + 不可升级红线
├── tests/                     自建 DOM 垫片测试（不装 jsdom）
└── tools/
    ├── nas_reaper.py          本地回收：超 48h 的三版本搬到 NAS 后删本地
    ├── schedule/              ★ 本地定时任务（Windows 任务计划程序）
    ├── make_theme_previews.py 离线主题预览页生成
    └── sync_from_workspace.py 工作区 → 本仓库的单向同步（含发布脱敏）
```

---

## 3. 模块详解

### 3.1 `web_app.py` —— 应用入口（721 行）

- **单实例 PID 锁**：`.flask_5555.pid`。启动前检查 PID 是否存活 + 端口是否监听，
  活着就拒绝重复启动；PID 死了但锁还在就自动清理。**要重启必须先杀进程再起**。
- **翻译任务队列**：`Queue` + 工作线程，任务有 `job_id`，前端轮询 `/api/status/<id>`。
  支持取消（`POST /api/cancel/<id>`）。
- **翻译引擎是懒加载**（函数内 `import`），所以本仓库不含引擎也能启动门户部分；
  `/translate` 的完整功能需要外部依赖，见 §5。
- **静态资源缓存头**：`after_request` 统一处理，注意不要和 `portal/assets.py` 的
  `Cache-Control` 打架（历史踩坑：被覆盖导致改了 JS 不生效）。

### 3.2 `portal/` —— 门户框架

**`registry.py`** —— 加一个新功能入口 = 往 `MODULES` 列表追加一条 dict，
不需要动 `portal.html`，也不需要动 `routes.py`。字段：

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

**`templating.py`** —— 刻意**不用 Jinja2**。门户页 CSS/JS 里大括号密集，
走 Jinja2 会到处冲突，所以这里是纯字符串替换 + mtime 热重载
（改 HTML 刷新即可生效，**改 `.py` 不行，必须重启**）。

**`assets/theme.js`** —— 主题引擎，三个要点：

1. **日间浅色 / 夜间深色，按昆明真实日出日落自动切换**（NOAA 简化式本地计算，
   `lat 25.0389 / lon 102.7183 / tz +8`，**不联网、不调 API**）。
2. 右上角三态按钮 `auto / light / dark`，选择存 `localStorage['kust.theme.mode']`。
3. 浅色变量由 JS 注入 `:root[data-theme="light"]`（选择器权重高于各页面自己的
   `:root` 深色兜底），所以接入只需一行
   `<script src="/theme.js"></script>`——**必须同步加载**，加 `defer`/`async` 会闪一下深色。

### 3.3 `modules/journals/` —— 期刊列表

两个视图：

- **近 60 天**：展示**本地近 60 天获取到**的文章链接（以本地日期时间为准），
  不是"期刊当天发布了什么"。窗口长度改 `WINDOW_DAYS` 即可。
- **主题归档**：近 2 个月目标期刊中与 filter 主题相关的命中文献，按相关度分档着色。

关键设计：

- **获取时间的唯一权威来源是 `journal_ledger.json` 的 `first_seen`**，
  首次写入后永不改写，窗口才会自然老化。
  *不能拿文件 mtime 当获取时间*——`journal_updates.json` 每次运行被整体重写，
  会把时间"刷新"成今天，文章永远老化不掉，窗口退化成"显示全部"。
- **英文标题下方挂中文小字**：由离线脚本 `journal_title_zh.py` 预翻译
  （本机 llama.cpp，5 条一批走 JSON，术语批内一致），结果存
  `journal_title_zh.json`，门户**只读**。缓存缺失时照常显示英文原文，不报错不空白。
- 卡片默认折叠 6 条；另有「**全部展开 / 收起全部**」总开关，只在"近 60 天"出现
  （该视图条目全部随载荷下发，能真铺开）。"主题归档"的单卡上限是**服务端**裁剪的，
  前端铺开也拿不到更多，所以不给这个按钮——**不给会骗人的按钮**。

### 3.4 `modules/archive/` —— 译文归档

- 近 `ARCHIVE_WINDOW_HOURS = 48` 小时的译文，按论文分组。
- **不做服务端下载封装**，一律 `<a target="_blank">` 交给浏览器原生 PDF 阅读器。
- 注意：窗口要和 `web_app.py` 的 `RETENTION_SECONDS` 对齐，保留期短于窗口会看不到东西。

### 3.5 `modules/news/` —— 今日早报

- 数据源：`daily_push_state.json` 的 `latest_daily` 键（由 `daily_run.py` 推送后落盘）
  + `news.json` 当日条目。
- `/today` 是 302 跳转到当日飞书文档（**返回 302 是预期行为，不是错误**）。
- 门户页的早报卡片是 `live: True`，由前端 JS 动态绑定当天的链接。

### 3.6 `modules/palette/` —— 科研配色

37 套色板 / 350 个色值，6 个分组，**全部有出处**：

| 分组 | 内容 | 色值来源 |
|---|---|---|
| 期刊分类配色 | Nature / Science / NEJM / Lancet / JAMA / JCO / BMJ / Frontiers | ggsci 官方定值 |
| 期刊推荐色标 | Crameri：batlow / lajolla / oslo / devon / roma / vik | cmcrameri 官方 256 点 RGB |
| 地学专属配色 | ICS/CGMW 年代色、地形高程、cmocean（thermal / haline / oxy / topo / delta） | ICS 官方色码 + cmocean |
| 连续色阶 | viridis / cividis / plasma / magma / inferno / Blues / Turbo | matplotlib 均匀采样 |
| 发散型配色 | RdBu / Spectral / PiYG | 同上 |
| 分类型配色 | D3 category10 / ColorBrewer Set2·Paired / UChicago / Okabe-Ito / COSMIC | ggsci + Color Universal Design |

★ **扩充色卡的铁律**：跑 `_build_palettes.py`，色值一律从权威源抓取并存档到
`_palette_src/`，**绝不凭记忆手写 hex**；抓不到的色板跳过并告警，不拿近似色凑数。

### 3.7 `modules/journals/` 的「今日更新」板块

页顶单独一块，展示**当天（本地日期）本机获取到**的文章链接，跨刊汇总、按获取时间倒序。

- **口径与下方完全一致**：都由服务端按「本地获取时间」切好（台账 `first_seen` 优先），
  前端只按区域 / 搜索过滤，**不自己算日期**，避免时区与口径分歧
- 条目渲染复用下方同一套 `.art` 样式，只多挂一个来源刊名，所以天生一致
- 只在「近 60 天」视图出现：主题归档是发表日期口径的另一套数据，混进来会误导
- 无获取时间的条目（undated）**不计入**今日 —— 无法证明是今天到的
- DOM 测试里有专门的断言段，含「不蹭用 `.card` 类名」这条守卫
  （`class="card` 会被卡片计数断言统计，蹭类名会误伤）

### 3.8 `modules/queue/` —— 翻译队列

文献翻译 + 标题翻译双队列，各自有自己的进度与取消。

### 3.9 `bilingual.py` + `nas_archive.py` —— 产物三版本与 NAS 文献仓库

翻译产出**三个版本**：原文 `uploads/`、中文版 `translated/`、双语对照 `duo/`（`bilingual.py`
负责左右 / 上下 / 逐页交替三种拼接，原子写出，用户不会下载到半截文件）。

`nas_archive.py` 把它们归档到 NAS，并在 `data/nas_index.json` 记索引：

| 能力 | 说明 |
|---|---|
| `archive_versions()` | 三版本落 NAS 三目录 + 写索引 |
| `lookup()` / `restore()` | 命中索引后从 NAS 回捞到本地（翻译队列 / 归档**依旧只读本地**） |
| `reap()` | 本地超过 48h 的三版本搬到 NAS 后删除本地副本 |

★ **删除前逐个校验**：NAS 副本存在、且大小与本地完全一致才删；校验不过的留在本地并记进日志。
NAS 不可达时直接跳过，**一个文件都不删**。

### 3.10 `tools/schedule/` —— 本地定时回收

「本地 PDF 超 48h 就搬走」交给 **Windows 任务计划程序**，不依赖任何外部调度服务。
任务名 `PostNews-NASReaper`，每日 09:00 / 21:00；`install_task.bat` 一键安装。

改这块前**必须读**这两个坑（都实测踩过）：

1. **内层路径只能用单引号**：任务参数二次解析，双引号会被 `powershell.exe` 拆开，
   带空格的 Python 路径直接失效 → `LastResult=1` 且**完全没有日志**（典型假成功）。
2. **必须注入 `PYTHONIOENCODING=utf-8`**：定时任务控制台是 cp936，打印中文/emoji
   会让 Python 当场崩，而手动跑完全正常。

判定真跑通：`status_task.bat` 里 **LastResult 必须为 0**，且 `logs/nas_reaper.log` 有新记录。

---

## 4. 数据契约（只读）

门户**只读** `D:\PostNews` 下的数据文件，绝不写入。写入方向只有反向一处：
`daily_run.py` 增量写 `daily_push_state.json` 的 `latest_daily`。

| 文件 | 用途 |
|---|---|
| `journals.json` | 期刊元数据（国内 6 + 国际 8） |
| `journal_ledger.json` | ★ 获取台账：每篇首次被本地获取的时间（**删了会让获取时间全部重置为今天**） |
| `journal_updates.json` | 最近一轮更新的文章 |
| `journal_batch_pending.json` | 待批量处理的一轮更新 |
| `journal_article_buffer.json` | 历史累积文章缓存 |
| `journal_doc_urls.json` | 期刊飞书速递文档链接 |
| `journal_topic_archive.json` | 主题相关文献回溯归档 |
| `journal_title_zh.json` | 英文标题的中文小字翻译缓存 |
| `daily_push_state.json` / `news.json` | 今日早报 |

---

## 5. 运行

### 前置

- Python 3.12（本机 `D:\Python 3.12.4\python.exe`），依赖：`flask`
- 门户 + 归档 + 期刊 + 色卡 + 早报 **不需要任何额外依赖**，裸 Flask 即可跑
- `/translate` 的完整功能需要（**不在本仓库**，见 `Geo-Mine-Translator`）：
  llama.cpp 服务端（`llama-server.exe`，端口 8090）、GGUF 模型（约 7 GB）、BabelDOC

### 启动 / 重启

```bash
cd D:\PostNews\Translate
"D:\Python 3.12.4\python.exe" web_app.py
```

> ⚠️ **改过任何 `.py` 必须重启 Flask 才生效**。只改 HTML/JS 直接刷新浏览器即可
> （`templating.py` 的 mtime 热重载只覆盖模板）。
> 重启：`taskkill /PID <pid> /T /F`（pid 在 `.flask_5555.pid`）后再启动。
>
> 判据：`curl /api/journals/list | grep -c '"window"'` → `0` 说明跑的还是旧代码。

### 5.3 环境变量

| 变量 | 用途 | 缺省 |
|---|---|---|
| `NAS_PDF_ROOT` | NAS 文献仓库根目录（UNC 或盘符均可） | 依次探测 `S:/Translator_PDFs` → UNC |
| `NAS_TTL_HOURS` | 本地 PDF 保留时长，超过后回收到 NAS | `48` |
| `POSTNEWS_PYTHON` | 定时任务用的 `python.exe` 绝对路径 | 自动探测（优先 `D:\Python 3.12.4`） |
| `NO_PROXY` | **必须含** `127.0.0.1,localhost`，否则本机回环请求会走代理 → 翻译 404 | 建议显式设置 |

> 代码里的 `<nas-host>` / `<lan-ip>` / `<nas-user>` 都是同步时自动替换的占位符，
> clone 后**必须自己配** `NAS_PDF_ROOT` 才能用 NAS 相关功能。

---

## 6. 测试

自建轻量 DOM 垫片，**不装 jsdom**，纯 Node 跑：

```bash
node tests/theme_dom_test.js                                  # 主题引擎 73 项断言
node tests/portal_journals_dom_test.js <页面html> <接口json>    # 期刊页 59 项断言
```

改配色后必跑 `theme_dom_test.js`——它会揪出"浅色模式漏配变量"这类问题。

---

## 7. 同步仓库

```bash
python tools/sync_from_workspace.py          # 把工作区 Web 层同步进来
python tools/sync_from_workspace.py --check  # 只比对差异，不写盘
```

改完工作区代码 → 跑同步 → `git commit` → `git push`。

---

## 8. 与其它仓库的关系

| 仓库 | 职责 |
|---|---|
| **kust_work**（本仓库） | Web 站源码：门户框架 + 5 个业务模块 + 双语版 / NAS 归档 + 定时回收 + 测试 + 文档 |
| `Geo-Mine-Translator` | PDF 翻译引擎：llama.cpp 接入、BabelDOC、术语表 |
| `D:\PostNews`（非 git） | 期刊抓取管线 + 数据文件（台账、归档、早报状态） |

---

## 9. 常见问题

| 症状 | 排查 |
|---|---|
| 页面能打开但数据少 / 不对 | 大概率是**进程跑的旧代码**，看 §5 判据，重启 Flask |
| 刷新后改的 JS 不生效 | `after_request` 覆盖了 `Cache-Control`，查 `assets.py` |
| 浅色模式下有元素发黑 | 漏配变量，跑 `node tests/theme_dom_test.js` |
| 期刊窗口里文章永远不老化 | 别用文件 mtime 当获取时间，必须用 ledger 的 `first_seen` |
| 复制色值去画图画出来不一样 | 色值必须从权威源取，见 §3.6 铁律 |
