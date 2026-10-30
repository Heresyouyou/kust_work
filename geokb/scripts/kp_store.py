# -*- coding: utf-8 -*-
"""知识点落盘内核 —— API 链路与 Agent 链路共用的**唯一**实现。

为什么要有这一层
----------------
原来 `kp_extract.py`（API）和 `agent_ingest.py kps`（Agent）各有一套
「渲染模板 + 字段清单 + 标签补位 + 归档 + kp_count 回写 + 索引」。
后果是同一篇论文两种模式产出的知识点**结构不同**：
  · API：summary 截 200、tags 补论文标签截 6、有 kp_score/kp_rule_score/kp_ai_value
  · Agent：summary 全文、tags 只有自有标签、缺三个评分字段、多一个错的 PDF url
检索加权、质量抽检、门户展示因此对不上 —— 用户要求「二者入库效果相同」，
根子在这里，不在提示词。

统一后两种模式只差一件事：**候选从哪来**（模型产 vs Agent 产）。
候选之后的一切（评分口径、字段、模板、归档、索引）走同一份代码。

对外接口
--------
    paper_meta(sha12)                     论文条目元数据（唯一权威来源）
    score_kp(kp, lang)                    规则分 + 60/40 融合（与 select 同口径）
    normalize(kp, meta, lang)             字段归一
    render(meta, kp, idx, extraction)     渲染 md（唯一模板）
    write(sha12, kps, extraction=…)       写盘 + 归档多余编号 + 回写 kp_count
    index(sha12, with_paper=False)        增量索引（同一嵌入文本构造）
    retire(sha12, keep_n)                 多余编号移入隔离区（rename，不删）
    patch_entry_kp_meta(sha12, n)         回写 kp_count / kp_version
"""
import glob
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))
sys.path.insert(0, os.path.join(BASE, "server"))

import kp_select  # noqa: E402

# GEOKB_KP_OUT 让测试写入临时目录，且**不回写**真实条目的 kp_count
KPOINTS_DIR = Path(os.environ.get("GEOKB_KP_OUT")
                   or os.path.join(BASE, "content", "kpoints"))
PAPERS_DIR = Path(BASE) / "content" / "papers"

EXTRACTION = {"api": "deepseek-kp", "agent": "agent-kp"}

_RE_FM = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)


# ─── 元数据 ────────────────────────────────────────────────────
def _rel_path(p) -> str:
    """NAS 绝对路径 → `papers_repo/...` 相对路径（门户靠它拼下载链接）。"""
    rel = str(p or "").replace("\\", "/")
    m = re.search(r"papers_repo/", rel)
    return rel[m.start():] if m else rel


def read_fm(path):
    """读 md 的 frontmatter → dict（失败返回 {}）。"""
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}
    m = _RE_FM.match(raw)
    if not m:
        return {}
    try:
        import yaml
        return yaml.safe_load(m.group(1)) or {}
    except Exception:  # noqa: BLE001
        return {}


def paper_meta(sha12):
    """论文条目的落盘元数据。**两种模式都从这里取**，不再各自拼。

    历史坑：Agent 侧曾自建 sources，url 写成 `/api/entry/kp-xxx/pdf`（指向知识点
    自身，必然 404）；这里统一取条目自带的 sources，PDF 链接才指向论文。
    """
    sha12 = sha12[:12]
    fm = read_fm(PAPERS_DIR / ("paper-%s.md" % sha12))
    src = fm.get("sources") or []
    if isinstance(src, dict):
        src = [src]
    path = (src[0] or {}).get("path") if src else ""
    # 旧条目常缺 journal/year（早期 Agent 写入没带），但 PDF 路径自带
    # `by_journal/<期刊>/<年>/` 或 `Addition/<年>/`——从路径反推，比读台账可靠
    # （台账没有 sha1 字段，只能靠标题模糊匹配，容易错配）。
    m = re.search(r"by_journal/([^/]+)/(\d{4})/", str(path or ""))
    m2 = re.search(r"/(?:Addition)/(\d{4})/", str(path or ""))
    journal = str(fm.get("journal") or (m.group(1) if m else ("Addition" if m2 else "")))
    year = str(fm.get("year") or (m.group(2) if m else (m2.group(1) if m2 else "")))
    if not src:
        src = [{"name": journal, "path": "", "url": "",
                "access": datetime.now().strftime("%Y-%m-%d")}]
    return {
        "sha12": sha12,
        "eid": "paper-%s" % sha12,
        "title": str(fm.get("title") or ""),
        "journal": journal,
        "year": year,
        "pub_date": str(fm.get("pub_date") or ""),
        "lang": str(fm.get("lang") or ""),
        "tags": [str(t) for t in (fm.get("tags") or [])][:8],
        "sources": src,
        "confidence": str(fm.get("confidence") or "C"),
        "path": path or "",
    }


# ─── 评分与归一 ────────────────────────────────────────────────
def score_kp(kp, lang=None):
    """规则分 + 60/40 融合。已由 `kp_select.select()` 裁过的候选原样返回。

    Agent 路径的候选没走过 select（是我直接写的），这里补算一遍，
    保证两种模式的 kp_score / kp_rule_score / kp_ai_value 口径一致。
    """
    c = dict(kp)
    if c.get("score") is not None and c.get("score_rule") is not None:
        return c
    sc = kp_select.score_candidate(c, lang or "zh")
    c.update(sc)
    rule = c["score"]
    ai = kp_select._ai_value(c)          # noqa: SLF001 同一仓库，口径必须同源
    c["score_rule"] = rule
    c["ai_value"] = ai
    if ai is not None:
        c["score"] = round(kp_select.W_RULE * rule
                           + kp_select.W_AI * (ai / 10.0), 3)
    c["verdict"] = kp_select._verdict(c)  # noqa: SLF001
    return c


def _clean(s):
    """去外层引号与换行：payload 里常把整句包在引号里，落 YAML 会变 title: "\\"…\\""。"""
    return str(s or "").strip().strip('"').strip("“”").strip().replace("\n", " ")


def normalize(kp, meta, lang=None):
    """字段归一：两种模式产出的候选都过这一道，之后结构必然一致。"""
    title = _clean(kp.get("title"))
    body = str(kp.get("body") or "").strip().replace("\r\n", "\n")
    if not title or not body:
        return None
    tags = [str(t).strip() for t in (kp.get("tags") or []) if str(t).strip()]
    # 自有标签优先、论文标签补位，总数收紧到 6：否则一篇论文的通用标签
    # 会把每条知识点刷成同一张脸，稀释检索区分度（API 侧原有规则，统一保留）
    all_tags = list(dict.fromkeys(tags + meta.get("tags", [])))[:6]
    sc = score_kp({**kp, "title": title, "body": body}, lang=lang or meta.get("lang"))
    return {
        "title": title,
        "body": body,
        "tags": all_tags,
        "evidence": str(kp.get("evidence") or "").strip(),
        "quote": str(kp.get("quote") or "").strip(),
        "type": kp.get("type") or kp_select.infer_type(title, body),
        "reuse": kp.get("reuse") or kp_select.infer_reuse(title, body),
        "why": str(kp.get("why") or "").strip()[:80],
        "confidence": kp.get("confidence") or meta.get("confidence") or "C",
        "score": sc.get("score"),
        "score_rule": sc.get("score_rule"),
        "ai_value": sc.get("ai_value"),
    }


# ─── 渲染（唯一模板） ──────────────────────────────────────────
def render(meta, kp, idx, extraction="agent-kp"):
    """渲染单条知识点 md，返回 (kid, text)。

    模板以 API 版为准（含 quote、条目回链、期刊年份）——它信息更全，
    Agent 版原来缺这些，合并时取并集而不是取交集。
    """
    kid = "kp-%s-%d" % (meta["sha12"], idx)
    today = datetime.now().strftime("%Y-%m-%d")
    fm = {
        "id": kid,
        "title": kp["title"],
        "module": "kpoints",
        "tags": kp["tags"],
        "summary": kp["body"][:200],
        "confidence": kp["confidence"],
        "status": "active",
        "updated": today,
        "sources": meta.get("sources") or [],
        "source_tier": "T3",
        "parent": meta.get("eid") or ("paper-%s" % meta["sha12"]),
        "evidence": kp["evidence"],
        "extraction": extraction,
        "kp_type": kp["type"],
        "kp_reuse": kp["reuse"],
        "kp_why": kp["why"] or None,
        "kp_score": kp["score"],
        "kp_rule_score": kp["score_rule"],
        "kp_ai_value": kp["ai_value"],
        "kp_version": kp_select.KP_VERSION,
    }
    if meta.get("journal"):
        fm["journal"] = meta["journal"]
    if meta.get("year"):
        fm["year"] = meta["year"]
    if meta.get("pub_date"):
        fm["pub_date"] = meta["pub_date"]

    import yaml
    head = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, width=10 ** 6).strip()
    doc = ["---", head, "---", "", "## 知识点", "", kp["body"], ""]
    if kp["evidence"]:
        doc += ["**证据行号**：%s" % kp["evidence"], ""]
    if kp["quote"]:
        doc += ["**原文依据**：%s" % kp["quote"], ""]
    doc += ["## 来源论文", "",
            "- %s（条目 `%s`）" % (meta.get("title") or "未知", fm["parent"])]
    if meta.get("journal") or meta.get("year"):
        doc += ["- 期刊/年份：%s %s" % (meta.get("journal", ""), meta.get("year", ""))]
    return kid, "\n".join(doc) + "\n"


# ─── 写盘 / 归档 / 回写 ───────────────────────────────────────
def retire(sha12, keep_n):
    """把编号 > keep_n 的旧文件移入 `state/kp_stale/<sha12>/`，返回被移走的 id。

    ⚠ 用 rename 而不是删除：本机有批量删除守卫（单轮 50 次即拦截，实测一次
    50 篇重跑在第 50 次删除上抛错、41 篇全部中止）。改名不算删除。
    1..keep_n 不需要动，写入时原地覆盖。
    """
    stale = Path(BASE) / "state" / "kp_stale" / sha12
    moved = []
    for f in glob.glob(str(KPOINTS_DIR / ("kp-%s-*.md" % sha12))):
        m = re.search(r"-(\d+)\.md$", f)
        if not m or int(m.group(1)) <= keep_n:
            continue
        stale.mkdir(parents=True, exist_ok=True)
        dst = stale / os.path.basename(f)
        try:
            if dst.exists():
                dst.unlink()
            os.replace(f, str(dst))
            moved.append(os.path.splitext(os.path.basename(f))[0])
        except OSError as e:
            print("  [WARN] 旧知识点归档失败 %s: %s" % (os.path.basename(f), e), flush=True)
    return moved


def _atomic_write_text(path, text):
    """原子写：先写 .tmp 再 os.replace。

    崩溃/断电时不会留下半截 md 被 scan 当成「已入库」。
    os.replace 在同卷上是原子操作（Windows 亦然）。
    """
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, str(path))


def patch_entry_kp_meta(sha12, count):
    """回写 kp_count / kp_version。scan 与门户据此判断「已按新算法处理过」，
    否则 0 条知识点的论文会被永久判成 P1（有条目、无知识点）。"""
    if os.environ.get("GEOKB_KP_OUT"):
        return                      # 测试写入临时目录时不要动真实条目
    p = PAPERS_DIR / ("paper-%s.md" % sha12[:12])
    if not p.exists():
        return
    raw = p.read_text(encoding="utf-8", errors="ignore")
    m = _RE_FM.match(raw)
    if not m:
        return
    fm, body = m.group(1), m.group(2)
    fm = re.sub(r"(?m)^kp_count:.*\n?", "", fm)
    fm = re.sub(r"(?m)^kp_version:.*\n?", "", fm)
    fm = fm.rstrip("\n") + "\nkp_count: %d\nkp_version: \"%s\"\n" % (
        count, kp_select.KP_VERSION)
    try:
        _atomic_write_text(p, "---\n" + fm + "---\n" + body)
    except OSError as e:
        print("  [WARN] 回写 kp_count 失败 %s: %s" % (sha12, e), flush=True)


def write(sha12, kps, extraction="agent-kp", meta=None, dry_run=False):
    """落盘一批知识点 → (ids, moved)。

    顺序：先覆盖写 1..n，再归档多余编号。反过来会在 n 变小时把新内容归档。
    """
    sha12 = sha12[:12]
    meta = meta or paper_meta(sha12)
    out = []
    for i, raw in enumerate(kps, start=1):
        kp = normalize(raw, meta)
        if not kp:
            print("  [WARN] kp%d 缺 title/body，跳过" % i, file=sys.stderr)
            continue
        kid, md = render(meta, kp, i, extraction)
        out.append((kid, md))
    if dry_run:
        return [k for k, _ in out], []
    KPOINTS_DIR.mkdir(parents=True, exist_ok=True)
    for kid, md in out:
        _atomic_write_text(KPOINTS_DIR / ("%s.md" % kid), md)
    moved = retire(sha12, len(out))
    patch_entry_kp_meta(sha12, len(out))
    return [k for k, _ in out], moved


def index(sha12, with_paper=False, prune=True):
    """增量索引。两种模式（API 批量 / Agent 单篇）都调这一个。

    与 `server/ingest.rebuild()` 共用同一段嵌入文本构造（`ioe.embed_text`），
    向量才与其他条目可比。
    """
    if os.environ.get("GEOKB_KP_OUT"):
        return 0                     # 测试目录不碰真实索引
    sha12 = sha12[:12]
    try:
        import ingest
        import index_one_entry as ioe
    except Exception as e:  # noqa: BLE001
        print("  [WARN] 索引模块不可用：%s" % e, flush=True)
        return 0
    files = sorted(glob.glob(str(KPOINTS_DIR / ("kp-%s-*.md" % sha12))))
    if with_paper:
        p = PAPERS_DIR / ("paper-%s.md" % sha12)
        if p.exists():
            files.append(str(p))
    entries = [e for e in (ingest.parse_entry(f) for f in files) if e]
    if not entries:
        if prune:
            ioe.prune(sha12)
        return 0
    try:
        conn = ingest.get_db()
        try:
            ingest.init_schema(conn)
            vecs = list(ioe.embed_model().embed([ioe.embed_text(e) for e in entries]))
            for e, v in zip(entries, vecs):
                ioe.upsert(conn, e, vec=v)
            conn.commit()
        finally:
            conn.close()      # 必须关：连接留着会持锁，同进程下一次写报 database is locked
    except Exception as e:  # noqa: BLE001
        print("  [WARN] 索引写入失败 %s: %s" % (sha12, e), flush=True)
        return 0
    if prune:
        try:
            ioe.prune(sha12)
        except Exception as e:  # noqa: BLE001
            print("  [WARN] 索引孤儿清理失败 %s: %s" % (sha12, e), flush=True)
    return len(entries)


# ─── 自检 ──────────────────────────────────────────────────────
def selfcheck():
    """落盘内核自检：模板渲染 + 字段齐全 + 空值处理。返回 (ok, [问题])。"""
    problems = []
    meta = {"sha12": "0123456789ab", "eid": "paper-0123456789ab",
            "title": "自检论文", "journal": "地学前缘", "year": "2025",
            "lang": "zh", "tags": ["矽卡岩", "白钨矿"], "confidence": "B",
            "sources": [{"name": "地学前缘", "path": "papers_repo/x.pdf",
                         "url": "", "access": "2026-10-23"}]}
    kid, md = render(meta, normalize({"title": "测试知识点", "body": "含 12.3 Ma 的结论",
                                      "tags": ["石榴石"], "value": 8,
                                      "reuse": "标定参照"}, meta), 1)
    for key in ("kp_type", "kp_reuse", "kp_score", "kp_rule_score", "kp_ai_value",
                "kp_version", "parent", "sources", "evidence", "summary"):
        if ("%s:" % key) not in md:
            problems.append("模板缺少字段 %s" % key)
    if "tags:\n- 石榴石\n- 矽卡岩" not in md and "- 石榴石" not in md:
        problems.append("tags 未按「自有标签优先 + 论文标签补位」渲染")
    if "papers_repo/x.pdf" not in md:
        problems.append("sources 未写入")
    if md.count("---") < 2:
        problems.append("frontmatter 分隔符异常")
    return (not problems), problems


if __name__ == "__main__":
    ok, probs = selfcheck()
    print("kp_store 自检：%s" % ("通过" if ok else "失败"))
    for p in probs:
        print("  [FAIL] %s" % p)
    sys.exit(0 if ok else 1)
