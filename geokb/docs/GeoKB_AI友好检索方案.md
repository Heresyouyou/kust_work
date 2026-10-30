# GeoKB AI 友好检索方案（免 API 通路）

> 目标：让公网 AI Agent 访问 `https://sjck.kust.work` 时，**即使不调用检索 API**，也能快速定位想要的文件。
> 全部改动实时从索引生成，2026-10-14 已通过公网 https 实测。

## 1. 问题背景

早期 Agent 反馈"只能看到路由说明，拿不到库内数据"，根因有三层：

| 摩擦点 | 表现 | 后果 |
|---|---|---|
| JSON 嵌套深 | 检索结果是 `data.hits[].body_markdown` 多级结构 | Agent 解析漏字段 |
| 关键参数不可见 | `format` 默认 `summary`，不含正文 | Agent 误判"接口拿不到数据" |
| 必须逐次请求 | 每次检索一次 HTTP，弱网/限流易断 | 检索链路不可靠 |

## 2. 解法：四条离线通路 + Markdown 输出

### 2.1 离线通路（不调用任何检索 API）

| 通路 | 体积 | 适用 |
|---|---|---|
| `GET /topics.md` | ~8 KB | 先看模块/标签分布，拿拼好的检索 URL 模板 |
| `GET /corpus.md` | 885 KB | 整页下载后用文本检索工具离线找（`?module=papers` 可降到 150 KB） |
| `GET /corpus.json` | 5.6 MB | 程序化检索，含 `pdf_url` / `md_url` |
| `GET /corpus.json.gz` | **1.4 MB** | 同上 gzip 版，弱网友好 |

`corpus.*` 每条携带：`id / title / module / tags / summary / confidence / status / source_tier / url / md_url / pdf_path / pdf_url`。
当前规模：**5277 条**（kpoints 4415、papers 748、glossary 43、其余 12 个模块 71）。

### 2.2 Markdown 输出（在线检索也降摩擦）

| 入口 | 说明 |
|---|---|
| `/api/search?q=…&format=md` | 检索结果直接给 Markdown：标题、id、分级、标签、摘要、取全文地址、PDF 地址 |
| `/api/entry/<id>.md` | 单条目 Markdown；`/api/entry/<id>` 加 `?format=md` 或 `Accept: text/markdown` 等价 |

### 2.3 推荐三步法（全程只需 1 次 API 请求）

```
1) curl -o corpus.json.gz https://sjck.kust.work/corpus.json.gz && gunzip corpus.json.gz
2) 本地在 title/summary/tags 匹配关键词 → 得到条目 id 与 pdf_url
3) 只对命中条目读正文：
   curl https://sjck.kust.work/api/entry/<id>.md        # Markdown
   curl https://sjck.kust.work/api/entry/<id>/fulltext  # 论文纯文本
```

## 3. 实测记录（2026-10-14 公网 https）

全通路体检（服务由计划任务以用户身份守护，开机自启）：

| 端点 | 结果 |
|---|---|
| `/health` | 200 |
| `/`、`/llms.txt` | 200，text/markdown，6201 B |
| `/llms-full.txt` | 200，11149 B |
| `/topics.md` | 200，8121 B |
| `/corpus.md` | 200，885176 B，0.13 s |
| `/corpus.json` | 200，5.76 MB，0.24 s |
| `/corpus.json.gz` | 200，1.47 MB，0.45 s |
| `/openapi.json` | 200，22985 B（28 个 path） |
| `/api/entry/paper-6ab352e2265f.md` | 200，2447 B |
| `/api/search?q=矽卡岩&format=md` | 200，1221 B |
| `/api/search?q=矽卡岩&format=full` | 200，4786 B |
| `/api/pdf/status` | 200 |

**PDF 下载三方校验**：从 `https://sjck.kust.work/api/entry/paper-6ab352e2265f/pdf`
下载 26.94 MB（13 s），`%PDF-` 头正常，MD5 与 NAS 副本、本地原件**三方一致**
（`4d29c053c0a39c7bee032f055f4652c0`）。

**模拟 Agent 离线流程**：下载 `corpus.json.gz` → 本地匹配"绿帘石"命中 66 条 →
取首条 `kp-04d9ee51a8aa-1` 的 `.md`，正文完整带标签与分级。**仅 1 次 API 请求。**

## 4. 代码落点

| 文件 | 作用 |
|---|---|
| `server/md_api.py` | 新增：Markdown 渲染 + 全量导出（corpus_rows / corpus_json / corpus_json_gz_bytes / corpus_markdown / topics_markdown / hits_markdown / entry_markdown） |
| `server/app.py` | `/api/search`、`/api/entries` 支持 `format=md`；`/api/entry/<id>.md`；`/topics.md`、`/corpus.md`、`/corpus.json`、`/corpus.json.gz`；`/api/entry/<id>` 增加 `source_tier` 字段 |
| `server/agent_docs.py` | 手册新增「不想调 API？三条离线通路」「7.5 四条离线通路」与 Markdown 示例 |
| `docs/openapi.yaml` | 新增 5 个 path，`format` 枚举补 `md`，共 28 个 path |

## 5. 已知边界

- `corpus.json?body=1` 会带正文，体积可达数十 MB，仅限内网或确有必要时使用。
- 离线索引为实时生成，非缓存；条目更新后重新下载即可，无需关心版本号。
- `/api/tags` 返回 483 KB（标签全表），Agent 若只找标准词，优先用 `/topics.md` 的高频标签段。

---
*本文档由 AI 生成，实测数据采集于 2026-10-14 17:5x（UTC+8）。*
