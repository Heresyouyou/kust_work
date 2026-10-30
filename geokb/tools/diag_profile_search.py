# -*- coding: utf-8 -*-
"""分阶段计时：把 /api/search 的耗时拆成 检索 / 标注 / JSON 序列化。只读。"""
import json, os, sys, time

sys.path.insert(0, r"D:/ZSK/geokb/server")
os.chdir(r"D:/ZSK/geokb")

import ingest, retriever
import app as appmod

M = appmod.get_model()


def phase(label, fn, *a, **kw):
    t0 = time.perf_counter()
    r = fn(*a, **kw)
    print("  %-22s %8.1f ms" % (label, (time.perf_counter() - t0) * 1000))
    return r


print("== 冷启动后第一次（模型已加载）==")
for q, kw in [
    ("矽卡岩", {}),
    ("矽卡岩", {"limit": 20}),
    ("garnet scheelite skarn", {"limit": 10}),
    ("白钨矿 矽卡岩 成矿", {"limit": 20}),
]:
    print("\nq=%r %s" % (q, kw))
    res = phase("retriever.search", retriever.search, q=q, model=M, **kw)
    n = res["hits_count"]
    phase("_annotate_translation", appmod._annotate_translation, res.get("hits"))
    phase("jsonify(body)", lambda: json.dumps(appmod.ok(res)))
    phase("jsonify(res)", lambda: json.dumps(res, ensure_ascii=False))
    print("  hits=%d total=%d body=%.1f KB" % (
        n, res["total_hits"], len(json.dumps(res)) / 1024))

print("\n== 纯 SQL 分项（直接打 DB）==")
conn = ingest.get_db()
terms, _ = retriever.expand_query("矽卡岩", ingest.load_synonyms())
phase("expand_query(jieba)", lambda: retriever.expand_query("矽卡岩", ingest.load_synonyms()))
phase("bm25_search", retriever.bm25_search, conn, terms, None, 200)
phase("vec_search(k=200)", retriever.vec_search, conn, M, "矽卡岩", None, 200)
phase("load_synonyms(yaml)", ingest.load_synonyms)
ids = [r[0] for r in retriever.bm25_search(conn, terms, None, 200)][:200]
phase("fetch_rows(200)", retriever.fetch_rows, conn, ids)
phase("COUNT(*) entries", lambda: conn.execute("SELECT COUNT(*) FROM entries").fetchone())
phase("meta 读表", lambda: conn.execute("SELECT key,value FROM meta").fetchall())
conn.close()

print("\n== 标注单条成本（NAS 目录列举/stat）==")
import translate_service as ts
one = None
res = retriever.search(q="矽卡岩", model=M, limit=5)
for h in res["hits"]:
    rel = ts.rel_of_hit(h)
    if rel:
        one = rel
        break
if one:
    phase("translation_files(NAS)", ts.translation_files, one)
    phase("translation_info(NAS)", ts.translation_info, one, True)
    phase("translation_files x10", lambda: [ts.translation_files(one) for _ in range(10)])
