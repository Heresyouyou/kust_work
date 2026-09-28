# -*- coding: utf-8 -*-
"""汇报说明书（/story）—— 顶部标签式的多汇报档案

一份汇报 = docs/<sid>/ 下一个目录（STORY.md 必需，pptx / 页图 / 提词 / 页面脚本可选）。
标签栏由 stories.py 注册表驱动：切换标签即切换「这一份的 story.md 正文 + 对应 PPT 入口」。
所以这里不做任何「某份汇报专属」的硬编码 —— 加一份汇报只改 stories.py + 放文件。

页面：GET /story                          → 跳到标签栏第一份
页面：GET /story/<sid>                    → 该汇报的说明书（标签页）
页面：GET /story/<sid>/deck               → 网页放映版（该汇报有页图才可用）
页面：GET /story/deck                     → 兼容旧链接 → 跳到默认放映页
页面：GET /story/admin                    → 后台管理（发布 / 编辑 / 重建页图 / 移除）→ admin.py
接口：GET /api/story/md/<sid>             → STORY.md 原文（text/markdown，浏览器直接可读）
接口：GET /api/story/ppt/<sid>            → PPT 原文件下载（attachment，保留原文件名）
接口：GET /api/story/asset/<sid>/<path>   → 该汇报目录下的图件 / JS / CSS（白名单式）
接口：GET /api/story/assets/<path>        → 模块级共享资源（校名标、story.css）
接口：GET /api/story/notes/<sid>          → 放映提词稿（deck_notes.js，由放映页 <script> 拉取）
接口：GET /api/story/index                → 汇报清单 JSON（供门户/外部核对）

本文件**只读不写**：所有落盘操作都在 admin.py（管理页）里，
这条边界保证了「看汇报」这条链路永远不会因为写权限出问题。
"""
import re
from html import escape
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, Response, abort, jsonify, redirect, send_file, url_for

from portal.templating import load_html, render_html

from . import stories as REG
from .admin import admin_bp
from .render import render as render_md

story_bp = Blueprint('story', __name__)

_DIR = Path(__file__).resolve().parent
_TEMPLATE = _DIR / 'templates' / 'story.html'
_DECK_TMPL = _DIR / 'templates' / 'deck.html'
# 模块级共享资源（校名标等）—— 与「每份汇报自己的资源 docs/<sid>/」分开：
#   /api/story/assets/<path>        → 这里（module/assets）
#   /api/story/asset/<sid>/<path>   → docs/<sid>/
_ASSETS = (_DIR / 'assets').resolve()

_MIME = {
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.html': 'text/html; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.md': 'text/markdown; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
}

# 旧的模块级资源目录 → 现在的归属
# （重构前 images/deck 都在 modules/story/assets/ 下，页图与配图现随汇报放到 docs/<sid>/）
_LEGACY = {'img': 'porphyry-zircon', 'deck': 'porphyry-zircon'}

def init_story(app, **_ignored):
    """注册汇报说明书模块（纯静态，无外部依赖、不读用户目录）。

    与 triangle / palette 一样，_MODULES 里 need_data=False。
    管理页（/story/admin）是独立 Blueprint，只有它才写盘。
    """
    app.register_blueprint(story_bp)
    app.register_blueprint(admin_bp)
    ids = '、'.join(s['id'] for s in REG.all()) or '（无）'
    print('[模块] 汇报说明书 → GET /story ｜ 共 %d 份：%s' % (len(REG.all()), ids))
    print('[模块] 汇报管理   → GET /story/admin（发布 / 编辑 / 重建页图 / 移除）')


# ── 页面 ────────────────────────────────────────────────────────────────

@story_bp.route('/story')
def story_index():
    """标签栏第一份汇报就是落地页。"""
    s = REG.first()
    if not s:
        return Response('/* no story registered */\n', status=404,
                        content_type='text/plain; charset=utf-8')
    return redirect(url_for('story.story_page', sid=s['id']))


@story_bp.route('/story/deck')
def story_deck_legacy():
    """旧链接（重构前 /story/deck 是唯一放映页）→ 跳到默认那份。"""
    s = REG.deck_story()
    if not s:
        return redirect(url_for('story.story_index'))
    return redirect(url_for('story.story_deck', sid=s['id']))


@story_bp.route('/story/<sid>')
def story_page(sid):
    """一份汇报的说明书页：标签栏 + hero + 正文（md 渲染 or 自定义片段）。"""
    s = REG.get(sid)
    if not s:
        return _missing(sid)

    if s.get('body'):
        # 版式复杂的汇报走自定义片段（与外壳共用 CSS）
        body = render_html(REG.body_path(s)).get_data(as_text=True)
    else:
        body = _md_body(s)

    return render_html(_TEMPLATE, {
        '<!--PAGE_TITLE-->': escape(s['title']) + ' · kust.work',
        '<!--BRAND-->': escape(s.get('brand') or '汇报说明书'),
        '<!--HDR_META-->': escape(s.get('meta') or ''),
        '<!--STORY_TABS-->': _tabs_html(s['id']),
        '<!--STORY_HERO-->': _hero_html(s),
        '<!--STORY_BODY-->': body,
        '<!--FOOT_LINK-->': _foot_html(s),
        '<!--STORY_JS-->': _js_html(s),
    })


@story_bp.route('/story/<sid>/deck')
def story_deck(sid):
    """网页放映版。页图是「按目录约定」而不是写死的文件名 —— 放进 docs/<sid>/deck/ 即可。"""
    s = REG.get(sid)
    if not s:
        return _missing(sid)

    pages = REG.deck_files(s)
    if not pages:
        return redirect(url_for('story.story_page', sid=sid))

    total = len(pages)
    notes = REG.notes_path(s)
    meta = 'window.STORY_DECK_META={"total":%d,"base":%s};' % (
        total, _js_str(REG.deck_base(s)))
    src = '<script>%s</script>' % meta
    if notes:
        src += '<script src="/api/story/notes/%s"></script>' % quote(sid)

    # 这里刻意不用 render_html 的「全量替换」：
    # 注入串里带 </script>，一旦模板的别处（例如某段注释）也出现同名字符串，
    # 全量替换就会把脚本块截断成两半 —— 页面看起来正常但 JS 静默失灵。
    # 所以只替**第一处**（<head> 内的那一处），并断言它确实存在。
    tpl = load_html(_DECK_TMPL)
    if '<!--DECK_HEAD_SRC-->' not in tpl:
        return Response('/* deck.html 缺少 <!--DECK_HEAD_SRC--> 注入点 */\n',
                        status=500, content_type='text/plain; charset=utf-8')
    html = tpl.replace('<!--DECK_TITLE-->', escape(s['title']) + ' · 汇报放映')
    html = html.replace('<!--DECK_HEAD_SRC-->', src, 1)
    return Response(html, mimetype='text/html')


# ── 文件接口 ────────────────────────────────────────────────────────────

@story_bp.route('/api/story/index')
def story_index_json():
    """汇报清单（标签、页数、文件、体积）—— 给门户或外部脚本核对用。"""
    out = []
    for s in REG.all():
        pptx = REG.pptx_path(s)
        md = REG.md_path(s)
        out.append({
            'id': s['id'],
            'tab': s.get('tab'),
            'title': s.get('title'),
            'meta': s.get('meta'),
            'md': md.name if md else None,
            'md_size': REG.human_size(md),
            'pptx': pptx.name if pptx else None,
            'pptx_size': REG.human_size(pptx),
            'deck_pages': len(REG.deck_files(s)),
            'url': url_for('story.story_page', sid=s['id']),
        })
    return jsonify({'code': 0, 'message': 'ok',
                    'data': {'total': len(out), 'stories': out}})


@story_bp.route('/api/story/md/<sid>')
def story_md(sid):
    """STORY.md 原文（inline —— 浏览器直接当文本读，方便对照页面）。"""
    s = REG.get(sid)
    p = REG.md_path(s) if s else None
    if not p:
        abort(404)
    resp = send_file(p, mimetype='text/markdown; charset=utf-8')
    resp.headers['Content-Type'] = _MIME['.md']
    resp.headers['Content-Disposition'] = 'inline; filename="%s"' % _ascii_fallback(p.name)
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@story_bp.route('/api/story/ppt/<sid>')
def story_ppt(sid):
    """PPT 原文件下载 —— 路径只在服务端按注册表解析，URL 里不出现中文名。"""
    s = REG.get(sid)
    p = REG.pptx_path(s) if s else None
    if not p:
        abort(404)
    resp = send_file(p, mimetype='application/vnd.openxmlformats-officedocument'
                                '.presentationml.presentation')
    # 走 RFC 5987：老浏览器看 filename（ASCII 兜底），新浏览器看 filename*
    disp = 'attachment; filename="%s"; filename*=UTF-8\'\'%s' % (
        _ascii_fallback(p.name), quote(p.name))
    resp.headers['Content-Disposition'] = disp
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@story_bp.route('/api/story/notes/<sid>')
def story_notes(sid):
    """放映提词稿（docs/<sid>/deck_notes.js）—— 不存在时回一个空数组，放映页不报错。"""
    s = REG.get(sid)
    p = REG.notes_path(s) if s else None
    if not p:
        return Response('window.STORY_DECK_NOTES = [];\n',
                        mimetype='text/javascript; charset=utf-8')
    resp = send_file(p, mimetype='text/javascript; charset=utf-8')
    resp.headers['Content-Type'] = _MIME['.js']
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


def _send_asset(root, rel):
    """白名单式发文件：只允许 root 之下。

    resolve() + relative_to() 双重校验，`../` 越界抛 ValueError ——
    与 reader / triangle 两个模块同一套写法。
    """
    try:
        target = (root / rel).resolve()
        target.relative_to(root)          # 越界会抛 ValueError
    except (ValueError, OSError):
        return Response('/* denied */\n', status=403,
                        content_type='text/plain; charset=utf-8')

    if not target.is_file():
        return Response('/* not found: %s */\n' % rel, status=404,
                        content_type='text/plain; charset=utf-8')

    resp = send_file(target, conditional=True)
    # 直接赋 header，不用 mimetype=（Werkzeug 会再追加一个 charset，得到畸形头）
    resp.headers['Content-Type'] = _MIME.get(target.suffix.lower(),
                                             'application/octet-stream')
    # 与 triangle 一致：no-cache 而不是强缓存。
    # send_file 的 conditional=True 让浏览器走 If-Modified-Since → 命中即 304，
    # 图件迭代时不会拿到旧图（大图只在 lightbox 才拉，代价可接受）。
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@story_bp.route('/api/story/asset/<sid>/<path:rel>')
def story_asset(sid, rel):
    """该汇报自己的资源（docs/<sid>/...）—— 页图、配图、页面脚本都在这里。"""
    s = REG.get(sid)
    if not s:
        abort(404)
    return _send_asset(REG.doc_dir(s).resolve(), rel)


@story_bp.route('/api/story/assets/<path:rel>')
def story_asset_shared(rel):
    """模块级共享资源（modules/story/assets/...）—— 目前放的是校名标。

    同时兜住重构前的旧路径：那时配图与页图都在 assets/ 下（img/、deck/），
    现已按汇报归档到 docs/<sid>/，所以这两类前缀仍做一次性 302 兼容，
    避免开着的旧标签页 / 书签拿到 404。
    """
    head, _, tail = rel.partition('/')
    if head in _LEGACY and tail:
        return redirect('/api/story/asset/%s/%s' % (_LEGACY[head], rel), code=302)
    return _send_asset(_ASSETS, rel)


# ── 组装 HTML ───────────────────────────────────────────────────────────

def _tabs_html(cur_sid):
    """标签栏：序号 + 名称 + 页数/时长。当前项高亮，其余为链接。"""
    out = []
    for i, s in enumerate(REG.all(), 1):
        on = ' on' if s['id'] == cur_sid else ''
        note = '<em>%s</em>' % escape(s['tab_note']) if s.get('tab_note') else ''
        out.append(
            '<a class="tab%s" href="%s"><b>%02d</b>%s%s</a>'
            % (on, url_for('story.story_page', sid=s['id']), i,
               escape(s.get('tab') or s['id']), note))
    return ''.join(out)


def _hero_html(s):
    """Hero：标题 + 概要 + 标签 + **PPT 入口** + 文件清单。

    PPT 入口按「有什么给什么」渲染：
      有页图 → 主按钮是网页放映版；有 pptx → 主/次按钮是下载原文件。
      两者都有时，放映在前、下载在后（先讲后取）。
    """
    pages = REG.deck_files(s)
    pptx = REG.pptx_path(s)
    md = REG.md_path(s)

    buttons = []
    if pages:
        buttons.append(
            '<a class="go" href="%s">%s开始放映<em>网页版 · %d 页</em></a>'
            % (url_for('story.story_deck', sid=s['id']), _ICON_PLAY, len(pages)))
    if pptx:
        cls = 'dl' if pages else 'go'
        buttons.append(
            '<a class="%s" href="/api/story/ppt/%s">%s下载 PPTX<em>%s%s</em></a>'
            % (cls, quote(s['id']), _ICON_DOWN,
               escape(REG.human_size(pptx) or ''),
               (' · %d 页' % s['deck_pages']) if s.get('deck_pages') else ''))

    if pages:
        tip = ('浏览器直接开讲：<code>←</code> <code>→</code> 翻页 · <code>F</code> 全屏 · '
               '<code>ESC</code> 总览 · <code>P</code> 演讲备注<br>'
               '在放映页按 <code>Ctrl+P</code> 即可打印成 PDF（一页一张，横向）')
    elif pptx:
        tip = ('PPT 已入库，页图还没生成 —— 到<a href="/story/admin">管理页</a>点'
               '「重建页图」即可转出网页放映版。')
    else:
        tip = ('本份只入库了正文档案（STORY.md），没有 PPT，因此没有网页放映入口。<br>'
               '在<a href="/story/admin">管理页</a>传一份 .pptx 就会自动转出页图与放映版。')

    tags = ''.join('<span>%s</span>' % escape(t) for t in s.get('tags') or [])

    # 文件清单：把「东西放在哪」直接写在页面上，方便核对与搬运
    docs = []
    if md:
        docs.append('<a class="doc" href="/api/story/md/%s"><span class="k">MD</span>'
                    '<span class="v">%s</span><span class="s">%s</span></a>'
                    % (quote(s['id']), escape(md.name),
                       escape(REG.human_size(md) or '')))
    if pptx:
        docs.append('<a class="doc" href="/api/story/ppt/%s"><span class="k">PPTX</span>'
                    '<span class="v">%s</span><span class="s">%s</span></a>'
                    % (quote(s['id']), escape(pptx.name),
                       escape(REG.human_size(pptx) or '')))
    if pages:
        docs.append('<span class="doc"><span class="k">页图</span>'
                    '<span class="v">deck/p01.png … p%02d.png</span>'
                    '<span class="s">%d 张</span></span>' % (len(pages), len(pages)))
    else:
        docs.append('<span class="doc off"><span class="k">页图</span>'
                    '<span class="v">未入库</span>'
                    '<span class="s">放入 deck/ 即启用放映</span></span>')

    return (
        '<section class="hero">\n'
        '  <!-- 校名标：两张按主题切换（见 story.html 的 .only-light / .only-dark）。\n'
        '       深色版是深红土色，压在深底上几乎不可见 —— 所以不能只用一张。 -->\n'
        '  <img class="hero-logo only-light" alt="昆明理工大学"\n'
        '       src="/api/story/assets/brand/kust_logo_h.png"\n'
        '       onerror="this.style.display=\'none\'">\n'
        '  <img class="hero-logo only-dark" alt="昆明理工大学"\n'
        '       src="/api/story/assets/brand/kust_logo_h_white.png"\n'
        '       onerror="this.style.display=\'none\'">\n'
        '  <p class="eyebrow">%s</p>\n'
        '  <h1>%s</h1>\n'
        '  <p class="sub">%s</p>\n'
        '  %s\n'
        '  <div class="cta">%s<span class="tip">%s</span></div>\n'
        '  <div class="docs">%s</div>\n'
        '</section>'
        % (escape(s.get('eyebrow') or ''),
           escape(s['title']), escape(s.get('subtitle') or ''),
           ('<div class="tags">%s</div>' % tags) if tags else '',
           ''.join(buttons), tip, ''.join(docs))
    )


def _md_body(s):
    """md 汇报的正文：目录胶囊 + 渲染后的 STORY.md。

    STORY.md 是**可选**的 —— 「只发 PPT」那一档就故意没有它，
    页面只剩 hero（放映入口 + PPT 下载）。所以这里给的是说明而不是报错。
    """
    p = REG.md_path(s)
    if not p:
        return ('<section id="s-none"><div class="sec-h"><span class="sec-n">放映</span>'
                '<h2>本份没有正文档案</h2></div>'
                '<p class="lead">这一份只入库了 PPT 与网页放映版，'
                '因此只有上方的放映入口与 PPT 下载，没有可渲染的正文。<br>'
                '需要正文时，把 <span class="mono">STORY.md</span> 放进 '
                '<span class="mono">docs/%s/</span>（或在<a href="/story/admin">管理页</a>'
                '粘贴一份），刷新即可。</p></section>' % escape(s['id']))

    try:
        text = p.read_text(encoding='utf-8')
    except OSError as e:
        return ('<section id="s-none"><div class="sec-h"><span class="sec-n">!</span>'
                '<h2>STORY.md 读取失败</h2></div>'
                '<p class="lead">%s</p></section>' % escape(str(e)))

    html, toc = render_md(text, s.get('section_first'))
    nav = ''
    if toc:
        nav = ('<nav class="toc">%s</nav>'
               % ''.join('<a href="#%s">%02d　%s</a>' % (anchor, i, escape(title))
                         for i, (anchor, title) in enumerate(toc, 1)))
    return '%s\n<article class="md">\n%s\n</article>' % (nav, html)


def _foot_html(s):
    """页脚右侧：优先给放映入口，其次给 PPT 下载，都没有就指向门户。"""
    pages = REG.deck_files(s)
    if pages:
        return '<a href="%s">进入放映版 →</a>' % url_for('story.story_deck', sid=s['id'])
    if REG.pptx_path(s):
        return '<a href="/api/story/ppt/%s">下载 PPTX →</a>' % quote(s['id'])
    return '<a href="/story">汇报清单</a>'


def _js_html(s):
    """该汇报自己的页面增强脚本（可选）。没有就留个注释，避免空 src 加载本页。"""
    p = REG.js_path(s)
    if not p:
        return '<!-- 本汇报无自定义脚本 -->'
    return '<script src="/api/story/asset/%s/%s"></script>' % (quote(s['id']), quote(p.name))


def _missing(sid):
    return Response(
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
        '<title>没有这份汇报 · kust.work</title>'
        '<script src="/theme.js"></script></head>'
        '<body style="font-family:-apple-system,\'PingFang SC\',sans-serif;'
        'background:var(--bg,#2b2b2b);color:var(--text,#e8e4df);padding:56px;line-height:1.7">'
        '<h2 style="margin-bottom:8px">没有这份汇报</h2>'
        '<p style="color:var(--muted,#8a867f);font-size:13px">未注册的 id：'
        '<span style="font-family:ui-monospace,Consolas,monospace">%s</span></p>'
        '<p style="margin-top:16px"><a href="/story" style="color:var(--ku,#d3a48e);'
        'font-size:13px">返回汇报清单</a></p></body></html>' % escape(sid),
        status=404, content_type='text/html; charset=utf-8')


def _js_str(s):
    """把字符串安全地塞进 JS 字面量（本项目里只有路径，够用且无歧义）。"""
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _ascii_fallback(name):
    """Content-Disposition 的 `filename=` 只能放 ASCII，真正的文件名交给 `filename*`（UTF-8）。

    纯中文名（如「组会汇报….pptx」）剥掉非 ASCII 后只剩 `-.pptx` —— 主名是空的，
    既难看又像隐藏文件。所以判据是「**主名（去扩展名）里至少有一个字母或数字**」，
    不满足就退回 `story<扩展名>`。
    """
    stem = re.sub(r'[^A-Za-z0-9._-]', '',
                  Path(name).stem.encode('ascii', 'ignore').decode('ascii')).strip('._- ')
    ext = Path(name).suffix
    if stem:
        return stem + ext
    return ('story' + ext) if ext else 'download'


_ICON_PLAY = ('<svg viewBox="0 0 24 24" aria-hidden="true">'
              '<path d="M8 5.14v13.72L19 12z"/></svg>')
_ICON_DOWN = ('<svg viewBox="0 0 24 24" aria-hidden="true">'
              '<path d="M12 3v12l5-5-1.4-1.4L12 12.2V3h0zM5 18h14v2H5z"/>'
              '<path d="M11 3h2v9.2l2.6-2.6L17 11l-5 5-5-5 1.4-1.4L11 12.2z"/></svg>')
