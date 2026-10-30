# -*- coding: utf-8 -*-
"""GeoKB 摄取管线（Phase 4c）：content/*.md -> SQLite（元数据 + FTS5 + sqlite-vec 向量）。
CLI 与后续 Web 管理端写操作共用本模块。"""
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, date

import jieba
import sqlite_vec
import yaml

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT = os.path.join(BASE, "content")
DB_PATH = os.path.join(BASE, "index", "geokb.db")
DIM = 512

# ─── 来源分级（source_tier）───────────────────────────────────
# 用户原则：来自论文的词条统一降权；官方、权威、经多方认证的词条为核心。
# tier 是**派生属性**，不写入 Markdown（避免一致性维护成本），重建索引即可生效。
SOURCE_TIER_BY_MODULE = {
    # T1 权威核心：官方数据/标准/多方认证/人工校订术语
    "data-sheets": "T1",        # USGS Mineral Commodity Summaries 等官方数据集
    "glossary": "T1",           # 人工校订术语库
    "standards": "T1",
    # T2 学术共识：教科书/权威综述/成熟理论
    "mineralogy": "T2",
    "petrology": "T2",
    "structural-geology": "T2",
    "geochemistry": "T2",
    "geochronology": "T2",
    "stratigraphy": "T2",
    "economic-geology": "T2",
    "exploration": "T2",
    "mining-methods": "T2",
    # T3 论文衍生：单篇论文的结论与抽出的知识点（统一降权）
    "papers": "T3",
    "knowledge-points": "T3",
    # T4 外部订阅/未核实
    "external-feed": "T4",
    "news": "T4",
}


def infer_source_tier(module, confidence):
    """推断来源分级。frontmatter 若显式写了 source_tier 则以它为准。

    规则：
      ① 模块显式映射优先；
      ② 未知模块按 confidence 兜底（A/B → T2，C/D → T4）；
      ③ 无论哪个模块，confidence=C/D 的自动产物一律压到 T4（未经核实）。
    """
    m = (module or "").lower().strip()
    if m in SOURCE_TIER_BY_MODULE:
        tier = SOURCE_TIER_BY_MODULE[m]
    else:
        tier = "T2" if str(confidence).upper() in ("A", "B") else "T4"
    # C/D 级自动产物无条件压到最低可信档（但 T1 官方数据保留原判）
    if str(confidence).upper() in ("C", "D") and tier != "T1":
        tier = "T4"
    return tier

# 地矿自定义分词词典（增量维护）
GEO_WORDS = [
    "矽卡岩", "斑岩型", "斑岩铜矿", "热液脉型", "同位素", "地球化学", "矿床学",
    "镜下鉴定", "干涉色", "消光角", "光性符号", "突起等级", "多色性", "解理夹角",
    "年代地层", "地质年代表", "显生宙", "白垩纪", "矿床模型", "品位吨位", "围岩蚀变",
    "流体包裹体", "辉钼矿", "白钨矿", "黑钨矿", "黄铁矿", "磁黄铁矿", "方铅矿",
    "闪锌矿", "黄铜矿", "辉石", "角闪石", "斜长石", "正长石", "黑云母", "白云母",
    "橄榄石", "石榴子石", "磷灰石", "萤石", "方解石", "重砂", "三江",
]
for w in GEO_WORDS:
    jieba.add_word(w)


def tokenize(text):
    """jieba 词 + 单字双通道，返回空格分隔串（供 FTS5 unicode61 分词）。"""
    words = [w.strip() for w in jieba.cut(text) if w.strip()]
    single = " ".join(w for w in words if len(w) == 1)
    return " ".join(words) + " " + single


def parse_entry(path):
    raw = open(path, encoding="utf-8").read()
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", raw, re.S)
    if not m:
        return None
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict) or "id" not in fm:
        return None
    return {
        "id": str(fm.get("id")),
        "title": str(fm.get("title", "")),
        "module": str(fm.get("module", "")),
        "tags": fm.get("tags") or [],
        "summary": str(fm.get("summary", "")),
        "confidence": str(fm.get("confidence", "C")),
        "status": str(fm.get("status", "active")),
        "updated": str(fm.get("updated", "")),
        # 发布日期（2026-10-24）：前端展示用；updated 退居为「入库日期」，仅管理、不展示
        "pub_date": str(fm.get("pub_date") or ""),
        "sources": fm.get("sources") or [],
        "body": m.group(2).strip(),
        "path": path.replace("\\", "/"),
        # 知识点溯源字段：kpoints 由 kp_extract.py 写入 parent(来源论文 id) 与
        # evidence(证据行号)。这两列若不入索引，API 侧父论文指针与证据行号会
        # 全部返回 null，知识点无法回溯到原文（2026-10-18 修复的静默丢字段）。
        "parent": str(fm.get("parent") or ""),
        "evidence": str(fm.get("evidence") or ""),
        # 来源分级：frontmatter 显式值优先，否则按模块+可信度推断
        "source_tier": str(fm.get("source_tier")
                           or infer_source_tier(fm.get("module", ""),
                                                fm.get("confidence", "C"))
                           ).upper(),
    }


def load_synonyms():
    """_synonyms.yaml -> {term_lower: [aliases]}"""
    syn_path = os.path.join(CONTENT, "glossary", "_synonyms.yaml")
    mapping = {}
    if os.path.exists(syn_path):
        try:
            data = yaml.safe_load(open(syn_path, encoding="utf-8").read())
            if isinstance(data, dict):
                for k, v in data.items():
                    mapping[str(k).lower()] = [str(x) for x in (v or [])]
        except yaml.YAMLError:
            pass
    return mapping


def collect_entries():
    entries = []
    for mod in sorted(os.listdir(CONTENT)):
        moddir = os.path.join(CONTENT, mod)
        if not os.path.isdir(moddir):
            continue
        for fn in sorted(os.listdir(moddir)):
            if fn.endswith(".md"):
                e = parse_entry(os.path.join(moddir, fn))
                if e:
                    entries.append(e)
    return entries


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def init_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS entries (
            id TEXT PRIMARY KEY, title TEXT, module TEXT, tags TEXT,
            summary TEXT, confidence TEXT, status TEXT, updated TEXT,
            pub_date TEXT,
            sources TEXT, body TEXT, path TEXT, body_chars INTEGER,
            source_tier TEXT DEFAULT 'T4',
            parent TEXT DEFAULT '', evidence TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_entries_module ON entries(module);
        CREATE VIRTUAL TABLE IF NOT EXISTS fts_entries USING fts5(
            id UNINDEXED, title, text, tokenize='unicode61'
        );
        """
    )
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_entries USING vec0("
        f"entry_id TEXT PRIMARY KEY, embedding float[{DIM}])"
    )
    # 兼容旧库：逐列探测，缺则补（source_tier 2026-10；parent/evidence 2026-10-18）
    # ⚠️ 补列必须在建索引之前——旧库 entries 表无 parent 列时，
    # `CREATE INDEX ... ON entries(parent)` 会直接抛 no such column（2026-10-18 踩过）。
    cols = [r[1] for r in conn.execute("PRAGMA table_info(entries)")]
    if "source_tier" not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN source_tier TEXT DEFAULT 'T4'")
    if "pub_date" not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN pub_date TEXT DEFAULT ''")
    for c in ("parent", "evidence"):
        if c not in cols:
            conn.execute(f"ALTER TABLE entries ADD COLUMN {c} TEXT DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_parent ON entries(parent)")
    conn.commit()


def _json_safe(obj):
    """Convert non-serializable types (date, etc.) to strings for JSON."""
    if isinstance(obj, list):
        return [_json_safe(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return obj


def rebuild(embed=True, log=print):
    """全量重建索引（源数据是唯一事实来源，索引可随时重建）。"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    init_schema(conn)
    entries = collect_entries()
    active = [e for e in entries if e["status"] != "deleted"]

    conn.execute("DELETE FROM entries")
    conn.execute("DELETE FROM fts_entries")
    try:
        conn.execute("DELETE FROM vec_entries")
    except sqlite3.OperationalError:
        pass

    vec_map = {}
    if embed:
        from fastembed import TextEmbedding
        model = TextEmbedding("BAAI/bge-small-zh-v1.5", cache_dir=os.path.join(BASE, "models"))
        texts = [f"{e['title']}。{e['summary']}。{'，'.join(map(str, e['tags']))}。{e['body'][:2000]}" for e in active]
        log(f"嵌入 {len(texts)} 个条目 …")
        vecs = list(model.embed(texts))
        vec_map = {e["id"]: v.tolist() for e, v in zip(active, vecs)}

    for e in entries:
        conn.execute(
            "INSERT INTO entries "
            "(id,title,module,tags,summary,confidence,status,updated,pub_date,sources,"
            " body,path,body_chars,source_tier,parent,evidence) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (e["id"], e["title"], e["module"], json.dumps(e["tags"], ensure_ascii=False),
             e["summary"], e["confidence"], e["status"], e["updated"],
             e.get("pub_date", ""),
             json.dumps(_json_safe(e["sources"]), ensure_ascii=False), e["body"],
             e["path"], len(e["body"]), e.get("source_tier", "T4"),
             e.get("parent", ""), e.get("evidence", "")),
        )
        if e["status"] != "deleted":
            fts_text = tokenize(e["title"] + " " + e["summary"] + " " + " ".join(map(str, e["tags"])) + " " + e["body"])
            conn.execute("INSERT INTO fts_entries VALUES (?,?,?)", (e["id"], e["title"], fts_text))
            if e["id"] in vec_map:
                import struct
                conn.execute(
                    "INSERT INTO vec_entries VALUES (?, ?)",
                    (e["id"], struct.pack(f"{DIM}f", *vec_map[e["id"]])),
                )
    conn.commit()

    meta = {
        "entries_total": len(entries),
        "entries_active": len(active),
        "embedded": len(vec_map),
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "dim": DIM,
    }
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    for k, v in meta.items():
        conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, str(v)))
    conn.commit()
    conn.close()
    log(f"重建完成: {meta}")
    return meta


if __name__ == "__main__":
    t0 = time.time()
    rebuild(embed="--no-embed" not in sys.argv)
    print(f"耗时 {time.time()-t0:.1f}s")
