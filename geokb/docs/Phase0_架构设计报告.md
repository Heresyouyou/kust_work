# GeoKB 架构设计报告（Phase 0）

- 日期：2026-10-13
- 状态：**待用户确认**（确认前不进入 Phase 1）
- 人格声明：以下所有标注「已验证」的结论均有本次实测证据；标注「未验证」的为设计推断，将在对应 Phase 以非破坏性方式验证。

---

## 0. 非破坏性预检结果（已验证）

| 检查项 | 结果 | 证据 |
|---|---|---|
| 端口 5556–5560 占用 | **全部空闲**，默认 5556 可用，无需顺延 | 本次 socket 探测（connect_ex）5 端口均返回非 0 |
| 工作目录 D:/ZSK 现状 | 仅含 `geokb_build_prompt.md` 与 `.workbuddy/`，无既有数据文件 | `ls -la` 实测 |
| 5555 端口现有服务 | 未触碰、未探测其内部状态，本方案全程不与其交互 | — |
| 本机 Python 运行时 | 3.13.12（managed）可用，将建独立 venv，不污染系统环境 | 预置运行时清单 |

> 已验证结论：5556 可直接作为 GeoKB 服务端口；D:/ZSK 为干净工作区，不存在"既有文件被误改"的风险面（Phase 1 扫描的其他指定目录仍需只读纪律）。

---

## 1. 决策项一：内容存储形式

### 1.1 候选方案对比（1–5 分，5 最优）

| 方案 | AI Agent 易解析 | 人类可维护 | 可版本化 | 检索效率 | 合计 | 简评 |
|---|---|---|---|---|---|---|
| Markdown + YAML Frontmatter | 5 | 5 | 5 | 2（裸文件无索引） | 17 | 源数据最优形态，但单独无法承担检索 |
| SQLite (FTS5) | 4 | 3 | 2（二进制库） | 4（BM25 强） | 13 | 索引层利器，不适合做"源" |
| 向量库（Chroma/Qdrant 等） | 4 | 2 | 2 | 4（语义强、关键词弱） | 12 | 语义检索必要组件，不适合做"源" |
| 知识图谱（RDF/Neo4j） | 3 | 2 | 3 | 3（关系查询强、构建成本极高） | 11 | 术语关系用"同义词映射表"即可覆盖 80% 价值，本期不引入 |

### 1.2 最终选型：**三层分离架构**

```
源数据层（唯一事实来源）   内容/*.md（YAML Frontmatter + Markdown 正文）   ← 人类可读、git 版本化
    ↓ 摄取/重建（单向派生，可随时全量重建）
关键词索引层              SQLite FTS5（BM25）                                ← index/geokb.db
语义索引层                sqlite-vec 向量表（与 FTS5 同库文件）              ← index/geokb.db
```

**正确性论证**：
1. **单一事实来源**：所有写操作只改 Markdown 源文件；两个索引层是纯派生物，任何不一致都可通过全量重建收敛，从根上消除「页面改了但检索不到」的不一致状态（验收标准 5 的关键）。
2. **索引同库**：FTS5 与 sqlite-vec 共用一个 `geokb.db`，一次连接完成混合检索，运维面只有一个文件，备份/迁移即拷贝。
3. **向量选型论证**：Chroma 依赖重（自带 duckdb 等，Windows 安装链长）；Qdrant 需独立服务进程（违反轻量与守护纪律）；**sqlite-vec** 为单一 SQLite 扩展，与现有技术栈同构。**未验证项**：sqlite-vec 在 Python 3.13/Windows 的预编译 wheel 可用性，Phase 4 第一步即验证；若不可用则降级为 Chroma 嵌入式模式（接口层已抽象，切换成本限于一个适配器文件）。
4. **嵌入模型**：候选 `BAAI/bge-small-zh-v1.5`（约 100MB，中英文兼顾，CPU 可跑）。模型需一次性下载，**待确认项 A**（见第 8 节）。若不允许下载，检索降级为「BM25 + 同义词扩展」纯关键词模式，接口不变。

---

## 2. 决策项二：模块划分方案

### 2.1 一级模块树（覆盖任务书全部要求项）

```
geokb/
├── mineralogy/        矿物学（晶体化学、鉴定特征、IMA 名录对齐）
├── petrology/         岩石学（火成/沉积/变质三大岩类）
├── structural/        构造地质学（褶皱、断裂、应变分析）
├── geochemistry/      地球化学（元素行为、同位素、分析方法的）
├── economic-geology/  矿床学/经济地质（成因类型、典型矿床、勘查）
├── geophysics/        地球物理（重磁电震方法原理与解释）
├── geochronology/     地质年代学（定年方法、地质年代表）
├── field-methods/     野外地质方法（填图、采样、编录、安全）
├── glossary/          术语词典（中英文对照 + 同义词映射，跨模块共享）
└── data-sheets/       数据速查（物理常数、矿物物性表、单位换算）  ← 增设，供 Agent 直接取数
```

- 模块即目录，模块自身元数据存于各目录 `_module.yaml`（名称、中文名、描述、排序号、父模块），支持二级子目录（如 `petrology/igneous/`）。
- 增设 `data-sheets` 的理由：Agent 高频需求是"取一个数"而非"读一篇文章"，速查表命中率与引用价值高（设计推断，未验证）。

### 2.2 条目元数据 Schema（YAML Frontmatter）

```yaml
---
id: min-quartz-001            # 唯一 ID：模块前缀-英文短名-序号，创建后不可变
title: 石英（Quartz）
module: mineralogy            # 一级模块 slug
tags: [氧化物矿物, 硅酸盐, 鉴定特征, 硬度]
summary: 二氧化硅最常见的同质多像变体，莫氏硬度 7，……  # ≤120 字，供检索摘要
confidence: A                 # 可信度分级：A=权威机构/经典教材  B=同行评议文献  C=可信二手汇编  D=未核实草稿
sources:
  - name: IMA/CNMNC 矿物名录
    url: https://...
    accessed: 2026-10-13
updated: 2026-10-13
status: active                # active | deprecated（失效标记）| deleted（软删除）
related: [petrology-granite-001]   # 可选，显式关联
---
（Markdown 正文）
```

- `status: deleted` 实现软删除：文件保留、索引排除、管理端可恢复。
- `status: deprecated` 实现失效内容标记：检索可见但响应中显著标注。
- 同义词映射独立成 `glossary/_synonyms.yaml`：`标准词 → [别名/英文/缩写]`，检索时双向扩展查询词。

---

## 3. 决策项三：检索架构

### 3.1 Agent 查询最优路径（四层漏斗）

| 层 | 输入 | 处理 | 输出/数据格式 |
|---|---|---|---|
| ① manifest | 无 | 读 `/manifest` 端点 | 全库结构概览 JSON：模块树、各模块条目数、最近更新、schema 版本——Agent 一次调用即知"库里有什么"，避免盲目检索 |
| ② 元数据粗筛 | module / tags / confidence / updated 过滤条件 | SQLite 普通索引 WHERE | 候选条目 ID 集（毫秒级） |
| ③ 混合检索 | 查询文本 | 关键词路：FTS5 BM25（中文按字/词混合分词，经同义词表扩展）；语义路：sqlite-vec 余弦近邻；两路结果用 **RRF（倒数排名融合）** 合并 | 带相关性评分（0–1 归一）与双路来源标注的 Top-N |
| ④ 上下文装配 | Top-N + token_budget 参数 | 按 summary → 正文截断的优先级装配，附来源与可信度 | 结构化 JSON，Agent 可直接引用或再请求 `/entry/<id>` 取全文 |

### 3.2 关键设计论证

- **为何混合而非纯语义**：地矿查询富含专有名词与符号（如 "SiO₂"、"矽卡岩"、"Re-Os 定年"），纯向量在此类精确 token 上不稳定；BM25 保精确命中、向量保语义泛化，RRF 融合是二者标准解（成熟做法，工程风险低）。
- **为何 RRF 而非加权分数**：BM25 与余弦相似度量纲不可比，加权需调参；RRF 只依赖排名，零参数、可复现。
- **中文检索**：FTS5 默认分词器对中文不友好，采用"单字 + jieba 词"双通道写入索引的成熟方案。**未验证**：jieba 对地矿专业词表的切分质量，Phase 3 将加载自定义地矿词典后再验证。

---

## 4. 决策项四：数据分享方法

### 4.1 候选对比

| 方案 | Agent 调用便利性 | 实现成本 | 内网适用性 | 结论 |
|---|---|---|---|---|
| **REST API（本服务）** | 高（任何 Agent 均可 HTTP 调用） | 低（Flask 自带） | 优 | **主方案** |
| MCP Server | 高（WorkBuddy/Claude 类原生） | 中 | 优 | **二期增强**：检索层已抽象，套一层 MCP wrapper 即可，本期预留接口边界 |
| 静态文件 + 索引 | 中（需 Agent 理解索引格式） | 低 | 优 | 以 `/manifest` + 源文件直读自然获得，零额外成本 |
| 直接文件读取 | 低（跨机不可行） | 0 | 仅限本机 | 不作为接口，但源文件天然可读是兜底 |

### 4.2 API 端点清单（全部返回 JSON，`/api` 前缀与页面路由隔离）

| 端点 | 方法 | 说明 |
|---|---|---|
| `/health` | GET | 存活与索引状态（条目数、索引构建时间、schema 版本） |
| `/manifest` | GET | 全库结构概览（模块树 + 统计 + schema），Agent 首次接入入口 |
| `/api/search` | GET/POST | 参数：`q`、`module`、`tags`、`confidence_min`、`limit`、`token_budget`；响应含评分、来源、可信度、双路命中标注 |
| `/api/module/<name>` | GET | 模块元数据 + 条目列表（不含正文） |
| `/api/entry/<id>` | GET | 条目全文（元数据 + Markdown 正文 + 渲染 HTML） |
| `/api/synonyms/<term>` | GET | 术语/同义词查询（供 Agent 查询前扩展） |

`/api/search` 请求/响应示例：

```jsonc
// GET /api/search?q=矽卡岩型钨矿&module=economic-geology&limit=5
{
  "query": "矽卡岩型钨矿",
  "expanded_terms": ["矽卡岩", "skarn", "白钨矿", "scheelite"],
  "hits": [
    {
      "id": "eco-skarn-w-001",
      "title": "矽卡岩型钨矿床",
      "module": "economic-geology",
      "score": 0.87,
      "matched_by": ["bm25", "vector"],
      "confidence": "A",
      "summary": "……",
      "sources": [{"name": "…", "url": "…", "accessed": "2026-10-13"}]
    }
  ],
  "index_built_at": "2026-10-13T21:00:00+08:00"
}
```

---

## 5. 决策项五：Web 管理端方案

### 5.1 信息架构（同一 Flask 服务、同一端口，路由三层隔离）

```
/            仪表盘（只读）：条目总数、模块分布、最近更新、可信度分布（图表）
/browse/     模块树导航 + 条目列表
/entry/<id>  条目详情（Markdown 渲染 + 元数据卡 + 来源列表）
/search      全站检索页（复用 /api/search，展示评分与双路命中标注）
/admin/      管理端（需鉴权）：
  ├── /admin/entries        条目新建/编辑/软删除/恢复（Markdown 编辑器 + 实时预览 + 结构化元数据表单）
  ├── /admin/organize       整理：条目跨模块移动、模块新建/重命名/排序
  ├── /admin/changelog      CHANGELOG 查看（含快照回滚入口）
  └── /admin/playground     检索测试台（输入问题看命中与评分，调优用）
```

### 5.2 技术选型（轻量优先，无构建链）

| 关注点 | 选型 | 论证 |
|---|---|---|
| 后端 | Flask（与既有门户同栈，运维心智一致） | 已验证可用；与 5555 服务进程完全独立 |
| 前端 | Jinja2 服务端渲染 + **本地化静态引入** htmx + Alpine.js | 不引入 npm/构建链；JS 库文件下载后存 `static/vendor/`，内网无外网依赖（**待确认项 B**：允许一次性下载这三个小文件） |
| 编辑器 | EasyMDE（本地引入）或 textarea + `/api/preview` 实时预览 | 编辑体验与零依赖的折中，Phase 5 实现时二选一 |
| 图表 | ECharts 本地引入（仪表盘可视化） | 单文件，内网可用 |
| 主题 | CSS 变量 `--bg/--fg/--accent` 等 + `data-theme` 属性切换，light/dark 双主题，**零硬编码颜色**（沿用门户 theme.js 同构方案） | 直接对齐用户既有约定 |
| 鉴权 | 管理端统一 token：HTTP Header `X-Admin-Token` 或登录表单置 cookie；凭据存 `geokb/config.local.yaml`（**git 忽略、不入代码、不入知识库**），支持环境变量覆盖 | 满足"简单即可"要求，风险面仅内网 |

### 5.3 写操作一致性设计（核心链路）

```
管理端写操作 → ① git 快照（见 §6.2）→ ② 落盘/修改 Markdown 源文件
            → ③ 调用统一摄取管线做增量索引重建（与 CLI 同一入口函数）
            → ④ 追加 changelog.jsonl → ⑤ 返回结果（含新索引版本号）
```

- ③为同步执行（单库体量下毫秒–秒级，**待 Phase 4 实测**；若条目量增长至秒级以上再改异步+任务状态页）。
- 任何一步失败即回滚快照并在 CHANGELOG 记录失败，保证「源文件、索引、日志」三者原子一致。

---

## 6. 决策项六：自更新机制

### 6.1 增量摄取流程（CLI 与 Web 管理端复用同一管线）

```
ingest(变更集) → 解析/校验 Frontmatter（schema 校验失败即拒收）
  → 更新 FTS5 行（按 id upsert）
  → 重算该条目 embedding 并 upsert 向量表
  → status=deleted/deprecated 的从索引剔除或降权
  → 追加 changelog.jsonl 一条（时间、操作、id、操作者、快照指针）
  → 刷新 manifest 缓存
```

### 6.2 版本追踪与备份

- **快照方案：git**。在 `D:/ZSK/geokb/` 初始化独立 git 仓库（与 GitHub kust_work 无关联；`.gitignore` 排除 `index/`、`venv/`、`config.local.yaml`）。每次写操作前自动 commit 快照，回滚 = `git revert` 指定快照点 + 全量重建索引。
  - 备选：纯文件快照（tar 打包 content/ 目录）。**推荐 git**（diff 可查、体积省、与 CHANGELOG 天然互补）——**待确认项 C**。
- **CHANGELOG 双形态**：`changelog.jsonl`（机器可读、页面数据源）+ 管理端页面渲染；不额外维护手写 CHANGELOG.md，避免双写漂移。
- **来源可信度分级**：A/B/C/D 四级（见 §2.2），Phase 2 摄取时强制填写；仪表盘按级分布可视化，D 级在检索响应中默认降权（可在 search 参数中覆盖）。
- **失效策略**：内容被新证据推翻时置 `status: deprecated` 而非删除，正文顶部标注失效原因与替代条目 ID，保留溯源链。

---

## 7. 目标目录结构（Phase 3–5 落盘后的形态）

```
D:/ZSK/geokb/
├── docs/                 本报告、运维文档、API 文档、frpc 预留说明
├── content/              源数据层：各模块目录 + _module.yaml + *.md
│   └── glossary/_synonyms.yaml
├── index/geokb.db        派生索引（FTS5 + sqlite-vec，可随时删除重建）
├── server/
│   ├── app.py            Flask 入口（页面 + /api + /admin 三蓝图）
│   ├── retriever.py      检索层（BM25/向量/RRF，MCP 预留边界）
│   ├── ingest.py         统一摄取管线（CLI 与管理端共用）
│   └── templates/ static/（含 vendor/ 本地 JS、CSS 变量主题）
├── scripts/
│   ├── serve_ctl.py      start/stop/status（封装 supervisor 配置生成与检查）
│   ├── ingest_cli.py     命令行增量摄取
│   └── acceptance_test.py  Phase 6 验收测试
├── models/               嵌入模型缓存（git 忽略）
├── config.local.yaml     凭据与端口（git 忽略）
├── supervisor_geokb.conf 守护配置（独立程序名，不影响既有配置）
├── frpc_geokb.ini        预留片段（不启用）
└── changelog.jsonl
```

frpc 预留片段（仅配置位，不执行）：

```ini
# frpc_geokb.ini —— 预留，未启用。公网发布前需补 token 并评估鉴权风险。
[geokb]
type = tcp
local_ip = 127.0.0.1
local_port = 5556
remote_port = <待分配>
```

---

## 8. 假设与待确认项（歧义清单，请逐项确认或修正）

| # | 事项 | 我的假设（默认方案） | 备选解释 | 影响面 |
|---|---|---|---|---|
| A | 嵌入模型下载 | 允许一次性下载 bge-small-zh-v1.5（约 100MB，存 `models/`，CPU 推理） | 不允许联网下载 → 纯 BM25 + 同义词模式 | 语义检索质量 |
| B | 前端库获取 | 允许一次性下载 htmx/Alpine/ECharts/EasyMDE 共约 1MB 到 `static/vendor/` | 完全离线 → 手写原生 JS（开发量增加） | 管理端交互体验 |
| C | 快照方案 | git 本地仓库（D:/ZSK/geokb/ 独立 repo，不推远端） | 文件 tar 快照 | 回滚粒度与 diff 可查性 |
| D | 管理端鉴权凭据 | 生成随机 token 写入 `config.local.yaml`，首次启动打印一次 | 用户自定义密码 | 无本质差异 |
| E | Phase 1 扫描范围 | 仅 D:/ZSK（目前基本为空）；如你有其他资料目录（PDF/笔记），请提供路径 | — | 资源盘点完整性 |
| F | Phase 2 联网抓取 | 本机可访问 USGS/Mindat/IMA 等站点（**未验证**），Phase 2 前先做连通性探测 | 校园网受限 → 改用手动提供素材 + 本地缓存 | 权威内容来源 |
| G | 守护方式 | 复用本机 supervisor，新增独立 `supervisor_geokb.conf`，程序名与既有服务隔离；调试期用 `scripts/serve_ctl.py` 前台/受控方式，严禁裸 Popen 长跑 | Windows 任务计划 | 服务稳定性 |

---

## 9. 风险登记

| 风险 | 等级 | 缓解 |
|---|---|---|
| sqlite-vec 在 Py3.13/Windows 不可用 | 中 | Phase 4 首日验证，备选 Chroma 嵌入式（检索层接口已隔离） |
| jieba 专业分词质量差 | 低 | Phase 3 加载地矿自定义词典 + 单字通道兜底 |
| 嵌入模型下载失败/太慢 | 中 | 待确认项 A 的降级路径 |
| 写操作并发（管理端多人同时编辑） | 低 | 单文件写锁 + 摄取管线串行化；内网单用户场景下风险可忽略 |
| 误触 5555 服务 | — | 本方案不读取、不连接、不重启任何既有服务；守护配置独立 |

---

## 10. Phase 0 小结

- **完成项**：六大决策项对比与选型论证；模块树与元数据 schema；检索四层漏斗；API 清单；管理端信息架构与一致性链路；自更新与快照机制；目录结构；frpc 预留位；端口与工作目录实测。
- **产出文件**：`D:/ZSK/geokb/docs/Phase0_架构设计报告.md`（本文件）。
- **已验证结论**：端口 5556 空闲；工作区干净无既有文件风险面。
- **未验证/待确认**：第 8 节 A–G 七项；sqlite-vec/jieba/嵌入模型的技术验证安排在后续 Phase。
- **下一步**：用户确认本报告（重点确认第 8 节）→ 进入 Phase 1 本地资源只读盘点。
