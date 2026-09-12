# kust.work 门户层 · 交付说明

> 实施日期：2026-09-12 | 方案：B（Blueprint 模块化单体）| 状态：已上线运行
> 本轮新增：今日早报 · 期刊列表 · 科研配色（文献翻译为既有功能，仅调整入口）
> 后续改版：**译文归档** 改为「按原文标题聚合 + 三个版本按钮直达 PDF」，并放开文件保留期到 48 小时

---

## 1. 现在怎么访问

| 路径 | 内容 |
| --- | --- |
| `http://kust.work/` | **欢迎主页（工作台）**，9 张卡片 · 3 个分组 · 5 个可用入口 |
| `http://kust.work/today` | **今日早报** → 302 跳转当日飞书早报文档（无链接时落 `/daily`） |
| `http://kust.work/daily` | 今日早报详情页：当日新闻列表 + 日报海报 + 飞书按钮 |
| `http://kust.work/journals` | **期刊列表**：14 种目标期刊，标题直达官网，卡内是本地抓取的最新文章 |
| `http://kust.work/palette` | **科研配色**：21 组 / 199 个色值，点击即复制色号 |
| `http://kust.work/translate` | 文献翻译（原 GeoTranslate，功能完全不变） |
| `http://kust.work/archive` | **译文归档**：近 48 小时论文按原文标题聚合，点版本按钮即开 PDF |

NAS Apache 反代**一行没动**，仍是 `ProxyPass / → http://192.168.10.100:5555/`。

---

## 2. 目录结构

```
Translate/
├── web_app.py              # 改动 2 处：/ 退到 /translate + 注册门户/模块（注入 data_dir）
├── portal/                 # ★ 门户层（只管入口和状态，不碰业务逻辑）
│   ├── registry.py         #   ★ 功能入口注册表 —— 加入口只改这个文件
│   ├── routes.py           #   GET / 门户页 · GET /api/portal/health
│   ├── templating.py       #   静态模板工具（刻意不用 Jinja2，见 §6）
│   └── templates/portal.html
├── modules/                # ★ 功能模块（每个模块一个 Blueprint，独立 try/except 注册）
│   ├── __init__.py         #   register_all() 统一注册
│   ├── news/               #   今日早报
│   │   ├── routes.py       #     GET /today · GET /daily · GET /daily/poster
│   │   │                   #     GET /api/news/latest · GET /api/news/list
│   │   └── templates/daily.html
│   ├── journals/           #   期刊列表
│   │   ├── routes.py       #     GET /journals · GET /api/journals/list
│   │   └── templates/journals.html
│   ├── palette/            #   科研配色
│   │   ├── routes.py       #     GET /palette · GET /api/palette/list
│   │   ├── palettes.json   #     ★ 色板数据（21 组 199 色，可直接扩写）
│   │   └── templates/palette.html
│   └── archive/            #   译文归档（论文分组 + 版本直达）
│       ├── routes.py       #     GET /archive · GET /api/archive/list
│       └── templates/archive.html
└── _gen_palettes.py        # 配色数据生成脚本（改配色需求时重跑，见 §7）
```

备份：`web_app.py.bak-portal-20260912_223957`

---

## 3. 四个功能各自怎么实现的

### 3.1 今日早报 —— 链接动态绑定，模板恒定

**链路：** `daily_run.py` 推送后落盘 → 门户读盘 → 前端 JS 回填

| 环节 | 说明 |
| --- | --- |
| 落盘 | `daily_run.py` 新增 `_record_latest_daily()`，把 `doc_url` / `poster` / `feishu_sent` **增量合并**进 `daily_push_state.json` 的 `latest_daily` 键 |
| 降级 | 该函数整体 try/except，落盘失败只打 warning，**绝不影响推送主流程** |
| 读取 | `modules/news/routes.py` 只读，不写 PostNews 任何业务文件 |
| 跳转 | `GET /today`：有链接 → `302` 到飞书；无链接 → `302` 到 `/daily`，保证怎么点都有东西看 |
| 前端 | `portal.html` 里的 JS 拉 `/api/news/latest`，**直接把卡片 `href` 改成当天飞书直链** |

**为什么用「服务端 302 + JS 增强」而不是把外链写进 HTML：**

链接每天变。写进模板 = 每天改代码。现在的设计里模板恒定，
JS 能跑就少一跳直连飞书，JS 挂了也只是退化成 302 跳转 —— **可用性不依赖 JS**。

三个状态都由 JS 自动判定并写到卡片角标上：

| 状态 | 角标 | 说明 |
| --- | --- | --- |
| 当天且有链接 | 今日已推送（绿） | 卡片 href 指向飞书文档，新窗口打开 |
| 当天但无链接 | 链接待记录（琥珀） | 改造前推送的记录没有链接，下次推送自动补上 |
| 非当天 | 待推送（灰） | 显示最后一次记录的日期与条数 |

> **⚠️ 首次生效说明**：`doc_url` 的落盘是本次新增的。2026-09-12 这次推送发生在改造之前，
> 链接无法回溯（`lark-cli` 不在 PATH，日志中亦无留存）。
> 因此已按事实补了一条记录 `{"date":"2026-09-12","news_count":12,"feishu_sent":"success"}`，
> 卡片会显示「链接待记录」。**下一次 `daily_run.py` 执行后链接自动出现，无需人工干预。**

### 3.2 期刊列表 —— 多源合并去重

合并四个文件（全部只读）：

| 文件 | 用途 |
| --- | --- |
| `journals.json` | 期刊元数据（国内 6 + 国际 8 = 14 种） |
| `journal_updates.json` | 最近一轮更新文章（主来源） |
| `journal_batch_pending.json` | 待批量处理的一轮更新（补漏） |
| `journal_article_buffer.json` | 历史累积缓存（字段最简，最后兜底） |
| `journal_doc_urls.json` | 期刊飞书速递文档链接 |

按 `url`/`title` 去重，每张卡片最多展开 6 篇，其余在「另有 N 篇未展开」里体现。

**实测当前数据水位**：14 种期刊中 2 种有更新，共 21 篇
（Geology 20 篇 + Ore Geology Reviews 1 篇）。其余 12 种卡片显示
「本轮未抓取到新文章」—— 这是本地数据的真实状态，不是页面故障；
卡片仍提供官网直达，可用作检索跳板。

### 3.3 科研配色 —— 全部来自可核对的权威来源

| 分组 | 色板 | 来源 |
| --- | --- | --- |
| 期刊分类配色 | Nature(npg) · Science(aaas) · NEJM · Lancet · JAMA · JCO | **ggsci** 官方公布定值 |
| 地学专属配色 | 国际年代地层表（ICS/CGMW）· 地形高程色阶 | **ICS/CGMW 官方色码**（经 Macrostrat intervals API 核对） |
| 连续色阶 | Viridis · Cividis · Plasma · Magma · Inferno · Blues | matplotlib 按 9 点均匀采样取精确 hex |
| 发散型配色 | RdBu · Spectral · PiYG | 同上，11 / 9 点采样 |
| 分类型配色 | D3 Category10 · ColorBrewer Set2 / Paired · UChicago | D3 / ColorBrewer / ggsci |

ICS 年代色带语义标签（第四纪 Q → 寒武纪 Cm），因为这是地质图件的国际标准色，
对应关系本身就是信息。

**复制行为**：优先 `navigator.clipboard`（仅 https / localhost 可用），
在校园网 `http://<IP>` 的非安全上下文下自动回落到 `document.execCommand('copy')`。
所以**通过 LAN IP 访问也能正常复制**。

### 3.4 文献翻译 —— 仅入口调整

`index.html` **0 行改动**，API 与 PDF 路径全部留在根路径（见 §5）。

### 3.5 译文归档 —— 论文分组 + 版本直达

**一行 = 一篇论文**，行内三个版本按钮，点击即用浏览器原生 PDF 阅读器打开。

```
GET /archive
  ▸ Ce-Fang Liu-2025b                 1.9 小时前 · 2 个版本 · 9.8 MB
      双语(置灰)  [中文 5.0MB ↗]  [原文 4.7MB ↗]
```

**分组键 = 清洗后的文件名。** 三个版本的同源文件清洗后标题一致，所以自动聚合成一行：

| 原始文件名 | 清洗后 |
| --- | --- |
| `X_a775d2f5.pdf` | `X` |
| `X_a775d2f5.no_watermark.zh.dual.pdf` | `X` |
| `X_a775d2f5.no_watermark.zh.mono.pdf` | `X` |

清洗规则：去掉 `.zh.dual.pdf` / `.zh.mono.pdf` 后缀 → 去掉 `.no_watermark` → 去掉 `_xxxxxxxx` 哈希后缀。
标题优先取**原文**的（无原文时才回落到译稿标题）。

**为什么用按钮直达而不是下载接口。**
三个按钮就是普通的 `<a target="_blank">`，指向 web_app.py **既有的**
`/uploads/*` 与 `/translated/*` 静态路由。这些路由返回
`Content-Disposition: inline; Content-Type: application/pdf`，浏览器会用自带
PDF 阅读器渲染，查看与下载都由浏览器自己的工具条负责。

这样做的收益：
- **服务端零下载封装** —— 没有附件流、没有内存打包、没有临时文件，
  下载报错面直接归零（原先的「勾选 + 服务端 zip 打包」方案已删除）
- **前端零 JS 依赖** —— 打开 PDF 是纯 HTML 链接行为，JS 挂了也不影响
- 不新增任何对外路由，暴露面与改造前完全一致

缺失的版本渲染成置灰的 `<span>`（不是链接，点不动），并标注「无」+ tooltip 说明。

**⚠️ 48 小时窗口依赖保留期。**
`ARCHIVE_WINDOW_HOURS = 48` 只是**展示窗口**；真正决定文件活多久的是
`web_app.py` 的 `RETENTION_SECONDS`。改造前它是 `7200`（2 小时），
会让 48 小时窗口永远是空的 —— 已一并调到 `48 * 3600`。**两者必须一起改。**

备份：`web_app.py.bak-portal-20260912_223957` 里仍是 7200 的旧值。

---

## 4. 怎么加一个新功能入口（3 步）

**第 1 步** — 建模块目录 `modules/my_feature/routes.py`：

```python
from pathlib import Path
from flask import Blueprint, jsonify
from portal.templating import render_html

my_bp = Blueprint('my_feature', __name__)

def init_my_feature(app, data_dir=None):      # 需要读 PostNews 数据就收下 data_dir
    app.register_blueprint(my_bp)
    print('[模块] 我的功能 → GET /myfeature')

@my_bp.route('/myfeature')                    # 页面带前缀
def my_page():
    return render_html(Path(__file__).parent / 'templates/my.html')

@my_bp.route('/api/my/list')                  # API 留根路径
def my_api():
    return jsonify([])
```

**第 2 步** — 在 `modules/__init__.py` 的 `_MODULES` 里加一行
`('我的功能', init_my_feature, True)`（第三个字段 = 是否需要 data_dir）。

**第 3 步** — 在 `portal/registry.py` 的 `MODULES` 追加一条：

```python
{
    'id': 'myfeature', 'group': '科研工具',
    'name': '我的功能', 'desc': '一句话说明',
    'url': '/myfeature', 'tag': 'Beta', 'status': 'beta',
    'live': True,        # 若卡片内容要由前端 JS 动态回填，加这个
},
```

门户卡片自动出现，**不需要动 portal.html**。

- `status` 三档：`online`（可点，绿角标）· `beta`（可点，琥珀角标）· `coming`（置灰不可点）
- 每张卡片带 `data-mod="<id>"`，`<span class="card-tag">` 带 `data-tag="<id>"`，
  前端 JS 靠这两个属性定位并改写（如今日早报就是在 `portal.html` 底部的第二个 IIFE 里做的）

---

## 5. 验证结果（2026-09-12 实测）

### 接口层（curl 实测）

| 项 | 结果 |
| --- | --- |
| `GET /` | 200 · 9 张卡片（5 可用 / 4 规划中）· 占位符已替换 |
| `GET /today` 无链接 | `302 → /daily` |
| `GET /today` 有链接（临时注入验证） | `302 → https://oqho0fclbhd.feishu.cn/docx/SXJFdaU2XoD1uOx6SAEc5t1ZnUc` |
| `GET /daily` | 200 |
| `GET /daily/poster` | 200 · `image/png` · 789,971 B |
| `GET /journals` | 200 |
| `GET /palette` | 200 |
| `GET /translate` | 200（未受影响） |
| `GET /archive` | 200（论文分组视图） |
| `GET /api/archive/list` | `{window_hours:48, total_papers, total_files, papers:[…]}` |
| `GET /api/archive/list?kind=dual\|mono\|original` | 单版本扁平视图（兼容保留） |
| `GET /api/archive/list?kind=translated\|upload` | 旧别名（译文=双语+中文）· 非白名单 kind → 400 |
| 版本按钮 URL（`/uploads/*`、`/translated/*`） | 全部 200 · `application/pdf` · **`Content-Disposition: inline`** |
| 48 小时边界 | 72h 前文件被排除 · 47.5h 保留（造时间戳实测） |
| 保留期 | `RETENTION_SECONDS = 172800`（48h）· 72h 测试文件已被清理线程自动删除 |
| `GET /api/news/latest` | `{"ok":true,"date":"2026-09-12","is_today":true,"has_url":false,"news_count":12,"news_total":12}` |
| `GET /api/news/list` | 12 条，标题/来源/链接完整 |
| `GET /api/journals/list` | 14 种 · 有更新 2 种 · 新文章 21 篇 |
| `GET /api/palette/list` | 21 组 · 199 色 · 5 个分组 |
| `GET /api/portal/health` | 200 |
| `GET /api/check`（原有） | 200 — 未受影响 |
| llama-server 进程数 | 1（未重复拉起，PID 2016） |

### 前端运行层（jsdom 真实 DOM 执行）

第一轮 4 个页面 29 项全绿；译文归档改版后单独复测 **39 项全绿**。

| 页面 | 验证项 |
| --- | --- |
| `/` | 无 JS 报错 · 今日早报角标→「链接待记录」· 描述回填「12 条资讯」· 期刊卡片回填「14 种期刊 · 21 篇新文章」且角标→「有更新」 |
| `/palette` | 无 JS 报错 · 21 张卡片 · 199 个色块 · 6 个筛选按钮 · 10 条渐变 · 12 个年代标签 · **点击色块/色号/整组复制均正确弹出「已复制 …」** |
| `/journals` | 无 JS 报错 · 14 张卡片 · 14 个标题外链 · 飞书速递链接存在 · 切换「国内期刊」后正确筛出 6 张 |
| `/daily` | 无 JS 报错 · 12 条新闻 · 海报已渲染 · 无链接时按钮置灰 · 标题回填「今日早报 · 2026-09-12」 |
| `/archive` | 无 JS 报错 · 论文行数=接口数 · 每行恰好 3 个按钮且顺序固定 · **页面零复选框** · 可用版本是 `target=_blank` 链接、缺失版本是不可点 `<span>` · **每个按钮 URL 逐个实拉：200 + `application/pdf` + `inline`** · 大小写不敏感筛选 · 空态/失败态 |

JS 语法另经 `node --check` 逐文件校验（5 个页面全部通过）。

---

## 6. 约束（改路由前必读）

**翻译页的 API 和静态资源必须留在根路径。**

`index.html` 里 4 处 `fetch` 用的是根绝对路径（`/api/check`、`/api/upload`、
`/api/status/`、`/api/cancel/`），后端产出的 PDF URL 也是（`/uploads/*`、`/translated/*`）。

所以：**页面路径可以带前缀（`/translate`），API 不能。**

这条约束带来的好处是翻译模块**前端 0 行改动**；违反它会让翻译页全线 404。

---

## 7. 四个实现决策（避免以后改错）

**① 门户页刻意不用 Jinja2。**
`render_template_string` 会把 `{` `}` 交给模板引擎。门户页和归档页里 CSS/JS 密集，
改用 `portal/templating.py` 的纯字符串替换，行为完全确定。
代价：占位符是 `<!--PORTAL_CARDS-->` 这种 HTML 注释，不是模板语法。

**② 模板带 mtime 热重载。**
改 `portal.html` / `daily.html` / `journals.html` / `palette.html` **不用重启 Flask**。
但改任何 `.py` 或 `palettes.json` 仍必须重启（Python 模块加载后不重新 import，老坑）。

**③ 模块注册用「独立 try/except」。**
`modules/__init__.py` 里每个模块单独包裹并记录成功/失败清单。
一个模块依赖缺失或写坏，只影响它自己的入口 —— 不会连累其他模块和翻译主服务。

**④ 门户整体也做了降级兜底。**
`web_app.py` 里 `import portal` 失败时，`/` 自动回落到翻译页，不让门户成为翻译服务的单点故障。

---

## 8. 改配色 / 加配色

配色数据是 `modules/palette/palettes.json`，结构：

```json
{
  "palettes": [
    {
      "id": "npg", "name": "Nature 风格", "source": "ggsci · npg",
      "type": "categorical",
      "group": "期刊分类配色",
      "note": "一句话说明",
      "colors": [{ "hex": "#E64B35" }, { "hex": "#4DBBD5" }]
    }
  ]
}
```

- `type`：`categorical` / `labeled` / `sequential` / `diverging`
  （后两种在页面上会额外渲染一条渐变预览条）
- 色块可选带 `label` 字段（如 ICS 年代色的「第四纪 Q」）
- 改完**重启 Flask** 生效

需要新增连续色阶时，直接改 `_gen_palettes.py` 里的 `PALETTES` 列表再重跑：

```powershell
cd D:\PostNews\Translate
py -3.12 _gen_palettes.py
```

---

## 9. 重启

```powershell
cd D:\PostNews\Translate
start_translate.bat          # 或先跑 start_all.bat
```

> 注意：Flask 有单实例 PID 锁（`.flask_5555.pid`）。
> 若提示「启动被拒绝: 已有 Flask 在运行」，说明已有实例，先确认再决定是否 kill。
> 任务列表是**内存态**，重启后 `/api/status/<旧 job>` 会失效（属预期，非 bug）。

---

## 10. 全局浅色 / 深色主题（2026-09-13 新增）

### 10.1 你会看到什么

右上角多了一个小按钮，显示当前模式：

| 按钮 | 含义 |
| --- | --- |
| ◐ 自动 | **默认**。昆明日出之后、日落之前用浅色，其余时间用深色，跨过日出/日落自己翻转 |
| ☀ 浅色 | 手动锁定浅色，直到你点回「自动」 |
| ☾ 深色 | 手动锁定深色 |

点击按钮按 `自动 → 浅色 → 深色 → 自动` 循环。选择存在浏览器 `localStorage`，
**同域所有页面、所有标签页共用**；换一个标签页改，其他标签页会跟着变。
鼠标悬停在按钮上会提示当天的日出日落时刻。

### 10.2 覆盖范围

**全部 6 个页面**：门户 `/`、期刊列表、科研配色、译文归档、今日早报、文献翻译。
PDF 白纸底、浮在 PDF 上的页码条/进度卡、断线红条**刻意不跟随主题** ——
它们浮在白色 PDF 之上，换浅色反而看不清。

### 10.3 怎么实现的（加新页面时照抄）

每个页面 `<head>` 里**只有一行**：

```html
</style>
<script src="/theme.js"></script>   <!-- 必须同步，不能加 defer/async -->
</head>
```

- **深色**写在各页面自己的 `<style>:root` 里（兜底：`/theme.js` 挂了页面照常可读）
- **浅色**由 `theme.js` 注入 `:root[data-theme="light"]` —— 属性选择器权重更高，
  不依赖样式表先后顺序，也不怕页面样式反向覆盖
- **切换按钮**由 `theme.js` 自己创建并挂到 `<header>` 末尾，HTML 里不需要预置元素

日出日落是**本地算**的（NOAA 简化式，昆明 `25.0389N, 102.7183E, UTC+8`），
不联网、不依赖任何外部 API。精度 ±1~2 分钟，判断日夜绰绰有余。

### 10.4 调色 / 加变量

- 改**浅色**：只改 `portal/assets/theme.js` 顶部的 `LIGHT_VARS`（一处）
- 改**深色**：要同步改 6 个页面的 `:root`（它们逐字一致）
- **加新变量**：深色和浅色都必须加，否则那个组件会「半深半浅」

改完**务必**跑一次：

```bash
cd D:\PostNews\Translate
node theme_dom_test.js        # 73 项断言，会揪出「浅色漏配变量」
py -3.12 _make_theme_previews.py   # 生成 output/*_theme_preview.html 离线预览
```

`_make_theme_previews.py` 产出的预览页把 `theme.js` 内联、接口数据内嵌，
**双击就能打开**，不依赖 Flask，方便直接看两套配色。

> 改 `theme.js` 不需要重启 Flask（有 mtime 缓存），浏览器刷新即可。
> `/theme.js` 走 `no-cache`，不会像其他 `.js` 那样吃 1 小时强缓存。
