# -*- coding: utf-8 -*-
"""极简静态模板工具

刻意不用 Jinja2：
  门户页/归档页里全是 CSS 与 JS，`{` `}` 密集出现，
  走 render_template_string 有把大括号误当模板语法的风险。
  这里只做「读文件 + 占位符替换」，行为完全确定。

带 mtime 缓存 → 改 HTML 不用重启 Flask（改 .py 仍然要重启）。
"""
from pathlib import Path

from flask import Response

_cache = {}


def load_html(path):
    """读模板文件，按 mtime 缓存；文件不存在或读失败返回降级页。"""
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return _fallback(p)

    hit = _cache.get(str(p))
    if not hit or hit[0] != mtime:
        try:
            _cache[str(p)] = (mtime, p.read_text(encoding='utf-8'))
        except OSError:
            return _fallback(p)
    return _cache[str(p)][1]


def render_html(path, replacements=None):
    """渲染模板 → Flask Response（text/html）。"""
    html = load_html(path)
    for key, value in (replacements or {}).items():
        html = html.replace(key, value)
    return Response(html, mimetype='text/html')


def page(title, body_html):
    """给非模板场景用的最小包装页（含主题兜底）。

    这里保留一份最小 :root —— 主题引擎缺失时页面仍是可读的深色，
    而不是黑字黑底。theme.js 正常时浅色变量会覆盖它。
    """
    return ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{title}</title>'
            '<style>:root{--bg:#2b2b2b;--text:#e8e4df;--muted:#8a867f;'
            '--accent:#4a9e6a;--border:#3a3a3a;}'
            ':root[data-theme="light"]{--bg:#f7f6f3;--text:#2a2926;'
            '--muted:#6e6a63;--accent:#2e7d4f;--border:#e2dfd8;}</style>'
            '<script src="/theme.js"></script></head>'
            '<body style="font-family:-apple-system,\'PingFang SC\',sans-serif;'
            'background:var(--bg,#2b2b2b);color:var(--text,#e8e4df);'
            'padding:48px;line-height:1.7">'
            f'{body_html}</body></html>')


def _fallback(p):
    return page('页面模板缺失',
                '<h2 style="margin-bottom:8px">页面模板缺失</h2>'
                f'<p style="color:#8a867f;font-size:13px">未找到：{p}</p>'
                '<p style="margin-top:16px"><a href="/" '
                'style="color:#4a9e6a;font-size:13px">返回工作台</a></p>')
