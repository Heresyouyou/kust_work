# -*- coding: utf-8 -*-
"""今日早报模块

数据来源（全部只读，绝不写入 PostNews 的业务文件）：
  daily_push_state.json   → latest_daily 键（由 daily_run.py 推送后落盘）
  news.json               → 当日新闻条目
  output/daily_poster_*.png → 当日海报

页面：
  GET /today          跳转今日飞书早报文档（无链接时降级到 /daily）
  GET /daily          今日早报详情页（新闻列表 + 海报 + 飞书按钮）
  GET /daily/poster   代理输出最新一张日报海报 PNG

接口：
  GET /api/news/latest  首页卡片动态绑定用：链接 / 日期 / 条数 / 状态
  GET /api/news/list    当日新闻条目
"""
import re
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, redirect, send_file

from portal.templating import render_html

news_bp = Blueprint('news', __name__)

_TEMPLATE = Path(__file__).parent / 'templates' / 'daily.html'

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


# ── 页面 ──
@news_bp.route('/today')
def today_redirect():
    """「今日早报」入口：拿到有效链接就 302 直达飞书文档。

    用服务端 302 而不是直接把外链写进 HTML —— 链接天天变，
    写死进模板就意味着每天要改代码；这里保持模板恒定。
    首页的 JS 会在拿到链接后直接把卡片 href 指向飞书，省掉这一跳。
    """
    url = (_latest_record().get('doc_url') or '').strip()
    if url.startswith(('http://', 'https://')):
        return redirect(url, code=302)
    # 没有可用链接 → 落到详情页，至少还能看到今日新闻
    return redirect('/daily', code=302)


@news_bp.route('/daily')
def daily_page():
    return render_html(_TEMPLATE)


@news_bp.route('/daily/poster')
def daily_poster():
    p = _newest_poster()
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
