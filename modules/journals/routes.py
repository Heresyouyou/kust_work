# -*- coding: utf-8 -*-
"""期刊列表模块

把 PostNews 现有的期刊检索清单做成一屏可用的卡片墙，提供两个视图：

  ① 近 60 天 —— 本地**近 60 天获取到**的文章链接（本地日期时间为准）
     卡片标题 → 期刊首页（新窗口直达官网）
     卡片内   → 已获取文章链接（点击进 DOI / 原文），带获取日期
     卡片底   → 飞书速递文档（若该期刊已生成）

  ② 主题归档 —— 近 2 个月目标期刊中与 filter 主题相关的命中文献
     （来自 journal_backfill.py 的回溯归档，按相关度分档着色）

为什么窗口是「获取时间」而不是「发布时间」
----------------------------------------
用户要的是"我手上能点开的链接"，不是"期刊方当天发布了什么"。
因此以**本地首次获取时间**为判据，由 journal_ledger.json 提供 ——
该台账首次写入 first_seen 后永不改写，窗口才会随时间自然老化。
（若改用文件 mtime，journal_updates.json 每次运行被重写会把时间"刷新"成今天，
文章永远老化不掉，窗口就退化成"显示全部"。）

时间解析优先级：
  台账 first_seen > pushed_at > 批次 date > 文件 mtime > 视为近期（计入 undated 统计）

数据来源（只读，按需合并）：
  journals.json               期刊元数据（国内 6 + 国际 8）
  journal_ledger.json         ★ 获取台账：每篇首次被本地获取的时间
  journal_updates.json        最近一轮更新的文章（无时间戳 → 用文件 mtime）
  journal_batch_pending.json  待批量处理的一轮更新（有批次 date）
  journal_article_buffer.json 历史累积文章缓存（字段较简，仅 title/url）
  journal_doc_urls.json       期刊飞书速递文档链接
  journal_topic_archive.json  近 N 个月主题相关文献回溯归档

页面：GET /journals
       （页顶「今日更新」板块 = 当天 00:00 之后**获取到**的文章跨刊汇总，
        与视图一同源同口径，都由服务端按 obtained_ts 切好，前端只做区域/搜索过滤；
        视图一「近 60 天」另有前端「全部展开 / 收起全部」总开关，
       靠 `_MAX_EXPAND_PER_CARD` 保证下发量够铺开；见 GEO_TRANSLATE_ARCHITECTURE.md §15.4）
接口：GET /api/journals/list     （两个视图的数据一次性返回，另含 `today` 切片）
"""
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from flask import Blueprint, jsonify

from portal.templating import render_html

journals_bp = Blueprint('journals', __name__)

_TEMPLATE = Path(__file__).parent / 'templates' / 'journals.html'

# ★ 卡片窗口：只展示「本地近 N 天获取到」的文章链接（以本地日期时间为准）
WINDOW_DAYS = 60
# 单张卡片默认展示条数（其余折叠，前端可「展开全部」）
_MAX_PER_CARD = 6
# 「展开全部」时单卡最多下发的条数（防载荷过大）
# 取 200：当前最大单刊 70 篇，留足余量让「全部展开」不会静默截断；
# 万一某刊真的超过 200，前端会明确提示「另有 N 篇超出单卡展示上限」，不假装已全量。
_MAX_EXPAND_PER_CARD = 200
# 主题归档视图单卡片最多展示条数（归档命中多的刊可展开更多）
_MAX_TOPIC_PER_CARD = 8
# 「今日更新」板块单日最多展示条数（当天全刊汇总，超出部分明确提示不静默截断）
_TODAY_MAX = 300

# 相关度分档：标签 / 阈值下限（与 journal_filter.STRONG_RELEVANCE_MIN_SCORE=8 对齐）
TIERS = [
    ('strong', '强相关', 19),
    ('mid', '相关', 12),
    ('low', '一般', 8),
]


def _tier_of(score):
    s = score if isinstance(score, (int, float)) else 0
    for key, _label, floor in TIERS:
        if s >= floor:
            return key
    return 'low'


_deps = {'data_dir': None}

# 国际 / 国内 的展示顺序与配色键
_REGION_ORDER = [('国际', 'international'), ('国内', 'domestic')]


def init_journals(app, data_dir=None):
    """注册期刊列表模块。"""
    _deps['data_dir'] = Path(data_dir) if data_dir else None
    app.register_blueprint(journals_bp)
    print('[模块] 期刊列表 → GET /journals')


# ── 取数 ──
def _read_json(name):
    root = _deps['data_dir']
    if not root:
        return None
    p = root / name
    try:
        if p.exists():
            import json
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        return None
    return None


# ── 英文标题的中文小字翻译 ──
# 由 D:\PostNews\journal_title_zh.py 离线预生成（本机 llama.cpp 翻译），
# 门户这里**只读**，不翻译、不联网 —— 缓存缺失时该条就没有 title_zh，
# 前端照常显示英文原文，不会报错也不会空白。
_TITLE_ZH_CACHE = {'mtime': 0, 'map': {}}


def _title_zh_map():
    """读标题翻译缓存，按 mtime 做进程内缓存（避免每请求都读盘）。"""
    root = _deps['data_dir']
    if not root:
        return {}
    p = root / 'journal_title_zh.json'
    try:
        if not p.exists():
            return {}
        mt = p.stat().st_mtime
        if mt != _TITLE_ZH_CACHE['mtime']:
            import json
            with open(p, 'r', encoding='utf-8') as f:
                data = json.load(f) or {}
            m = {}
            for k, v in (data.get('titles') or {}).items():
                zh = (v or {}).get('zh') if isinstance(v, dict) else v
                if zh:
                    m[k] = zh.strip()
            _TITLE_ZH_CACHE['mtime'] = mt
            _TITLE_ZH_CACHE['map'] = m
        return _TITLE_ZH_CACHE['map']
    except Exception:
        return _TITLE_ZH_CACHE['map']


def _zh_of(title):
    """标题 → 中文小字翻译；无缓存返回 ''。键规则见 journal_title_zh.py::_key"""
    if not title:
        return ''
    k = re.sub(r'[^a-z0-9]', '', title.lower())
    return _title_zh_map().get(k, '')


def _file_mtime(name):
    """数据文件的最后写入时间 —— 作为该文件内容「被获取到」的时间代理。

    语义正确性：journal_updates.json 每次检测运行都被整体重写，内容就是那一轮
    新文章，所以「文件写入时刻」即这批文章的获取时刻；而且它会随时间变旧，
    文章能正常老化出窗口。（更准的来源是 journal_ledger.json 的 first_seen。）
    """
    root = _deps['data_dir']
    try:
        p = root / name if root else None
        if p and p.exists():
            return datetime.fromtimestamp(p.stat().st_mtime)
    except Exception:
        pass
    return None


def _parse_ts(v):
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


def _norm_key(title, url):
    """跨源同文判定键：标题指纹 + URL 兜底。

    同一篇论文在官网是 `.../geology/article/54/9/901/731860/...`，走 CrossRef
    兜底时变成 `https://doi.org/10.1130/g54149.1` —— URL 完全不同，只能按标题判。
    """
    t = re.sub(r'\W+', '', (title or '').lower())[:72]
    if t:
        return 't:' + t
    u = re.sub(r'\W+', '', (url or '').lower())
    return ('u:' + u) if u else ''


def _date_label(dt, now):
    """卡片上的获取日期：同年显示 MM-DD，跨年显示 YYYY-MM-DD。"""
    if not dt:
        return ''
    return dt.strftime('%m-%d') if dt.year == now.year else dt.strftime('%Y-%m-%d')


def _collect_recent(name, ledger, updates, pending, buffer_, ts_map, cutoff, now):
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
            key = _norm_key(title, url)
            if not key or key in seen:
                continue
            seen.add(key)
            dt = _parse_ts(a.get('first_seen') or a.get('pushed_at')) or fallback_ts
            if dt is not None and dt < cutoff:
                stats['expired'] += 1
                continue
            if dt is None:
                # 解析不出时间：这些是"当前列表"里的存量，无法证明其超出窗口，
                # 保守起见保留（并计入 undated 统计，便于事后核查）。
                stats['undated'] += 1
            out.append({
                'title': title or url,
                'title_zh': _zh_of(title),
                'url': url,
                'source': a.get('source') or source,
                'volume': str(a.get('volume') or ''),
                'issue': str(a.get('issue') or ''),
                'obtained_at': dt.isoformat(timespec='seconds') if dt else '',
                'obtained_label': _date_label(dt, now),
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


def _collect_topic(archive):
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


def _build():
    # 以**本地时间**为准计算 60 天滚动窗口（机器时区 +0800，与用户所在地一致）
    now = datetime.now()
    cutoff = now - timedelta(days=WINDOW_DAYS)

    journals = _read_json('journals.json') or {}
    updates = _read_json('journal_updates.json') or {}
    docs = _read_json('journal_doc_urls.json') or {}
    pending_all = _read_json('journal_batch_pending.json') or {}
    pending = pending_all.get('batch_updates') or {}
    buffer_ = _read_json('journal_article_buffer.json') or {}
    archive = _read_json('journal_topic_archive.json') or {}

    # ★ 获取台账：本模块 60 天窗口的主时间源
    ledger_all = _read_json('journal_ledger.json') or {}
    ledger = ledger_all.get('journals') or {}
    ledger_meta = ledger_all.get('_meta') or {}

    ts_map = {
        'pending': _parse_ts(pending_all.get('date')) or _file_mtime('journal_batch_pending.json'),
        'updates': _file_mtime('journal_updates.json'),
        'buffer': _file_mtime('journal_article_buffer.json'),
    }

    topic_by_j = _collect_topic(archive)
    topic_meta = archive.get('_meta') or {}

    groups, total_articles, updated_count = [], 0, 0
    topic_total, topic_journals, tier_count = 0, 0, {}
    undated_total, expired_total = 0, 0

    # 「今日更新」板块：把当天（本地日期）获取到的文章跨刊汇总，
    # 仍以 obtained_ts（获取时间）为唯一判据，与下方 60 天窗口同源同口径。
    today_date = now.date()
    today_items = []

    for region_label, key in _REGION_ORDER:
        rows = []
        for j in journals.get(key, []) or []:
            name = j.get('name') or ''
            articles, astat = _collect_recent(name, ledger, updates, pending, buffer_,
                                              ts_map, cutoff, now)
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
            for a in articles:
                ts = a.get('obtained_ts') or 0
                if not ts:
                    continue
                try:
                    if datetime.fromtimestamp(ts).date() != today_date:
                        continue
                except Exception:
                    continue
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
                'articles': articles[:_MAX_EXPAND_PER_CARD],
                'article_newest': articles[0]['obtained_label'] if articles else '',
                'article_undated': astat['undated'],
                'doc_url': doc_url,
                # — 主题归档 —
                'topic_total': len(topic),
                'topic_tiers': tier_count[name],
                'topic_articles': topic[:_MAX_TOPIC_PER_CARD],
            })
            total_articles += len(articles)
            if articles:
                updated_count += 1
            topic_total += len(topic)
            if topic:
                topic_journals += 1

        if rows:
            groups.append({
                'region': region_label,
                'count': len(rows),
                'journals': rows,
            })

    today_items.sort(key=lambda x: x['obtained_ts'], reverse=True)

    return {
        'ok': True,
        'total_journals': sum(g['count'] for g in groups),
        'total_articles': total_articles,
        'updated_journals': updated_count,
        'max_per_card': _MAX_PER_CARD,
        'max_expand_per_card': _MAX_EXPAND_PER_CARD,
        'max_topic_per_card': _MAX_TOPIC_PER_CARD,
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
            'items': today_items[:_TODAY_MAX],
            'hidden': max(0, len(today_items) - _TODAY_MAX),
        },
        'groups': groups,
    }


@journals_bp.route('/journals')
def journals_page():
    return render_html(_TEMPLATE)


@journals_bp.route('/api/journals/list')
def api_journals_list():
    return jsonify(_build())
