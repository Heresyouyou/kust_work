# -*- coding: utf-8 -*-
"""PDF 阅读器模块 —— 自研内核，不依赖浏览器原生 PDF 渲染

为什么要有这个模块
------------------
门户里原有的做法是把 PDF 丢给浏览器内置阅读器（`<a target="_blank">`），
或者把浏览器内置阅读器塞进 iframe、再用 Chromium 私有的 postMessage 协议
去同步左右两侧。后者在本项目里有个致命的先天缺陷：

    同一篇论文，原文 22 页、中文版 10 页、双语版 10 页（实测数据）。
    Chromium 的 PDF 插件只能按**页号**同步，页数不同就永远对不齐。

本模块把整个人机界面换成自研的 canvas 阅读器：
  · 解码/光栅化用本地 vendor 的 pdf.js（纯 JS，不碰浏览器 PDF 插件）
  · 文档模型、布局、视口、渲染调度、**跨文档同步**、UI 全部自研
  · 同步以「归一化锚点」为单位，页数不同也能保持读到同一段内容

访问方式
--------
  GET /reader                              阅读器页面（带 file/peer 直接打开）
  GET /PDFreader                           调试入口（无参显示文件选择器；带参同上）
  GET /PDFreader/auto                      自动挑最近一篇（原文+中文→对照；只有原文→单栏；都没有→回到 /PDFreader）
      ?file=<原文URL>&peer=<译文URL>       直接打开（可带 layout/fit/sync/sidebar/title）
      ?src=<URL>                           单栏简写
      常见组合：
        file=原文  & peer=中文   → 英文原文 ⟷ 中文版 对照（用户主诉）
        file=原文  & peer=双语   → 英文原文 ⟷ 双语版 对照（次选）
        file=双语                → 双语版 单栏
        file=中文                → 中文版 单栏
  GET /api/reader/info                     内核与构建信息（自检）
  GET /api/reader/files                    最近可读 PDF（页面「最近文件」用）
  GET /api/reader/assets/<path:rel>        框架源码 + 内核静态资源

资产路径约定
------------
页面路径在 /reader，但**静态资源一律走 /api/reader/assets/**（项目既有铁律：
页面可带前缀，API 与静态资源留根路径；而且 ES Module 的相对导入要靠这条才成立）。
"""
import time
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, Response, jsonify, request, send_file

from portal.templating import render_html

reader_bp = Blueprint('reader', __name__)

_DIR = Path(__file__).parent
_ASSETS = (_DIR / 'assets').resolve()
_TEMPLATE = _DIR / 'templates' / 'reader.html'
_VENDOR = _ASSETS / 'vendor' / 'pdfjs'
_VENDOR_META = _VENDOR / 'VENDOR.json'

# 「最近文件」窗口（天）
FILES_WINDOW_DAYS = 30
_FILES_LIMIT = 40

# 由 init_reader 注入
_dirs = {'upload': None, 'translated': None}

# 扩展名 → MIME。
# ⚠️ .mjs 必须是 text/javascript：ES Module 会被浏览器按 MIME 严格校验，
#    给成 application/octet-stream 会直接拒绝加载（整页白屏）。
_MIME = {
    '.mjs': 'text/javascript; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.map': 'application/json; charset=utf-8',
    '.wasm': 'application/wasm',
    '.pfb': 'application/octet-stream',
    '.ttf': 'font/ttf',
    '.otf': 'font/otf',
    '.bcmap': 'application/octet-stream',
    '.icc': 'application/octet-stream',
    '.md': 'text/markdown; charset=utf-8',
    '.txt': 'text/plain; charset=utf-8',
}

# (版本 id, 显示名, 目录键, 文件名后缀, URL 前缀)
_VARIANTS = [
    ('dual', '双语', 'translated', '.zh.dual.pdf', '/translated'),
    ('mono', '中文', 'translated', '.zh.mono.pdf', '/translated'),
    ('original', '原文', 'upload', None, '/uploads'),
]


def init_reader(app, upload_dir=None, translated_dir=None):
    """注册阅读器模块。"""
    _dirs['upload'] = Path(upload_dir) if upload_dir else None
    _dirs['translated'] = Path(translated_dir) if translated_dir else None
    app.register_blueprint(reader_bp)
    print('[模块] PDF 阅读器   → GET /reader （自研内核 · 左右同步 · 不依赖浏览器 PDF 插件）')


# ═══════════════════════════════════════════════════════════════
#  页面
# ═══════════════════════════════════════════════════════════════
@reader_bp.route('/reader')
@reader_bp.route('/PDFreader')
def reader_page():
    """阅读器主入口。

    - 带 file/peer/src → 直接渲染阅读器
    - 无参（/PDFreader 调试入口）→ 渲染服务器端的落地页：自检 + 最近文件 + 一键对照
    """
    if not _has_reader_params(request.args):
        return _render_debug_landing()
    return render_html(_TEMPLATE)


@reader_bp.route('/PDFreader/auto')
def reader_auto():
    """自动选最近一篇已有「原文 + 中文」的论文，用对照模式直接打开。

    优先级：原文+中文（mono）→ 对照模式 → 用户主要诉求是「左英文 右中文」
    退而求其次：原文+双语（dual）→ 双语对照（仍可见左英文）
    最后退到：仅原文 → 单栏
    都没有就回到 /PDFreader 让用户挑。
    """
    cutoff = time.time() - FILES_WINDOW_DAYS * 86400
    best = None  # (mtime, original_url, peer_url, peer_kind)

    trans_root = _dirs.get('translated')
    if trans_root and trans_root.exists():
        try:
            entries = list(trans_root.iterdir())
        except OSError:
            entries = []
        # 第一遍：原文 + 中文（mono）—— 用户主诉
        for p in entries:
            if not p.is_file() or not p.name.lower().endswith('.zh.mono.pdf'):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff:
                continue
            base = _clean_title(p.name, '.zh.mono.pdf')
            orig = _find_original_for_base(base)
            if not orig:
                continue
            cand = (st.st_mtime, orig,
                    '%s/%s' % ('/translated', quote(p.name)), 'mono')
            if best is None or cand[0] > best[0]:
                best = cand

        # 第二遍：原文 + 双语（dual）—— 退路，仍可看英文
        if best is None:
            for p in entries:
                if not p.is_file() or not p.name.lower().endswith('.zh.dual.pdf'):
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_mtime < cutoff:
                    continue
                base = _clean_title(p.name, '.zh.dual.pdf')
                orig = _find_original_for_base(base)
                if not orig:
                    continue
                cand = (st.st_mtime, orig,
                        '%s/%s' % ('/translated', quote(p.name)), 'dual')
                if best is None or cand[0] > best[0]:
                    best = cand

    if best is None:
        # 退而求其次：最近原文
        up = _dirs.get('upload')
        if up and up.exists():
            try:
                latest = max(
                    (p for p in up.iterdir() if p.is_file() and p.name.lower().endswith('.pdf')),
                    key=lambda p: p.stat().st_mtime,
                    default=None,
                )
            except OSError:
                latest = None
            if latest and latest.stat().st_mtime >= cutoff:
                best = (latest.stat().st_mtime,
                        '/uploads/%s' % quote(latest.name), None, None)

    if best is None:
        return Response(
            '<!doctype html><html lang="zh-CN"><head>'
            '<meta charset="utf-8"><title>PDFreader</title>'
            # 主题引擎：同步加载，浅色由它注入 :root[data-theme="light"] 覆盖。
            '<script src="/theme.js"></script>'
            '<style>'
            # 深色兜底 token；浅色由 theme.js 覆盖同名变量。
            ':root{--bg:#2b2b2b;--surface:#1f1f1f;--text:#e8e4df;--muted:#8a867f;--accent:#4a9e6a;--vb-hover-fg:#9dc9ae;}'
            'body{font:14px/1.6 -apple-system,sans-serif;padding:40px;'
            'background:var(--bg);color:var(--text);}'
            'a{color:var(--vb-hover-fg);}'
            '</style></head><body>'
            '<h2>暂无可用 PDF</h2><p>近 %d 天内 <code>uploads/</code> 和 '
            '<code>translated/</code> 目录都没有文件。先去翻译页生成一份，'
            '再回到 <a href="/PDFreader">/PDFreader</a>。</p>'
            '</body></html>' % FILES_WINDOW_DAYS,
            content_type='text/html; charset=utf-8',
        )

    _, file_url, peer_url, peer_kind = best
    # file_url/peer_url 内已含 quote() 编码（%20），外层再 quote 会把 % 编成 %25。
    # 这里我们只把它们放进 URL 的 query，需要再 encode 一次整体；safe='/%' 让已编码的 %20 与
    # 路径分隔符 / 不再被改写。
    qs = 'file=' + quote(file_url, safe='/%')
    if peer_url:
        qs += '&peer=' + quote(peer_url, safe='/%') + '&layout=continuous&sync=1'
    return Response('', status=302, headers={'Location': '/PDFreader?' + qs})


def _has_reader_params(args) -> bool:
    return any(args.get(k) for k in ('file', 'src'))


def _find_original_for_base(base):
    """按论文基名（如 'Ce-Fang Liu-2025b'）在 uploads/ 找对应原文。"""
    up = _dirs.get('upload')
    if not up or not up.exists():
        return None
    try:
        for p in up.iterdir():
            if p.is_file() and p.name.lower().endswith('.pdf'):
                # _clean_title 不带后缀时只剥 .pdf；这里需要保留后缀剥离哈希
                if _clean_title(p.name).startswith(base):
                    return '/uploads/%s' % quote(p.name)
    except OSError:
        return None
    return None


def _render_debug_landing():
    """自包含的调试落地页：不依赖 reader 自身（reader 挂了也能选文件）。

    包含：
      · 自检（/api/reader/info）
      · 最近 N 天文件聚合（/api/reader/files）
      · 三个快捷入口：原文 / 中文 / 双语 / 对照模式
      · 调试提示：F12 控制台、window.__reader、键盘快捷键
    """
    return Response(_LANDING_HTML, content_type='text/html; charset=utf-8')


_LANDING_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PDFreader · 调试入口 · kust.work</title>
<script src="/theme.js"></script>
<style>
  :root {
    /* 页面不加载 reader.css，所以这里要自备深色兜底 token 集（与门户一致）。
       浅色由 /theme.js 注入的 :root[data-theme="light"] 覆盖同名变量。 */
    --bg: #2b2b2b; --surface: #1f1f1f; --surface-2: #262626;
    --border: #3a3a3a; --border-hover: #4a4a4a;
    --text: #e8e4df; --muted: #8a867f; --dim: #6f6b64;
    --accent: #4a9e6a; --accent-dim: #2d6e46; --accent-line: #2d6e46;
    --accent-soft: #232823;
    --vb-hover-bg: #24352a; --vb-hover-fg: #9dc9ae;
    --err: #c0524a; --placeholder: #6a665f;
    --gap:14px; --r:8px;
  }
  html,body { margin:0; padding:0; }
  body {
    background: var(--bg); color: var(--text);
    font: 14px/1.55 -apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
    min-height:100vh; box-sizing:border-box;
  }
  .wrap { max-width: 980px; margin: 0 auto; padding: 28px 22px 56px; }
  header { display:flex; align-items:flex-end; gap:14px; flex-wrap:wrap; margin-bottom: 22px; }
  h1 { margin:0; font-size: 22px; font-weight: 600; letter-spacing: .3px; }
  h1 .badge { display:inline-block; margin-left:8px; padding:1px 8px; font-size:11px;
    border-radius:10px;
    background: color-mix(in srgb, var(--accent) 18%, transparent);
    color: var(--vb-hover-fg);
    border:1px solid color-mix(in srgb, var(--accent) 35%, transparent);
    vertical-align:middle; }
  .sub { color: var(--muted); font-size: 13px; }
  a.back { margin-left:auto; font-size:12px; color: var(--muted);
    text-decoration:none; padding:4px 10px; border-radius:14px;
    border:1px solid var(--border); }
  a.back:hover { color: var(--vb-hover-fg); border-color: color-mix(in srgb, var(--accent) 50%, transparent); }
  section { margin-top: 26px; }
  h2 { font-size: 13px; font-weight: 600; color: var(--muted);
    text-transform: uppercase; letter-spacing: .8px; margin: 0 0 10px; }
  .panel { background: var(--surface); border:1px solid var(--border);
    border-radius: var(--r); padding: 14px 16px; }
  .grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(280px,1fr)); gap: 10px; }
  .paper { background: var(--surface-2); border:1px solid var(--border);
    border-radius: var(--r); padding: 12px 14px; transition: border-color .15s; }
  .paper:hover { border-color: color-mix(in srgb, var(--accent) 45%, transparent); }
  .paper h3 { margin:0 0 8px; font-size: 14px; font-weight: 600;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .paper .meta { font-size: 11px; color: var(--muted); margin-bottom: 10px; }
  .row { display:flex; flex-wrap:wrap; gap: 6px; }
  .btn {
    display:inline-block; font-size: 12px; padding: 4px 10px; border-radius: 14px;
    text-decoration:none; color: var(--text);
    background: var(--surface-2); border:1px solid var(--border);
    cursor: pointer;
  }
  .btn:hover { background: color-mix(in srgb, var(--accent) 18%, transparent);
    border-color: color-mix(in srgb, var(--accent) 50%, transparent); color: var(--vb-hover-fg); }
  .btn.dual { background: color-mix(in srgb, var(--accent) 12%, transparent);
    border-color: color-mix(in srgb, var(--accent) 35%, transparent); color: var(--vb-hover-fg); }
  .btn.dual:hover { background: color-mix(in srgb, var(--accent) 25%, transparent); }
  .empty { color: var(--muted); font-style: italic; padding: 8px 4px; }
  kbd { display:inline-block; font-family: ui-monospace, Menlo, Consolas, monospace;
    font-size: 11px; padding: 1px 6px; border-radius: 4px;
    background: var(--surface-2); border:1px solid var(--border); }
  code { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12.5px;
    background: var(--surface-2); padding: 1px 6px; border-radius: 4px; }
  .selftest { display:grid; grid-template-columns: auto 1fr; gap: 6px 14px; font-size: 12.5px; }
  .selftest .k { color: var(--muted); }
  .selftest .v { font-family: ui-monospace, Menlo, Consolas, monospace; }
  .ok { color: var(--vb-hover-fg); } .bad { color: var(--err); }
  .quickbar { display:flex; gap: 10px; flex-wrap:wrap; }
  .hint { background: var(--surface); border-left: 3px solid color-mix(in srgb, var(--accent) 50%, transparent);
    border-radius: 0 var(--r) var(--r) 0; padding: 10px 14px; font-size: 12.5px; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>PDFreader <span class="badge">DEBUG</span></h1>
    <span class="sub">自研内核阅读器调试入口 · 不依赖浏览器原生 PDF 插件</span>
    <a class="back" href="/">&larr; 工作台</a>
  </header>

  <section>
    <h2>快速入口</h2>
    <div class="quickbar panel">
      <a class="btn dual" href="/PDFreader/auto">▶ 自动打开最近一篇（有中文则 英文⟷中文 对照；只有双语就 英文⟷双语）</a>
      <a class="btn" href="/reader" target="_blank">空阅读器（自己拖文件）</a>
      <a class="btn" href="/api/reader/info" target="_blank">/api/reader/info 自检</a>
    </div>
  </section>

  <section>
    <h2>自检</h2>
    <div class="panel selftest" id="selftest">
      <span class="k">状态</span><span class="v">加载中…</span>
    </div>
  </section>

  <section>
    <h2>最近论文 <span class="sub" id="files_meta"></span></h2>
    <div class="panel" id="files">
      <div class="empty">加载中…</div>
    </div>
  </section>

  <section>
    <h2>调试提示</h2>
    <div class="hint">
      阅读器打开后按 <kbd>F12</kbd> 看控制台；实例挂在 <code>window.__reader</code>：
      <code>__reader.gotoPage(5)</code> · <code>__reader.setScale(1.2)</code> ·
      <code>__reader.toggleSync()</code> · <code>__reader.find('partial melting')</code> ·
      <code>__reader.stats()</code>。
      <br>快捷键：<kbd>+</kbd>/<kbd>-</kbd> 缩放 · <kbd>0</kbd> 适配宽度 ·
      <kbd>f</kbd> 查找 · <kbd>s</kbd> 开关同步 · <kbd>←</kbd>/<kbd>→</kbd> 翻页。
    </div>
  </section>
</div>

<script>
(async () => {
  const $ = (s, r=document) => r.querySelector(s);
  const el = (t, c={}, ...kids) => {
    const e = document.createElement(t);
    for (const [k, v] of Object.entries(c)) {
      if (k === 'class') e.className = v;
      else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2).toLowerCase(), v);
      else if (v != null) e.setAttribute(k, v);
    }
    for (const k of kids) {
      if (k == null || k === false) continue;       // 允许 `cond && el(...)` 的零/假短路
      e.append(k.nodeType ? k : document.createTextNode(k));
    }
    return e;
  };

  // ── 自检
  try {
    const info = await fetch('/api/reader/info').then(r => r.json());
    const ok = info.ok && info.has_template && info.counts.cmaps > 100 && info.counts.wasm >= 4;
    const st = $('#selftest');
    st.innerHTML = '';
    const row = (k, v, cls='') => { st.append(el('span', {class:'k'}, k)); st.append(el('span', {class:'v '+cls}, v)); };
    row('模块', info.module);
    row('OK', ok ? '✓ 正常' : '✗ 异常', ok ? 'ok' : 'bad');
    row('模板', info.has_template ? 'reader.html ✓' : 'reader.html 缺失 ✗', info.has_template ? 'ok' : 'bad');
    row('内核模块', info.core_modules.length + ' 个');
    row('UI 模块', info.ui_modules.length + ' 个');
    row('cmaps', info.counts.cmaps + ' 个');
    row('wasm', info.counts.wasm + ' 个');
    row('main pdf', (info.builds['pdf.min.mjs']/1024|0) + ' KB · ' + (info.builds['pdf.worker.min.mjs']/1024|0) + ' KB(worker)');
    row('legacy pdf', (info.builds['legacy/pdf.min.mjs']/1024|0) + ' KB · ' + (info.builds['legacy/pdf.worker.min.mjs']/1024|0) + ' KB(worker)');
  } catch (e) {
    $('#selftest').innerHTML = '<span class="k">状态</span><span class="v bad">自检失败: ' + e.message + '</span>';
  }

  // ── 最近文件
  try {
    const data = await fetch('/api/reader/files').then(r => r.json());
    $('#files_meta').textContent = '· 共 ' + data.total + ' 篇 · 窗口 ' + data.window_days + ' 天';
    const box = $('#files');
    if (!data.papers.length) { box.innerHTML = '<div class="empty">没有近 ' + data.window_days + ' 天的 PDF。先去翻译页生成。</div>'; return; }
    box.innerHTML = '';
    for (const p of data.papers) {
      const fmap = Object.fromEntries(p.files.map(f => [f.kind, f]));
      const o = fmap.original, m = fmap.mono, d = fmap.dual;
      const ago = ((Date.now()/1000 - p.mtime) / 3600).toFixed(1);
      const card = el('div', {class:'paper'},
        el('h3', {title: p.title}, p.title),
        el('div', {class:'meta'}, ago + ' 小时前 · ' + p.files.map(f => f.label).join(' / ')),
        el('div', {class:'row'},
          o && el('a', {class:'btn', href:'/PDFreader?file=' + encodeURIComponent(o.url), target:'_blank'}, '英文原文'),
          m && el('a', {class:'btn', href:'/PDFreader?file=' + encodeURIComponent(m.url), target:'_blank'}, '中文版'),
          d && el('a', {class:'btn', href:'/PDFreader?file=' + encodeURIComponent(d.url), target:'_blank'}, '双语版'),
          // 主对照按钮：左英文 ⟷ 右中文（用户主诉场景）
          o && m && el('a', {class:'btn dual', href:'/PDFreader?file=' + encodeURIComponent(o.url) + '&peer=' + encodeURIComponent(m.url) + '&layout=continuous&sync=1', target:'_blank'}, '英文原文 ⟷ 中文版 对照'),
          // 双语对照：左英文 ⟷ 右双语（仍可读）
          o && d && el('a', {class:'btn', href:'/PDFreader?file=' + encodeURIComponent(o.url) + '&peer=' + encodeURIComponent(d.url) + '&layout=continuous&sync=1', target:'_blank'}, '英文原文 ⟷ 双语版 对照'),
        ),
      );
      box.appendChild(card);
    }
  } catch (e) {
    $('#files').innerHTML = '<div class="empty bad">最近文件加载失败: ' + e.message + '</div>';
  }
})();
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════
#  静态资产（框架源码 + vendor 内核）
# ═══════════════════════════════════════════════════════════════
@reader_bp.route('/api/reader/assets/<path:rel>')
def reader_asset(rel):
    """白名单式静态服务：只允许 assets/ 目录下的文件，防目录穿越。"""
    try:
        target = (_ASSETS / rel).resolve()
        target.relative_to(_ASSETS)          # 越界会抛 ValueError
    except (ValueError, OSError):
        return Response('/* denied */\n', status=403, content_type='text/plain; charset=utf-8')

    if not target.is_file():
        return Response('/* not found: %s */\n' % rel,
                        status=404, content_type='text/plain; charset=utf-8')

    resp = send_file(target, conditional=True)
    # 直接赋 header，不用 mimetype=（Werkzeug 会再追加一个 charset，得到畸形头）
    resp.headers['Content-Type'] = _MIME.get(target.suffix.lower(), 'application/octet-stream')
    # vendor 下的 pdf.js 内容稳定 → 长缓存；框架自身源码在迭代 → 每次带 ETag 回验
    if target.is_relative_to(_VENDOR):
        resp.headers['Cache-Control'] = 'public, max-age=604800, immutable'
    else:
        resp.headers['Cache-Control'] = 'no-cache'
    return resp


# ═══════════════════════════════════════════════════════════════
#  自检信息
# ═══════════════════════════════════════════════════════════════
@reader_bp.route('/api/reader/info')
def reader_info():
    """内核与构建信息。排查「阅读器打不开」时先看这里。"""
    import json

    meta = {}
    try:
        meta = json.loads(_VENDOR_META.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        meta = {'error': 'VENDOR.json 读取失败'}

    core = sorted(p.name for p in (_ASSETS / 'kpdf').glob('*.js'))
    ui = sorted(p.name for p in (_ASSETS / 'ui').glob('*.js'))

    builds = {}
    for rel in ('pdf.min.mjs', 'pdf.worker.min.mjs',
                'legacy/pdf.min.mjs', 'legacy/pdf.worker.min.mjs'):
        p = _VENDOR / rel
        builds[rel] = p.stat().st_size if p.is_file() else None

    return jsonify({
        'ok': True,
        'module': 'reader',
        'vendored': meta,
        'core_modules': core,
        'ui_modules': ui,
        'builds': builds,
        'counts': {
            'cmaps': len(list((_VENDOR / 'cmaps').glob('*.bcmap'))),
            'standard_fonts': len(list((_VENDOR / 'standard_fonts').glob('*'))),
            'wasm': len(list((_VENDOR / 'wasm').glob('*.wasm'))),
        },
        'has_template': _TEMPLATE.is_file(),
    })


# ═══════════════════════════════════════════════════════════════
#  最近文件（供页面无参数时直接点开）
# ═══════════════════════════════════════════════════════════════
def _clean_title(name, suffix=None):
    """与 modules/archive 同一套清洗规则（模块间保持独立，不互相 import）。

    'X-2025b_a775d2f5.no_watermark.zh.dual.pdf' → 'X-2025b'
    """
    import re

    t = name
    if suffix and t.lower().endswith(suffix.lower()):
        t = t[:-len(suffix)]
    if not suffix and t.lower().endswith('.pdf'):
        t = t[:-4]
    t = re.sub(r'\.no_watermark$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'[_]{1,2}[a-f0-9]{8}$', '', t, flags=re.IGNORECASE)
    return t or name


@reader_bp.route('/api/reader/files')
def reader_files():
    """按论文聚合的近 N 天文件，供阅读器落地页与「打开对照」使用。"""
    cutoff = time.time() - FILES_WINDOW_DAYS * 86400
    groups = {}

    for kind, label, dirkey, suffix, prefix in _VARIANTS:
        root = _dirs.get(dirkey)
        if not root or not root.exists():
            continue
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for p in entries:
            if not p.is_file():
                continue
            low = p.name.lower()
            if suffix:
                if not low.endswith(suffix):
                    continue
            elif not low.endswith('.pdf'):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff:
                continue
            title = _clean_title(p.name, suffix)
            g = groups.setdefault(title, {'title': title, 'mtime': 0.0, 'files': []})
            g['mtime'] = max(g['mtime'], st.st_mtime)
            g['files'].append({
                'kind': kind,
                'label': label,
                'name': p.name,
                'size': st.st_size,
                'mtime': st.st_mtime,
                'available': True,
                'url': '%s/%s' % (prefix, quote(p.name)),
            })

    papers = sorted(groups.values(), key=lambda g: g['mtime'], reverse=True)
    order = {v[0]: i for i, v in enumerate(_VARIANTS)}
    for g in papers:
        g['files'].sort(key=lambda f: order.get(f['kind'], 9))

    return jsonify({
        'window_days': FILES_WINDOW_DAYS,
        'total': len(papers),
        'papers': papers[:_FILES_LIMIT],
        'query': request.args.get('q', ''),
    })
