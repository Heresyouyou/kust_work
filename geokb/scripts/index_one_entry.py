# -*- coding: utf-8 -*-
"""把单个条目文件增量写入 GeoKB 索引（entries + FTS5 + 向量）。

场景
----
门户 journals 页「下载 PDF」按钮即时建档（extraction: manual）后，
阅读/翻译按钮依赖 5556 按条目 id 查库（pdf_service / translate_service
都走 `SELECT ... FROM entries WHERE id = ?`），不能等每日全量 rebuild
（embed 5328 条约 4 分钟）——必须秒级可见。

做法
----
与 server/ingest.rebuild() 的插入逻辑逐行同源（同一 embed 模型、
同一 tokenize、同一 struct.pack），只是范围从「全部条目」缩到「一个」，
且用 DELETE+INSERT 保证可重复执行（FTS5 不支持 OR REPLACE）。

用法
----
    python index_one_entry.py <eid>

退出码：0 成功；1 条目文件不存在/解析失败；2 其他异常。
"""
import glob
import os
import struct
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "server"))

import ingest  # noqa: E402  （复用 parse_entry / tokenize / get_db / init_schema / DIM）


def find_entry_file(eid):
    """按 id 在 content/<module>/ 下找条目文件（与 collect_entries 的目录约定一致）。"""
    hits = glob.glob(os.path.join(ingest.CONTENT, "*", f"{eid}.md"))
    return hits[0] if hits else None


def prune(sha12):
    """删除 index 里已不存在的 kp-<sha12>-* 行（知识点条数变少时的孤儿）。

    知识点条数不再固定（可能是 0–8 任意值），重算后旧编号对应的文件会被删掉；
    若只做 upsert，索引里会留下指向已删文件的死行（/api/entry 还能查到，
    点 PDF 却 404）。这里只删行，不做嵌入，毫秒级。
    """
    conn = ingest.get_db()
    ingest.init_schema(conn)
    pat = "kp-%s-%%" % sha12
    ids = [r[0] for r in conn.execute("SELECT id FROM entries WHERE id LIKE ?", (pat,))]
    drop = [i for i in ids if not find_entry_file(i)]
    for i in drop:
        for t in ("entries", "fts_entries"):
            try:
                conn.execute("DELETE FROM %s WHERE id = ?" % t, (i,))
            except Exception:  # noqa: BLE001
                pass
        try:
            conn.execute("DELETE FROM vec_entries WHERE entry_id = ?", (i,))
        except Exception:  # noqa: BLE001
            pass
    conn.commit()
    print("PRUNE %s | 检查 %d 条，删除孤儿 %d 条" % (sha12, len(ids), len(drop)))
    return 0


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "--prune":
        if len(argv) < 2:
            print("usage: index_one_entry.py --prune <sha12>")
            return 2
        return prune(argv[1].strip())
    if len(argv) < 1:
        print("usage: index_one_entry.py <eid> | --prune <sha12>")
        return 2
    eid = argv[0].strip()
    path = find_entry_file(eid)
    if not path:
        print(f"条目文件不存在: {eid}")
        return 1
    e = ingest.parse_entry(path)
    if not e:
        print(f"条目解析失败: {path}")
        return 1

    conn = ingest.get_db()
    ingest.init_schema(conn)
    upsert(conn, e)
    # 必须 commit：upsert 按约定不提交（批量时由调用方控制事务），单条路径
    # 漏了这一步会留下**未提交事务**，同进程里下一次 connect 直接报
    # `database is locked`（实测 agent_ingest commit 的 7 个 eid 只成功第 1 个）。
    conn.commit()
    conn.close()
    print(f"OK {eid} | {e['module']} | {e['title'][:50]}")
    return 0


_MODEL = None


def embed_model():
    """进程内复用同一个 embedding 模型（批量索引时避免每篇重载，单篇也更快）。"""
    global _MODEL
    if _MODEL is None:
        from fastembed import TextEmbedding
        _MODEL = TextEmbedding("BAAI/bge-small-zh-v1.5",
                               cache_dir=os.path.join(ingest.BASE, "models"))
    return _MODEL


def embed_text(e):
    """条目的嵌入文本（与 rebuild 同构）。"""
    return (f"{e['title']}。{e['summary']}。"
            f"{'，'.join(map(str, e['tags']))}。{e['body'][:2000]}")


def embed_one(e):
    return list(embed_model().embed([embed_text(e)]))[0]


def upsert(conn, e, vec=None):
    """单条 upsert（entries + FTS5 + 向量），不 commit —— 由调用方控制批次。

    与 server/ingest.rebuild() 的嵌入文本构造保持一致（顺序/截断长度相同），
    向量才与其他条目可比。vec 可由调用方预先批量算好（批量索引快 5~10 倍）。
    """
    eid = e["id"]
    if vec is None:
        vec = embed_one(e)

    conn.execute("DELETE FROM entries WHERE id = ?", (eid,))
    # parent / evidence 必须一起写：知识点靠 parent 指回论文、靠 evidence 回溯原文。
    # 旧版 INSERT 漏了这两列，增量索引出来的知识点在 API 里 parent 恒为 null
    # （rebuild 写、增量不写 → 两条路径数据不一致，2026-10-23 修复）。
    conn.execute(
        "INSERT INTO entries "
        "(id,title,module,tags,summary,confidence,status,updated,pub_date,sources,"
        " body,path,body_chars,source_tier,parent,evidence) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (e["id"], e["title"], e["module"], ingest.json.dumps(e["tags"], ensure_ascii=False),
         e["summary"], e["confidence"], e["status"], e["updated"],
         e.get("pub_date", ""),
         ingest.json.dumps(ingest._json_safe(e["sources"]), ensure_ascii=False),
         e["body"], e["path"], len(e["body"]), e.get("source_tier", "T4"),
         e.get("parent", ""), e.get("evidence", "")))
    if e["status"] != "deleted":
        fts_text = ingest.tokenize(e["title"] + " " + e["summary"] + " "
                                   + " ".join(map(str, e["tags"])) + " " + e["body"])
        conn.execute("DELETE FROM fts_entries WHERE id = ?", (eid,))
        conn.execute("INSERT INTO fts_entries VALUES (?,?,?)",
                     (e["id"], e["title"], fts_text))
        conn.execute("DELETE FROM vec_entries WHERE entry_id = ?", (eid,))
        conn.execute(
            "INSERT INTO vec_entries VALUES (?, ?)",
            (e["id"], struct.pack(f"{ingest.DIM}f", *vec.tolist())))
    return True


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {type(exc).__name__}: {exc}")
        sys.exit(2)
