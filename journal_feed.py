# -*- coding: utf-8 -*-
"""期刊列表**共享数据层**（2026-10-17 新增）

为什么抽出成独立模块
--------------------
`/journal` 页面要同时挂在两个服务上，且要求「一比一复刻 + 二者同步更新」：

    open.kust.work/journal   →  5555 门户（D:/PostNews/Translate，Waitress）
    sjck.kust.work/journal   →  5556 GeoKB（D:/ZSK/geokb，纯 API 服务）

如果各写一份取数逻辑，任何一边改了窗口/排序/字段，另一边立刻漂移 ——
「同步更新」就变成一句空话。所以把**取数与数据整形**整段搬到这里，做成
**纯函数**（不 import flask、不碰 request），两个服务都 import 它：

    from journal_feed import build_feed
    data = build_feed(DATA_DIR)      # DATA_DIR = D:/PostNews

`build_feed()` 返回的结构即 /api/journals/list 与 /api/journal/list 的
**唯一载荷契约**；前端模板也是共享的一份（journal_page.html）。
改动只需落在这里，两端同时生效 —— 这才是真正的"同步"。

模块边界
--------
本模块只管「读 JSON → 合并去重 → 排序 → 注入 NAS 归档状态」。
**不写任何文件、不发起网络请求、不调用 DeepSeek**，纯读；
PDF 抓取、入库、翻译属于各自服务的路由层，不在这里。

数据来源（只读，D:/PostNews 下）
------------------------------
    journals.json               期刊元数据（国内 6 + 国际 8）
    journal_ledger.json       ★ 获取台账：每篇首次被本地获取的时间（窗口主时间源）
    journal_updates.json        最近一轮更新的文章（无时间戳 → 用文件 mtime）
    journal_batch_pending.json  待批量处理的一轮更新（有批次 date）
    journal_article_buffer.json 历史累积文章缓存（字段较简，仅 title/url）
    journal_doc_urls.json       期刊飞书速递文档链接
    journal_topic_archive.json  近 N 个月主题相关文献回溯归档
    journal_title_zh.json       英文标题的离线中文小字翻译（本机 llama.cpp 预生成）

为什么窗口用「获取时间」而不是「发布时间」
----------------------------------------
用户要的是"我手上能点开的链接"，不是"期刊方当天发布了什么"。
以**本地首次获取时间**为判据（journal_ledger.json 的 first_seen，首次写入后
永不改写），窗口才会随时间自然老化。若改用文件 mtime，journal_updates.json
每次运行都被重写会把时间"刷新"成今天，文章永远老化不掉，窗口退化成"显示全部"。
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

# ── 窗口与裁剪常量（两端共用，改一处即两端生效）──────────────────
# ★ 卡片窗口：只展示「本地近 N 天获取到」的文章链接（以本地日期时间为准）
WINDOW_DAYS = 60
# 单张卡片默认展示条数（其余折叠，前端可「展开全部」）
MAX_PER_CARD = 6
# 「展开全部」时单卡最多下发的条数（防载荷过大）。
# 取 200：当前最大单刊 70 篇，留足余量让「全部展开」不会静默截断；
# 万一某刊真的超过 200，前端会明确提示「另有 N 篇超出单卡展示上限」，不假装已全量。
MAX_EXPAND_PER_CARD = 200
# 主题归档视图单卡片最多展示条数（归档命中多的刊可展开更多）
MAX_TOPIC_PER_CARD = 8
# 「今日更新」板块单日最多展示条数（当天全刊汇总，超出部分明确提示不静默截断）
TODAY_MAX = 300

# 相关度分档：标签 / 阈值下限（与 journal_filter.STRONG_RELEVANCE_MIN_SCORE=8 对齐）
TIERS = [
    ('strong', '强相关', 19),
    ('mid', '相关', 12),
    ('low', '一般', 8),
]

# 国际 / 国内 的展示顺序与配色键
REGION_ORDER = [('国际', 'international'), ('国内', 'domestic')]

# NAS 论文仓库 / GeoKB 本地台账（注入归档状态用）
#
# ★ 必须走 UNC，不能写 `S:/`：本模块被 5555 的 Flask 调用，而 Flask 由 supervisor
#   以 RunLevel=HighestAvailable 拉起（High 完整性），`EnableLinkedConnections` 未设
#   → 提权进程**看不见用户映射的 S:**。症状是 `(NAS_REPO / rel).exists()` 恒为 False，
#   于是所有论文都被判成「未归档」、前端补抓按钮乱亮，日志却毫无异常。
#   唯一真相与完整说明见 D:/PostNews/nas_paths.py。
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from nas_paths import PAPERS_REPO as NAS_REPO  # noqa: E402

GEOKB_DIR = Path('D:/ZSK/geokb')
LEDGER_PATH = GEOKB_DIR / 'state' / 'papers_index.jsonl'
LEDGER_FALLBACK = NAS_REPO / 'index.jsonl'


def _tier_of(score):
    s = score if isinstance(score, (int, float)) else 0
    for key, _label, floor in TIERS:
        if s >= floor:
            return key
    return 'low'


# ── 台账缓存（NAS 的 SMB 读很慢，按 mtime+size 做进程内缓存）────────
_ledger_cache = {'sig': None, 'data': {}}
# URL 二级索引：norm_title(url) -> meta。标题会变（文章页 citation_title vs
# 期刊列表题录、中英文混排、出版社事后改题），URL（doi.org/10.xxxx）不会。
# 2026-10-18 实测 8 篇 sciengine：PDF 与台账都在，仅因两套标题 norm 后仍不同
# 而显示「未归档」。标题 miss 时按 URL 再查一次。
_ledger_url_idx = {}


def norm_title(s):
    """跨源同文判定键（与 geokb daily_archive_papers._norm_title 同规则）。

    ⚠️ 2026-10-19 修复：原先这里做了 `[:80]` 截断，导致同一篇论文
    「文章侧全称」与「台账侧被源站列表截断的简称」永远配不上
    （实例：'...phosphate-rich systems: integrated apatite in-situ REE-O-Sr
    signatures' 截断成 '...phosphaterichsystemsintegra'，台账存的是
    '...phosphate-rich systems'）→ 页面显示「未归档」、按钮不出现下载入口，
    但 NAS 上文件其实早就存在。
    现在保留完整键；截断型简称的兜底匹配见 `lookup_ledger()` 的前缀分支。
    """
    return re.sub(r'\W+', '', (s or '').lower())


def load_ledger():
    """{norm_title: 最新 meta}；文件未变直接用缓存。

    主副本在**本地** D:/ZSK/geokb/state/papers_index.jsonl —— NAS 上的
    S:/GeoKB/papers_repo/index.jsonl 被 QTS 独占锁定（open 'a' 与 os.replace
    均 PermissionError / WinError 5，持续不释放），且旧记录缺 eid 字段。
    读 NAS 旧文件仅作迁移期兜底。
    """
    sig = None
    for p in (LEDGER_PATH, LEDGER_FALLBACK):
        try:
            sig = (str(p), p.stat().st_mtime, p.stat().st_size)
            break
        except OSError:
            continue
    if sig is None:
        return _ledger_cache['data']
    if _ledger_cache['sig'] == sig and _ledger_cache['data']:
        return _ledger_cache['data']

    data = {}
    url_idx = {}
    for p in (LEDGER_FALLBACK, LEDGER_PATH):   # 先旧后新 → 本地覆盖旧
        if not p.exists():
            continue
        try:
            with open(p, encoding='utf-8', errors='replace') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        m = json.loads(line)
                    except Exception:
                        continue
                    k = norm_title(m.get('title'))
                    if not k:
                        continue
                    prev = data.get(k)
                    # 别让纯题录（metadata_only，无 repo_path）覆盖掉真 PDF 条目：
                    # 那会把已归档的文章误判成未归档，触发重复下载。
                    if prev is not None and is_pdf_entry(prev) and not is_pdf_entry(m):
                        continue
                    data[k] = m   # 后写覆盖先写：取最新一条
                    uk = norm_title(m.get('url'))
                    if uk:
                        prev_u = url_idx.get(uk)
                        # 不让纯题录覆盖已有 PDF 条目（与 data 的写入规则一致）
                        if (prev_u is not None and is_pdf_entry(prev_u)
                                and not is_pdf_entry(m)):
                            continue
                        url_idx[uk] = m
        except Exception:
            continue
    _ledger_cache['sig'] = sig
    _ledger_cache['data'] = data
    global _ledger_url_idx
    _ledger_url_idx = url_idx
    return data


def invalidate_ledger_cache():
    """强制下一次 load_ledger() 重建缓存。

    调用方：5555 的 POST /api/journals/fetch-pdf 补抓成功后。
    该路由在本服务进程内写完台账，若不同步失效缓存，共享层仍按旧 mtime/size
    命中缓存，刚抓到的这篇会在列表里继续显示「未归档」直到进程重启。
    5556 是独立进程，靠自己的 mtime 判定自动重建，无需（也不能）跨进程失效。
    """
    _ledger_cache['sig'] = None


# 前缀兜底的最小键长：短键做 startswith 容易把「Introduction to X」与
# 「Introduction to X and Y」误判成同文；≥40 个字符后这种巧合基本不存在。
_PREFIX_MIN = 40


def is_pdf_entry(meta):
    """台账条目是否代表「NAS 上真有 PDF」。

    题录条目（`source: metadata_only`，repo_path 为空、size=0）由 GeoKB 入库
    链路写入，用于检索但**没有 PDF**。若不区分，幂等判断会把它们当成
    「已归档」而跳过补抓 —— 实测 28 条这类条目中有 14 条落在 60 天窗口内，
    导致 journals 页永远显示「在线抓取」，而补抓脚本又因「已在 NAS」永不下载，
    两边都以为对方会处理，形成静默死角。
    """
    return bool(rel_of_meta(meta))


def lookup_ledger_url(url):
    """按 URL 键查台账（标题 miss 时的二级匹配）。只返回真有 PDF 的条目。

    为什么需要：台账 title 来自**文章页 citation_title**，feed 标题来自
    **期刊列表题录**，两者可能不同（中英文混排、出版社事后改题、措辞差异）。
    2026-10-18 实测 8 篇 sciengine：PDF 与台账俱全，仅因两套标题 norm 后
    仍不同，页面一直显示「在线抓取」，补抓脚本又反复重下。URL（doi.org/10.xxxx）
    是稳定标识，按它匹配可救回这类同文。
    """
    if not url:
        return None
    m = _ledger_url_idx.get(norm_title(url))
    return m if (m is not None and is_pdf_entry(m)) else None


def lookup_ledger(ledger, key, url=None):
    """按标题键查台账，带回退。**只返回真有 PDF 的条目**（见 is_pdf_entry）。

    顺序：
      1. 精确命中（绝大多数）
      2. 前缀互为包含，且**候选唯一** + **NAS 文件真实存在**。
         救回「文章全称 vs 台账截断简称」这类同文 —— 台账 title 常被源站
         列表截断（实例：'...phosphate-rich systems' 与全称
         '...systems: integrated apatite in-situ REE-O-Sr signatures'），
         旧版 `[:80]` 截断键让它们永远配不上，页面显示「未归档」、
         下载入口消失，而 NAS 上文件早就存在。
      3. URL 二级匹配（`url` 传入时）：标题变体（中英文/改题）的救生船。

    为什么不按「键长是否会被截断」做启发式：实测第二类失配的键只有 78 字符
    （未达 80），截断发生在**源站侧**而不是我们的 `[:80]`。故对所有 miss
    都试一遍；代价是每次 miss 遍历一次键集合（约 800 项，亚毫秒级），
    且用「候选唯一 + 文件存在」双重约束把误配压到近零。
    """
    if not key:
        return lookup_ledger_url(url) if url else None
    m = ledger.get(key)
    if m is not None:
        # 精确命中即同一篇：没 PDF 就是真没归档，不再拿前缀兜底去配别篇
        return m if is_pdf_entry(m) else None
    if len(key) >= _PREFIX_MIN:
        cands = [k for k in ledger
                 if len(k) >= _PREFIX_MIN and (k.startswith(key) or key.startswith(k))]
        if len(cands) == 1:
            m = ledger[cands[0]]
            rel = rel_of_meta(m)
            # 前缀命中必须落到真实文件，否则宁可显示未归档（按钮会去补抓，不会给死链）
            if rel and (NAS_REPO / rel).exists():
                return m
    return lookup_ledger_url(url) if url else None


def rel_of_meta(meta):
    """repo_path（S:/GeoKB/papers_repo/...）→ 仓库相对路径 by_journal/... 。

    台账里 repo_path 的分隔符取决于写入方（Windows 用 \\、也可能混 /），
    统一归一为 / 再切，避免 os.sep 依赖（也顺手去掉了一个 NameError 源）。
    """
    p = str((meta or {}).get('repo_path') or '').replace('\\', '/')
    marker = 'papers_repo/'
    i = p.find(marker)
    return p[i + len(marker):] if i >= 0 else ''


def attach_archive_state(articles):
    """给每篇文章注入归档状态，供前端决定按钮可用性：

         archived    True/False    原文 PDF 是否已在 NAS
         pdf_rel     str           仓库相对路径（回源用）
         eid         str|None      条目 id（阅读/翻译靠它查知识库）
         archived_on str           归档日期
         has_mono    bool          中文版已就绪
         has_dual    bool          双语对照版已就绪

    eid 直接取台账的 eid 字段（归档脚本与门户补抓都会写）。
    不用「读 NAS 文件重算 sha1」反推 —— 逐个读会把列表接口拖到分钟级，
    且 SMB 大文件读失败会连带整个列表 500。

    标题键走 `lookup_ledger()`（精确 + 前缀兜底 + URL 二级匹配）：台账侧的 title
    可能被源站列表截断（如 '...phosphate-rich systems'，全文实际有副标题），
    也可能是另一套标题（文章页 citation_title vs 期刊列表题录，中英文混排）；
    旧版靠 `[:80]` 截断键比较，这类同文会显示成「未归档」，下载入口消失。
    URL 匹配救回标题变体（2026-10-18：8 篇 sciengine 实测）。
    """
    ledger = load_ledger()
    for a in articles:
        meta = (lookup_ledger(ledger, norm_title(a.get('title')),
                              url=a.get('url')) or {})
        rel = rel_of_meta(meta)
        archived = bool(rel)
        a['archived'] = archived
        a['pdf_rel'] = rel
        a['archived_on'] = meta.get('date') or ''
        a['eid'] = meta.get('eid') or ''
        a['has_mono'] = bool(meta.get('has_mono'))
        a['has_dual'] = bool(archived and meta.get('has_dual'))


# ── 取数 ─────────────────────────────────────────────────────────
def read_json(root, name):
    p = Path(root) / name
    try:
        if p.exists():
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        return None
    return None


def file_mtime(root, name):
    """数据文件的最后写入时间 —— 作为该文件内容「被获取到」的时间代理。

    语义正确性：journal_updates.json 每次检测运行都被整体重写，内容就是那一轮
    新文章，所以「文件写入时刻」即这批文章的获取时刻；而且它会随时间变旧，
    文章能正常老化出窗口。（更准的来源是 journal_ledger.json 的 first_seen。）
    """
    try:
        p = Path(root) / name
        if p.exists():
            return datetime.fromtimestamp(p.stat().st_mtime)
    except Exception:
        pass
    return None


def parse_ts(v):
    """宽松解析时间戳（台账 first_seen / 批次 date / 文件 mtime）。失败返回 None。"""
    if isinstance(v, datetime):
        return v
    if not v:
        return None
    s = str(v).strip().replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(s[:19], fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(str(v))
    except Exception:
        return None


def norm_key(title, url):
    """跨源同文判定键：标题指纹 + URL 兜底。

    同一篇论文在官网是 `.../geology/article/54/9/901/731860/...`，走 CrossRef
    兜底时变成 `https://doi.org/10.1130/g54149.1` —— URL 完全不同，只能按标题判。
    """
    t = re.sub(r'\W+', '', (title or '').lower())[:72]
    if t:
        return 't:' + t
    u = re.sub(r'\W+', '', (url or '').lower())
    return ('u:' + u) if u else ''


def date_label(dt, now):
    """卡片上的获取日期：同年显示 MM-DD，跨年显示 YYYY-MM-DD。"""
    if not dt:
        return ''
    return dt.strftime('%m-%d') if dt.year == now.year else dt.strftime('%Y-%m-%d')


# ── 英文标题的中文小字翻译 ───────────────────────────────────────
# 由 D:/PostNews/journal_title_zh.py 离线预生成（本机 llama.cpp 翻译），
# 这里**只读**，不翻译、不联网 —— 缓存缺失时该条就没有 title_zh，
# 前端照常显示英文原文，不会报错也不会空白。
_title_zh_cache = {'mtime': 0, 'map': {}}


def title_zh_map(root):
    """读标题翻译缓存，按 mtime 做进程内缓存（避免每请求都读盘）。"""
    p = Path(root) / 'journal_title_zh.json'
    try:
        if not p.exists():
            return {}
        mt = p.stat().st_mtime
        if mt != _title_zh_cache['mtime']:
            with open(p, 'r', encoding='utf-8') as f:
                data = json.load(f) or {}
            m = {}
            for k, v in (data.get('titles') or {}).items():
                zh = (v or {}).get('zh') if isinstance(v, dict) else v
                if zh:
                    m[k] = zh.strip()
            _title_zh_cache['mtime'] = mt
            _title_zh_cache['map'] = m
        return _title_zh_cache['map']
    except Exception:
        return _title_zh_cache['map']


class _ZhResolver:
    """把 _zh_of 的调用包一层，避免把 root 到处传。"""

    def __init__(self, root):
        self._root = root
        self._map = None

    def __call__(self, title):
        # 键规则见 journal_title_zh.py::_key
        if not title:
            return ''
        if self._map is None:
            self._map = title_zh_map(self._root)
        k = re.sub(r'[^a-z0-9]', '', title.lower())
        return self._map.get(k, '')


def collect_recent(name, ledger, updates, pending, buffer_, ts_map,
                   cutoff, now, zh_of):
    """合并多源文章 → 解析「获取时间」→ 去重 → 按获取时间倒序 → 60 天窗口过滤。

    来源优先级（先到先得，同一篇不会重复添加）：台账 > 待推送批次 > 最近一轮更新 > 历史缓存。
    返回 (items, stats)，stats = {'undated': 无时间戳条数, 'expired': 超窗口条数}。
    """
    seen, out = set(), []
    stats = {'undated': 0, 'expired': 0}

    def push(lst, fallback_ts, source):
        for a in lst or []:
            if not isinstance(a, dict):
                continue
            title = (a.get('title') or '').strip()
            url = (a.get('url') or '').strip()
            if not title and not url:
                continue
            key = norm_key(title, url)
            if not key or key in seen:
                continue
            seen.add(key)
            dt = parse_ts(a.get('first_seen') or a.get('pushed_at')) or fallback_ts
            if dt is not None and dt < cutoff:
                stats['expired'] += 1
                continue
            if dt is None:
                # 解析不出时间：这些是"当前列表"里的存量，无法证明其超出窗口，
                # 保守起见保留（并计入 undated 统计，便于事后核查）。
                stats['undated'] += 1
            out.append({
                'title': title or url,
                'title_zh': zh_of(title),
                'url': url,
                'source': a.get('source') or source,
                'volume': str(a.get('volume') or ''),
                'issue': str(a.get('issue') or ''),
                'obtained_at': dt.isoformat(timespec='seconds') if dt else '',
                'obtained_label': date_label(dt, now),
                # 当天条目一律是「今天」，MM-DD 无信息量；「今日更新」板块用它显示时刻。
                # 60 天卡片仍用 obtained_label（跨日跨月，日期才有意义）。
                'obtained_hm': dt.strftime('%H:%M') if dt else '',
                'obtained_ts': dt.timestamp() if dt else 0.0,
            })

    led = ledger.get(name) if isinstance(ledger, dict) else None
    if led:
        push(led, None, 'ledger')
    pend = pending.get(name) or {}
    push(pend.get('new_articles') if isinstance(pend, dict) else pend,
         ts_map.get('pending'), 'pending')
    up = updates.get(name) or {}
    push(up.get('new_articles') if isinstance(up, dict) else up,
         ts_map.get('updates'), 'updates')
    push(buffer_.get(name) if isinstance(buffer_, dict) else None,
         ts_map.get('buffer'), 'buffer')

    out.sort(key=lambda x: x['obtained_ts'], reverse=True)
    return out, stats


def collect_topic(archive):
    """把主题归档按期刊名分组，返回 {刊名: [结构化条目]}（保持归档内的相关度降序）"""
    by_j = {}
    for a in archive.get('articles') or []:
        if not isinstance(a, dict):
            continue
        j = (a.get('journal') or '').strip()
        if not j:
            continue
        score = a.get('relevance_score')
        date = a.get('date') or ''
        date_est = a.get('date_est') or ''
        by_j.setdefault(j, []).append({
            'journal': j,
            'title': (a.get('title') or '').strip(),
            'url': (a.get('url') or '').strip(),
            'doi': (a.get('doi') or '').strip(),
            'date': date,
            'date_est': date_est,
            'date_label': date or (('≈' + date_est) if date_est else '日期待核实'),
            'date_basis': a.get('date_basis') or '',
            'volume': str(a.get('volume') or ''),
            'issue': str(a.get('issue') or ''),
            'pages': a.get('pages') or '',
            'score': score if isinstance(score, (int, float)) else 0,
            'tier': _tier_of(score),
            'keywords': (a.get('matched_keywords') or [])[:6],
            'region': a.get('region') or '',
        })
    return by_j


def build_feed(data_dir):
    """★ 唯一载荷构建入口：两个服务的 /api/...list 都调它。

    参数 data_dir = D:/PostNews。返回可直接 jsonify 的 dict。
    """
    # 以**本地时间**为准计算 60 天滚动窗口（机器时区 +0800，与用户所在地一致）
    now = datetime.now()
    cutoff = now - timedelta(days=WINDOW_DAYS)
    root = Path(data_dir)

    journals = read_json(root, 'journals.json') or {}
    updates = read_json(root, 'journal_updates.json') or {}
    docs = read_json(root, 'journal_doc_urls.json') or {}
    pending_all = read_json(root, 'journal_batch_pending.json') or {}
    pending = pending_all.get('batch_updates') or {}
    buffer_ = read_json(root, 'journal_article_buffer.json') or {}
    archive = read_json(root, 'journal_topic_archive.json') or {}

    # ★ 获取台账：本模块 60 天窗口的主时间源
    ledger_all = read_json(root, 'journal_ledger.json') or {}
    ledger = ledger_all.get('journals') or {}
    ledger_meta = ledger_all.get('_meta') or {}

    ts_map = {
        'pending': parse_ts(pending_all.get('date')) or file_mtime(root, 'journal_batch_pending.json'),
        'updates': file_mtime(root, 'journal_updates.json'),
        'buffer': file_mtime(root, 'journal_article_buffer.json'),
    }

    zh_of = _ZhResolver(root)
    topic_by_j = collect_topic(archive)
    topic_meta = archive.get('_meta') or {}

    groups, total_articles, updated_count = [], 0, 0
    topic_total, topic_journals, tier_count = 0, 0, {}
    undated_total, expired_total = 0, 0
    rows_all = []          # 全部刊卡片行（注入归档状态时遍历）

    # 「今日更新」板块：把当天（本地日期）获取到的文章跨刊汇总，
    # 仍以 obtained_ts（获取时间）为唯一判据，与下方 60 天窗口同源同口径。
    today_date = now.date()
    today_items = []

    for region_label, key in REGION_ORDER:
        rows = []
        for j in journals.get(key, []) or []:
            name = j.get('name') or ''
            articles, astat = collect_recent(name, ledger, updates, pending, buffer_,
                                              ts_map, cutoff, now, zh_of)
            # 标题右侧三按钮（下载/阅读/翻译）需要刊名 → handler 集按刊路由
            for a in articles:
                a.setdefault('journal', name)
            undated_total += astat['undated']
            expired_total += astat['expired']
            up = updates.get(name) or {}
            doc = docs.get(name) or {}
            doc_url = doc.get('url') if isinstance(doc, dict) else ''
            if not doc_url and isinstance(doc, str):
                doc_url = doc

            topic = topic_by_j.get(name) or []
            tier_count[name] = {}
            for t in topic:
                tier_count[name][t['tier']] = tier_count[name].get(t['tier'], 0) + 1

            site = (j.get('website') or '').strip()

            # 今日切片：无获取时间的条目无法证明是今天到的，一律不计入
            has_today = False
            today_count = 0
            for a in articles:
                ts = a.get('obtained_ts') or 0
                if not ts:
                    continue
                try:
                    if datetime.fromtimestamp(ts).date() != today_date:
                        continue
                except Exception:
                    continue
                has_today = True
                today_count += 1
                item = dict(a)
                item['journal'] = name
                item['journal_url'] = site
                item['region'] = region_label
                today_items.append(item)

            rows.append({
                'id': j.get('id') or name,
                'name': name,
                'name_en': j.get('name_en') or '',
                'short_name': j.get('short_name') or '',
                'name_cn': j.get('name_cn') or '',
                'category': j.get('category') or '',
                'publisher': j.get('publisher') or '',
                'frequency': j.get('frequency') or '',
                'issues_per_year': j.get('issues_per_year'),
                'level': j.get('level') or [],
                'priority': j.get('priority') or '',
                'website': site,
                'site_host': urlparse(site).netloc.replace('www.', '') if site else '',
                'status': up.get('status') or '',
                # —— 近 60 天获取到的文章（按获取时间倒序）——
                'article_total': len(articles),
                'articles': articles[:MAX_EXPAND_PER_CARD],
                'article_newest': articles[0]['obtained_label'] if articles else '',
                # 排序用：本刊 60 天内最新一次的获取时刻（articles 已按时间倒序）
                'article_newest_ts': articles[0]['obtained_ts'] if articles else 0.0,
                # 当天是否有货 / 当天几条 —— 卡片排序与「今日 +N」标记都靠它
                'has_today': has_today,
                'today_count': today_count,
                'article_undated': astat['undated'],
                'doc_url': doc_url,
                # — 主题归档 —
                'topic_total': len(topic),
                'topic_tiers': tier_count[name],
                'topic_articles': topic[:MAX_TOPIC_PER_CARD],
            })
            total_articles += len(articles)
            if articles:
                updated_count += 1
            topic_total += len(topic)
            if topic:
                topic_journals += 1

        # ★ 卡片排序：**当天有更新的刊排最前**，其余按「最近一次获取时间」倒序。
        #   此前是完全按 journals.json 的固定次序，最新的刊常常被压到屏幕外。
        #   用稳定排序（Python sort 稳定），键相同的卡片保持 journals.json 原始次序，
        #   所以同一档位内的相对位置不会乱跳 —— 刷新页面不会看到卡片"随机"换位。
        #   没有文章（article_newest_ts=0）的刊自然沉到本区域最底。
        rows.sort(key=lambda r: (0 if r['has_today'] else 1, -r['article_newest_ts']))

        if rows:
            groups.append({
                'region': region_label,
                'count': len(rows),
                'journals': rows,
            })
            rows_all.extend(rows)

    today_items.sort(key=lambda x: x['obtained_ts'], reverse=True)

    # ★ 归档状态注入（NAS → 用户 下载按钮的前提）：
    #   每篇文章带上 archived / pdf_rel / eid，前端据此决定「下载 PDF」是否可点。
    #   一次性读台账建索引，逐篇 O(1) 命中，避免 N 次 SMB 往返。
    for row in rows_all:
        attach_archive_state(row.get('articles') or [])
        attach_archive_state(row.get('topic_articles') or [])
    attach_archive_state(today_items)

    return {
        'ok': True,
        'total_journals': sum(g['count'] for g in groups),
        'total_articles': total_articles,
        'updated_journals': updated_count,
        'max_per_card': MAX_PER_CARD,
        'max_expand_per_card': MAX_EXPAND_PER_CARD,
        'max_topic_per_card': MAX_TOPIC_PER_CARD,
        # ★ 60 天窗口元信息（前端展示窗口边界，便于核对"获取时间"口径）
        'window': {
            'days': WINDOW_DAYS,
            'from': cutoff.date().isoformat(),
            'to': now.date().isoformat(),
            'now': now.strftime('%Y-%m-%d %H:%M'),
            'ledger_available': bool(ledger),
            'ledger_updated_at': ledger_meta.get('updated_at') or '',
            'undated_total': undated_total,
            'expired_total': expired_total,
        },
        'tiers': [{'key': k, 'label': l, 'floor': f} for k, l, f in TIERS],
        'topic': {
            'total': topic_total,
            'journals_with_hits': topic_journals,
            'window_from': topic_meta.get('window_from') or '',
            'window_to': topic_meta.get('window_to') or '',
            'generated_at': topic_meta.get('generated_at') or '',
            'min_score': topic_meta.get('min_score'),
            'candidate_count': topic_meta.get('candidate_count'),
            'date_exact': topic_meta.get('date_exact'),
            'date_estimated': topic_meta.get('date_estimated'),
            'date_missing': topic_meta.get('date_missing'),
            'available': bool(topic_total),
        },
        # 「今日更新」板块：当天（本地日期）跨刊汇总，与下方窗口同源同口径
        'today': {
            'date': now.strftime('%Y-%m-%d'),
            'total': len(today_items),
            'journal_count': len({i['journal'] for i in today_items}),
            'items': today_items[:TODAY_MAX],
            'hidden': max(0, len(today_items) - TODAY_MAX),
        },
        'groups': groups,
    }


if __name__ == '__main__':
    # 自检：直接跑本文件可快速核对载荷（不依赖任何 Flask 服务）
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else 'D:/PostNews'
    d = build_feed(root)
    print(json.dumps({
        'total_journals': d['total_journals'],
        'total_articles': d['total_articles'],
        'updated_journals': d['updated_journals'],
        'today_total': d['today']['total'],
        'topic_total': d['topic']['total'],
        'window': d['window']['from'] + ' ~ ' + d['window']['to'],
        'groups': [(g['region'], g['count']) for g in d['groups']],
    }, ensure_ascii=False, indent=2))
