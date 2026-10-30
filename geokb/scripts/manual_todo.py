# -*- coding: utf-8 -*-
"""人工补缺工作台 —— 「自动流程碰不到、只能人去官网下」的论文总池。

和 `inbox_todo.py`（批量入库）的分工
-----------------------------------
| | 批量入库 | 人工补缺（本模块） |
|---|---|---|
| 池子 | 当天没下到的 + 历史纯题录 | **被拦 / 被冻 / 被政策停抓** 的全部 |
| 时效 | 每日滚动，几天内清空 | 长期滞留，可能挂几个月 |
| 人的动作 | 补一篇是一篇 | **挑**：能下的下，下不了的标「无权」 |
| 关键矛盾 | 数量少 | 数量大（3800+），必须能分组+批量 |

为什么要单独一个页面而不是塞进批量入库：
批量入库是「今天没下到的」，几十条，一条条点没问题。
这里 3800+ 条，其中 3056 条是 SD 全线停抓。平铺出来人只会关掉页面。
所以本模块的**第一职责是把池子切成能干活的小块**：按源站 × 处理方式分组，
每组给一句「这组该怎么办」，再配批量打开 / 批量标记。

六个桶（按「人能不能下手」排，不按失败码排）
------------------------------------------
| bucket | 中文 | 人能不能下到 | 页面默认 |
|---|---|---|---|
| blocked | 风控拦截 | 能 —— 浏览器过人机验证即可 | 默认显示 |
| broken  | 本地故障 | 能 —— PDF 在，是我们代码坏了 | 默认显示 |
| policy  | 政策停抓 | 能 —— 官网有，是我们主动放弃 | 默认显示 |
| wall    | 订阅墙 | 不一定 —— 看有没有权限 | 折叠 |
| dead    | 源头无全文 | 不能 —— 源站自己就没传 | 折叠 |
| legacy  | 历史遗留 | 不一定 —— 早期只存了题录 | 折叠 |

⚠️ 判定顺序：**冻结表 > 政策 > 错误码**。
   理由：被 Cloudflare 拦住时，`paywalled`/`not_pdf` 全是假信号（MEMORY 第 7 节）；
   而 SD 全线是被我们主动停抓的，它报什么错都不影响「这站现在不抓」。

数据源（四路合并，同 key 去重）
-----------------------------
| 池 | 文件 | 含义 |
|---|---|---|
| A | `state/history_manifest_frozen.jsonl` | 已物理移出的冻结站条目（793） |
| B | `state/history_manifest.jsonl` | 仍在自动队列里的（含 SD 3056） |
| C | `state/journal_no_pdf.jsonl` | 每日管线当天没下到的 |
| D | `state/papers_index.jsonl` | 判「已入库」+ 早期 metadata_only |

状态流转
--------
    pending ──(人下载后丢进投放箱)──> downloaded ──(12:01/18:01 自动入库)──> 已入库，出池
       └────(标「无权访问」)────────> skipped（默认隐藏，可切换查看）

⚠️ 本模块**不往 sys.path 插 scripts**。5556 用 importlib 按文件路径加载它；
   一旦插入 scripts，`scripts/ingest.py` 会遮住 `server/ingest.py`，整站检索崩
   （MEMORY 第 2 节）。兄弟模块一律走 `_sibling()` 按路径加载。

CLI
---
    python scripts/manual_todo.py                # 终端看分组摘要
    python scripts/manual_todo.py --json         # 完整快照
    python scripts/manual_todo.py --host gsw     # 只看某个源站
    python scripts/manual_todo.py --mark KEY --state skipped
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
STATE = BASE / "state"
SCRIPTS = Path(__file__).resolve().parent

LEDGER = STATE / "papers_index.jsonl"
MANIFEST = STATE / "history_manifest.jsonl"
FROZEN_MANIFEST = STATE / "history_manifest_frozen.jsonl"
NO_PDF = STATE / "journal_no_pdf.jsonl"
MARKS = STATE / "manual_marks.json"
FREEZE_TABLE = STATE / "frozen_sources.json"
INBOX = BASE / "inbox" / "papers"

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.I)


# ─── 兄弟模块加载（按文件路径，绝不插 sys.path）───────────────────────────
_MODS = {}


def _sibling(alias: str, filename: str):
    """惰性加载同目录模块。加载失败返回 None，调用方负责降级。"""
    if alias in _MODS:
        return _MODS[alias]
    mod = None
    try:
        spec = importlib.util.spec_from_file_location(
            "geokb_mt_" + alias, str(SCRIPTS / filename))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception:  # noqa: BLE001
        mod = None
    _MODS[alias] = mod
    return mod


def _tools():
    """inbox_todo：复用它的 key 归一 / 已归档判据 / 投放箱探测，口径不分叉。"""
    return _sibling("inbox_todo", "inbox_todo.py")


_AB = {"mod": None, "tried": False}


def pace_host(url: str = "", doi: str = "") -> str:
    """限速桶 host（GSW 三刊归一 / DOI 前缀换算），与派发、限速同一套。"""
    if not _AB["tried"]:
        _AB["mod"] = _sibling("antibot", "antibot_session.py")
        _AB["tried"] = True
    mod = _AB["mod"]
    if mod is not None:
        try:
            return mod.pace_host(url or "", doi or "")
        except Exception:  # noqa: BLE001
            pass
    try:
        from urllib.parse import urlparse
        return (urlparse(url or "").netloc or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def is_sd(doi: str = "", url: str = "", journal: str = "") -> bool:
    mod = _sibling("sd_policy", "sd_policy.py")
    if mod is None:
        d = (doi or "").lower().lstrip()
        return d.startswith("10.1016/")
    try:
        return bool(mod.is_sd(doi or "", url or "", journal or ""))
    except Exception:  # noqa: BLE001
        return False


def frozen_hosts() -> dict:
    """{host: rec} —— 直接读冻结表（含「最短冻结期未满」判定）。"""
    mod = _sibling("source_freeze", "source_freeze.py")
    if mod is not None:
        try:
            return mod.load(force=True) or {}
        except Exception:  # noqa: BLE001
            pass
    try:
        raw = json.loads(FREEZE_TABLE.read_text(encoding="utf-8"))
        hosts = raw.get("hosts") or {}
        return hosts if isinstance(hosts, dict) else {}
    except (OSError, ValueError):
        return {}


# ─── 桶定义 ──────────────────────────────────────────────────────────────
BUCKET_ORDER = ("blocked", "broken", "policy", "wall", "dead", "legacy")
BUCKET_CN = {
    "blocked": "风控拦截",
    "broken": "本地故障",
    "policy": "政策停抓",
    "wall": "订阅墙",
    "dead": "源头无全文",
    "legacy": "历史遗留",
}
BUCKET_HINT = {
    "blocked": "机器被 Cloudflare 挡在门外，浏览器点进去通常能过人机验证",
    "broken": "PDF 在源站是有的，是我们自己的 handler 出错，人工下最快",
    "policy": "Elsevier 端点被内容防护拦死，自动化已放弃，官网照样能下",
    "wall": "需要订阅权限；没有权限就标「无权访问」，别在这类上耗时间",
    "dead": "源站自己就没上传全文（多为最新网络首发），只能等或换渠道",
    "legacy": "早期口径只存了题录，全文从没下过",
}
# 默认展示哪些桶：能下手的排前面且默认展开，下不了的折叠
BUCKET_OPEN = {"blocked": True, "broken": True, "policy": True,
               "wall": False, "dead": False, "legacy": False}
BUCKET_RANK = {b: i for i, b in enumerate(BUCKET_ORDER)}

REASON_CN = {
    "blocked_captcha": "源站弹人机验证",
    "paywalled": "付费墙",
    "not_open_access": "非开放获取",
    "no_pdf_url": "页面无全文链接",
    "err_exception": "请求异常",
    "landing_no_pdf": "落地页无全文",
    "timeout": "请求超时",
    "err_index": "本地索引构建失败",
    "err_parse": "页面解析失败",
    "err_item_timeout": "单篇超时",
    "guard_fail": "PDF 校验未过",
    "no_fulltext": "源站没有全文",
    "not_found": "文章页 404",
    "not_pdf": "返回的不是 PDF",
    "not_pdf_office": "返回的不是 PDF",
}

# 源站中文名（分组卡片上好认）
HOST_CN = {
    "pubs.geoscienceworld.org": "GSW 平台（GSA / 美国矿物学家 / 经济地质）",
    "academic.oup.com": "Oxford Academic",
    "link.springer.com": "Springer",
    "www.sciencedirect.com": "ScienceDirect（Elsevier）",
    "sciencedirect.com": "ScienceDirect（Elsevier）",
    "dgyk.cbpt.cnki.net": "大地构造与成矿学",
    "www.kcdz.ac.cn": "矿床地质",
    "www.sciengine.com": "SciEngine（中国科技期刊）",
    "www.earthsciencefrontiers.net.cn": "地学前缘",
    "www.geojournals.cn": "地质期刊集群",
}


# ─── 小工具 ──────────────────────────────────────────────────────────────
def _jsonl(path: Path):
    if not path.exists():
        return []
    out = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return out


def _clean_err(s: str) -> str:
    """traceback 压成一行错误码。和 inbox_todo 同一套规则，输出保持一致。"""
    t = _tools()
    if t is not None:
        try:
            return t._clean_reason(s)
        except Exception:  # noqa: BLE001
            pass
    s = (s or "").strip()
    if "Traceback" in s:
        for line in reversed([x.strip() for x in s.splitlines() if x.strip()]):
            if re.search(r"\b\w*(Error|Exception)\b", line):
                return line[:140]
    return s[:140]


def _reason_cn(err: str) -> str:
    e = (err or "").strip()
    if not e:
        return ""
    # 冻结表里的原因本来就是中文人话（如「Cloudflare 人机挑战」），
    # 直接透出，别按错误码表查成空串。
    if re.search(r"[\u4e00-\u9fa5]", e):
        return e[:60]
    if re.search(r"\b\w*(Error|Exception)\b", e):
        return REASON_CN["err_exception"]
    code = re.split(r"[:\s]", e, maxsplit=1)[0]
    return REASON_CN.get(code, "")


def _bucket_of(err: str, frozen_kind: str, sd_flag: bool) -> str:
    """判定顺序：冻结表 > 政策 > 错误码。

    冻结表最权威 —— 那是已经定性过的结论（含「被 CF 拦住」时 paywalled 不算数的处理）。
    SD 次之 —— 全线主动停抓，具体报什么错都不改变「这站不抓」。
    """
    if frozen_kind in ("blocked", "wall", "broken", "dead"):
        return frozen_kind
    if sd_flag:
        return "policy"
    e = (err or "").lower()
    if not e:
        return "legacy"
    if "captcha" in e or "cf challenge" in e or "cf_challenge" in e \
            or "http_403" in e or "http_429" in e or " 403" in e:
        return "blocked"
    if "paywall" in e or "not_open_access" in e:
        return "wall"
    if "no_fulltext" in e or "not_found" in e or "404" in e \
            or "not_pdf" in e or "no_pdf_url" in e or "landing_no_pdf" in e:
        return "dead"
    return "broken"


def _page_url(doi: str, url: str) -> str:
    """人点进去的入口：有 DOI 走 doi.org（自动跳出版社，最稳），否则用原链接。"""
    if doi:
        return "https://doi.org/" + doi
    return url or ""


def _year_of(rec: dict) -> str:
    y = str(rec.get("year") or "").strip()
    if y[:4].isdigit() and len(y) >= 4:
        return y[:4]
    d = str(rec.get("date") or "")
    if d[:4].isdigit():
        return d[:4]
    return ""


# ─── 人工标记 ────────────────────────────────────────────────────────────
def load_marks() -> dict:
    try:
        raw = json.loads(MARKS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = raw.get("items") if isinstance(raw, dict) else None
    return items if isinstance(items, dict) else {}


def _save_marks(items: dict):
    STATE.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "updated": datetime.now().isoformat(timespec="seconds"),
        "note": "人工补缺页面的状态：downloaded=已下载待入库 / skipped=无权访问",
        "items": items,
    }
    tmp = str(MARKS) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, str(MARKS))


def mark(key: str, state: str = "downloaded") -> dict:
    """改一条的状态。state ∈ pending / downloaded / skipped。"""
    key = (key or "").strip()
    if not key:
        raise ValueError("key 不能为空")
    if state not in ("pending", "downloaded", "skipped"):
        raise ValueError("state 只能是 pending / downloaded / skipped")
    items = dict(load_marks())
    if state == "pending":
        items.pop(key, None)
    else:
        items[key] = {"state": state, "ts": datetime.now().isoformat(timespec="seconds")}
    _save_marks(items)
    return {"key": key, "state": state, "total": len(items)}


def mark_many(keys, state: str = "downloaded") -> dict:
    """批量改状态。一次写盘，避免 N 次 IO。"""
    if state not in ("pending", "downloaded", "skipped"):
        raise ValueError("state 只能是 pending / downloaded / skipped")
    items = dict(load_marks())
    ts = datetime.now().isoformat(timespec="seconds")
    n = 0
    for k in keys or []:
        k = (k or "").strip()
        if not k:
            continue
        if state == "pending":
            if items.pop(k, None) is not None:
                n += 1
        else:
            items[k] = {"state": state, "ts": ts}
            n += 1
    _save_marks(items)
    return {"state": state, "changed": n, "total": len(items)}


# ─── 主逻辑 ──────────────────────────────────────────────────────────────
def _collect():
    """四路合并成候选条目。返回 list[dict]（未判状态）。"""
    t = _tools()
    if t is None:
        raise RuntimeError("inbox_todo.py 加载失败，无法归一 key")
    frz = frozen_hosts()

    bucket_map = {}     # key -> 条目

    def put(key, rec, pool, host, bucket):
        if not key or key == "u:":
            return
        old = bucket_map.get(key)
        if old is not None and BUCKET_RANK.get(bucket, 9) >= BUCKET_RANK.get(old["bucket"], 9):
            # 同一篇多池命中时保留「更该人工处理」的那条（桶更靠前）
            return
        raw_reason = _clean_err(rec.get("last_error") or rec.get("reason") or "")
        # 冻结池里的历史脏值：source_of 早期不认识 doi.org 的出版社前缀，
        # last_error 记成 "skipped: no url / unknown source"（当时根本没发请求），
        # 与「被 Cloudflare 拦」毫无关系。这类改用冻结表自己的原因，
        # 否则人工点开会以为是我们解析出错。
        if pool == "frozen" and (not raw_reason or "unknown source" in raw_reason
                                 or raw_reason.startswith("skipped")):
            raw_reason = (frz.get(host) or {}).get("reason") or raw_reason
        bucket_map[key] = {
            "key": key,
            "title": (rec.get("title") or "").strip(),
            "journal": (rec.get("journal") or "").strip(),
            "year": _year_of(rec),
            "date": (str(rec.get("date") or ""))[:10],
            "doi": t.doi_of(rec.get("url", ""), rec.get("doi", "")),
            "url": (rec.get("url") or "").strip(),
            "entry_url": _page_url(t.doi_of(rec.get("url", ""), rec.get("doi", "")),
                                   (rec.get("url") or "").strip()),
            "host": host,
            "bucket": bucket,
            "bucket_cn": BUCKET_CN.get(bucket, bucket),
            "reason": raw_reason,
            "reason_cn": _reason_cn(raw_reason),
            "attempts": int(rec.get("attempts") or 0),
            "captcha_fails": int(rec.get("captcha_fails") or 0),
            "reach_p": float(rec.get("reach_p") or 0) or 0.0,
            "pool": pool,
            "soft_fails": int(rec.get("soft_fails") or 0),
        }

    # A. 已物理移出的冻结站条目
    for r in _jsonl(FROZEN_MANIFEST):
        h = pace_host(r.get("url", ""), r.get("doi", ""))
        fk = (frz.get(h) or {}).get("kind") or ""
        err = r.get("last_error") or ""
        sd = is_sd(r.get("doi", ""), r.get("url", ""), r.get("journal", ""))
        key = t.entry_key(r.get("title", ""), r.get("doi", ""), r.get("url", ""))
        put(key, r, "frozen", h, _bucket_of(err, fk, sd))

    # B. 仍在自动队列里的（SD 3056 走这里）
    for r in _jsonl(MANIFEST):
        h = pace_host(r.get("url", ""), r.get("doi", ""))
        fk = (frz.get(h) or {}).get("kind") or ""
        err = r.get("last_error") or ""
        sd = is_sd(r.get("doi", ""), r.get("url", ""), r.get("journal", ""))
        if not (sd or fk or err):
            continue        # 干净待办 → 归自动流程，不进人工池
        key = t.entry_key(r.get("title", ""), r.get("doi", ""), r.get("url", ""))
        put(key, r, "manifest", h, _bucket_of(err, fk, sd))

    # C. 当天没下到的
    for r in _jsonl(NO_PDF):
        h = pace_host(r.get("url", ""), r.get("doi", ""))
        fk = (frz.get(h) or {}).get("kind") or ""
        err = r.get("reason") or ""
        sd = is_sd(r.get("doi", ""), r.get("url", ""), r.get("journal", ""))
        key = t.entry_key(r.get("title", ""), r.get("doi", ""), r.get("url", ""))
        put(key, r, "no_pdf", h, _bucket_of(err, fk, sd))

    # D. 台账里只有题录的（早期遗留）
    for r in _jsonl(LEDGER):
        if r.get("source") != "metadata_only":
            continue
        h = pace_host(r.get("url", ""), r.get("doi", ""))
        sd = is_sd(r.get("doi", ""), r.get("url", ""), r.get("journal", ""))
        key = t.entry_key(r.get("title", ""), r.get("doi", ""), r.get("url", ""))
        put(key, r, "ledger", h, "policy" if sd else "legacy")

    return list(bucket_map.values())


def snapshot(with_items: bool = True, limit: int = 0) -> dict:
    """页面快照。counts / groups / items 三段。"""
    t = _tools()
    if t is None:
        raise RuntimeError("inbox_todo.py 加载失败")
    ledger = _jsonl(LEDGER)

    # 已入库判据：台账里有真 PDF（必须看 repo_path，只看标题会把纯题录当已下载）
    have = {}
    for r in ledger:
        if not t.has_real_pdf(r):
            continue
        for k in (t.entry_key(r.get("title", ""), r.get("doi", ""), r.get("url", "")),
                  "t:" + t.norm_title_key(r.get("title", ""))[:160]):
            if k and k not in ("t:", "u:"):
                have.setdefault(k, r)

    marks = load_marks()

    # 投放箱探测（复用批量入库的实现：按 PDF 头解析 DOI/标题，有缓存与预算）
    probe_by_key = {}
    try:
        for p in t.inbox_probes() or []:
            if p.get("key") and p["key"] != "u:":
                probe_by_key.setdefault(p["key"], p)
    except Exception:  # noqa: BLE001
        pass

    pending, downloaded, skipped, archived = [], [], [], 0
    for it in _collect():
        hit = have.get(it["key"]) or have.get(
            "t:" + t.norm_title_key(it["title"])[:160])
        if hit:
            archived += 1
            continue
        mk = marks.get(it["key"]) or {}
        pr = probe_by_key.get(it["key"])
        if pr:
            it["state"] = "downloaded"
            it["file"] = pr.get("name", "")
            it["file_size"] = pr.get("size", 0)
            it["why"] = "投放箱里已找到这篇 PDF"
            downloaded.append(it)
            continue
        if mk.get("state") == "skipped":
            it["state"] = "skipped"
            it["marked_at"] = mk.get("ts", "")
            skipped.append(it)
            continue
        if mk.get("state") == "downloaded":
            it["state"] = "downloaded"
            it["marked_at"] = mk.get("ts", "")
            it["why"] = "已手动勾选「已下载」"
            downloaded.append(it)
            continue
        it["state"] = "pending"
        it["entry_url"] = _page_url(it["doi"], it["url"])
        pending.append(it)

    for b in (pending, downloaded, skipped):
        # 三次稳定排序（从次要到主要）：桶 → 可达概率降序 → 年份降序。
        # 效果：能下手的桶排前面，桶内先给成功率高的、新的。
        b.sort(key=lambda x: (x.get("year") or ""), reverse=True)
        b.sort(key=lambda x: -(x.get("reach_p") or 0))
        b.sort(key=lambda x: BUCKET_RANK.get(x["bucket"], 9))

    # 分组：host × bucket
    groups = {}
    for it in pending:
        gk = "%s|%s" % (it["host"] or "-", it["bucket"])
        g = groups.setdefault(gk, {
            "id": gk,
            "host": it["host"] or "-",
            "host_cn": HOST_CN.get(it["host"], ""),
            "bucket": it["bucket"],
            "bucket_cn": BUCKET_CN.get(it["bucket"], it["bucket"]),
            "hint": BUCKET_HINT.get(it["bucket"], ""),
            "n": 0,
            "journals": {},
            "done": 0,
            "years": {},
        })
        g["n"] += 1
        g["journals"][it["journal"]] = g["journals"].get(it["journal"], 0) + 1
        g["years"][it["year"]] = g["years"].get(it["year"], 0) + 1

    # 本组已补数（downloaded 里同 host+bucket 的）
    for it in downloaded:
        gk = "%s|%s" % (it["host"] or "-", it["bucket"])
        if gk in groups:
            groups[gk]["done"] += 1

    glist = sorted(groups.values(),
                   key=lambda g: (BUCKET_RANK.get(g["bucket"], 9), -g["n"]))
    for g in glist:
        g["journals"] = sorted(g["journals"].items(), key=lambda x: -x[1])[:6]
        g["years"] = sorted([y for y in g["years"] if y], reverse=True)[:8]

    by_bucket = {}
    for it in pending:
        by_bucket[it["bucket"]] = by_bucket.get(it["bucket"], 0) + 1

    items = pending if limit <= 0 else pending[:limit]

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": {
            "pending": len(pending),
            "downloaded": len(downloaded),
            "skipped": len(skipped),
            "archived": archived,
            "by_bucket": by_bucket,
            "by_host": _count_by_host(pending),
            "inbox_files": _inbox_files(),
        },
        "buckets": [{"id": b, "cn": BUCKET_CN[b], "hint": BUCKET_HINT[b],
                     "open": BUCKET_OPEN[b], "n": by_bucket.get(b, 0)}
                    for b in BUCKET_ORDER],
        "groups": glist,
        "items": items,
        "downloaded_items": downloaded,
        "skipped_items": skipped[:200],
        "inbox": {"dir": str(INBOX),
                  "files": [{"name": p.get("name"), "size": p.get("size")}
                            for p in list(probe_by_key.values())[:50]]},
    }


def _count_by_host(items):
    out = {}
    for it in items:
        out[it["host"]] = out.get(it["host"], 0) + 1
    return out


def _inbox_files():
    n = 0
    try:
        for p in INBOX.glob("*.pdf"):
            if p.is_file():
                n += 1
    except OSError:
        pass
    return n


def summary(snap: dict = None) -> dict:
    """轻量摘要（不含 items）：首屏只传这个，几十 KB。"""
    snap = snap if snap is not None else snapshot(with_items=False)
    out = dict(snap)
    out.pop("items", None)
    out.pop("downloaded_items", None)
    out.pop("skipped_items", None)
    return out


def query(snap: dict, group: str = "", bucket: str = "", host: str = "",
          q: str = "", year: str = "", journal: str = "", offset: int = 0,
          limit: int = 80, state: str = "pending") -> dict:
    """在快照上做筛选 + 分页。group 形如 `host|bucket`。

    分页而不是全量下发：3885 条 2.19MB，两端都没有 gzip，
    公网/手机端会明显卡；按组每次 80 条约 60KB，切组无感。
    """
    src = {"pending": snap.get("items") or [],
           "downloaded": snap.get("downloaded_items") or [],
           "skipped": snap.get("skipped_items") or []}.get(state)
    if src is None:
        raise ValueError("state 只能是 pending / downloaded / skipped")
    rows = src
    if group:
        gh, _, gb = group.partition("|")
        rows = [x for x in rows if (x.get("host") or "-") == gh
                and (not gb or x.get("bucket") == gb)]
    if bucket:
        rows = [x for x in rows if x.get("bucket") == bucket]
    if host:
        h = host.lower()
        rows = [x for x in rows if h in (x.get("host") or "").lower()]
    if year:
        rows = [x for x in rows if (x.get("year") or "") == str(year)]
    if journal:
        # 按刊细分：SD 池 3053 条里 OGR 占 2403，不切开根本没法看
        rows = [x for x in rows if (x.get("journal") or "") == journal]
    if q:
        needle = q.strip().lower()
        rows = [x for x in rows
                if needle in (x.get("title") or "").lower()
                or needle in (x.get("journal") or "").lower()
                or needle in (x.get("doi") or "").lower()]
    total = len(rows)
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = max(1, min(500, int(limit)))
    except (TypeError, ValueError):
        limit = 80
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "state": state,
        "items": rows[offset:offset + limit],
    }


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="人工补缺池（被拦/被冻/被政策停抓）")
    ap.add_argument("--json", action="store_true", help="输出完整快照")
    ap.add_argument("--host", default="", help="只看某个源站（子串匹配）")
    ap.add_argument("--bucket", default="", help="只看某个桶")
    ap.add_argument("--limit", type=int, default=0, help="JSON 里最多带几条 items")
    ap.add_argument("--mark", default="", help="改某条状态")
    ap.add_argument("--state", default="downloaded",
                    choices=["pending", "downloaded", "skipped"])
    args = ap.parse_args(argv)

    if args.mark:
        print(json.dumps(mark(args.mark, args.state), ensure_ascii=False))
        return 0

    snap = snapshot(limit=args.limit)
    if args.json:
        print(json.dumps(snap, ensure_ascii=False, indent=1))
        return 0

    c = snap["counts"]
    print("待补 %d ｜ 已下载待入库 %d ｜ 无权访问 %d ｜ 已自动归档(出池) %d ｜ 投放箱 %d 个 PDF"
          % (c["pending"], c["downloaded"], c["skipped"], c["archived"], c["inbox_files"]))
    print("投放目录：%s\n" % snap["inbox"]["dir"])

    print("── 按处理方式 ─────────────────────────────────────────")
    for b in snap["buckets"]:
        if b["n"]:
            print("  %-6s %-8s %5d 条   %s"
                  % (b["id"], b["cn"], b["n"], b["hint"]))

    print("\n── 按源站分组（页面工作流单位）────────────────────────")
    for g in snap["groups"]:
        if args.host and args.host.lower() not in g["host"].lower():
            continue
        if args.bucket and args.bucket != g["bucket"]:
            continue
        print("  %-32s %-8s %5d 条  已补 %d   %s"
              % (g["host"], g["bucket_cn"], g["n"], g["done"],
                 HOST_CN.get(g["host"], "")))
        if g["journals"]:
            print("      刊：" + " / ".join("%s %d" % (j or "-", n)
                                           for j, n in g["journals"][:4]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
