# -*- coding: utf-8 -*-
"""笔记整理模块

卡片陈列页：静态归档的笔记卡片 + 逐篇打开阅读。
笔记正文是自带样式的静态 HTML，放在 assets/notes/ 下直接发；
卡片清单写死在下面的 NOTES 里，新增笔记 = 加一条 + 丢一个 html 进 assets/notes/。
后端不出数据、不存库、不接表单。
"""
from pathlib import Path

from flask import Blueprint, Response, jsonify, send_file

from portal.templating import render_html

notes_bp = Blueprint('notes', __name__)

_DIR = Path(__file__).parent
_TEMPLATE = _DIR / 'templates' / 'notes.html'
_ASSETS = (_DIR / 'assets').resolve()
_DOWNLOADS = (_DIR / 'downloads').resolve()
_LAB_EXAM_EXE = '实验室安全自动学习.exe'
_MIME = {
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.html': 'text/html; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.svg': 'image/svg+xml',
}

# 卡片清单：字段与模板一一对应，按需要加即可
NOTES = [
    {
        'title': '实验室安全自动学习 · 桌面客户端',
        'cat': '实验室安全考试',
        'tag': 'Windows · 单文件',
        'desc': '自动学习 + 考试辅助的桌面工具。单个 exe 交付：内置全部 8849 题的'
                '题库与浏览器驱动，拷到任意机器双击即可运行，不依赖任何其他文件。'
                '仅做自动学习与题目匹配，不自动答题、不提交试卷。',
        'tags': ['零外部依赖', '内置题库', '内容匹配'],
        'date': '2026-10-20',
        'url': '/api/notes/labexam/client.exe',
        'new_tab': False,
        'download': True,
        'go': '下载',
    },
    {
        'title': '实验室安全考试 · 题库检索',
        'cat': '实验室安全考试',
        'tag': '8849 题',
        'desc': '按题干 / 选项 / 答案关键词检索校实验室安全考试题库，命中即出原题与'
                '标准答案；可标记错题、写备注，导出错题本。',
        'tags': ['23 个题库', '错题本', '自测遮答案'],
        'date': '2026-10-19',
        'url': '/api/notes/assets/labexam/index.html',
        'new_tab': True,
    },
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
    # 下载类卡片：加 download 属性，浏览器直接存盘而不是打开
    dl = ' download' if n.get('download') else ''
    return (
        '    <a class="card" href="{url}"{target}{dl}>\n'
        '      <div class="card-top">\n'
        '        <span class="cat">{cat}</span>{tag}\n'
        '      </div>\n'
        '      <h2 class="card-t">{title}</h2>\n'
        '      <p class="card-d">{desc}</p>\n'
        '      {chips}\n'
        '      <div class="card-foot">\n'
        '        <span class="date">{date}</span>\n'
        '        <span class="go">{go} &rarr;</span>\n'
        '      </div>\n'
        '    </a>'
    ).format(
        url=_esc(n['url']),
        target=' target="_blank" rel="noopener"' if n.get('new_tab') else '',
        dl=dl,
        cat=_esc(n.get('cat', '')),
        tag='<span class="tag">%s</span>' % _esc(n['tag']) if n.get('tag') else '',
        title=_esc(n['title']),
        desc=_esc(n.get('desc', '')),
        chips='<ul class="chips">%s</ul>' % chips if chips else '',
        date=_esc(n.get('date', '')),
        go=_esc(n.get('go', '打开')),
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


def _lab_exam_exe_path():
    """定位打包好的客户端 exe（单文件，零外部依赖）。"""
    return _DOWNLOADS / _LAB_EXAM_EXE


@notes_bp.route('/api/notes/labexam/client.exe')
def labexam_client_exe():
    """下载「实验室安全自动学习」客户端。

    交付件是**单个 exe**：内置全部题库数据与浏览器驱动，不依赖任何其他文件，
    拷到任意机器双击即可运行。浏览器用系统自带 Chrome/Edge（Win10/11 预装 Edge）。

    conditional=True → 支持断点续传（54 MB，一次传不完不用从头来）。
    """
    exe = _lab_exam_exe_path()
    if not exe.is_file():
        return Response('客户端尚未构建\n', status=404,
                        content_type='text/plain; charset=utf-8')
    # conditional=True → 带 ETag/Last-Modified，客户端更新后重下即可
    # （web_app 的 after_request 会把 Cache-Control 统一成 no-cache，
    #   这里不去覆盖它 —— ETag 回验足够，且避免客户端拿到过期版本）
    return send_file(
        str(exe),
        mimetype='application/octet-stream',
        as_attachment=True,
        download_name=_LAB_EXAM_EXE,
        conditional=True,
    )


@notes_bp.route('/api/notes/labexam/client.json')
def labexam_client_info():
    """客户端元信息：体积 / 版本 / 摘要，供下载按钮显示。"""
    import hashlib

    exe = _lab_exam_exe_path()
    if not exe.is_file():
        return jsonify({'ok': False, 'available': False})
    h = hashlib.md5()
    with open(exe, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    st = exe.stat()
    return jsonify({
        'ok': True,
        'available': True,
        'name': _LAB_EXAM_EXE,
        'size': st.st_size,
        'size_mb': round(st.st_size / 1048576, 1),
        'md5': h.hexdigest(),
        'modified': int(st.st_mtime),
        'url': '/api/notes/labexam/client.exe',
        'note': '单文件、零外部依赖；需系统自带 Chrome 或 Edge（Win10/11 预装 Edge）',
    })
