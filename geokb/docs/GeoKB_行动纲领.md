# GeoKB 行动纲领（CHARTER）

> 本文件是 GeoKB 项目的**最高行动纲领**，由原始任务书（`D:/ZSK/geokb_build_prompt.md`）与用户在 2026-10-13 确认的决策固化而成。后续任何会话、任何 Phase 开始前，先读本文件与本目录下的《Phase0_架构设计报告.md》，即可无损恢复全部上下文。
> 本文件的效力高于会话中的临时讨论；如需变更，须由用户明确指示后修改本文件。

- 固化日期：2026-10-13
- 项目根目录：`D:/ZSK/geokb/`
- 架构报告：`D:/ZSK/geokb/docs/Phase0_架构设计报告.md`

---

## 1. 人格与工作方式（不可变）

- 角色：资深知识工程师 + 全栈工程师（地矿学科体系 × Agent 检索 × 本地化知识服务 × 轻量 Web）。
- 明确声明假设；给出方案正确性论证；**严格区分「已验证」与「未验证」结论**；动手前先做非破坏性验证。
- 全程简体中文输出；关键方案对比与结构化结论一律用表格。
- 遇需求歧义：先列假设与备选解释，向用户确认后再推进，不擅自猜测。
- 每个 Phase 结束输出结构化小结：完成项 / 产出文件路径 / 已验证结论 / 未验证待确认项 / 下一步。

## 2. 红线约束（不可变）

| # | 红线 | 说明 |
|---|---|---|
| R1 | 不干扰 5555 现有服务 | 本机 5555 有 Flask 门户在 supervisor 下运行；GeoKB 用 **5556**（2026-10-13 实测 5556–5560 全空闲）；**仅限用户明确指示时修改 5555 代码**（如 registry.py 入口注册），修改后须用户自行重启 5555 生效 |
| R2 | 非破坏性优先 | 一切扫描/索引/写入不得修改、移动、删除既有文件；外部资源目录全程只读 |
| R3 | 禁止失控长跑进程 | 调试期禁止裸 Popen/后台跑长期服务；正式运行用受管守护 + start/stop/status 脚本 |
| R4 | 暂不公网发布 | 仅内网测试；仅预留 frpc 配置片段与说明（`frpc_geokb.ini`），不执行穿透 |
| R5 | 中文输出 | 报告、日志、文档全部简体中文 |
| R6 | 资源获取合法合规 | 仅抓取公开、允许学术使用的资源；每个条目标注来源与获取日期 |

## 3. 用户确认的决策（2026-10-13，A–G）

| # | 事项 | 确认结果 |
|---|---|---|
| A | 嵌入模型 | **允许**一次性下载 `BAAI/bge-small-zh-v1.5`（约 100MB）至 `models/`；若下载失败降级为 BM25+同义词模式 |
| B | 前端库 | **允许**一次性下载 htmx / Alpine.js / ECharts / EasyMDE（约 1MB）至 `static/vendor/` 本地化，运行期零外网依赖 |
| C | 快照方案 | **git 本地仓库**（`D:/ZSK/geokb/` 独立 repo，不推远端；`.gitignore` 排除 index/、venv/、models/、config.local.yaml） |
| D | 管理端鉴权 | 自动生成随机 token 写入 `config.local.yaml`（git 忽略），支持环境变量覆盖 |
| E | Phase 1 扫描范围 | `D:/ZSK` + **`D:/PostNews/pdfs`**（用户口述 `D://pdfs`，Phase 1 实测定位为 PostNews 下：819 篇斑岩 Cu-Mo 主题期刊 PDF + 20 篇顶层精选） + **`D:/PostNews/Translate`**（重点产出：books/books_text 四部合法开放书籍纯文本、corpus/glossary_verified.json 293 条已验证双语术语、modules/notes 12 矿种镜下鉴定手册 HTML） |
| F | 外网连通性 | **已实测（2026-10-13）**：CrossRef ✅、USGS ✅、ICS ✅、hf-mirror ✅（嵌入模型走镜像，HF 主站 502）；IMA ❌（证书+403，**用户已拒绝该域请求，不再尝试**）、RRUFF ❌、Mindat/OpenGeology/oapen ❌（先验）。详见 `docs/Phase2_线上资源报告.md` |
| G | 守护方式 | **supervisor**（推荐确认）：新增独立 `supervisor_geokb.conf`，程序名与既有配置隔离；封装 `scripts/serve_ctl.py` 提供 start/stop/status |

## 4. 已冻结的架构选型（详见 Phase 0 报告）

1. **存储三层分离**：Markdown+YAML Frontmatter 为唯一事实来源 → SQLite FTS5（BM25）+ sqlite-vec（向量）同库派生索引 `index/geokb.db`，可随时全量重建。
2. **模块树**：mineralogy / petrology / structural / geochemistry / economic-geology / geophysics / geochronology / field-methods / glossary（含 `_synonyms.yaml` 中英同义词映射）/ data-sheets。
3. **条目 schema**：id、title、module、tags、summary、confidence(A-D)、sources(name/url/accessed)、updated、status(active/deprecated/deleted)、related。
4. **检索四层漏斗**：/manifest → 元数据粗筛 → 混合检索（BM25 × 向量，RRF 融合）→ token 预算上下文装配。中文分词：单字 + jieba（加载地矿自定义词典）。
5. **API**：`/health`、`/manifest`、`/api/search`、`/api/module/<name>`、`/api/entry/<id>`、`/api/synonyms/<term>`；MCP 二期套壳预留。
6. **Web 端**：Flask 单服务 5556 三蓝图（只读页 / /api / /admin）；Jinja SSR + 本地 htmx/Alpine；CSS 变量 light/dark 双主题，**禁止硬编码颜色**；管理端 token 鉴权。
7. **一致性链路**：写操作 → git 快照 → 落盘 Markdown → 统一摄取管线增量重建（CLI 与管理端共用 `ingest.py`）→ changelog.jsonl → 返回新索引版本。
8. **自更新**：软删除（status=deleted 可恢复）、失效标记（deprecated 保留溯源）、可信度四级降权、CHANGELOG 双形态（jsonl + 管理端页面）。

## 5. Phase 路线与验收

| Phase | 内容 | 状态 |
|---|---|---|
| 0 | 架构分析与方案设计 | ✅ 完成，报告已获用户确认（2026-10-13） |
| 1 | 本地资源只读盘点（范围见 §3-E）→《本地资源清单》 | ✅ 完成（2026-10-13），产出 `docs/Phase1_本地资源清单.md` |
| 2 | 权威线上资源获取（连通性已实测，F 项闭环） | ✅ 完成（2026-10-13），产出 `docs/Phase2_线上资源报告.md` + `raw_sources/`（USGS MCS2025、ICS 地层表 v2024-12、来源登记） |
| 3 | 知识库内容构建（按模块树与 schema 落盘 + 术语词典） | ✅ 完成（2026-10-13）：82 条目 / 10 模块 / 校验零错误 + 819 条论文题录，见 `docs/Phase3_内容构建小结.md` |
| 4 | 检索与服务层实现（Flask API 5556 端口 + 混合检索 + supervisor 守护） | ✅ 完成（2026-10-13），见 `docs/Phase4_检索服务层小结.md` |
| 5 | Web 管理端（展示侧 + /admin 管理侧 + **5555 门户入口「团队知识库」**） | ✅ 完成（2026-10-13），见 `docs/Phase5_Web管理端小结.md` |
| 6 | 自更新机制 + 运维文档 + 验收测试（≥10 题，命中率 ≥80% 且 Top-3 命中正确条目） | ✅ 完成（2026-10-14），见 `docs/Phase6_自更新与验收小结.md` |

**总验收标准**（摘自任务书）：架构报告确认 ✔ / 一级模块全覆盖且条目有来源 / 服务稳定、API 有文档 / 检索命中达标 / 管理端全流程 + 主题无硬编码颜色 + 未鉴权不可访问 / 未影响 5555、未改既有文件 / frpc 仅预留未暴露。

## 6. 变更记录

| 日期 | 变更 | 触发 |
|---|---|---|
| 2026-10-13 | 初版固化 | 用户确认 Phase 0 报告与 A–G 决策 |
| 2026-10-13 | E 项路径修正（D://pdfs → D:/PostNews/pdfs）；F 项补充 Phase 1 发现的合规先验；Phase 1 标记完成 | Phase 1 盘点结果 |
| 2026-10-13 | F 项闭环：连通性矩阵实测，IMA 被用户拒绝不再尝试，HF 模型改走 hf-mirror；Phase 2 标记完成 | Phase 2 探测与下载结果 |
| 2026-10-13 | Phase 3 全部完成（82 条目/10 模块/零校验错误 + 819 论文题录）；Phase 4 完成（Flask API 5556 端口上线、6 端点冒烟通过、supervisor 守护就绪） | 内容构建 + 检索服务层 |
| 2026-10-13 | **Phase 5 前置需求**：用户要求将 5555 门户 `registry.py` 中「地学术语库」(id:terms) 改为「团队知识库」(id:geokb)，url 指向 `http://127.0.0.1:5556`，状态改为 online。**已修改 registry.py，待用户重启 5555 生效**。R1 红线同步修订：允许用户明确指示时修改 5555 代码 | 用户新增需求 |
| 2026-10-13 | **Phase 5 完成**：Web 管理端上线；展示侧（仪表盘/模块树/检索/详情）+ /admin 鉴权 CRUD + light/dark 主题 + 写后同步重建索引一致性；已验证新建→可检、删除→不可检、恢复→可检。总进度 85%。 | Web 管理端 |
| 2026-10-14 | **Phase 6 完成**：自更新机制上线（`ingest_external_feeds.py`），融合 PostNews 期刊推送 + 新闻 → 关键词筛选 → PDF 下载 → GeoKB 条目录入；PDF 统一仓库 `D:/PostNews/papers_repo/` 856 条；本地 LLM（Qwen 3.5 4B/0.8B）在 llama-cli 下只输出思考过程，无法稳定输出 JSON，当前关闭 LLM、改用规则兜底；验收 12 题模块命中率 100%、Top-3 83%；新增 `/papers` Web 页面；产出 `Phase6_自更新与验收小结.md` 与 `geokb-self-update` skill。总进度 100%。 | 用户新增需求：期刊/新闻融合与 PDF 集中 |
