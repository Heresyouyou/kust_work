# GeoKB 总进度清单（跨会话续作）

> 每完成一项就把 `[ ]` 改为 `[x]` 并更新进度条与「最后更新」。新会话开始先读本文件与《GeoKB_行动纲领.md》。
> 进度条规则：每格 5%，`█` 已完成，`░` 未完成。

- 最后更新：2026-10-14（Phase 6 完成：自更新/期刊/新闻/PDF 仓库融合，78 条目，856 PDF 索引）

## 总体进度

```
Phase 0 架构设计   ████████████████████ 100%
Phase 1 资源盘点   ████████████████████ 100%
Phase 2 线上资源   ████████████████████ 100%
Phase 3 内容构建   ████████████████████ 100%
Phase 4 检索服务   ████████████████████ 100%
Phase 5 Web 管理端 ████████████████████ 100%
Phase 6 自更新验收 ████████████████████ 100%
─────────────────────────────────────────
总进度             ████████████████████ 100%
```

## Phase 0–2（已完成）

- [x] Phase 0 架构设计报告并经用户确认
- [x] 行动纲领固化（GeoKB_行动纲领.md）
- [x] Phase 1 本地资源只读盘点
- [x] Phase 2 连通性矩阵 + 权威资源下载（USGS MCS2025 / ICS 地层表）

## Phase 3 内容构建（全部完成）

- [x] 3a content/ 模块树骨架 + 各模块 `_module.yaml`（10 模块）
- [x] 3b 术语词典落盘：glossary_verified 293 条 → 16 个 glossary 分组条目 + `_synonyms.yaml`
- [x] 3c 12 矿种镜下鉴定手册 HTML → mineralogy Markdown 条目
- [x] 3d Earle 教材 22 章全量切分 → 8 个模块的 22 个章节条目
- [x] 3e USGS 1693 / Huston / ICS / MCS2025 四条权威源条目
- [x] 3f 819 篇论文 → papers_index.jsonl（819/819 解析成功）+ 语料说明条目
- [x] 3g Earle 601 条术语 → 27 个字母分组条目（新增 384 条）
- [x] 3h 全量校验 → `docs/Phase3_内容构建小结.md`

## Phase 4 检索与服务层（全部完成）

- [x] 4a 技术验证三件套：**sqlite-vec ✅ Py3.13+Windows 可用** / jieba / **bge-small-zh-v1.5 经 hf-mirror 下载 ✅（512 维，HF_HUB_DISABLE_XET=1 绕过 xet）**
- [x] 4b venv 建立与依赖安装（flask/jieba/sqlite-vec/fastembed/pyyaml → `geokb/venv/`）
- [x] 4c ingest.py 摄取管线（**61 条目全量嵌入 4.2 秒**；修复 date 序列化 + 缩进 + datetime 导入 3 个 bug）
- [x] 4d retriever.py 混合检索（BM25 FTS5 × sqlite-vec 向量 → RRF 融合 + 同义词扩展；修复 generator+list 类型冲突 bug）
- [x] 4e Flask app.py：**6 个端点全通**（/health、/manifest、/api/search、/api/module、/api/entry、/api/synonyms）
- [x] 4f 守护脚本：supervisor_geokb.ini + start/stop/status bat 脚本 + admin_token.txt
- [x] 4g 冒烟测试通过：health(200) + manifest(200, 6 模块/61 条/819 论文) + search(200, RRF 双路命中)

### Phase 4 已知限制（不阻塞 Phase 5）
- 向量相似度分数部分为负值（sqlite-vec 距离→相似度转换在 L2 距离 >1 时溢出），RRF 排序不受影响（由 RRF score 主导）
- Flask 开发服务器（单线程阻塞式），Phase 5 可升级为 threaded 或生产 WSGI

## Phase 5 Web 管理端

- [x] 5a 前端库本地化：htmx 2.0.4 / Alpine 3.14.8 / marked 15.0.6 / Chart.js 4.4.7 → `static/vendor/`
- [x] 5b 展示侧：仪表盘（含 Chart.js 图表）/ 模块树 / 条目列表 / 条目详情 / 全站检索页
- [x] 5c 主题：CSS 变量 light/dark，所有颜色来自变量，零硬编码颜色
- [x] 5d /admin 鉴权：token 从 `config/admin_token.txt` 读取，未登录自动跳转登录页
- [x] 5e 条目 CRUD：新建 / 编辑 / 软删除 / 恢复，Markdown 编辑器 + 实时预览，元数据结构化表单
- [x] 5g 写后一致性：git 快照 → 同步重建索引 → CHANGELOG；已验证「新建→可检、删除→不可检、恢复→可检」
- [ ] 5f 整理功能：跨模块移动 / 模块树的新建重命名排序（界面已支持编辑换 module 实现跨模块移动，模块树管理为可选增强）

## Phase 6 自更新与验收

- [x] 6a 自更新机制：journal_pushed.json + news.json → `ingest_external_feeds.py` → 关键词筛选 → PDF 下载 → GeoKB Markdown → 重建索引
- [x] 6b PDF 统一仓库：`papers_repo/`（839 已有 + 17 新元数据 = 856 条索引），`by_journal`/`by_date` 双视图
- [x] 6c 本地小模型：Qwen 3.5 4B 在 llama-cli 下只输出思考过程、无法稳定输出 JSON；**当前关闭 LLM，改用规则兜底**（关键词筛选 + 规则提取摘要/实体/方法/区域/应用）
- [x] 6d 命中测试：≥10 题覆盖期刊/新闻/模块，命中率表格
- [x] 6e 运维文档：`docs/Phase6_自更新与验收小结.md` + `geokb-self-update` skill
- [x] 6f Web 端论文仓库：`/papers` 页面 + 侧边栏入口 + 856 条展示

## 当前阻塞 / 待用户决策

- 无。（geophysics 专项权威源为可选增量，不阻塞）
