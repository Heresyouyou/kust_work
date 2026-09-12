# kust.work 门户化改造设计方案

> 目标：kust.work 打开是**欢迎主页** → 主页上点「文献翻译」→ 进入 GeoTranslate
> 现状基线：GeoTranslate v4（Flask 5555 + llama-server 8090）
> 版本：v1 / 2026-09-12

---

## 0. 结论先行

| 选择 | 方案 | 改动量 | 风险 |
|------|------|--------|------|
| **立即可用** | **方案 A：单 Flask + 门户层** | web_app.py 约 15 行 + 新增 portal.html | 极低（不动 NAS、不动前端） |
| **长期目标** | 方案 B：Blueprint 模块化单体 | 中等重构，1~2 天 | 低 |
| 备选 | 方案 C：独立门户服务 + NAS 反代分流 | 需改 NAS Apache | 中高（触发 QTS 已知坑） |
| 不推荐 | 方案 D：子域名 translate.kust.work | 依赖校园网 DNS 权限 | 外部阻塞 |

**建议：先上方案 A，把门户用起来；等第 2 个功能模块要挂上去时，再按方案 B 的目录结构平滑演进。**
方案 A 的代码与方案 B 不冲突 —— 今天写的 `portal.html` 就是方案 B 的 `portals/templates/portal.html`。

---

## 1. 现状盘点（实测，非推测）

### 1.1 访问链路

```
浏览器
  │  kust.work  →  DNS A 记录直接指向私网 10.109.0.222（仅校园网内可达）
  ▼
NAS (QNAP) Apache :80
  │  ProxyPass /  →  http://192.168.10.100:5555/
  ▼
Windows Flask (waitress, host=0.0.0.0, port=5555)   ← 就是 GeoTranslate 本体
  │  GET /  →  render_template_string(index.html)
  ▼
llama-server :8090（本地推理）
```

### 1.2 决定方案边界的 5 个硬事实

| # | 事实（代码位置） | 对设计的影响 |
|---|---|---|
| 1 | NAS 反代是**单条全路径规则** `ProxyPass / → 5555` | 门户要么"住进"这个后端，要么必须改 NAS 配置 |
| 2 | `web_app.py` L164-166：`GET /` 直接吐 `index.html` | 门户要占 `/`，翻译页必须换路径 |
| 3 | 前端 **4 处根绝对路径请求**：`/api/check`(L798) `/api/upload`(L812) `/api/status/`(L848) `/api/cancel/`(L945) | 翻译页挪到 `/translate/` 后，**这些请求仍会发往根路径** |
| 4 | 后端产出**根绝对 URL**：`/uploads/{name}`(L246)、`/translated/{name}`(L183/194/504) | PDF 预览的 URL 同样不能带子路径前缀 |
| 5 | Flask 有**单实例 PID 锁**（`.flask_5555.pid`）+ waitress threads=8 | 改完必须重启；不能再起第二个实例抢 5555 |

> **第 3、4 条是本次设计真正的"钉子"。**
> GeoTranslate 的前端和后端**全部使用根绝对路径**，这意味着它**不能被简单地挂载到 `/translate/` 子路径下**——
> 即使把 HTML 从 `/translate/` 返回，其中的 `/api/upload` 依然会打到根，需要额外代理规则兜住。
>
> 反过来说：**只要 API 与静态资源继续留在根路径，翻译页挂在哪个 URL 都无所谓**。
> 这正是方案 A 能做到"前端零改动"的原因。

---

## 2. 方案 A：单 Flask 进程 + 门户层（推荐 · 立即可用）

### 2.1 架构

```
浏览器
  │  kust.work
  ▼
NAS Apache :80     ProxyPass /  →  192.168.10.100:5555     ← 完全不动
  ▼
Windows Flask :5555（单进程，就是现在这一个）
  ├── GET  /                  →  portal.html     ← 新增：欢迎主页
  ├── GET  /translate         →  index.html      ← 原来的翻译 SPA（仅换 URL）
  ├── GET/POST /api/*         →  翻译业务 API     ← 原样不动
  ├── GET  /uploads/*         →  原 PDF          ← 原样不动
  └── GET  /translated/*      →  译文 PDF        ← 原样不动
```

**为什么前端 4 处 `fetch('/api/...')` 不需要改？**
因为 `/translate` 是页面 URL，而 `/api/*` 留在根路径。浏览器从 `/translate` 页面发起的 `/api/check` 会解析为 `https://kust.work/api/check` → Apache `ProxyPass /` 全量转发 → Flask 命中 `@app.route('/api/check')` ✅。
后端产出的 `/translated/xxx.pdf` 同理 ✅。

### 2.2 改动清单（web_app.py 仅 2 处）

**改动 1 — 门户模板加载（跟随现有 L126-131 的写法）**

```python
INDEX_HTML_PATH  = BASE / 'index.html'
PORTAL_HTML_PATH = BASE / 'portal.html'          # 新增

PORTAL_HTML = (PORTAL_HTML_PATH.read_text(encoding='utf-8')
               if PORTAL_HTML_PATH.exists()
               else '<h1>portal.html not found</h1>')
```

**改动 2 — 路由（替换现行 L164-166）**

```python
# 现在
@app.route('/')
def index():
    return render_template_string(INDEX_HTML)

# 改为
@app.route('/')
def portal():
    # 用 Response 而不是 render_template_string：门户页里若出现 {{ }} / {% %} 不会被 Jinja 误解析
    return Response(PORTAL_HTML, mimetype='text/html')

@app.route('/translate')
def index():
    return render_template_string(INDEX_HTML)   # 原逻辑一字不动
```

> ⚠️ 现有 `index()` 用的是 `render_template_string`。如果新写的 `portal.html` 里含 `{{`、`{%`（比如 CSS/JS 模板字符串），Jinja2 会尝试渲染并报错。门户页用 `Response()` 直接吐字符串最稳妥。

**新增文件 — `portal.html`（欢迎主页）**

页面结构建议：

| 区域 | 内容 |
|------|------|
| 顶栏 | 站点名（如 `kust.work`）+ 用户名/日期 |
| 主体 | 功能入口卡片区 |
| 卡片 1 | **文献翻译** → `href="/translate"`（P0，本次实现） |
| 卡片 2+ | 预留「每日新闻」「期刊速递」「文件柜」→ 灰态/`即将上线`（P2） |
| 右上角 | 服务状态灯：调 `GET /api/check` 显示 llama-server / BabelDOC 状态 |

### 2.3 优点 / 缺点

| ✅ 优点 | ⚠️ 缺点 |
|--------|--------|
| 零 NAS 改动 —— 不碰 QTS Apache 那堆已知坑 | 门户与翻译**同进程同端口**，Flask 重启时门户一起断（秒级） |
| 零新增常驻进程，不增加 Windows/NAS 负载 | 一个进程挂了两个一起挂 |
| 前端 index.html **一行不用改** | 改代码需重启 Flask（已有 PID 锁机制，属既定流程） |
| 30 分钟内可上线 | "门户"和"翻译"在代码里仍是同一个文件 |
| 天然是方案 B 的第一步 | |

### 2.4 风险与对策

| 风险 | 对策 |
|------|------|
| `portal.html` 缺失导致 `/` 白屏 | 上表改动 1 已做 fallback（返回提示页而非 500） |
| 老用户书签/习惯直接进翻译页 | `/translate` 直接可访问；门户页在显眼位置放"直达翻译" |
| 与 `<Directory /> Require all denied` 冲突 | 不适用 —— 反代是全路径转发，与静态目录无关 |
| 需要回滚 | 改动集中 2 处，`git checkout web_app.py` 即可回退 |

---

## 3. 方案 B：Blueprint 模块化单体（长期目标架构）

当门户下要挂 **3 个以上模块**时使用。

### 3.1 目录结构

```
Translate/
├── app.py                       # 应用工厂 + 注册蓝图 + PID 锁 + waitress 启动
├── common/
│   └── health.py                # 统一的 /api/check 健康聚合
├── portals/
│   ├── routes.py                # Blueprint('portal')  → '/'      门户主页
│   └── templates/portal.html
└── modules/
    └── geo_translate/
        ├── routes.py            # 页面蓝本  url_prefix='/translate'
        ├── api.py               # API 蓝本   url_prefix='/api'   ← 故意留在根
        ├── engine/              # llama_http_engine / run_babeldoc / babeldoc_translator
        ├── data/geology_terms.json
        └── templates/index.html
```

### 3.2 关键设计：**页面带前缀，API 不带**

```python
app.register_blueprint(portal_bp)                                   # '/'
app.register_blueprint(geo_page_bp,  url_prefix='/translate')       # 页面
app.register_blueprint(geo_api_bp,   url_prefix='/api')             # API 留在根
```

这样：

- URL 语义清晰：人访问 `/translate`，机器调 `/api/upload`
- **现有 index.html 的 4 处 fetch 和 2 处后端 URL 构造全部保持原样**
- 未来加第二个模块时：`/news` + 自己的 API 前缀，与翻译模块零冲突

> 💡 更彻底的版本是把 API 也放到 `/translate/api/*`，但要改前端 4 处 fetch 为相对路径（`api/check`）并注入 `<base href="/translate/">`。收益是 URL 完全自洽，成本是踩一次前端路径的坑。**建议先不做**。

### 3.3 收益时机

| 模块数 | 方案 A 更好 | 方案 B 更好 |
|--------|------------|------------|
| 1 个（现在） | ✅ 收益≈0，成本≈0 | 成本中等，收益≈0 |
| 2 个 | 尚可（web_app.py 会变长到 800+ 行） | 开始体现 |
| 3 个以上 | ❌ 单文件难维护 | ✅ 明确收益 |

---

## 4. 方案 C：独立门户服务 + NAS Apache 反代分流（备选）

### 4.1 架构

```
NAS Apache :80
  ├── /translate, /api/, /uploads/, /translated/  →  192.168.10.100:5555   (GeoTranslate)
  └── /  (兜底)                                    →  192.168.10.100:5560   (门户服务)
```

### 4.2 NAS 侧配置（`/etc/config/apache/extra/apache-reverse-proxy.conf`）

```apache
LoadModule proxy_module        modules/mod_proxy.so
LoadModule proxy_http_module   modules/mod_proxy_http.so
LoadModule proxy_connect_module modules/mod_proxy_connect.so

ProxyPreserveHost On
ProxyRequests Off

<Directory />
    Require all granted
</Directory>

# ① 翻译模块 —— 长的前缀必须写在兜底 / 之前！
ProxyPass        "/translate"   "http://192.168.10.100:5555/"
ProxyPassReverse "/translate"   "http://192.168.10.100:5555/"
ProxyPass        "/api/"        "http://192.168.10.100:5555/api/"
ProxyPassReverse "/api/"        "http://192.168.10.100:5555/api/"
ProxyPass        "/uploads/"    "http://192.168.10.100:5555/uploads/"
ProxyPassReverse "/uploads/"    "http://192.168.10.100:5555/uploads/"
ProxyPass        "/translated/" "http://192.168.10.100:5555/translated/"
ProxyPassReverse "/translated/" "http://192.168.10.100:5555/translated/"

# ② 门户 —— 兜底放最后
ProxyPass        "/"            "http://192.168.10.100:5560/"
ProxyPassReverse "/"            "http://192.168.10.100:5560/"
```

> **为什么 `/api/` `/uploads/` `/translated/` 这三条不能省？**
> 因为 `/translate` 返回的 HTML 里写死的是根绝对路径（事实 #3、#4）。浏览器在 `/translate` 页面上请求 `/api/upload`，如果 Apache 没把 `/api/` 也转发给 5555，就会被兜底规则送到门户服务 → 404。

### 4.3 已知风险（改造前必须逐条确认）

| 风险 | 说明 | 对策 |
|------|------|------|
| **`/webdav/` 会被兜底规则吃掉** | 现状已有 `http://kust.work/webdav/Public/`（PROPFIND 返回 207）。新增 `ProxyPass /` 兜底后，若 `/webdav/` 规则写在它后面就失效 | **改造前先 dump 现有 Apache 配置**，把 `/webdav/` 规则放在最前 |
| ProxyPass 顺序 | `/` 是通配，必须在最后 | 长短前缀顺序：最具体 → 最宽泛 |
| `<Directory /> Require all denied` | 主 `apache.conf` L26 会先 403 拦截 | 反代配置里 `<Directory /> Require all granted` 覆盖 |
| LoadModule 顺序 | `proxy_http.so` 先于 `proxy.so` → `undefined symbol` | proxy → proxy_http → proxy_connect |
| Include 位置 | 必须在 `apache-default-modules.conf` 之后 | 确认 `grep reverse-proxy apache.conf` 有输出 |
| Windows 静态 IP | 反代硬编码 `192.168.10.100`，该地址必须被本机长期占住 | NAS DHCP 池起点已在 `.150`，`.100` 已做 MAC 静态绑定 ✅（已固化） |
| 新增常驻进程 | 门户服务需随开机自启 | 做成计划任务/服务，或干脆门户也用 Flask + waitress |

### 4.4 优点 / 缺点

| ✅ 优点 | ⚠️ 缺点 |
|--------|--------|
| 门户与翻译**故障隔离** | 改 NAS Apache = 引入 6 个已知坑（见上表） |
| 门户可用任意技术栈 | Windows 多一个常驻进程 + 端口 |
| URL 最清晰 | 上线/回滚都要 SSH 上 NAS |
| 未来可扩成 `/news`、`/files` 多服务 | NAS CPU 本就紧张，多一条规则多一层转发 |

---

## 5. 方案 D：子域名 `/` 独立域名（不推荐）

```
translate.kust.work  →  NAS:80  →  192.168.10.100:5555
kust.work            →  NAS:80  →  192.168.10.100:5560
```

- `kust.work` 的公网 A 记录**直接指向私网 IP**（`10.109.0.222`），说明 DNS 记录在校园网侧管理
- 加子域名需要校园网 DNS 管理权限，**不确定性高**
- 还要额外处理 HTTPS 证书
- **除非你确认能自己加 DNS 记录，否则不作为方案**

---

## 6. 方案对比总表

| 维度 | A 单 Flask 门户 | B Blueprint 单体 | C 独立门户 + 反代 | D 子域名 |
|------|----------------|-----------------|------------------|---------|
| 改动量 | 15 行 + 1 文件 | 中等重构 | 新服务 + NAS 配置 | DNS + 证书 |
| 是否改 NAS | ❌ 不用 | ❌ 不用 | ✅ 必须 | ✅ 必须 |
| 新增进程 | 0 | 0 | +1 | +1 |
| 前端改动 | **0 行** | 0 行（推荐版） | 0 行 | 0 行 |
| 故障隔离 | ❌ 同生共死 | ❌ 同生共死 | ✅ 隔离 | ✅ 隔离 |
| 扩展性 | 一般 | ✅ 好 | ✅ 好 | ✅ 好 |
| 上线耗时 | 半小时 | 1~2 天 | 半天 + 排障 | 取决于 DNS |
| 回滚难度 | 极低（git） | 低 | 中（需改回 Apache） | 中 |
| **推荐度** | ⭐⭐⭐⭐⭐ 现在做 | ⭐⭐⭐⭐ 下一步 | ⭐⭐ 备选 | ⭐ 不做 |

---

## 7. 落地路线（建议）

### Phase 1 —— 门户上线（方案 A）
1. 新建 `portal.html`（欢迎主页 + 「文献翻译」入口 + 状态灯）
2. 改 `web_app.py` 2 处（门户路由 + `/translate` 路由）
3. 重启 Flask（`kill $(cat .flask_5555.pid)` → `py -3.12 web_app.py`）
4. 冒烟验证：
   ```bash
   curl --noproxy "*" -s http://127.0.0.1:5555/ | grep -o "文献翻译"     # 门户出来
   curl --noproxy "*" -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5555/translate   # 200
   curl --noproxy "*" -s http://127.0.0.1:5555/api/check                 # 状态正常
   curl --noproxy "*" -s -o /dev/null -w "%{http_code}" http://kust.work/ # 200
   ```
   > ⚠️ 必须加 `--noproxy "*"`：会话 shell 预置了 `http_proxy=127.0.0.1:64781`，不加会把内网请求丢给代理，返回假 502/503 误判服务挂了。
5. 回归：登录 kust.work → 点「文献翻译」→ 上传一个 PDF → 确认能翻、能下载

### Phase 2 —— 模块化（方案 B）
6. 拆 `app.py` + 蓝图，按 §3.1 目录重组
7. 门户卡片支持配置化（读一个 `modules.json`，自动渲染入口卡片）

### Phase 3 —— 扩展（可选）
8. 挂第二个模块（新闻 / 期刊），验证架构
9. 若届时确实需要故障隔离，再迁到方案 C

---

## 8. 待确认事项

| # | 问题 | 影响 |
|---|------|------|
| 1 | 门户主页除了「文献翻译」，还想放什么入口？（新闻 / 期刊 / 文件柜 / 其他） | 决定门户 UI 与 Phase 2 的范围 |
| 2 | 需要几个门户入口 → 决定选 A 还是直接上 B | 架构走向 |
| 3 | 门户是否需要登录/鉴权？（现在是裸奔，校园网内谁都能访问） | 是否要加会话层 |
| 4 | 是否接受为了门户去动 NAS Apache 配置？ | A/B 与 C/D 的分水岭 |

---

*本方案基于 `D:\PostNews\Translate` 实盘代码核对（路由 L164-166 / 前端 fetch L798-945 / URL 构造 L183-504），以及 NAS 反代现状。*
