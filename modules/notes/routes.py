# -*- coding: utf-8 -*-
"""笔记整理模块

卡片陈列页：静态归档的笔记卡片 + 逐篇打开阅读。
笔记正文是自带样式的静态 HTML，放在 assets/notes/ 下直接发；
卡片清单写死在下面的 NOTES 里，新增笔记 = 加一条 + 丢一个 html 进 assets/notes/。
后端不出数据、不存库、不接表单。
"""
from pathlib import Path

from flask import Blueprint, Response, send_file

from portal.templating import render_html

notes_bp = Blueprint('notes', __name__)

_DIR = Path(__file__).parent
_TEMPLATE = _DIR / 'templates' / 'notes.html'
_ASSETS = (_DIR / 'assets').resolve()
_MIME = {
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.html': 'text/html; charset=utf-8',
    '.svg': 'image/svg+xml',
}

# 卡片清单：字段与模板一一对应，按需要加即可
NOTES = [
    {
        'title': '普通角闪石综合鉴定记录（按定向切面组织）',
        'cat': '晶体光学',
        'tag': '实操记录',
        'desc': '按题板要求顺序：⊥OA 切面测轴性与光性、∥光轴面切面测干涉色'
                '颜色消光延性、⊥C 切面测解理突起夹角，含 5 幅 SVG 参考图与'
                '11 项汇总定名。',
        'tags': ['角闪石', '定向切面', '5幅参考图'],
        'date': '2026-10-12',
        'url': '/api/notes/assets/handbook/hornblende-systematic-id.html',
        'new_tab': True,
    },
    {
        'title': '晶体光学与岩矿镜下鉴定知识手册',
        'cat': '晶体光学',
        'tag': 'HTML 版',
        'desc': '屏幕阅读版，八章：晶体光学基础、正交/锥光理论、鉴定 SOP、'
                '基础矿物镜下识别详解、火成岩薄片综合鉴定、记录模板与速查表。'
                '带目录锚点跳转。',
        'tags': ['8章', '章节导航', '屏幕阅读'],
        'date': '2026-10-11',
        'url': '/api/notes/assets/handbook/mineral-microscopy-handbook.html',
        'new_tab': True,
    },
    {
        'title': '岩矿镜下鉴定考试速用手册（A4 打印版 v6）',
        'cat': '晶体光学',
        'tag': '打印版',
        'desc': '44 页 A4 排版，逐页铺满不跨页、黑白配色，含页码目录。'
                '适配直接打印带进考场。',
        'tags': ['A4打印', '44页', '页码目录'],
        'date': '2026-10-11',
        'url': '/api/notes/assets/handbook/mineral-microscopy-print-a4.html',
        'new_tab': True,
    },
]


def _esc(s):
    """HTML 转义：卡片文案都是手写的，转义防意外破版。"""
    return (str(s).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def _card_html(n):
    """把一条卡片记录渲染成 <a class="card">。"""
    chips = ''.join('<li>%s</li>' % _esc(t) for t in n.get('tags', []))
    return (
        '    <a class="card" href="{url}"{target}>\n'
        '      <div class="card-top">\n'
        '        <span class="cat">{cat}</span>{tag}\n'
        '      </div>\n'
        '      <h2 class="card-t">{title}</h2>\n'
        '      <p class="card-d">{desc}</p>\n'
        '      {chips}\n'
        '      <div class="card-foot">\n'
        '        <span class="date">{date}</span>\n'
        '        <span class="go">打开 &rarr;</span>\n'
        '      </div>\n'
        '    </a>'
    ).format(
        url=_esc(n['url']),
        target=' target="_blank" rel="noopener"' if n.get('new_tab') else '',
        cat=_esc(n.get('cat', '')),
        tag='<span class="tag">%s</span>' % _esc(n['tag']) if n.get('tag') else '',
        title=_esc(n['title']),
        desc=_esc(n.get('desc', '')),
        chips='<ul class="chips">%s</ul>' % chips if chips else '',
        date=_esc(n.get('date', '')),
    )


def init_notes(app, **_ignored):
    """注册笔记整理模块（纯前端，静态卡片陈列）。"""
    app.register_blueprint(notes_bp)
    print('[模块] 笔记整理 → GET /notes')


@notes_bp.route('/notes')
def notes_page():
    return render_html(_TEMPLATE, {
        '__COUNT__': str(len(NOTES)),
        '__CARDS__': '\n'.join(_card_html(n) for n in NOTES),
    })


@notes_bp.route('/api/notes/assets/<path:rel>')
def notes_asset(rel):
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
    # 归档页会迭代，改完立刻能刷出来：不走 after_request 的 1h 强缓存
    resp.headers['Cache-Control'] = 'no-cache'
    return resp
