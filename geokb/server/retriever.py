# -*- coding: utf-8 -*-
"""GeoKB 混合检索层：BM25（FTS5）× 向量（sqlite-vec）→ 归一化 → RRF 融合 → 动态截断。

本次重设后的设计目标：
- 5556 面向公网 AI：无需鉴权，支持 POST/JSON 复杂过滤，字段裁剪，排序分页。
- 取消固定 15 篇限制，改为基于相关度的动态数量控制。
- 5555 管理端调用同一检索函数，但走本地直读 + 5556 内部调用均可。
"""
import json
import math
import os
import re
import sqlite3
from datetime import datetime

import jieba

from ingest import DIM, get_db, load_synonyms
from ingest import tokenize  # noqa: F401  jieba.add_word 已在导入时执行


# 公网访问源（与 agent_docs.ORIGIN 同一环境变量，避免域名漂移）
PUBLIC_ORIGIN = os.environ.get("GEOKB_PUBLIC_ORIGIN", "https://sjck.kust.work")

CONF_ORDER = {"A": 4, "B": 3, "C": 2, "D": 1}
CONF_WEIGHT = {"A": 1.0, "B": 0.95, "C": 0.85, "D": 0.70}
STATUS_WEIGHT = {"active": 1.0, "deprecated": 0.6, "deleted": 0.0}
# 来源分级权重。用户原则：论文衍生内容统一降权，官方/权威/多方认证内容为核心。
# T1 官方数据·标准·人工校订术语 | T2 教科书·权威综述 | T3 单篇论文衍生 | T4 外部订阅未核实
SOURCE_WEIGHT = {"T1": 1.00, "T2": 0.82, "T3": 0.62, "T4": 0.45}
SOURCE_DEFAULT = 0.62

# ── 星标提权 ──────────────────────────────────────────────
# 任何访客均可通过 POST /api/entry/<id>/star 星标/取消（公网无鉴权，
# 与本服务整体定位一致）。星标状态存 state/starred.json（不入索引、
# 不重建），检索时命中星标 ×STAR_BOOST 提权，并在 hit 里返回 starred。
STAR_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "state", "starred.json")
STAR_BOOST = 1.5
_star_cache = {"t": 0.0, "ids": frozenset()}


def starred_ids():
    """读取星标集合，带 5 秒缓存（避免每次检索都读盘）。"""
    import time
    now = time.time()
    if now - _star_cache["t"] < 5:
        return _star_cache["ids"]
    ids = frozenset()
    try:
        with open(STAR_FILE, encoding="utf-8") as f:
            ids = frozenset(json.load(f).keys())
    except Exception:
        pass
    _star_cache["t"] = now
    _star_cache["ids"] = ids
    return ids


def _pdf_url(sources_j, eid):
    """有 PDF 原文时给出永久公网下载链接，否则 None。

    设计意图：Agent 无需理解 sources 结构，直接把这个 URL 转述给
    终端用户即可完成"论文 PDF 分享"。
    """
    try:
        srcs = json.loads(sources_j or "[]")
    except (json.JSONDecodeError, TypeError):
        return None
    if any(isinstance(s, dict) and s.get("path") for s in srcs):
        return "%s/api/entry/%s/pdf" % (PUBLIC_ORIGIN, eid)
    return None


def _relevance_boosts(q_text, terms, title, tags_j):
    """主题级匹配提权系数：标签精确命中 ×TAG_BOOST，标题短语命中 ×TITLE_PHRASE_BOOST。

    为什么不在 SQL 里做：标签是 JSON 文本列、短语匹配需要原始查询串，
    都更适合在取行后的打分循环里算；主路径与模糊兜底路径共用本函数，
    避免两处提权规则漂移。
    """
    if not q_text:
        return 1.0
    factor = 1.0
    try:
        tag_set = {str(t).strip().lower() for t in json.loads(tags_j or "[]")}
    except (json.JSONDecodeError, TypeError):
        tag_set = set()
    term_set = {t.strip().lower() for t in terms if t and t.strip()}
    if tag_set & term_set:
        factor *= TAG_BOOST
    if q_text.lower() in (title or "").lower():
        factor *= TITLE_PHRASE_BOOST
    return factor


# ---------------------------------------------------------------------------
# 查询扩展
# ---------------------------------------------------------------------------
def expand_query(q, synonyms):
    """同义词扩展：中文词↔英文词双向。返回 (扩展词列表, 命中的映射说明)。"""
    terms = [
        w.strip()
        for w in jieba.cut(q)
        if w.strip() and not re.fullmatch(r"[\s，。、？！%&()（）]+", w)
    ]
    expanded, traced = list(dict.fromkeys(terms)), []
    for t in list(terms):
        for alias in synonyms.get(t.lower(), []):
            if alias.lower() not in [x.lower() for x in expanded]:
                expanded.append(alias)
                traced.append(f"{t}→{alias}")
    return expanded, traced


# ---------------------------------------------------------------------------
# 候选生成
# ---------------------------------------------------------------------------
# ── 相关性提权参数 ────────────────────────────────────────
# FTS 列顺序：id UNINDEXED, title, text。标题命中是比正文命中强得多的
# 相关信号（用户搜"矽卡岩"，标题含它的条目几乎必然相关），给 8 倍权重。
BM25_WEIGHTS = (1.0, 8.0, 1.0)
# 查询词（含同义词扩展）与条目标签精确相等 → 提权。
# 标签是人工受控词表，精确命中说明条目主题就是查询对象。
TAG_BOOST = 1.35
# 原始查询串整体是标题子串（短语命中）→ 提权。
# 分词后的单词命中可能来自不同句子，短语命中才是真正的主题匹配。
TITLE_PHRASE_BOOST = 1.25


def bm25_search(conn, terms, modules=None, limit=200):
    """FTS5 BM25 检索（标题列 8 倍加权）。返回 [(id, score)]，score 已取正。"""
    if not terms:
        return []
    q = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
    w = ", ".join(str(x) for x in BM25_WEIGHTS)
    sql = (
        "SELECT f.id, bm25(fts_entries, " + w + ") FROM fts_entries f "
        "JOIN entries e ON e.id = f.id "
        "WHERE fts_entries MATCH ? AND e.status != 'deleted'"
    )
    args = [q]
    if modules:
        sql += f" AND e.module IN ({','.join('?' * len(modules))})"
        args.extend(modules)
    sql += " ORDER BY bm25(fts_entries, " + w + ") LIMIT ?"
    args.append(limit)
    return [(r[0], -r[1]) for r in conn.execute(sql, args)]


def vec_search(conn, model, q, modules=None, limit=200):
    """sqlite-vec KNN 检索。返回 [(id, similarity)]。"""
    if model is None or not q:
        return []
    qv = list(model.embed([q]))[0]
    import struct
    blob = struct.pack(f"{len(qv)}f", *qv)
    knn_sql = (
        "SELECT v.entry_id, distance FROM vec_entries v "
        "JOIN entries e ON e.id = v.entry_id "
        "WHERE e.status != 'deleted' AND v.embedding MATCH ? AND k = ?"
    )
    args = [blob, limit]
    if modules:
        knn_sql += f" AND e.module IN ({','.join('?' * len(modules))})"
        args = [blob] + modules + [limit]
    return [(r[0], 1.0 - r[1]) for r in conn.execute(knn_sql, args)]


def fuzzy_search(conn, q, modules=None, limit=50):
    """兜底模糊检索：标题/摘要/正文子串匹配。"""
    if not q:
        return []
    pattern = f"%{q}%"
    sql = (
        "SELECT id FROM entries "
        "WHERE status = 'active' AND (title LIKE ? OR summary LIKE ? OR body LIKE ?)"
    )
    args = [pattern, pattern, pattern]
    if modules:
        sql += f" AND module IN ({','.join('?' * len(modules))})"
        args.extend(modules)
    sql += " LIMIT ?"
    args.append(limit)
    # 模糊结果统一给予 0.5 基础分
    return [(r[0], 0.5) for r in conn.execute(sql, args)]


# ---------------------------------------------------------------------------
# 融合
# ---------------------------------------------------------------------------
def rrf_fuse(*ranked_lists, k=60):
    """RRF：score = Σ 1/(k+rank)。返回 [(id, {rrf, sources})]。"""
    scores = {}
    for src_idx, lst in enumerate(ranked_lists):
        for rank, (eid, _score) in enumerate(lst):
            d = scores.setdefault(eid, {"rrf": 0.0, "sources": []})
            d["rrf"] += 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: -x[1]["rrf"])


def normalize_scores(scores):
    """对 [(id, score)] 做 Min-Max 归一化，至少返回 [0,1]。"""
    if not scores:
        return []
    vals = [s for _, s in scores]
    min_s, max_s = min(vals), max(vals)
    if max_s - min_s < 1e-9:
        return [(eid, 1.0) for eid, _ in scores]
    return [(eid, (s - min_s) / (max_s - min_s)) for eid, s in scores]


# ---------------------------------------------------------------------------
# 数据获取与过滤
# ---------------------------------------------------------------------------
def fetch_rows(conn, ids):
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id,title,module,tags,summary,confidence,status,updated,pub_date,sources,path,"
        f"body_chars,source_tier FROM entries WHERE id IN ({ph})",
        ids,
    ).fetchall()
    return {r[0]: r for r in rows}


def _as_list(value):
    if value is None:
        return None
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(value)]


def _match_tags(entry_tags, required):
    if not required:
        return True
    low = [str(t).lower() for t in entry_tags]
    return any(t.lower() in low for t in required)


def _date_in_range(updated, date_from, date_to):
    if not updated:
        return False
    try:
        d = datetime.strptime(str(updated)[:10], "%Y-%m-%d")
    except ValueError:
        return False
    if date_from and d < datetime.strptime(str(date_from)[:10], "%Y-%m-%d"):
        return False
    if date_to and d > datetime.strptime(str(date_to)[:10], "%Y-%m-%d"):
        return False
    return True


# ---------------------------------------------------------------------------
# 动态截断
# ---------------------------------------------------------------------------
def adaptive_cutoff(hits, limit=0, offset=0, min_score=None, score_ratio=0.55,
                    min_results=3, global_cap=50, gap_threshold=0.15):
    """按分数动态截断。hits 须已按 score 降序。

    limit>0 表示调用方要显式分页：直接切 offset:offset+limit。
    注意：offset 必须在这里切——原先"先 hits[:limit] 再 hits[offset:]"的写法
    会让第 2 页起恒为空（limit=20&offset=20 先取前 20 条，再切 [20:] 得空）。
    """
    if limit:
        return hits[offset:offset + min(limit, 100)]
    if not hits:
        return []
    max_score = hits[0]["score"]
    threshold = max_score * score_ratio if min_score is None else max(min_score, max_score * score_ratio)
    selected = []
    for i, hit in enumerate(hits):
        if hit["score"] < 0.05:
            break
        if hit["score"] < threshold and len(selected) >= min_results:
            if i > 0 and (hits[i - 1]["score"] - hit["score"]) > gap_threshold:
                break
        selected.append(hit)
    return selected[:global_cap]


# ---------------------------------------------------------------------------
# 主检索入口
# ---------------------------------------------------------------------------
def search(q=None, *, module=None, modules=None, tags=None,
           exclude_modules=None, exclude_tags=None,
           confidence_min=None, status=None,
           date_from=None, date_to=None,
           sort="relevance", order="desc",
           limit=0, min_score=None, score_ratio=0.55,
           min_results=3, global_cap=50,
           token_budget=None, fields=None, fmt="summary",
           expand=True, fuzzy=True,
           offset=0,
           model=None, synonyms=None):
    """面向 AI 与 5555 的统一检索入口。

    参数说明：
      q: 查询词；None/空 表示纯元数据过滤。
      module/modules: 单模块 或 模块列表。
      tags: 标签列表（含任意一个即命中）。
      exclude_modules / exclude_tags: 排除列表。
      confidence_min: 'A'/'B'/'C'/'D'，过滤最低可信度。
      status: 状态或列表；默认排除 deleted。
      date_from / date_to: 'YYYY-MM-DD' 日期区间。
      sort: 'relevance' | 'updated' | 'confidence' | 'score'。
      order: 'desc' | 'asc'。
      limit: 0=自适应截断；>0 最多返回条数（上限 100）。
      fields: 返回字段白名单；None 返回默认字段。
      fmt: 'summary' 只返回摘要等；'full' 额外返回 body_markdown。
      expand: 是否启用同义词扩展。
      fuzzy: 主结果不足时是否启用子串模糊兜底。
      offset: 分页偏移（在截断前结果上做偏移）。
    """
    synonyms = synonyms if synonyms is not None else load_synonyms()
    conn = get_db()
    try:
        # 规范化过滤条件
        modules = _as_list(modules) or (_as_list(module) if module else None)
        tags = _as_list(tags)
        exclude_modules = _as_list(exclude_modules)
        exclude_tags = _as_list(exclude_tags)
        status_list = _as_list(status) or ["active", "deprecated"]
        if "deleted" in status_list:
            # 显式请求才返回 deleted；默认不返回
            pass

        # ---- 1. 候选生成 ---------------------------------------------------
        q_text = (q or "").strip()
        terms, traced = [], []
        if q_text:
            terms, traced = expand_query(q_text, synonyms) if expand else (
                [w.strip() for w in jieba.cut(q_text) if w.strip()], []
            )

        bm = bm25_search(conn, terms, modules=modules, limit=200) if q_text else []
        vc = vec_search(conn, model, q_text, modules=modules, limit=200) if q_text else []
        fused = rrf_fuse(bm, vc)

        # ---- 2. 取行并元数据过滤 ----------------------------------------
        if q_text:
            # 有查询词时，先取融合 Top 再过滤
            candidate_ids = [eid for eid, _ in fused[: max(limit * 5 if limit else 200, 200)]]
            rows = fetch_rows(conn, candidate_ids)
        else:
            # 无查询词时，按 SQL 过滤全量
            where = ["1=1"]
            args = []
            if modules:
                where.append(f"module IN ({','.join('?' * len(modules))})")
                args.extend(modules)
            if status_list:
                where.append(f"status IN ({','.join('?' * len(status_list))})")
                args.extend(status_list)
            else:
                where.append("status != 'deleted'")
            sql = (
                f"SELECT id,title,module,tags,summary,confidence,status,updated,pub_date,sources,path,"
                f"body_chars,source_tier FROM entries WHERE {' AND '.join(where)}"
            )
            rows = {r[0]: r for r in conn.execute(sql, args).fetchall()}
            fused = [(eid, {"rrf": 1.0, "sources": []}) for eid in rows]

        # ---- 3. 归一化与加权 ----------------------------------------------
        bm_norm = {eid: s for eid, s in normalize_scores(bm)}
        vc_norm = {eid: s for eid, s in normalize_scores(vc)}
        total_rrf = sum(info["rrf"] for _, info in fused) or 1.0

        hits = []
        stars = starred_ids()
        for eid, info in fused:
            r = rows.get(eid)
            if not r:
                continue
            (_id, title, mod, tags_j, summary, conf, status, updated, pub_date, sources_j, path,
             bchars, tier) = r

            # 过滤
            if confidence_min and CONF_ORDER.get(conf, 0) < CONF_ORDER.get(confidence_min, 0):
                continue
            if status not in status_list:
                continue
            if modules and mod not in modules:
                continue
            if exclude_modules and mod in exclude_modules:
                continue
            if tags and not _match_tags(json.loads(tags_j or "[]"), tags):
                continue
            if exclude_tags and _match_tags(json.loads(tags_j or "[]"), exclude_tags):
                continue
            if date_from or date_to:
                if not _date_in_range(updated, date_from, date_to):
                    continue

            # 分数计算
            if q_text:
                base_score = info["rrf"] / total_rrf
                bm_hit = bm_norm.get(eid, 0.0)
                v_hit = vc_norm.get(eid, 0.0)
                # 若两路都命中，做线性加权；RRF 已体现，此处微调
                if bm_hit and v_hit:
                    base_score = 0.6 * base_score + 0.25 * bm_hit + 0.15 * v_hit
                elif bm_hit:
                    base_score = 0.75 * base_score + 0.25 * bm_hit
                elif v_hit:
                    base_score = 0.75 * base_score + 0.25 * v_hit
            else:
                # 无查询词（纯元数据过滤/模块列表）：所有命中等权。
                # 不能用 rrf/total_rrf —— 分母是候选总数，分数随语料规模衰减
                # （748 篇论文时约 0.0008），会全线跌破 adaptive_cutoff 的
                # 0.05 绝对地板，导致 /api/module/<name>、/api/entries 返回空列表。
                base_score = 1.0
                bm_hit = v_hit = 0.0

            # 来源分级降权：论文衍生内容统一压低，权威内容保持核心
            base_score *= (CONF_WEIGHT.get(conf, 0.7) * STATUS_WEIGHT.get(status, 0.0)
                           * SOURCE_WEIGHT.get(tier, SOURCE_DEFAULT))
            # 主题级匹配提权（标签精确命中 / 标题短语命中）
            base_score *= _relevance_boosts(q_text, terms, title, tags_j)
            # 星标提权
            is_starred = eid in stars
            if is_starred:
                base_score *= STAR_BOOST

            hit = {
                "id": eid,
                "title": title,
                "module": mod,
                "tags": json.loads(tags_j or "[]"),
                "summary": summary,
                "confidence": conf,
                "status": status,
                "source_tier": tier or "T3",
                "updated": updated, "pub_date": pub_date,
                "sources": json.loads(sources_j or "[]"),
                "path": path,
                "pdf_url": _pdf_url(sources_j, eid),
                "starred": is_starred,
                "score": round(base_score, 4),
                "matched_by": [s for s in ("bm25", "vector") if
                               (s == "bm25" and eid in bm_norm) or
                               (s == "vector" and eid in vc_norm)],
                "score_detail": {"bm25": round(bm_hit, 4), "vector": round(v_hit, 4)},
                "body_chars": bchars,
            }
            if fmt == "full":
                # 仅在 full 模式读取正文
                body_text = open(path, encoding="utf-8").read()
                hit["body_markdown"] = body_text
            hits.append(hit)

        # ---- 4. 排序 -------------------------------------------------------
        reverse = order.lower() == "desc"
        if sort == "relevance":
            hits.sort(key=lambda x: x["score"], reverse=reverse)
        elif sort == "updated":
            hits.sort(key=lambda x: x["updated"] or "", reverse=reverse)
        elif sort == "confidence":
            hits.sort(key=lambda x: CONF_ORDER.get(x["confidence"], 0), reverse=reverse)
        elif sort == "score":
            hits.sort(key=lambda x: x["score"], reverse=reverse)
        else:
            hits.sort(key=lambda x: x["score"], reverse=reverse)

        # ---- 5. 模糊兜底 ---------------------------------------------------
        if fuzzy and q_text and len(hits) < min_results:
            existing = {h["id"] for h in hits}
            fuzzy_hits = fuzzy_search(conn, q_text, modules=modules, limit=50)
            for eid, fscore in fuzzy_hits:
                if eid in existing:
                    continue
                r = rows.get(eid)
                if not r:
                    # 兜底读取
                    rr = conn.execute(
                        "SELECT id,title,module,tags,summary,confidence,status,updated,pub_date,sources,path,"
                        "body_chars,source_tier FROM entries WHERE id = ?", (eid,)
                    ).fetchone()
                    if not rr:
                        continue
                    r = rr
                (_id, title, mod, tags_j, summary, conf, status, updated, pub_date, sources_j, path,
                 bchars, tier) = r
                if status not in status_list:
                    continue
                if exclude_modules and mod in exclude_modules:
                    continue
                is_starred = eid in stars
                fscore_s = fscore * CONF_WEIGHT.get(conf, 0.7) \
                    * STATUS_WEIGHT.get(status, 0.0) \
                    * SOURCE_WEIGHT.get(tier, SOURCE_DEFAULT)
                fscore_s *= _relevance_boosts(q_text, terms, title, tags_j)
                if is_starred:
                    fscore_s *= STAR_BOOST
                hit = {
                    "id": eid, "title": title, "module": mod,
                    "tags": json.loads(tags_j or "[]"), "summary": summary,
                    "confidence": conf, "status": status, "source_tier": tier or "T3",
                    "updated": updated, "pub_date": pub_date,
                    "sources": json.loads(sources_j or "[]"), "path": path,
                    "pdf_url": _pdf_url(sources_j, eid),
                    "starred": is_starred,
                    "score": round(fscore_s, 4),
                    "matched_by": ["fuzzy"],
                    "score_detail": {"fuzzy": round(fscore, 4)},
                    "body_chars": bchars,
                }
                if fmt == "full":
                    hit["body_markdown"] = open(path, encoding="utf-8").read()
                hits.append(hit)
            hits.sort(key=lambda x: x["score"], reverse=reverse)

        # ---- 6. 动态截断 / token 预算 / 字段裁剪 --------------------------
        total_before_cut = len(hits)
        hits = adaptive_cutoff(hits, limit=limit, offset=offset, min_score=min_score,
                               score_ratio=score_ratio, min_results=min_results,
                               global_cap=global_cap)

        # 分页元信息：per_page=0 表示未显式分页（自适应截断模式）
        per_page = min(limit, 100) if limit else 0
        page = (offset // per_page) + 1 if per_page else 1
        pages = (total_before_cut + per_page - 1) // per_page if per_page else 1

        if token_budget:
            used = 0
            kept = []
            for h in hits:
                used += len(h.get("summary") or "") + len(h.get("title") or "") + 80
                if used > token_budget * 4:
                    break
                kept.append(h)
            hits = kept

        if fields:
            hits = [{k: h.get(k) for k in fields if k in h} for h in hits]

        meta = conn.execute("SELECT key, value FROM meta").fetchall()
        return {
            "query": q_text,
            "expanded_terms": terms,
            "term_expansion": traced,
            "module_filter": modules[0] if modules and len(modules) == 1 else modules,
            "status_filter": status_list,
            "strategy": (
                "bm25+vector+rrf+adaptive-cutoff" if q_text else
                "metadata-filter+adaptive-cutoff"
            ) + ("+fuzzy" if any("fuzzy" in h.get("matched_by", []) for h in hits) else ""),
            "total_hits": total_before_cut,
            "hits_count": len(hits),
            "limit": limit,
            "offset": offset,
            "page": page,
            "pages": pages,
            "per_page": per_page,
            "hits": hits,
            "index_built_at": dict(meta).get("built_at"),
        }
    finally:
        conn.close()
