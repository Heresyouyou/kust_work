# GeoKB 架构优化分析报告

> 报告时间：2026-10-14
> 当前架构：5556 公网 AI API + 5555 用户可视化 + 5554 本地管理

## 1. 当前架构状态

| 端口 | 监听地址 | 状态 | 职责 | 验证结果 |
|---|---|---|---|---|
| 5556 | `0.0.0.0:5556` | 运行中 | 面向公网 Agent/AI 的只读 API | `/health` 200；`/admin` 404 |
| 5555 | `0.0.0.0:5555` | 运行中 | 用户可视化门户，/geokb 公开浏览 | 门户入口指向 `/geokb` |
| 5554 | `127.0.0.1:5554` | 运行中 | 本地管理端，需 Token | 登录后 Dashboard 正常；外部访问 403 |

关键文件：
- `D:/ZSK/geokb/server/app.py` — 5556 AI API
- `D:/ZSK/geokb/server/admin/app.py` — 5554 入口
- `D:/ZSK/geokb/server/admin/routes.py` — 5554 路由（含 `/admin/self-update`）
- `D:/PostNews/Translate/modules/geokb_public/routes.py` — 5555 可视化
- `C:/Users/youyo/.workbuddy/skills/geokb-self-update/SKILL.md` — 运维纪律已更新

## 2. 检索性能基准

在本地 127.0.0.1 用 curl/Python 测试：

| 查询 | 响应时间 | hits_count(limit=5) | Top-1 标题 |
|---|---|---|---|
| 钨矿 | 113 ms | 5 | GoldHaven 加拿大 Kuhn 钨矽卡岩靶区钻探… |
| 斑岩铜矿 | 46 ms | 5 | 术语表·alteration |
| IMA | 43 ms | 5 | Earle 教材术语表·S |
| USGS | 42 ms | 5 | USGS 矿产商品概要 2025 |
| 锆石 U-Pb | 45 ms | 5 | 经济地质中的同位素 |
| 钨矿（adaptive） | 37 ms | 50 | — |

**结论**：首词查询约 110ms（含模型/分词冷启动），后续查询稳定在 40-50ms；adaptive 截断返回 50 条时仍 <40ms，性能足够。

## 3. 当前结构与调用方式可优化点

### 3.1 模块分布不均（已验证）

```
glossary        43 条（55%）
economic-geology 19 条（24%）
mineralogy       13 条（17%）
data-sheets       1 条
geochemistry      1 条
geochronology     1 条
```

**影响**：
- 非 glossary 模块覆盖不足，AI 查询「岩石学」「矿床实例」「区域地质」时召回偏少。
- `glossary` 内部 43 条全堆在一个模块，未再细分（如 `glossary-mineralogy`、`glossary-structures`），导致同义词扩展和标签共现信号稀释。

**建议**：
1. 将 glossary 拆分为二级模块：`glossary-mineralogy`、`glossary-structures`、`glossary-terminology`。
2. 补充 `petrology`、`structural-geology`、`regional-geology` 等模块。
3. 新增条目时通过关键词自动分配模块，减少人工指定。

### 3.2 向量模型与嵌入维度

- 当前模型：`BAAI/bge-small-zh-v1.5`，512 维，模型文件约 91MB。
- 问题：中文地矿领域术语（如"矽卡岩"、"斑岩型"、"VMS"）在通用模型中语义对齐不足；外文术语（如"porphyry Cu-Mo"）与中文混合查询时向量相关性下降。

**建议**：
1. 收集 500+ 领域问答对，用 LoRA 微调 bge-small，预计可提升 Top-3 命中率 10-15%。
2. 若微调成本高，可先建立领域词表并通过同义词扩展（`config/synonyms.json`）补偿。
3. 考虑多语言模型（如 `BAAI/bge-m3`）以改善中英混合查询，但模型更大、推理更慢。

### 3.3 检索数量控制策略

当前 adaptive cutoff：
- `score_ratio=0.55`、`min_results=3`、`global_cap=50`。
- 实测 "钨矿" 返回 50 条，说明分数下降缓慢，存在大量低相关结果。

**建议**：
1. 对无明确模块/标签的泛化查询，降低 `global_cap` 到 20 或加入相邻分数差 `gap_threshold=0.10`。
2. 对带 `module` 的精确查询，可提高 `score_ratio=0.70`。
3. 在 API 返回中增加 `score_gap` 或 `suggested_filters`，帮助 AI 判断截断点。

### 3.4 5555 可视化层脆弱点

当前 `geokb_public` 通过 `urllib` 同步调用 127.0.0.1:5556：
- 5556 异常时 5555 页面会报错或空白。
- 每次请求都重新打开连接，没有连接池。
- Markdown 渲染依赖后端 `markdown` 库；若未安装则回退到 `<pre>`。

**建议**：
1. 增加 5556 不可用时返回降级页（"检索服务维护中"）。
2. 使用 `urllib` 时设置 `timeout=5` 并捕获异常。
3. 将 Markdown 渲染改为前端 `marked.js`，减少后端依赖。
4. 引入缓存：5556 `/manifest` 和 `/api/modules` 可缓存 60 秒。

### 3.5 5556 API 的 AI 友好度

优点：
- `/openapi.json` 自描述完整。
- 支持 POST JSON 复杂过滤、字段裁剪、分页。

可改进：
1. `/api/search` 返回 `body_markdown` 仅在 `format=full` 时提供，AI 需要权衡 token 消耗；建议增加 `format=ai` 自动按 `token_budget` 裁剪正文。
2. 返回字段中没有 `url` 或 `permalink`，AI 无法直接引用条目链接；建议增加 `permalink: /api/entry/<id>`。
3. `/api/entry/<id>` 返回原始 Markdown，AI 解析成本高；建议增加 `?format=html`。
4. 缺少批量查询接口 `/api/entries/batch`。

### 3.6 限流与监控

当前 5556 限流：
- 内存滑动窗口，60 秒 120 次/IP。
- 重启后清零。
- 无持久化访问日志。

**建议**：
1. 在 5556 写入结构化访问日志 `logs/access_YYYYMMDD.log`，字段：timestamp、ip、method、path、status、latency_ms、agent。
2. 对异常 User-Agent 或高频 IP 增加临时黑名单。
3. 在 `/health` 中暴露最近 1 分钟 QPS 和限流命中次数。

### 3.7 索引一致性风险

- 5554 管理端保存 Markdown 后调用 `rebuild_index.py` 异步重建。
- `rebuild_index.py` 当前没有锁机制，连续多次编辑可能触发并发重建。
- 5556 读取旧索引时，5554 正在重建可能导致短暂 500。

**建议**：
1. 在 `rebuild_index.py` 增加文件锁（`index/.rebuild.lock`），避免并发重建。
2. 重建完成后原子重命名数据库（当前 SQLite 不支持 rename db，但可写临时索引后切换）。
3. 5556 捕获 `sqlite3.OperationalError` 并返回 503，提示"索引重建中"。

### 3.8 外部 feed 自更新链路

- `ingest_external_feeds.py` 目前是离线脚本，由 `/admin/self-update` 触发。
- 没有进度反馈到 5554 管理端。
- 失败时只在日志文件中记录。

**建议**：
1. 增加状态文件 `index/self_update_status.json`，包含 `running`、`last_run`、`last_error`、`added`、`failed`。
2. 5554 Dashboard 读取该文件展示运行状态。
3. 失败时通过 5555 门户通知管理员（写入文件或发送消息）。

## 4. 推荐优化优先级

| 优先级 | 优化项 | 预计收益 | 工作量 |
|---|---|---|---|
| P0 | 给 `rebuild_index.py` 加文件锁；5556 捕获索引异常返回 503 | 避免并发重建导致 500 | 小 |
| P0 | 5555 `geokb_public` 增加 5556 异常降级 | 提升可用性 | 小 |
| P1 | 拆分 glossary 模块，补充 petrology/structural-geology | 提升检索召回 | 中 |
| P1 | 5556 增加访问日志与 `/health` QPS 指标 | 可观测性 | 小 |
| P1 | API 增加 `format=ai`、`permalink`、`format=html` | AI 调用效率 | 小 |
| P2 | 领域同义词表扩充 + 考虑 bge-m3 或微调 | 检索质量提升 10-15% | 大 |
| P2 | 自更新状态文件 + Dashboard 展示 | 管理体验 | 中 |

## 5. 结论

当前三层架构（5556/5555/5554）已经落地并验证通过，端口职责清晰，检索性能良好。主要优化空间集中在：
1. **数据质量**：模块拆分、领域词表扩充；
2. **AI 调用效率**：字段裁剪、permalink、HTML 输出；
3. **可用性**：降级页、并发重建锁、访问日志。

建议按 P0→P1→P2 的顺序分阶段实施，避免一次性大改影响在线服务。
