# -*- coding: utf-8 -*-
"""今日早报模块

数据来源（全部只读，绝不写入 PostNews 的业务文件）：
  daily_push_state.json   → latest_daily 键（由 daily_run.py 推送后落盘）
  news.json               → 当日新闻条目
  output/daily_poster_*.png → 当日海报

页面：
  GET /today          今日早报入口：302 → /daily 详情页（不再外跳飞书）
  GET /daily          今日早报详情页（新闻列表 + 海报 + 飞书按钮）
  GET /daily/poster   代理输出最新一张日报海报 PNG

接口：
  GET /api/news/latest  首页卡片动态绑定用：链接 / 日期 / 条数 / 状态
  GET /api/news/list    当日新闻条目
"""
import json
import re
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, redirect, request, send_file

from portal.templating import render_html

news_bp = Blueprint('news', __name__)

_TEMPLATE = Path(__file__).parent / 'templates' / 'daily.html'
_TEMPLATE_ARCHIVE = Path(__file__).parent / 'templates' / 'daily_archive.html'

# 由 init_news 注入
_deps = {'data_dir': None}

# 海报文件名白名单：只允许 daily_poster_<8位日期>.png 形态，杜绝路径穿越
_POSTER_RE = re.compile(r'^daily_poster_\d{8}(_\d+)?\.png$', re.IGNORECASE)


def init_news(app, data_dir=None):
    """注册今日早报模块。data_dir 指向 PostNews 根目录。"""
    _deps['data_dir'] = Path(data_dir) if data_dir else None
    app.register_blueprint(news_bp)
    print('[模块] 今日早报 → GET /today（跳转飞书文档）, GET /daily（详情页）')


# ── 取数 ──
def _root():
    return _deps['data_dir']


def _read_json(name):
    root = _root()
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


def _latest_record():
    """读取 daily_run.py 落盘的最近一次早报记录。"""
    state = _read_json('daily_push_state.json') or {}
    rec = state.get('latest_daily') or {}
    return rec if isinstance(rec, dict) else {}


def _newest_poster():
    """output/ 下最新一张日报海报（只认白名单命名）。"""
    root = _root()
    if not root:
        return None
    out = root / 'output'
    if not out.is_dir():
        return None
    best, best_m = None, -1.0
    try:
        for p in out.iterdir():
            if not p.is_file() or not _POSTER_RE.match(p.name):
                continue
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if m > best_m:
                best, best_m = p, m
    except OSError:
        return None
    return best


def _today_str():
    return datetime.now().strftime('%Y-%m-%d')


def _age_days(date_str):
    """记录日期距今多少天；解析失败返回 None。"""
    if not date_str:
        return None
    try:
        d = datetime.strptime(date_str[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None
    return (datetime.now().date() - d).days


def _news_payload():
    """当日新闻条目（news.json），统一成前端好用的结构。"""
    data = _read_json('news.json') or {}
    raw = data.get('news') or []
    items = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        items.append({
            'num': it.get('num') or '',
            'cat': it.get('cat') or '',
            'title': it.get('title') or '',
            'summary': it.get('summary') or '',
            'source': it.get('source') or '',
            'url': it.get('url') or '',
            'pub_date': it.get('pub_date') or '',
        })
    return {
        'date': data.get('date') or '',
        'publisher': data.get('publisher') or '',
        'count': len(items),
        'items': items,
    }


def _latest_payload():
    """首页卡片与详情页共用的聚合状态。"""
    rec = _latest_record()
    date_str = rec.get('date') or ''
    url = rec.get('doc_url') or ''
    age = _age_days(date_str)
    poster = _newest_poster()
    news = _news_payload()

    # 记录是否属于「今天」：优先按记录日期判断，缺日期时退化为按新闻文件判断
    is_today = (age == 0) if age is not None else False

    return {
        'ok': True,
        'date': date_str,
        'is_today': is_today,
        'age_days': age,
        'url': url,
        'has_url': bool(url),
        'doc_title': rec.get('doc_title') or '',
        'news_count': rec.get('news_count') or news['count'],
        'feishu_sent': rec.get('feishu_sent') or '',
        'poster_ok': bool(rec.get('poster_ok')),
        'poster_url': '/daily/poster' if poster else '',
        'news_date_text': news['date'],
        'news_total': news['count'],
    }


# ── 早报归档（按日期落盘快照） ──
# 每次访问 /daily 都把当天早报内容（新闻条目 + 海报 + 推送状态）写成一个日期快照；
# 历史海报（output/daily_poster_*.png）也会被回填进归档列表，
# 即便当时没落盘快照 JSON，也能在归档里看到那天的海报。
def _archive_dir():
    root = _root()
    d = root / 'daily_archive' if root else None
    if d:
        d.mkdir(parents=True, exist_ok=True)
    return d


def _date8(date_str):
    """从任意日期串（'YYYY-MM-DD' 或 'YYYYMMDD'）提取 8 位日期 → 'YYYYMMDD'；不足 8 位数字返回 ''。"""
    digits = re.sub(r'\D', '', date_str or '')
    return digits[:8] if len(digits) >= 8 else ''


def _poster_for_date8(date8):
    """output/ 下匹配 daily_poster_<date8>(_N)?.png 的最新一张。"""
    if not date8:
        return None
    root = _root()
    if not root:
        return None
    out = root / 'output'
    if not out.is_dir():
        return None
    pat = re.compile(r'^daily_poster_' + re.escape(date8) + r'(_\d+)?\.png$', re.IGNORECASE)
    best, best_m = None, -1.0
    try:
        for p in out.iterdir():
            if not p.is_file() or not pat.match(p.name):
                continue
            m = p.stat().st_mtime
            if m > best_m:
                best, best_m = p, m
    except OSError:
        return None
    return best


def _write_snapshot():
    """把当前 /daily 的内容落盘成日期快照（幂等，重复访问只覆盖当天）。"""
    try:
        rec = _latest_record()
        date_str = rec.get('date') or _today_str()
        date8 = _date8(date_str)
        if not date8:
            return
        # 统一存成 YYYY-MM-DD 便于展示与回链
        date_disp = '%s-%s-%s' % (date8[:4], date8[4:6], date8[6:8])
        adir = _archive_dir()
        if not adir:
            return
        poster = _poster_for_date8(date8) or _newest_poster()
        payload = {
            'date': date_disp,
            'doc_url': rec.get('doc_url') or '',
            'doc_title': rec.get('doc_title') or '',
            'feishu_sent': rec.get('feishu_sent') or '',
            'poster': poster.name if poster else '',
            'news_count': rec.get('news_count') or 0,
            'items': _news_payload().get('items') or [],
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        (adir / (date8 + '.json')).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    except Exception:
        pass


def _archive_entries():
    """归档列表：扫描历史海报 + 已落盘快照，合并去重，按日期倒序。"""
    root = _root()
    entries = {}
    # 1. 历史海报（优先，保证能看到过去的海报）
    if root:
        out = root / 'output'
        if out.is_dir():
            for p in out.iterdir():
                m = re.match(r'^daily_poster_(\d{8})(_\d+)?\.png$', p.name, re.IGNORECASE)
                if not m:
                    continue
                d8 = m.group(1)
                entries.setdefault(d8, {
                    'date8': d8, 'date': d8[:4] + '-' + d8[4:6] + '-' + d8[6:],
                    'poster': p.name, 'news_count': 0, 'has_snapshot': False, 'doc_url': '',
                })
    # 2. 快照（补充新闻条数，标记 has_snapshot）
    adir = _archive_dir()
    if adir and adir.is_dir():
        for p in adir.iterdir():
            if not p.name.endswith('.json'):
                continue
            try:
                rec = json.loads(p.read_text(encoding='utf-8'))
            except Exception:
                continue
            d8 = _date8(rec.get('date') or '')
            if not d8:
                continue
            e = entries.setdefault(d8, {
                'date8': d8,
                'date': '%s-%s-%s' % (d8[:4], d8[4:6], d8[6:8]),
                'poster': '', 'news_count': 0, 'has_snapshot': False, 'doc_url': '',
            })
            e['has_snapshot'] = True
            e['news_count'] = rec.get('news_count') or len(rec.get('items') or [])
            e['doc_url'] = rec.get('doc_url') or e.get('doc_url') or ''
    return sorted(entries.values(), key=lambda x: x['date8'], reverse=True)


# ── 页面 ──
@news_bp.route('/today')
def today_redirect():
    """「今日早报」入口：始终落到 /daily 详情页（左侧新闻标题+链接、右侧海报）。

    旧实现会 302 直达飞书文档；现改为默认展示站内详情页，
    飞书文档按钮在详情页右上角另行提供，避免一进来就外跳飞书。
    首页「今日早报」卡片也固定指向 /daily（见 portal/registry.py）。
    """
    return redirect('/daily', code=302)


@news_bp.route('/daily')
def daily_page():
    _write_snapshot()   # 访问即落盘当天快照，归档才能累积历史
    return render_html(_TEMPLATE)


@news_bp.route('/daily/archive')
def daily_archive_page():
    return render_html(_TEMPLATE_ARCHIVE)


@news_bp.route('/daily/poster')
def daily_poster():
    p = _newest_poster()
    if not p:
        return jsonify({'error': 'no poster'}), 404
    return send_file(str(p), mimetype='image/png')


@news_bp.route('/daily/poster/<date8>')
def daily_poster_date(date8):
    """按日期取历史海报：daily_poster_<date8>.png。"""
    if not re.match(r'^\d{8}$', date8 or ''):
        return jsonify({'error': 'bad date'}), 400
    p = _poster_for_date8(date8)
    if not p:
        return jsonify({'error': 'no poster'}), 404
    return send_file(str(p), mimetype='image/png')


# ── 接口 ──
@news_bp.route('/api/news/latest')
def api_news_latest():
    return jsonify(_latest_payload())


@news_bp.route('/api/news/list')
def api_news_list():
    return jsonify(_news_payload())


@news_bp.route('/api/daily/archive')
def api_daily_archive():
    """归档列表：历史海报 + 已落盘快照，按日期倒序。"""
    return jsonify({'ok': True, 'entries': _archive_entries()})


@news_bp.route('/api/daily/snapshot')
def api_daily_snapshot():
    """某天早报的聚合负载：优先读快照，无快照但有海报也能看海报（新闻未存档）。"""
    date_str = request.args.get('date') or request.args.get('d') or ''
    date8 = _date8(date_str)
    if date8:
        adir = _archive_dir()
        snap = None
        if adir:
            sp = adir / (date8 + '.json')
            if sp.exists():
                try:
                    snap = json.loads(sp.read_text(encoding='utf-8'))
                except Exception:
                    snap = None
        if snap:
            items = snap.get('items') or []
            age = _age_days(snap.get('date') or '')
            poster = snap.get('poster') or ''
            # 海报按快照里实际存的文件名取日期（今天海报未生成时回退到当时最新的那张）
            pm = re.match(r'^daily_poster_(\d{8})', poster or '')
            poster_url = ('/daily/poster/' + pm.group(1)) if pm else ('/daily/poster/' + date8 if poster else '')
            return jsonify({
                'ok': True,
                'date': snap.get('date') or '',
                'is_today': (age == 0) if age is not None else False,
                'age_days': age,
                'has_url': bool(snap.get('doc_url')),
                'url': snap.get('doc_url') or '',
                'doc_title': snap.get('doc_title') or '',
                'news_count': len(items),
                'feishu_sent': snap.get('feishu_sent') or '',
                'poster_url': poster_url,
                'news_date_text': snap.get('date') or '',
                'news_total': len(items),
                'items': items,
                'historical': True,
            })
        # 无快照但有海报：展示海报，新闻未存档
        poster = _poster_for_date8(date8)
        return jsonify({
            'ok': True,
            'date': date_str,
            'is_today': False,
            'age_days': _age_days(date_str),
            'has_url': False,
            'url': '',
            'doc_title': '',
            'news_count': 0,
            'feishu_sent': '',
            'poster_url': ('/daily/poster/' + date8) if poster else '',
            'news_date_text': date_str,
            'news_total': 0,
            'items': [],
            'historical': True,
            'note': '该日仅保留海报，新闻条目未存档',
        })
    return jsonify({'ok': False, 'error': 'no snapshot'}), 404
