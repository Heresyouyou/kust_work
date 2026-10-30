# -*- coding: utf-8 -*-
"""AI 友好的 Markdown 输出与静态全量索引导出。

背景：公网 Agent 访问知识库时，纯 JSON 有三大摩擦——
  1) 嵌套结构解析成本高，容易漏字段；
  2) 必须先猜参数（format=full）才能拿到正文；
  3) 每次检索都要发一次请求，弱网/限流下容易断。

本模块提供三条"低摩擦通路"：
  A. `format=md`：/api/search、/api/entries、/api/entry/<id> 直接吐 Markdown；
  B. `/corpus.json(.gz)`：全量条目的静态导出，Agent 下载一次即可在本地
     用 grep/字符串匹配离线检索，完全不需要调用 API；
  C. `/corpus.md`、`/topics.md`：人类与 Agent 都能读懂的主题导航，
     自带检索 URL 模板，照着拼就能查。

所有内容实时从 DB 生成，不会与索引脱节。
"""
import gzip
import io
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ingest  # noqa: E402

# 公网访问源：与 agent_docs.ORIGIN 共用同一环境变量，避免域名漂移
BASE_URL = os.environ.get("GEOKB_PUBLIC_ORIGIN", "https://sjck.kust.work")

# 导出时每条目携带的字段（保持精简，控制体积）
_EXPORT_FIELDS = ("id", "title", "module", "tags", "summary",
                  "confidence", "status", "updated", "source_tier")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# A. Markdown 渲染
# ---------------------------------------------------------------------------
def _tier_label(tier):
    return {"T1": "T1 权威", "T2": "T2 较权威", "T3": "T3 论文语料",
            "T4": "T4 待核"}.get(tier or "T4", str(tier))


def hits_markdown(res, q=""):
    """把检索结果渲染成 Markdown（Agent 友好，无嵌套）。"""
    hits = res.get("hits") or []
    # 带原文 PDF 的条目集合（用于在结果里直接给出下载地址）
    pdf_ids = set()
    try:
        for eid in corpus_pdf_index():
            pdf_ids.add(eid)
    except Exception:
        pass
    lines = []
    lines.append("# GeoKB 检索结果")
    lines.append("")
    lines.append("- 查询词：`%s`" % (q or "(无)"))
    lines.append("- 命中：**%d** 条" % len(hits))
    lines.append("- 生成时间：%s" % _now())
    lines.append("")
    if not hits:
        lines.append("> 未命中。请尝试：换用英文术语（矿床学文献多为英文）、"
                     "先用 `/api/tags` 或 `/topics.md` 查标准词、"
                     "或拆成长尾关键词。**不要凭空编造地学内容。**")
        return "\n".join(lines) + "\n"
    lines.append("---")
    for i, h in enumerate(hits, 1):
        eid = h.get("id", "")
        title = (h.get("title") or "").strip()
        lines.append("")
        lines.append("## %d. %s" % (i, title))
        lines.append("")
        lines.append("- 条目ID：`%s`" % eid)
        lines.append("- 模块：%s ｜ 分级：%s ｜ 置信度：%s" % (
            h.get("module"), _tier_label(h.get("source_tier")),
            h.get("confidence")))
        tags = h.get("tags") or []
        if tags:
            lines.append("- 标签：%s" % "、".join(map(str, tags)))
        if h.get("score") is not None:
            lines.append("- 相关度：%.3f" % h["score"])
        if h.get("summary"):
            lines.append("")
            lines.append((h.get("summary") or "").strip())
        lines.append("")
        lines.append("取全文：`GET %s/api/entry/%s.md`（Markdown）"
                     "或 `GET %s/api/entry/%s`（JSON）" % (BASE_URL, eid, BASE_URL, eid))
        if eid in pdf_ids:
            lines.append("")
            lines.append("原文 PDF：`GET %s/api/entry/%s/pdf`" % (BASE_URL, eid))
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("> 引用纪律：T3 为论文抽取语料，作定论前请优先采信 T1/T2；"
                 "检索不到就明确说明超出知识库范围。")
    return "\n".join(lines) + "\n"


def entry_markdown(d):
    """把单个条目渲染成 Markdown。"""
    lines = []
    lines.append("# %s" % (d.get("title") or d.get("id")))
    lines.append("")
    lines.append("- 条目ID：`%s`" % d.get("id"))
    lines.append("- 模块：%s ｜ 分级：%s ｜ 置信度：%s ｜ 状态：%s" % (
        d.get("module"), _tier_label(d.get("source_tier")),
        d.get("confidence"), d.get("status")))
    if d.get("updated"):
        lines.append("- 更新：%s" % d.get("updated"))
    # 知识点溯源（kp-* 才有）：来源论文 + 证据行号，Agent 可直接回原文核对
    if d.get("parent"):
        pt = d.get("parent_title") or ""
        lines.append("- 来源论文：`%s`%s" % (d["parent"], ("（%s）" % pt) if pt else ""))
        lines.append("- 论文永久链接：%s" % (d.get("parent_url")
                                          or "%s/api/entry/%s" % (BASE_URL, d["parent"])))
    if d.get("evidence"):
        lines.append("- 证据行号：%s" % d["evidence"])
    tags = d.get("tags") or []
    if tags:
        lines.append("- 标签：%s" % "、".join(map(str, tags)))
    if d.get("summary"):
        lines.append("")
        lines.append("## 摘要")
        lines.append("")
        lines.append(str(d.get("summary")).strip())
    srcs = d.get("sources") or []
    rels = [s.get("path") for s in srcs if isinstance(s, dict) and s.get("path")]
    if rels:
        lines.append("")
        lines.append("## 原文 PDF")
        lines.append("")
        lines.append("下载：`GET %s/api/entry/%s/pdf`" % (BASE_URL, d.get("id")))
        lines.append("")
        lines.append("在线预览：`GET %s/api/entry/%s/pdf/preview`" % (BASE_URL, d.get("id")))
        lines.append("")
        lines.append("纯文本（无需解析 PDF）：`GET %s/api/entry/%s/fulltext`" % (BASE_URL, d.get("id")))
    if d.get("body_markdown"):
        lines.append("")
        lines.append("## 正文")
        lines.append("")
        lines.append(str(d["body_markdown"]).strip())
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# B. 静态全量导出（Agent 下载后本地检索，零 API 调用）
# ---------------------------------------------------------------------------
def corpus_rows(module=None, with_body=False):
    """全量条目的精简列表。with_body=True 时附带正文（体积大，慎用）。"""
    conn = ingest.get_db()
    try:
        sql = ("SELECT id,title,module,tags,summary,confidence,status,updated,"
               "source_tier%s FROM entries WHERE status != 'deleted'"
               % (",body" if with_body else ""))
        args = []
        if module:
            sql += " AND module = ?"
            args.append(module)
        sql += " ORDER BY module, id"
        out = []
        for r in conn.execute(sql, args):
            d = dict(zip(_EXPORT_FIELDS + (("body_markdown",) if with_body else ()), r))
            try:
                d["tags"] = json.loads(d.get("tags") or "[]")
            except (json.JSONDecodeError, TypeError):
                d["tags"] = []
            d["url"] = "%s/api/entry/%s" % (BASE_URL, d["id"])
            d["md_url"] = "%s/api/entry/%s.md" % (BASE_URL, d["id"])
            out.append(d)
        return out
    finally:
        conn.close()


def corpus_pdf_index():
    """id -> PDF 相对路径 的映射（供离线检索后直接拼下载地址）。"""
    conn = ingest.get_db()
    try:
        out = {}
        for eid, sources in conn.execute(
                "SELECT id, sources FROM entries WHERE status != 'deleted'"):
            try:
                srcs = json.loads(sources or "[]")
            except (json.JSONDecodeError, TypeError):
                srcs = []
            rels = [s.get("path") for s in srcs
                    if isinstance(s, dict) and s.get("path")]
            if rels:
                out[eid] = rels[0]
        return out
    finally:
        conn.close()


def corpus_json(module=None):
    rows = corpus_rows(module=module)
    pdfs = corpus_pdf_index()
    for r in rows:
        rel = pdfs.get(r["id"])
        if rel:
            r["pdf_path"] = rel
            r["pdf_url"] = "%s/api/entry/%s/pdf" % (BASE_URL, r["id"])
    return {
        "ok": True,
        "data": {
            "generated_at": _now(),
            "total": len(rows),
            "module_filter": module,
            "usage": (
                "下载本文件后即可离线检索：用任意文本检索工具在 "
                "title/summary/tags 字段里匹配关键词，命中后用 pdf_url 或 "
                "url 取详情。无需再调用任何 API。"
            ),
            "entries": rows,
        },
    }


def corpus_json_gz_bytes(module=None):
    raw = json.dumps(corpus_json(module), ensure_ascii=False).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6, mtime=0) as g:
        g.write(raw)
    return buf.getvalue()


def _one_line(text, limit=140):
    s = re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip()
    return s if len(s) <= limit else s[:limit] + "…"


def corpus_markdown(module=None):
    rows = corpus_rows(module=module)
    by_mod = {}
    for r in rows:
        by_mod.setdefault(r["module"], []).append(r)
    lines = ["# GeoKB 全量条目索引", ""]
    lines.append("- 生成时间：%s" % _now())
    lines.append("- 条目总数：**%d**" % len(rows))
    lines.append("- 模块分布：%s" % "、".join(
        "%s(%d)" % (m, len(v)) for m, v in sorted(by_mod.items(), key=lambda x: -len(x[1]))))
    lines.append("")
    lines.append("> 用法：本页可整页下载后用文本检索工具离线查找；"
                 "也可以改用结构化版本 `GET %s/corpus.json.gz`（gzip，约省 85%% 体积）。" % BASE_URL)
    lines.append("")
    for m, items in sorted(by_mod.items(), key=lambda x: -len(x[1])):
        lines.append("## %s（%d 条）" % (m, len(items)))
        lines.append("")
        for r in items:
            tag_list = list(r.get("tags") or [])[:6]
            lines.append("- **%s** `%s`%s" % (
                r.get("title"), r.get("id"),
                " ｜ " + "、".join(map(str, tag_list)) if tag_list else ""))
        lines.append("")
    return "\n".join(lines) + "\n"


def topics_markdown():
    """主题导航：按模块/分面给出检索 URL 模板。"""
    conn = ingest.get_db()
    try:
        mods = list(conn.execute(
            "SELECT module, COUNT(*) FROM entries WHERE status != 'deleted' "
            "GROUP BY module ORDER BY 2 DESC"))
        tag_counts = {}
        for (tags_j,) in conn.execute(
                "SELECT tags FROM entries WHERE status != 'deleted'"):
            try:
                tags = json.loads(tags_j or "[]")
            except (json.JSONDecodeError, TypeError):
                tags = []
            for t in tags:
                tag_counts[str(t)] = tag_counts.get(str(t), 0) + 1
    finally:
        conn.close()
    top_tags = sorted(tag_counts.items(), key=lambda x: -x[1])[:60]

    lines = ["# GeoKB 主题导航", ""]
    lines.append("- 生成时间：%s" % _now())
    lines.append("")
    lines.append("## 按模块浏览（复制链接即可）")
    lines.append("")
    lines.append("| 模块 | 条目数 | 检索链接（把 `<关键词>` 换掉） |")
    lines.append("|---|---|---|")
    for m, n in mods:
        lines.append("| %s | %d | `%s/api/search?q=<关键词>&modules=%s&format=md` |"
                     % (m, n, BASE_URL, m))
    lines.append("")
    lines.append("## 高频标签（点格式链接即按标签检索）")
    lines.append("")
    for t, n in top_tags:
        lines.append("- %s（%d） → `%s/api/search?q=<关键词>&tags=%s&format=md`"
                     % (t, n, BASE_URL, t))
    lines.append("")
    lines.append("## 三条离线通路（不调用 API）")
    lines.append("")
    lines.append("1. **全量索引（Markdown）**：`GET %s/corpus.md` —— 下载后用文本检索工具离线查找。" % BASE_URL)
    lines.append("2. **全量索引（结构化 gzip）**：`GET %s/corpus.json.gz` —— 约 %d 条，含 pdf_url，解压后 JSON。" % (BASE_URL, sum(n for _, n in mods)))
    lines.append("3. **原文清单**：`GET %s/corpus.md?module=papers` —— 只列论文条目，按标题找原文。" % BASE_URL)
    lines.append("")
    lines.append("## 在线检索（推荐给能发请求的 Agent）")
    lines.append("")
    lines.append("```")
    lines.append("GET %s/api/search?q=矽卡岩&format=md      # Markdown 结果")
    lines.append("GET %s/api/search?q=skarn&format=full     # JSON + 正文")
    lines.append("GET %s/api/entry/<id>.md                   # 单条目 Markdown")
    lines.append("```" % ())
    return "\n".join(lines) + "\n"
