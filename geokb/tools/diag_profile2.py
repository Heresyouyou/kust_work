# -*- coding: utf-8 -*-
"""精确分项：检索各阶段 + HTTP 响应组装。只读。"""
import json, os, statistics, sys, time

sys.path.insert(0, r"D:/ZSK/geokb/server")
os.chdir(r"D:/ZSK/geokb")
import ingest, retriever, app as appmod

M = appmod.get_model()


def t(label, fn, n=1):
    xs = []
    r = None
    for _ in range(n):
        t0 = time.perf_counter()
        r = fn()
        xs.append((time.perf_counter() - t0) * 1000)
    if n > 1:
        print("  %-34s p50 %7.2f  max %7.1f ms" % (label, statistics.median(xs), max(xs)))
    else:
        print("  %-34s %8.2f ms" % (label, xs[0]))
    return r


conn = ingest.get_db()
print("== 单请求内的 SQL 阶段（复用同一连接）==")
terms, _ = retriever.expand_query("矽卡岩", ingest.load_synonyms())
t("get_db() 每次新建连接", lambda: ingest.get_db().close(), 5)
t("expand_query (jieba+同义词)", lambda: retriever.expand_query("矽卡岩", ingest.load_synonyms()), 5)
bm = t("bm25 top200", lambda: retriever.bm25_search(conn, terms, None, 200), 5)
vc = t("vec k=200 (512d 暴力)", lambda: retriever.vec_search(conn, M, "矽卡岩", None, 200), 5)
ids = [x[0] for x in bm][:200]
rows = t("fetch_rows(200)", lambda: retriever.fetch_rows(conn, ids), 5)
t("COUNT(*) WHERE status!=deleted", lambda: conn.execute(
    "SELECT COUNT(*) FROM entries WHERE status != 'deleted'").fetchone(), 5)
t("SELECT 全表 status IN(active,dep)", lambda: conn.execute(
    "SELECT id FROM entries WHERE status IN ('active','deprecated')").fetchall(), 5)
t("SELECT 全表(含 body) 18k 行", lambda: conn.execute(
    "SELECT id,title,module,tags,summary,confidence,status,updated,pub_date,sources,path,"
    "body_chars,source_tier FROM entries WHERE status IN ('active','deprecated')").fetchall(), 5)
t("EXPLAIN idx? status 查询", lambda: conn.execute(
    "EXPLAIN QUERY PLAN SELECT id FROM entries WHERE status='active'").fetchall(), 1)
print("   plan:", conn.execute(
    "EXPLAIN QUERY PLAN SELECT id FROM entries WHERE status='active'").fetchall())
conn.close()

print("\n== 端到端：retriever.search 各参数 ==")
for q, kw in [("矽卡岩", {"limit": 20}), ("矽卡岩", {}), ("garnet skarn", {"limit": 10})]:
    res = t("search q=%r %s" % (q, kw), lambda: retriever.search(q=q, model=M, **kw), 5)
    print("     hits=%d total=%d" % (res["hits_count"], res["total_hits"]))

res = retriever.search(q="矽卡岩", model=M, limit=20)
t("json.dumps(整包 20 hits)", lambda: json.dumps(res, ensure_ascii=False), 5)
t("_annotate_translation(20 hits)", lambda: appmod._annotate_translation(res["hits"]), 5)

res2 = retriever.search(q="", model=None, limit=20)
t("search q='' limit=20 (列表页)", lambda: retriever.search(q="", model=None, limit=20), 5)
print("     hits=%d total=%d" % (res2["hits_count"], res2["total_hits"]))
