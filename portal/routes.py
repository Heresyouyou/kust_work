# -*- coding: utf-8 -*-
"""门户路由

  GET /                      欢迎主页（服务端渲染卡片）
  GET /api/portal/health     归一化健康状态（供首页状态灯轮询）
  GET /theme.js              主题引擎（浅色/深色，按昆明日出日落自动切换）

依赖注入：门户不 import web_app（避免循环引用），
由 web_app 启动时通过 init_portal() 传入取数回调。
"""
from html import escape
from pathlib import Path

import requests
from flask import Blueprint, jsonify

from .assets import asset_response
from .registry import get_groups
from .templating import page, render_html

portal_bp = Blueprint('portal', __name__)

_TEMPLATE = Path(__file__).parent / 'templates' / 'portal.html'
_CARDS_SLOT = '<!--PORTAL_CARDS-->'

_LLAMA_HEALTH = 'http://127.0.0.1:8090/health'

# 运行时依赖（由 init_portal 注入）
_runtime = {'get_active': None}


def init_portal(app, get_active=None):
    """注册门户蓝图。

    get_active: 无参可调用对象，返回当前活跃任务数（queued + translating）。
                由 web_app 提供，内部用 jobs_lock 加锁取数，避免跨线程迭代竞态。
    """
    _runtime['get_active'] = get_active
    app.register_blueprint(portal_bp)
    # 打印注册结果，便于启动时肉眼确认入口数量
    total = sum(len(m) for _, m in get_groups())
    online = sum(1 for _, ms in get_groups() for m in ms if m.get('status') in ('online', 'beta'))
    print(f'[门户] 已注册 {total} 个入口（可用 {online} 个）→ GET /')


# ── 状态样式映射 ──
_LEVEL = {
    'online': ('on', 'card-tag on', ''),
    'beta': ('beta', 'card-tag beta', ''),
    'coming': ('off', 'card-tag', ' off'),
}


def _card(m):
    """渲染单张功能卡片。coming 状态渲染为不可点击的 div。

    每张卡片带 data-mod="<id>"，供 portal.html 里的 JS 按 id 定位并动态改写
    （如今日早报的角标/描述由后端当天数据回填）—— 这样 HTML 模板本身保持恒定，
    天天变的文案不进模板，前端结构不需要跟着改。
    """
    status = m.get('status') or 'coming'
    _, tag_cls, card_extra = _LEVEL.get(status, _LEVEL['coming'])

    accent = ' accent' if m.get('accent') and status in ('online', 'beta') else ''
    name = escape(m.get('name') or '')
    desc = escape(m.get('desc') or '')
    tag = escape(m.get('tag') or '')
    url = m.get('url')
    mod = escape(m.get('id') or '', quote=True)
    tag_id = f' data-tag="{mod}"' if mod else ''

    head = (f'<div class="card-h"><span class="card-n">{name}</span>'
            f'<span class="{tag_cls}"{tag_id}>{tag}</span></div>'
            f'<p class="card-d">{desc}</p>')

    if status == 'coming' or not url:
        return f'<div class="card{card_extra}" data-mod="{mod}">{head}</div>'

    href = escape(url, quote=True)
    return (f'<a class="card{accent}" data-mod="{mod}" href="{href}">{head}'
            f'<p class="card-p">{escape(url)} &rarr;</p></a>')


def _render_cards():
    blocks = []
    for group_name, mods in get_groups():
        cards = '\n'.join(_card(m) for m in mods)
        blocks.append(
            f'<section class="grp">'
            f'<h2 class="grp-t">{escape(group_name)}</h2>'
            f'<div class="grid">{cards}</div>'
            f'</section>'
        )
    return '\n'.join(blocks) if blocks else page(
        '暂无入口', '<p style="color:#8a867f">portal/registry.py 里还没有注册任何模块</p>')


@portal_bp.route('/')
def portal_home():
    return render_html(_TEMPLATE, {_CARDS_SLOT: _render_cards()})


@portal_bp.route('/theme.js')
def theme_js():
    """主题引擎。

    放在门户蓝图里而不是 web_app，是为了让「页面带前缀、静态资源留根路径」
    这条既有约定继续成立（页面在 /journals 等前缀下，资源仍走 /theme.js）。
    """
    return asset_response('theme.js')


@portal_bp.route('/api/portal/health')
def portal_health():
    """归一化状态：ok / loading / down。

    llama.cpp 的 /health 在就绪时返回 200 {"status":"ok"}，
    加载中返回 503 {"error":{"message":"Loading model"...}}。
    requests 不会因 503 抛异常，所以要显式看 status_code。
    """
    llama = 'down'
    try:
        # trust_env=False: 本地回环不走 HTTP_PROXY(代理会改写请求行 -> llama 404)
        _sess = requests.Session()
        _sess.trust_env = False
        r = _sess.get(_LLAMA_HEALTH, timeout=2)
        body = r.json() if r.content else {}
        if r.status_code == 200 and body.get('status') == 'ok':
            llama = 'ok'
        elif r.status_code == 503:
            llama = 'loading'
        else:
            llama = 'loading' if isinstance(body, dict) and body else 'down'
    except Exception:
        llama = 'down'

    active = None
    if _runtime['get_active'] is not None:
        try:
            active = _runtime['get_active']()
        except Exception:
            active = None

    return jsonify({'llama': llama, 'active': active})
