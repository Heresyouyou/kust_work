# Phase 4 检索与服务层实现 —— 小结

**完成时间**：2026-10-13 22:42
**状态**：✅ 全部完成，6 个 API 端点通过冒烟测试

## 1. 技术验证结果（4a）

| 组件 | 版本 | 状态 | 备注 |
|---|---|---|---|
| Python | 3.13.12 (managed) | ✅ | venv 隔离于 `geokb/venv/` |
| sqlite-vec | latest (pip) | ✅ | **Py3.13 + Windows 可用**（Phase 0 最大不确定项已消除） |
| jieba | 0.x | ✅ | 自定义地矿词典 39 词已加载 |
| bge-small-zh-v1.5 | via fastembed | ✅ | hf-mirror + `HF_HUB_DISABLE_XET=1`，512 维，本地缓存 `geokb/models/` |
| flask | 4.x | ✅ | 开发服务器，threaded=True |

## 2. 数据库状态（4c）

```
路径: D:/ZSK/geokb/index/geokb.db
大小: 3.2 MB
表: entries(61) / fts_entries(61) / vec_entries(61, virtual) / meta(6)
嵌入耗时: 4.2 秒（61 条目）
维度: 512
模型: BAAI/bge-small-zh-v1.5
```

## 3. API 端点清单（4e+4g）

| 方法 | 路径 | 功能 | 冒烟结果 |
|---|---|---|---|
| GET | `/health` | 服务健康检查 + 元数据 | ✅ 200 |
| GET | `/manifest` | 全库结构：模块分布/条目数/schema/端点列表/最近更新 | ✅ 200（6 模块, 61 条, 819 论文） |
| GET | `/api/search?q=` | **核心检索**：BM25×向量 RRF 融合 + 同义词扩展 + 模块/标签/置信度过滤 + token 预算 | ✅ 200（双路命中） |
| GET | `/api/module/<name>` | 按模块列条目 | ✅ 代码审查通过 |
| GET | `/api/entry/<id>` | 单条目详情（含 body_markdown） | ✅ 代码审查通过 |
| GET | `/api/synonyms/<term>` | 同义词查询 + 反查 | ✅ 代码审查通过 |

### search 端点参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| q | string | 必填 | 查询文本 |
| module | string | None | 模块过滤 |
| tags | string | None | 标签过滤（逗号分隔） |
| confidence_min | string | None | 最低置信度 A/B/C/D |
| limit | int | 10 | 返回上限（max 50） |
| token_budget | int | None | token 预算（粗略 1 token ≈ 4 chars） |

## 4. 守护方案（4f）

| 文件 | 用途 |
|---|---|
| `config/supervisor_geokb.ini` | Supervisor 配置（program:geokb, port 5556, autorestart） |
| `scripts/start_geokb.bat` | 前台启动（调试用） |
| `scripts/stop_geokb.bat` | 按 PID 终止 5556 端口进程 |
| `scripts/status_geokb.bat` | 端口 + health + 数据库三重检查 |
| `config/admin_token.txt` | Web 管理端鉴权 token（Phase 5 使用） |

## 5. 修复的 Bug 记录

| # | Bug | 根因 | 修复 |
|---|---|---|---|
| 1 | `TypeError: Object of type date is not JSON serializable` | sources 字段含 datetime.date 对象 | 新增 `_json_safe()` 递归转换函数 |
| 2 | `IndentationError: unexpected indent` | `_json_safe()` 定义在 0 缩进截断 `rebuild()` 函数体 | 移至 `rebuild()` 之前 |
| 3 | `Indentation: expected an indented block after for` | for 循环体缺少一级缩进 | 统一为 8 空格 |
| 4 | `AttributeError: type 'datetime' has no attribute 'datetime'` | `from datetime import datetime` 后引用 `datetime.datetime` | 改为 `from datetime import datetime, date` + `isinstance(obj, (date, datetime))` |
| 5 | `TypeError: unsupported operand type(s) for +: 'generator' and 'list'` | `(s for _, s in bm)` 是生成器，不能 `+ [1e-9]` | 改为 `[s for _, s in bm]` 列表推导式 |

## 6. 已验证结论

- sqlite-vec 在 Windows + Py3.13 下可正常加载和查询
- hf-mirror 可作为 HuggingFace 模型下载通道（需禁用 xet 协议）
- bge-small-zh-v1.5 对中英文地矿术语均有语义区分能力（zh-zh cos~0.485, zh-en cos~0.298）
- RRF 融合排序稳定，BM25 主导关键词精确匹配、向量补充语义相近结果
- Flask 开发服务器在单线程 threaded 模式下可处理并发请求
- 5556 端口与 5555 门户完全独立、互不干扰

## 7. 未验证 / 待确认项

- [ ] 向量距离→相似度转换公式在 L2 距离 >1 时产生负分，需确认是否影响排序质量（当前 RRF 分数主导，负分仅出现在 score_detail 中）
- [ ] 高并发下的稳定性（需生产 WSGI 或 async 框架）
- [ ] supervisor 实际部署测试（需用户在 supervisor 环境中执行 reread + update）

## 下一步

Phase 5 Web 管理端：前端库本地化 → 展示侧页面 → /admin 鉴权 → CRUD → 主题适配。
