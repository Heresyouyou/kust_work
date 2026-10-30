# -*- coding: utf-8 -*-
"""GeoKB HTTP 服务（AI 纯 API 版）。

- 端口：5556
- 定位：面向公网 Agent/AI 的只读 API，无需访问凭据。
- 防护：仅保留按 IP 的滑动窗口限流，拦截简单高频 DDoS/恶意请求。
- 无 HTML 展示页，避免对 AI 造成干扰；机器自描述通过 /openapi.json 与 /manifest。
"""
import json
import os
import re
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone

import yaml
from flask import Flask, jsonify, redirect, request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SERVER)
import ingest
import retriever
import agent_docs
import pdf_service
import md_api
import translate_service
# /journal 子页面（2026-10-17）：与 open.kust.work/journal 一比一复刻。
# 共用 PostNews 侧的取数模块（journal_feed）与模板（journal_page.html），
# 端点差异由 journal_page.CFG_GEOKB 声明 —— 所以"同步更新"是结构保证。
import journal_view

app = Flask(__name__, template_folder=os.path.join(SERVER, "templates"),
            static_folder=os.path.join(SERVER, "static"),
            static_url_path="/static")
pdf_service.register(app)
translate_service.register(app)
journal_view.register(app)

_MODEL = None

# ---------------------------------------------------------------------------
# 简单限流：按 IP 60 秒滑动窗口，120 次/分钟
# ---------------------------------------------------------------------------
RATE_WINDOW = 60
RATE_LIMIT = 120
_request_log = defaultdict(lambda: deque())


def _client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _rate_limit_check():
    ip = _client_ip()
    now = datetime.now(timezone.utc).timestamp()
    log = _request_log[ip]
    while log and now - log[0] > RATE_WINDOW:
        log.popleft()
    if len(log) >= RATE_LIMIT:
        return True
    log.append(now)
    return False


@app.before_request
def before_request():
    if _rate_limit_check():
        return jsonify({"ok": False, "error": "rate_limited",
                        "message": "请求过于频繁，请稍后再试"}), 429


@app.after_request
def after_request(resp):
    # 允许公网 Agent 跨域访问；5555 等本地 Origin 也放行
    origin = request.headers.get("Origin", "*")
    resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Max-Age"] = "86400"
    return resp


# ---------------------------------------------------------------------------
# AI 友好兼容层：接住"猜路径"的 Agent
#   背景：外部 AI 常凭经验猜 /api/v1/...、/api/docs、/swagger.json，旧版一律
#   裸 404，Agent 容易误判"站点不可用/被墙"。这里统一做兼容跳转；未知路径
#   返回带指引的 JSON。本服务全程无需鉴权——若外部看到 403，那是调用方
#   自己的出口网关（本服务 5556 上不存在任何 403 路由）。
# ---------------------------------------------------------------------------
def _redir(target):
    """308 永久重定向并保留查询串（308 保留请求方法）。"""
    if request.query_string:
        target += "?" + request.query_string.decode("utf-8", "ignore")
    return redirect(target, code=308)


@app.get("/api/v1/<path:rest>")
@app.get("/v1/<path:rest>")
def _alias_v1(rest):
    """/api/v1/* 或 /v1/* → /api/*（本服务没有版本前缀）。"""
    return _redir("/api/" + rest)


@app.get("/api/v1")
@app.get("/v1")
def _alias_v1_root():
    return _redir("/api")


_DOC_ALIASES = ("/api/docs", "/api-docs", "/docs", "/documentation",
                "/swagger.json", "/openapi", "/api/spec", "/api/openapi.json",
                "/.well-known/openapi.json")


def _make_doc_alias(target):
    def _h():
        return _redir(target)
    return _h


for _p in _DOC_ALIASES:
    app.add_url_rule(_p, "doc_alias_" + _p.strip("/").replace("/", "_").replace(".", "_"),
                     _make_doc_alias("/openapi.json"), methods=["GET"])


@app.get("/search")
def _alias_search():
    """/search → /api/search（保留查询串）。"""
    return _redir("/api/search")


@app.get("/api")
def api_index():
    """/api 端点索引：给走到门口的 Agent 一张地图（顺带纠正 /api 是路径不是端点）。"""
    return ok({
        "hint": "这是 API 根索引，不是端点。检索用 /api/search，取全文用 /api/entry/<id>。",
        "guide": agent_docs.ORIGIN + "/",
        "one_line_prompt": agent_docs.ORIGIN + "/prompt.md",
        "prompt_template": agent_docs.ORIGIN + "/prompt-full.md",
        "openapi": "/openapi.json",
        "offline_index": ["/corpus.md", "/corpus.json.gz", "/topics.md"],
        "auth": "无需任何鉴权（无 API Key / Token / Cookie / IP 白名单）",
        "endpoints": {
            "search": "/api/search?q=<关键词>&format=full",
            "entry": "/api/entry/<id>",
            "entry_md": "/api/entry/<id>.md",
            "fulltext": "/api/entry/<id>/fulltext",
            "pdf_meta": "/api/entry/<id>/pdf/meta",
            "pdf_download": "/api/entry/<id>/pdf",
            "pdf_preview": "/api/entry/<id>/pdf/preview",
            "modules": "/api/modules",
            "tags": "/api/tags",
            "entries": "/api/entries",
            "crawl_progress": "/api/crawl/progress",
            "inbox_todo": "/api/inbox/todo",
            "health": "/health",
        },
    })


@app.errorhandler(404)
def not_found(e):
    """未知路径 → 带指引的 JSON，避免 Agent 误判"站点不存在/被墙"。"""
    path = request.path
    if path.startswith(("/api/v1/", "/v1/")):
        hint = "本服务没有 v1 版本前缀，正确路径是 /api/...（支持自动跳转，请跟进 308 重定向）"
    elif "openapi" in path or "swagger" in path or path.endswith("/docs"):
        hint = "OpenAPI 规范在 /openapi.json（也可看 /api 端点索引）"
    else:
        hint = ("本服务为纯 API：检索用 /api/search，取全文用 /api/entry/<id>，"
                "下载 PDF 用 /api/entry/<id>/pdf。")
    return jsonify({
        "ok": False,
        "error": "not_found",
        "message": "路径不存在: %s" % path,
        "hint": hint,
        "guide": agent_docs.ORIGIN + "/",
        "openapi": "/openapi.json",
        "auth": "无需鉴权",
    }), 404


# ---------------------------------------------------------------------------
# 通用辅助
# ---------------------------------------------------------------------------
def get_model():
    global _MODEL
    if _MODEL is None:
        from fastembed import TextEmbedding
        _MODEL = TextEmbedding("BAAI/bge-small-zh-v1.5", cache_dir=os.path.join(BASE, "models"))
    return _MODEL


def ok(data):
    return jsonify({"ok": True, "data": data})


def err(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


# scripts/crawl_status.py 用文件路径加载，**不往 sys.path 里插 scripts** ——
# 那里有个 ingest.py 会遮蔽 server/ingest.py（同名不同物），插进去整站检索会崩。
_CRAWL = {"mod": None}


def _crawl():
    if _CRAWL["mod"] is None:
        import importlib.util
        path = os.path.join(BASE, "scripts", "crawl_status.py")
        spec = importlib.util.spec_from_file_location("geokb_crawl_status", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _CRAWL["mod"] = mod
    return _CRAWL["mod"]


# ---------------------------------------------------------------------------
# 译文检测标注：给带 PDF 的条目（papers 及有 sources.path 的 kpoints）
# 附加 translation 字段 —— 全部检测在后端完成，前端拿到即可渲染按钮：
#   has_mono / has_dual：NAS 同目录已有 .zh.mono.pdf / .zh.dual.pdf
#   job：进行中/失败任务（有任务状态即视为不可点击；失败带 error 原因）
# 无 PDF 的条目不加字段（前端不渲染翻译按钮）。
# ---------------------------------------------------------------------------
def _annotate_translation(hits):
    """给检索结果 hits 批量标注 translation（原地修改）。"""
    if not hits:
        return hits
    for h in hits:
        if not isinstance(h, dict):
            continue
        rel = translate_service.rel_of_hit(h)
        if not rel:
            continue
        try:
            info = translate_service.translation_info(rel, is_rel=True)
        except Exception:
            continue
        if info:
            h["translation"] = {
                "has_mono": info.get("has_mono", False),
                "has_dual": info.get("has_dual", False),
                "job": info.get("job"),
            }
    return hits


def _as_list(value):
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(value).strip()]


def _param(name, default=None, source=None):
    """从 GET args 或 POST JSON body 取值。"""
    src = source if source is not None else (request.get_json(silent=True) or {})
    if request.method == "GET":
        src = request.args
    if name in src:
        return src.get(name, default)
    return default


def _bool_param(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "yes", "on")


def _first_param(names, default=None, src=None):
    """按优先级取第一个非空参数——兼容 AI 的常见命名习惯
    （很多 Agent 会把查询词叫作 query / keyword / search 而不是 q）。"""
    for n in names:
        v = _param(n, None, src)
        if v is not None and str(v).strip() != "":
            return v
    return default


def _search_kwargs():
    """统一提取 /api/search 与 /api/entries 的检索参数。"""
    src = request.get_json(silent=True) or {} if request.method == "POST" else None
    q = str(_first_param(("q", "query", "keyword", "keywords", "search", "kw"),
                         "", src)).strip()
    modules = _as_list(_first_param(("modules", "module"), None, src))
    tags = _as_list(_first_param(("tags", "tag"), None, src))
    exclude_modules = _as_list(_param("exclude_modules", None, src))
    exclude_tags = _as_list(_param("exclude_tags", None, src))
    confidence_min = _param("confidence_min", None, src)
    status = _as_list(_param("status", None, src))
    date_from = _param("date_from", None, src)
    date_to = _param("date_to", None, src)
    sort = _param("sort", "relevance", src)
    order = _param("order", "desc", src)
    limit = int(_param("limit", 0, src) or 0)
    offset = int(_param("offset", 0, src) or 0)
    min_score = _param("min_score", None, src)
    score_ratio = float(_param("score_ratio", 0.55, src))
    global_cap = int(_param("global_cap", 50, src))
    min_results = int(_param("min_results", 3, src))
    token_budget = _param("token_budget", None, src)
    fields = _as_list(_param("fields", None, src))
    fmt = _param("format", "summary", src)
    expand = _bool_param(_param("expand", True, src), True)
    fuzzy = _bool_param(_param("fuzzy", True, src), True)

    return {
        "q": q,
        "modules": modules,
        "tags": tags,
        "exclude_modules": exclude_modules,
        "exclude_tags": exclude_tags,
        "confidence_min": confidence_min,
        "status": status,
        "date_from": date_from,
        "date_to": date_to,
        "sort": sort,
        "order": order,
        "limit": limit,
        "offset": offset,
        "min_score": float(min_score) if min_score is not None else None,
        "score_ratio": score_ratio,
        "global_cap": global_cap,
        "min_results": min_results,
        "token_budget": int(token_budget) if token_budget else None,
        "fields": fields,
        "fmt": fmt,
        "expand": expand,
        "fuzzy": fuzzy,
    }


# ---------------------------------------------------------------------------
# 自描述 / 元数据
# ---------------------------------------------------------------------------
@app.get("/")
def root():
    # 根目录直接返回 Markdown 用法手册（不做内容协商、不跳转）。
    # 目的：Agent 访问 https://sjck.kust.work 就能读到"怎么用"，
    # 避免只看 JSON 路由表而漏掉 format=full 这类关键参数。
    return _md_response(agent_docs.render_llms_txt())


@app.get("/index.json")
def root_json():
    """根目录的机器可读版本（JSON 路由表，供程序使用）。"""
    return ok({
        "service": "GeoKB AI API",
        "version": "2.0.0",
        "description": "面向公网 Agent 的地矿学科知识库只读 API，无需访问凭据。",
        "how_to_use_markdown": "/llms.txt",
        "links": {
            "llms_txt": "/llms.txt",
            "one_line_prompt": "/prompt.md",
            "prompt_template": "/prompt-full.md",
            "llms_full": "/llms-full.txt",
            "health": "/health",
            "manifest": "/manifest",
            "openapi": "/openapi.json",
            "search": "/api/search",
            "entries": "/api/entries",
            "entry_by_id": "/api/entry/{id}",
            "modules": "/api/modules",
            "module": "/api/module/{name}",
            "tags": "/api/tags",
            "facets": "/api/facets",
            "synonyms": "/api/synonyms/{term}",
            "expand": "/api/expand",
            "graph": "/api/graph/{id}",
        },
        "tips": [
            "要拿正文：/api/search?q=<关键词>&format=full",
            "或直接按 id 取全文：/api/entry/<id>",
            "默认 format=summary 只返回 id/title/summary，不含正文",
        ],
        "rate_limit": f"{RATE_LIMIT} requests / {RATE_WINDOW} seconds per IP",
    })


def _md_response(text):
    from flask import Response
    return Response(text, mimetype="text/markdown")


@app.get("/llms.txt")
@app.get("/.well-known/llms.txt")
def llms_txt():
    """精简版 Agent 用法手册（llms.txt 约定）。"""
    return _md_response(agent_docs.render_llms_txt())


@app.get("/llms-full.txt")
def llms_full():
    """完整版 Agent 使用手册：示例、参数全表、模块/标签、分级与排错。"""
    return _md_response(agent_docs.render_llms_full())


@app.get("/prompt.md")
@app.get("/prompt.txt")
def prompt_md():
    """用户端只需发的一行开头（完整规则在根目录 `/`，由 AI 自己读）。"""
    return _md_response(agent_docs.render_prompt_md())


@app.get("/prompt-full.md")
@app.get("/prompt-full.txt")
def prompt_full_md():
    """详版提示词：对方 AI 不能访问网页时，整段粘贴作系统提示。"""
    return _md_response(agent_docs.render_prompt_full_md())


# ---------------------------------------------------------------------------
# AI 友好通路：Markdown 输出 + 静态全量导出（无需调用 API 也能离线检索）
# ---------------------------------------------------------------------------
@app.get("/topics.md")
def topics_md():
    """主题导航：模块/标签 + 检索 URL 模板 + 三条离线通路。"""
    return _md_response(md_api.topics_markdown())


@app.get("/corpus.md")
def corpus_md():
    """全量条目索引（Markdown，可整页下载后离线文本检索）。"""
    module = request.args.get("module")
    return _md_response(md_api.corpus_markdown(module=module))


@app.get("/corpus.json")
def corpus_json():
    """全量条目结构化导出（含 pdf_url），供 Agent 下载后本地检索。"""
    module = request.args.get("module")
    body = request.args.get("body") in ("1", "true", "yes")
    if body:
        rows = md_api.corpus_rows(module=module, with_body=True)
        payload = {"ok": True, "data": {
            "generated_at": md_api._now(), "total": len(rows),
            "module_filter": module, "with_body": True, "entries": rows}}
        return app.response_class(
            json.dumps(payload, ensure_ascii=False), mimetype="application/json")
    return app.response_class(
        json.dumps(md_api.corpus_json(module), ensure_ascii=False),
        mimetype="application/json")


@app.get("/corpus.json.gz")
def corpus_json_gz():
    """全量导出的 gzip 版（体积约降 85%，弱网友好）。"""
    module = request.args.get("module")
    data = md_api.corpus_json_gz_bytes(module=module)
    resp = app.response_class(data, mimetype="application/gzip")
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Content-Disposition"] = "attachment; filename=geokb-corpus.json.gz"
    return resp


@app.get("/health")
def health():
    try:
        conn = ingest.get_db()
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        # 条数用实时表值：meta 只在全量重建索引时写，增量入库后会滞后，
        # 导致 /health 与 /llms.txt 的数字对不上（第三方 AI 会拿它做连通性自证）。
        live_total = conn.execute(
            "SELECT COUNT(*) FROM entries WHERE status != 'deleted'").fetchone()[0]
        live_active = conn.execute(
            "SELECT COUNT(*) FROM entries WHERE status = 'active'").fetchone()[0]
        conn.close()
        return ok({
            "status": "healthy",
            "entries_total": live_total,
            "entries_active": live_active,
            "embedded": int(meta.get("embedded", 0)),
            "index_built_at": meta.get("built_at"),
            "index_snapshot_entries": int(meta.get("entries_total", 0)),
            "embedding_dim": ingest.DIM,
            "model": "BAAI/bge-small-zh-v1.5",
            "client_ip": _client_ip(),
        })
    except Exception as e:
        return err(f"index unavailable: {e}", 503)


@app.get("/manifest")
def manifest():
    conn = ingest.get_db()
    try:
        mods = conn.execute(
            "SELECT module, COUNT(*) n, MAX(updated) latest FROM entries "
            "WHERE status != 'deleted' GROUP BY module ORDER BY module"
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM entries WHERE status != 'deleted'"
        ).fetchone()[0]
        recent = conn.execute(
            "SELECT id, title, module, updated FROM entries WHERE status != 'deleted' "
            "ORDER BY updated DESC, id LIMIT 10"
        ).fetchall()
        papers = 0
        pj = os.path.join(BASE, "index", "papers_index.jsonl")
        if os.path.exists(pj):
            papers = sum(1 for _ in open(pj, encoding="utf-8"))
        return ok({
            "name": "GeoKB 地矿学科知识库",
            "description": "面向 Agent 的地矿学科检索服务：Markdown 源数据 + FTS5/sqlite-vec 混合检索",
            "modules": [
                {"module": m, "entries": n, "latest_updated": l} for m, n, l in mods
            ],
            "entries_total": total,
            "papers_indexed": papers,
            "entry_schema": {
                "id": "唯一 ID", "title": "标题", "module": "模块 slug", "tags": "标签",
                "summary": "摘要", "confidence": "A/B/C/D",
                "status": "active|deprecated|deleted",
                "sources": "来源列表（name/url/accessed）", "updated": "更新日期",
            },
            "confidence_levels": {"A": "权威机构/经典教材", "B": "同行评议/用户验证",
                                  "C": "可信二手汇编", "D": "未核实草稿"},
            "endpoints": [
                "/", "/health", "/manifest", "/openapi.json",
                "/api/search", "/api/entries", "/api/entry/<id>",
                "/api/entry/<id>/related", "/api/module/<name>", "/api/modules",
                "/api/tags", "/api/facets", "/api/synonyms/<term>", "/api/expand", "/api/graph/<id>",
            ],
            "recent_updates": [dict(zip(("id", "title", "module", "updated"), r)) for r in recent],
        })
    finally:
        conn.close()


@app.get("/openapi.json")
def openapi_spec():
    path = os.path.join(BASE, "docs", "openapi.yaml")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            spec = yaml.safe_load(f)
        # 运行时补丁：移除安全要求以匹配无认证策略
        spec["security"] = []
        for p in spec.get("paths", {}).values():
            for op in p.values():
                if isinstance(op, dict):
                    op["security"] = []
        return jsonify(spec)
    return err("OpenAPI 规范未生成", 404)


# ---------------------------------------------------------------------------
# API 路由
# ---------------------------------------------------------------------------
@app.get("/api/search")
@app.post("/api/search")
def api_search():
    try:
        kwargs = _search_kwargs()
    except (ValueError, TypeError) as e:
        return err(f"参数解析失败: {e}", 400)
    try:
        res = retriever.search(model=get_model(), **kwargs)
        if kwargs.get("fmt") == "md":
            return _md_response(md_api.hits_markdown(res, kwargs.get("q", "")))
        _annotate_translation(res.get("hits"))
        return ok(res)
    except Exception as e:
        return err(f"search failed: {e}", 500)


@app.get("/api/entries")
@app.post("/api/entries")
def api_entries():
    """无查询词时的纯元数据过滤/列表接口。"""
    try:
        kwargs = _search_kwargs()
    except (ValueError, TypeError) as e:
        return err(f"参数解析失败: {e}", 400)
    kwargs["q"] = ""  # 强制空查询
    try:
        res = retriever.search(model=None, **kwargs)
        if kwargs.get("fmt") == "md":
            return _md_response(md_api.hits_markdown(res, ""))
        _annotate_translation(res.get("hits"))
        return ok(res)
    except Exception as e:
        return err(f"entries failed: {e}", 500)


@app.get("/api/module/<name>")
def api_module(name):
    try:
        res = retriever.search(
            q="", modules=[name], sort="id", order="asc",
            limit=0, global_cap=1000, model=None
        )
        _annotate_translation(res.get("hits"))
        return ok({
            "module": name,
            "count": res["hits_count"],
            "entries": res["hits"],
        })
    except Exception as e:
        return err(f"module failed: {e}", 500)


@app.get("/api/entry/<eid>")
def api_entry(eid):
    conn = ingest.get_db()
    try:
        r = conn.execute(
            "SELECT id,title,module,tags,summary,confidence,status,updated,pub_date,sources,path,body,"
            "source_tier,parent,evidence FROM entries WHERE id = ?", (eid,)
        ).fetchone()
        if not r:
            return err(f"条目不存在: {eid}", 404)
        keys = ("id", "title", "module", "tags", "summary", "confidence",
                "status", "updated", "pub_date", "sources", "path", "body_markdown", "source_tier",
                "parent", "evidence")
        d = dict(zip(keys, r))
        for k in ("tags", "sources"):
            try:
                d[k] = json.loads(d[k] or "[]")
            except (json.JSONDecodeError, TypeError):
                d[k] = []
        # 知识点溯源：kp-* 带 parent（来源论文 id）与 evidence（证据行号）。
        # 索引层此前后无这两列 → 恒为 null（2026-10-18 修复）。这里再补父论文
        # 标题与永久链接，AI 拿到一条知识点即可回到原文，不必二次检索。
        if d.get("parent"):
            pr = conn.execute("SELECT title FROM entries WHERE id = ?",
                              (d["parent"],)).fetchone()
            if pr:
                d["parent_title"] = pr[0]
            d["parent_url"] = "%s/api/entry/%s" % (agent_docs.ORIGIN, d["parent"])
            d["parent_pdf_url"] = "%s/api/entry/%s/pdf" % (agent_docs.ORIGIN, d["parent"])
        else:
            d.pop("parent", None)
        if not d.get("evidence"):
            d.pop("evidence", None)
        # 永久下载链接：有 PDF 原文的条目直接给出完整公网 URL，
        # Agent 可原样转述给终端用户，无需自己拼路径。
        if any(isinstance(s, dict) and s.get("path") for s in d["sources"]):
            d["pdf_url"] = "%s/api/entry/%s/pdf" % (agent_docs.ORIGIN, eid)
            d["pdf_preview_url"] = "%s/api/entry/%s/pdf/preview" % (agent_docs.ORIGIN, eid)
        # 一键翻译检测：译文是否存在 / 是否有进行中或失败的任务
        try:
            tinfo = translate_service.translation_info(eid)
        except Exception:
            tinfo = None
        if tinfo:
            d["translation"] = {
                "has_mono": tinfo.get("has_mono", False),
                "has_dual": tinfo.get("has_dual", False),
                "job": tinfo.get("job"),
            }
        # 星标状态
        d["starred"] = eid in retriever.starred_ids()
        # Agent 友好：显式要 .md 或声明 Accept: text/markdown 时直接给 Markdown
        wants_md = (request.path.endswith(".md")
                    or "text/markdown" in (request.headers.get("Accept") or "").lower()
                    or request.args.get("format") == "md")
        if wants_md:
            return _md_response(md_api.entry_markdown(d))
        return ok(d)
    finally:
        conn.close()


@app.get("/api/entry/<eid>.md")
def api_entry_md(eid):
    """单条目 Markdown 版（Agent 友好，无 JSON 嵌套）。"""
    return api_entry(eid)


# ── 星标 ─────────────────────────────────────────────────
# 任何访客均可星标/取消（公网无鉴权，与本服务整体定位一致）。
# 存储：state/starred.json {eid: 时间戳}；检索时星标命中 ×1.5 提权。
STAR_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "state", "starred.json")


def _load_stars():
    try:
        with open(STAR_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_stars(d):
    tmp = STAR_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STAR_FILE)
    # 清检索层缓存，让提权立即生效
    retriever._star_cache["t"] = 0.0


@app.post("/api/entry/<eid>/star")
def star_entry(eid):
    conn = ingest.get_db()
    try:
        exists = conn.execute("SELECT 1 FROM entries WHERE id = ?", (eid,)).fetchone()
    finally:
        conn.close()
    if not exists:
        return err(f"条目不存在: {eid}", 404)
    d = _load_stars()
    d[eid] = datetime.now(timezone.utc).isoformat()
    _save_stars(d)
    return ok({"id": eid, "starred": True, "total_starred": len(d)})


@app.delete("/api/entry/<eid>/star")
def unstar_entry(eid):
    d = _load_stars()
    removed = d.pop(eid, None)
    _save_stars(d)
    return ok({"id": eid, "starred": False, "total_starred": len(d),
               "was_starred": removed is not None})


@app.get("/api/stars")
def list_stars():
    d = _load_stars()
    return ok({"count": len(d), "ids": sorted(d.keys()),
               "starred_at": d})


@app.get("/api/entry/<eid>/related")
def api_entry_related(eid):
    conn = ingest.get_db()
    try:
        r = conn.execute(
            "SELECT title, tags, summary FROM entries WHERE id = ? AND status = 'active'", (eid,)
        ).fetchone()
        if not r:
            return err(f"条目不存在或非 active: {eid}", 404)
        title, tags_j, summary = r
        tags = json.loads(tags_j or "[]")
        q_text = title + " " + (summary or "") + " " + " ".join(map(str, tags))
        limit = int(request.args.get("limit", 10))
        try:
            res = retriever.search(q=q_text, exclude_modules=None, exclude_tags=None,
                                   limit=limit + 5, model=get_model())
            hits = [h for h in res["hits"] if h["id"] != eid][:limit]
            return ok({"entry_id": eid, "query_used": q_text, "hits": hits})
        except Exception as e:
            return err(f"related failed: {e}", 500)
    finally:
        conn.close()


@app.get("/api/synonyms/<term>")
def api_synonyms(term):
    syn = ingest.load_synonyms()
    low = term.lower()
    aliases = syn.get(low, [])
    if not aliases:
        std = [k for k, v in syn.items() if low in [x.lower() for x in v]]
        if std:
            aliases = std
    return ok({"term": term, "aliases": aliases,
               "hint": "/api/search 会自动做同义词扩展，本端点供 Agent 查询前预扩展"})


@app.get("/api/expand")
def api_expand():
    q = (request.args.get("q") or "").strip()
    if not q:
        return err("缺少 q 参数")
    synonyms = ingest.load_synonyms()
    terms, traced = retriever.expand_query(q, synonyms)
    # 基于标签共现推荐相关概念
    conn = ingest.get_db()
    try:
        tag_counts = {}
        for row in conn.execute("SELECT tags FROM entries WHERE status = 'active'").fetchall():
            for t in json.loads(row[0] or "[]"):
                if any(term.lower() in str(t).lower() for term in terms):
                    for tt in json.loads(row[0] or "[]"):
                        tt = str(tt).lower()
                        if tt not in [term.lower() for term in terms]:
                            tag_counts[tt] = tag_counts.get(tt, 0) + 1
        top_tags = sorted(tag_counts.items(), key=lambda x: -x[1])[:10]
        return ok({
            "query": q,
            "expanded_terms": terms,
            "term_expansion": traced,
            "suggested_tags": [{"tag": t, "count": c} for t, c in top_tags],
            "suggested_queries": [f"{q} {t}" for t, _ in top_tags[:5]],
        })
    finally:
        conn.close()


@app.get("/api/graph/<eid>")
def api_graph(eid):
    depth = int(request.args.get("depth", 1))
    depth = max(1, min(depth, 3))
    limit = int(request.args.get("limit", 20))
    conn = ingest.get_db()
    try:
        r = conn.execute(
            "SELECT id, title, module, tags FROM entries WHERE id = ? AND status = 'active'", (eid,)
        ).fetchone()
        if not r:
            return err(f"条目不存在: {eid}", 404)
        nodes = {eid: {"id": eid, "title": r[1], "module": r[2], "depth": 0}}
        edges = []
        current_level = {eid: json.loads(r[3] or "[]")}
        for d in range(1, depth + 1):
            next_level = {}
            if len(nodes) >= limit:
                break
            for parent_id, ptags in current_level.items():
                ptags_low = [str(t).lower() for t in ptags]
                for row in conn.execute(
                    "SELECT id, title, module, tags FROM entries "
                    "WHERE status = 'active' AND id != ?", (parent_id,)
                ).fetchall():
                    cid, ctitle, cmod, ctags_j = row
                    if cid in nodes:
                        continue
                    ctags = json.loads(ctags_j or "[]")
                    overlap = [str(t).lower() for t in ctags if str(t).lower() in ptags_low]
                    if overlap:
                        nodes[cid] = {"id": cid, "title": ctitle, "module": cmod, "depth": d}
                        edges.append({"source": parent_id, "target": cid,
                                      "relation": "shared_tag", "tags": overlap})
                        next_level[cid] = ctags
                        if len(nodes) >= limit:
                            break
            current_level = next_level
            if not current_level:
                break
        return ok({
            "entry_id": eid,
            "depth": depth,
            "nodes": list(nodes.values()),
            "edges": edges,
        })
    finally:
        conn.close()


@app.get("/api/modules")
def api_modules():
    conn = ingest.get_db()
    try:
        rows = conn.execute(
            "SELECT module, COUNT(*) n, MAX(updated) latest FROM entries "
            "WHERE status != 'deleted' GROUP BY module ORDER BY module"
        ).fetchall()
        return ok({"modules": [
            {"module": m, "entries": n, "latest_updated": l} for m, n, l in rows
        ]})
    finally:
        conn.close()


@app.get("/api/tags")
def api_tags():
    module = request.args.get("module")
    conn = ingest.get_db()
    try:
        sql = "SELECT tags FROM entries WHERE status = 'active'"
        args = []
        if module:
            sql += " AND module = ?"
            args.append(module)
        tag_counts = {}
        for row in conn.execute(sql, args).fetchall():
            for t in json.loads(row[0] or "[]"):
                t = str(t).strip()
                if t:
                    tag_counts[t] = tag_counts.get(t, 0) + 1
        top = sorted(tag_counts.items(), key=lambda x: -x[1])
        return ok({"tags": [{"tag": t, "count": c} for t, c in top]})
    finally:
        conn.close()


@app.get("/api/facets")
def api_facets():
    """五分面受控索引（deposit_type/process/method/commodity/region → 规范词计数）。"""
    fp = os.path.join(BASE, "index", "facets.json")
    if not os.path.exists(fp):
        return err("facets.json 未生成，请先运行 scripts/build_facet_index.py", 503)
    try:
        data = json.load(open(fp, encoding="utf-8"))
    except Exception as e:
        return err(f"facets.json 读取失败: {e}", 503)
    facet = request.args.get("facet")
    include_ids = request.args.get("include_ids", "0") == "1"
    out = data["facets"]
    if facet:
        out = {facet: out.get(facet, [])}
    if not include_ids:
        out = {k: [{"term": x["term"], "count": x["count"]} for x in v]
               for k, v in out.items()}
    return ok({"total_entries": data["total_entries"], "facets": out})


@app.get("/api/crawl/progress")
def api_crawl_progress():
    """历史清单爬取进度（只读快照）。

    数据源：state/history_manifest.jsonl（待爬）+ history_done.jsonl（已完成）
    + pending_papers_ingest.jsonl（待入库）+ 调度器日志尾部（此刻在干什么）。
    拼装逻辑在 scripts/crawl_status.py —— 5555 的 /geokb/progress 页面吃同一份，
    避免页面上看到的数和 API 返回的数对不上。
    """
    try:
        return ok(_crawl().snapshot())
    except Exception as e:  # noqa: BLE001
        return err("进度快照失败: %s: %s" % (type(e).__name__, str(e)[:120]), 503)


# ---------------------------------------------------------------------------
# 批量入库待补清单（自动下载失败 → 人工补投）
#   拼装逻辑在 scripts/inbox_todo.py，5555 的 /geokb/inbox 页面吃同一份。
#   同样用文件路径加载、不插 sys.path —— 理由同 crawl_status。
# ---------------------------------------------------------------------------
_INBOX = {"mod": None, "ts": 0.0, "sig": "", "data": None}
INBOX_TTL = 45          # 秒；同一批请求别反复读 3 个 jsonl + 探测投放箱


def _inbox():
    if _INBOX["mod"] is None:
        import importlib.util
        path = os.path.join(BASE, "scripts", "inbox_todo.py")
        spec = importlib.util.spec_from_file_location("geokb_inbox_todo", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _INBOX["mod"] = mod
    return _INBOX["mod"]


def _inbox_sig(mod):
    """数据指纹：3 个 jsonl + 标记文件 + 投放箱 PDF 的 mtime。"""
    parts = []
    for p in (mod.LEDGER, mod.MANIFEST, mod.NO_PDF, mod.MARKS):
        try:
            parts.append(str(os.path.getmtime(str(p))))
        except OSError:
            parts.append("-")
    try:
        parts.append(",".join(sorted(
            "%s:%d" % (p.name, os.path.getmtime(str(p)))
            for p in mod.INBOX.glob("*.pdf"))))
    except OSError:
        parts.append("-")
    return "|".join(parts)


@app.get("/api/inbox/todo")
def api_inbox_todo():
    """待补清单快照：pending（待下载）/ downloaded（已下载待入库）/ history（已入库）。"""
    try:
        mod = _inbox()
        sig = _inbox_sig(mod)
        now = datetime.now().timestamp()
        if (_INBOX["data"] is None or sig != _INBOX["sig"]
                or now - _INBOX["ts"] > INBOX_TTL):
            _INBOX["data"] = mod.snapshot()
            _INBOX["sig"] = sig
            _INBOX["ts"] = now
        return ok(_INBOX["data"])
    except Exception as e:  # noqa: BLE001
        return err("待补清单失败: %s: %s" % (type(e).__name__, str(e)[:120]), 503)


@app.post("/api/inbox/mark")
def api_inbox_mark():
    """人工勾选「已下载」/ 撤销。

    只允许本机调用（门户 5555 转发过来）—— 这个服务公网可达，
    若放开会让任何人都能改「已下载」状态。状态本身无敏感信息，但没必要。
    """
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return err("local_only", 403)
    body = request.get_json(silent=True) or {}
    try:
        res = _inbox().mark(body.get("key", ""), body.get("state", "downloaded"))
    except ValueError as e:
        return err(str(e), 400)
    except OSError as e:
        return err("写入标记失败: %s" % e, 500)
    _INBOX["data"] = None        # 标记变了，下次请求重算
    return ok(res)


# ---------------------------------------------------------------------------
# 人工补缺工作台（被拦 / 被冻 / 被政策停抓 → 人去官网下）
#   拼装逻辑在 scripts/manual_todo.py，5555 的 /geokb/manual 页面吃同一份。
#   列表**分页下发**：3885 条全量 2.19MB，两端都没 gzip，公网/手机会卡；
#   首屏只给 summary（5.6KB），切组再按 80 条一页拉。
# ---------------------------------------------------------------------------
_MANUAL = {"mod": None, "ts": 0.0, "sig": "", "data": None}
MANUAL_TTL = 60          # 秒；同一批请求别反复读 4 个 jsonl + 探测投放箱


def _manual():
    if _MANUAL["mod"] is None:
        import importlib.util
        path = os.path.join(BASE, "scripts", "manual_todo.py")
        spec = importlib.util.spec_from_file_location("geokb_manual_todo", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MANUAL["mod"] = mod
    return _MANUAL["mod"]


def _manual_sig(mod):
    """数据指纹：4 个池子 + 冻结表 + 人工标记 + 投放箱 PDF 的 mtime。"""
    parts = []
    for p in (mod.LEDGER, mod.MANIFEST, mod.FROZEN_MANIFEST, mod.NO_PDF,
              mod.MARKS, mod.FREEZE_TABLE):
        try:
            parts.append(str(os.path.getmtime(str(p))))
        except OSError:
            parts.append("-")
    try:
        parts.append(",".join(sorted(
            "%s:%d" % (p.name, os.path.getmtime(str(p)))
            for p in mod.INBOX.glob("*.pdf"))))
    except OSError:
        parts.append("-")
    return "|".join(parts)


def _manual_snap():
    mod = _manual()
    sig = _manual_sig(mod)
    now = datetime.now().timestamp()
    if (_MANUAL["data"] is None or sig != _MANUAL["sig"]
            or now - _MANUAL["ts"] > MANUAL_TTL):
        _MANUAL["data"] = mod.snapshot()
        _MANUAL["sig"] = sig
        _MANUAL["ts"] = now
    return mod, _MANUAL["data"]


@app.get("/api/manual/todo")
def api_manual_todo():
    """摘要：counts / buckets / groups。不含条目明细，首屏用。"""
    try:
        mod, snap = _manual_snap()
        return ok(mod.summary(snap))
    except Exception as e:  # noqa: BLE001
        return err("人工补缺摘要失败: %s: %s" % (type(e).__name__, str(e)[:120]), 503)


@app.get("/api/manual/items")
def api_manual_items():
    """条目列表（分页）。group=host|bucket，q=标题/期刊/DOI 子串。"""
    try:
        mod, snap = _manual_snap()
        return ok(mod.query(
            snap,
            group=request.args.get("group", ""),
            bucket=request.args.get("bucket", ""),
            host=request.args.get("host", ""),
            q=request.args.get("q", ""),
            year=request.args.get("year", ""),
            journal=request.args.get("journal", ""),
            offset=request.args.get("offset", 0),
            limit=request.args.get("limit", 80),
            state=request.args.get("state", "pending"),
        ))
    except ValueError as e:
        return err(str(e), 400)
    except Exception as e:  # noqa: BLE001
        return err("人工补缺列表失败: %s: %s" % (type(e).__name__, str(e)[:120]), 503)


@app.post("/api/manual/mark")
def api_manual_mark():
    """改状态：downloaded（已下载待入库）/ skipped（无权访问）/ pending（撤销）。

    支持单条（key）和批量（keys[]）。只允许本机调用，同 /api/inbox/mark：
    这个服务公网可达，放开等于任何人都能改状态。
    """
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return err("local_only", 403)
    body = request.get_json(silent=True) or {}
    state = (body.get("state") or "downloaded").strip()
    try:
        mod = _manual()
        if body.get("keys"):
            res = mod.mark_many(body["keys"], state)
        else:
            res = mod.mark(body.get("key", ""), state)
    except ValueError as e:
        return err(str(e), 400)
    except OSError as e:
        return err("写入标记失败: %s" % e, 500)
    _MANUAL["data"] = None       # 标记变了，下次请求重算
    return ok(res)


if __name__ == "__main__":
    port = int(os.environ.get("GEOKB_PORT", "5556"))
    host = os.environ.get("GEOKB_HOST", "0.0.0.0")
    translate_service.start_background()
    print(f"GeoKB AI API serving on {host}:{port}")
    print(f"  OpenAPI: http://127.0.0.1:{port}/openapi.json")
    print(f"  Health:  http://127.0.0.1:{port}/health")
    app.run(host=host, port=port, threaded=True)
