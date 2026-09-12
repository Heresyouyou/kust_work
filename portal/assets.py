# -*- coding: utf-8 -*-
"""门户静态资产（主题引擎等）

只服务 portal/assets/ 下的白名单文件，不做通用静态目录 ——
web_app 建 Flask 时用的是 static_folder=None，这里保持
「显式白名单 + mtime 缓存」的可控性，行为和 templating.load_html 一致：
改 JS 不用重启 Flask，改 .py 才要重启。

断网/文件缺失时返回 404 + 一行注释，而不是 500：
主题引擎缺失只会退化成「仅深色」，不该把页面拖挂。
"""
from pathlib import Path

from flask import Response

_DIR = Path(__file__).parent / 'assets'

# 白名单：文件名 → MIME
_ASSETS = {
    'theme.js': 'text/javascript; charset=utf-8',
}

_cache = {}


def read_asset(name):
    """读资产内容（按 mtime 缓存）。不在白名单或读失败 → (None, mime)。"""
    mime = _ASSETS.get(name)
    if not mime:
        return None, 'text/plain; charset=utf-8'
    p = _DIR / name
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None, mime

    hit = _cache.get(name)
    if not hit or hit[0] != mtime:
        try:
            _cache[name] = (mtime, p.read_text(encoding='utf-8'))
        except OSError:
            return None, mime
    return _cache[name][1], mime


def asset_response(name):
    """构造资产响应。"""
    body, mime = read_asset(name)
    if body is None:
        return Response('/* asset not found: %s */\n' % name,
                        status=404, mimetype='text/plain; charset=utf-8')
    # 用 content_type 而不是 mimetype：mimetype 会让 Flask 再拼一次
    # charset，得到 "text/javascript; charset=utf-8; charset=utf-8"。
    resp = Response(body, content_type=mime)
    # 缓存策略由 web_app 的 after_request 统一决定（/theme.js 走 no-cache）
    return resp
