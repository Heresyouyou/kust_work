# GeoKB 外部 AI 访问诊断与兼容层优化（2026-10-14）

> **域名切换说明（2026-10-15）**：本文记录的是 lib.kust.work 时期的诊断过程，
> 属历史存档。自 2026-10-15 起，公网域名已切换为 **sjck.kust.work**
> （lib 易被误判为"昆明理工大学图书馆"域名），文中 URL 按当时实况保留，
> 实际访问请使用 sjck.kust.work。

## 一、问题现象

多个外部 AI 在提示词为"通过 lib.kust.work 知识库获取信息"时无法使用知识库。
某模型反馈：DNS/HTTPS 正常，但 `/`、`/api`、`/api/v1`、`/api/docs`、
`/openapi.json` 全部返回 `403 No policy rule matched`，据此判断"站点前有
网关/反向代理，默认拒绝未匹配规则的请求"，并索要 API Key / Token / Cookie。

## 二、诊断结论：403 来自该模型自己的出口网关，不是本服务

证据链（五条，全部可复现）：

| # | 证据 | 说明 |
|---|---|---|
| 1 | 本服务返回码实测 | `/` → 200、`/openapi.json` → 200、`/api`、`/api/v1`、`/api/docs` → 404。**任何路径都不返回 403** |
| 2 | 代码级排除 | 全库检索无 "policy" 字样；5556 端口上不存在任何 403 路由（唯一的 403 在 5554 本地管理端） |
| 3 | 服务日志零痕迹 | 该模型声称的 403 探测请求在 `logs/geokb-api.err.log` 中**没有任何记录**（日志中仅有的 /api/v1 请求是本人 21:14 的实测，返回 404） |
| 4 | 双外部视角验证 | 本机经隧道 + WebFetch（外部网络）两个独立视角访问 `/`、`/openapi.json` 均 200，OpenAPI 3.0.3 / 32 路径完整返回 |
| 5 | 协议指纹矛盾 | 该模型报告"HTTP/2 连接正常"，而本服务由 Python Werkzeug 直接响应 **HTTP/1.1**（响应头 `Server: Werkzeug/3.1.9`），前面没有任何 CDN/网关——它连通的是它自己的代理 |

**结论**：该 AI 运行环境的网络安全策略（出口白名单）拦截了未授权域名，
在请求到达本服务之前就返回了 403。此类限制只能通过更换该模型的网络环境 /
联系其提供商放行域名解决，服务端无解，也**不应该**为此引入鉴权——
鉴权会挡住所有"裸请求"的正常 AI，与"面向公网 Agent 免凭据"的架构决策冲突。

## 三、已实施的优化（针对"能访问但找不到/不会用"的真实痛点）

### 1. AI 友好兼容层（`server/app.py`）

| 优化 | 行为 |
|---|---|
| `/api/v1/<rest>`、`/v1/<rest>` | 308 跳转到 `/api/<rest>`（保留查询串与方法） |
| `/api/v1`、`/v1` | 308 → `/api` |
| `/search` | 308 → `/api/search` |
| `/api/docs`、`/docs`、`/api-docs`、`/documentation`、`/swagger.json`、`/openapi`、`/api/spec`、`/api/openapi.json`、`/.well-known/openapi.json` | 308 → `/openapi.json` |
| `/api` | 新增端点索引 JSON（常用端点 + 离线通路 + 鉴权说明） |
| 未知路径 | 友好 404 JSON：hint + guide + openapi + "无需鉴权"，不再裸 404 |

### 2. 查询参数别名（`server/app.py` `_first_param`）

`/api/search` 与 `/api/entries` 的查询词参数除规范的 `q` 外，
兼容 `query` / `keyword` / `keywords` / `search` / `kw`（GET 与 POST JSON 均可）。
背景：多个 AI 习惯用 `query=`，旧版只认 `q`，会导致空查询 → "检索不到内容"。

### 3. 文档同步

- 根路径 Markdown 指南（`/`、`/llms.txt`）：新增"收到 403 先怀疑自己的出口
  网关"避坑条（含 Werkzeug 指纹自证法）、兼容层端点表、参数别名说明、
  `curl -sI /health` 快速验证法。
- `docs/openapi.yaml`：`QueryQ` 参数补别名说明；新增 `/api/v1/{rest}`、
  `/search`、`/api`、`/api/docs` 四个兼容层路径定义（28 → 32 路径，
  线上 `/openapi.json` 实时生效）。

## 四、验证记录（公网 https://lib.kust.work 实测）

- 兼容层：`/api/v1/search?q=` → 308 → 200；`/api/docs` → 308 → openapi；
  `/search?q=` → 200；`/api` → 索引 JSON；未知路径 → 友好 404 JSON。
- 参数别名：`query=` / `keyword=` / POST `{"query":...}` 均正常返回命中。
- 回归：8 个元数据端点全 200；`format=full` 含正文；PDF Range 206；
  `/api/pdf/status` 749 文件 / 15.82GB / reachable。

## 五、给用户的使用建议

1. 让其他 AI 使用时，直接在提示词里给**具体入口**而非只说域名，例如：
   "先读取 https://lib.kust.work/ 的使用说明，再按说明调用
   https://lib.kust.work/api/search?q=关键词&format=full"。
2. 对弱网/无 API 环境的 AI，让它先下载
   `https://lib.kust.work/corpus.json.gz`（1.4MB）离线检索。
3. 遇到坚持"需要鉴权"的模型，把上面第二节的证据链发给它——
   问题在它的网络环境，不在知识库。
