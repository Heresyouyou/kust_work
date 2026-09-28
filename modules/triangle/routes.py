# -*- coding: utf-8 -*-
"""三角计算模块（三角函数计算器）

已知任意 3 项（角 A/B/C 或边 a/b/c）→ 求解其余 3 项，并绘制三角形。
纯前端计算，无后端依赖；静态资源走 /api/triangle/assets/（仿 reader 模块的白名单式服务）。

页面：GET /triangle
接口：GET /api/triangle/assets/<path:rel>   前端脚本与样式
"""
from pathlib import Path

from flask import Blueprint, Response, send_file

from portal.templating import render_html

triangle_bp = Blueprint('triangle', __name__)

_DIR = Path(__file__).parent
_TEMPLATE = _DIR / 'templates' / 'triangle.html'
_ASSETS = (_DIR / 'assets').resolve()
_MIME = {
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.html': 'text/html; charset=utf-8',
    '.svg': 'image/svg+xml',
}


def init_triangle(app, **_ignored):
    """注册三角计算模块（纯前端，无外部依赖）。"""
    app.register_blueprint(triangle_bp)
    print('[模块] 三角计算 → GET /triangle')


@triangle_bp.route('/triangle')
def triangle_page():
    return render_html(_TEMPLATE)


@triangle_bp.route('/api/triangle/assets/<path:rel>')
def triangle_asset(rel):
    """白名单式静态服务：只允许 assets/ 目录下的文件，防目录穿越。"""
    try:
        target = (_ASSETS / rel).resolve()
        target.relative_to(_ASSETS)  # 越界会抛 ValueError
    except (ValueError, OSError):
        return Response('/* denied */\n', status=403, content_type='text/plain; charset=utf-8')

    if not target.is_file():
        return Response('/* not found: %s */\n' % rel,
                        status=404, content_type='text/plain; charset=utf-8')

    resp = send_file(target, conditional=True)
    # 直接赋 header，不用 mimetype=（Werkzeug 会再追加一个 charset，得到畸形头）
    resp.headers['Content-Type'] = _MIME.get(target.suffix.lower(), 'application/octet-stream')
    # 源码在迭代，改完立刻能刷出来：不走 after_request 的 1h 强缓存
    resp.headers['Cache-Control'] = 'no-cache'
    return resp
