# -*- coding: utf-8 -*-
"""译文归档模块

以「一篇论文一行」为粒度组织**最近 48 小时**产出或上传的 PDF：

  行 = 原文标题（由文件名清洗而来）
  行内 = 双语 / 中文 / 原文 三个按钮，点击即在浏览器里打开对应版本

按钮是普通的 <a target="_blank">，指向 web_app.py 既有的
`/uploads/*` 与 `/translated/*` 静态路由 —— PDF 由浏览器原生阅读器渲染，
查看与下载都走浏览器自己的 PDF 工具条，服务端不做任何下载封装，
因此不存在「打包/附件流」带来的下载报错面。

三个版本同源时清洗出的标题一致（`X_a775d2f5.no_watermark.zh.dual.pdf`
与 `X_a775d2f5.pdf` 都归到 `X`），所以同一篇论文的三个版本会落在同一行。

页面：GET /archive
接口：GET /api/archive/list                    论文分组视图（页面用）
      GET /api/archive/list?kind=dual|mono|original|translated|upload
                                              旧扁平视图，保持兼容

⚠️ 保留期依赖：本模块只负责「展示窗口」，真正决定文件活多久的是
web_app.py 的 RETENTION_SECONDS。若保留期短于 ARCHIVE_WINDOW_HOURS，
窗口内自然会是空的 —— 两者必须一起调。
"""
import re
import time
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, jsonify, request

from portal.templating import render_html

archive_bp = Blueprint('archive', __name__)

_TEMPLATE = Path(__file__).parent / 'templates' / 'archive.html'

# 展示窗口：只看最近 48 小时的文件（需与 web_app.py 的 RETENTION_SECONDS 匹配）
ARCHIVE_WINDOW_HOURS = 48

# 单次最多返回的论文数
_LIMIT = 300

# 由 init_archive 注入
_dirs = {'translated': None, 'upload': None}

# (版本 id, 显示名, 目录键, 文件名后缀过滤, URL 前缀)
#   后缀为 None 表示该目录下所有 .pdf 都算
_VARIANTS = [
    ('dual',     '双语', 'translated', '.zh.dual.pdf', '/translated'),
    ('mono',     '中文', 'translated', '.zh.mono.pdf', '/translated'),
    ('original', '原文', 'upload',     None,            '/uploads'),
]

_BY_KIND = {v[0]: v for v in _VARIANTS}

# 旧 kind 参数 → 新版本 id（translated 是「双语+中文」的历史混合视图）
_LEGACY = {'translated': ('dual', 'mono'), 'upload': ('original',)}

# 文件名清洗：去掉 .no_watermark 与 _xxxxxxxx 哈希后缀，得到可读标题
_HASH_RE = re.compile(r'[_]{1,2}[a-f0-9]{8}$', re.IGNORECASE)


def init_archive(app, upload_dir=None, translated_dir=None):
    """注册归档模块。"""
    _dirs['translated'] = Path(translated_dir) if translated_dir else None
    _dirs['upload'] = Path(upload_dir) if upload_dir else None
    app.register_blueprint(archive_bp)
    print('[模块] 译文归档 → GET /archive （按论文分组 · 近 %d 小时 · 点按钮开 PDF）'
          % ARCHIVE_WINDOW_HOURS)


def _clean_title(name, suffix=None):
    """'X-2025b_a775d2f5.no_watermark.zh.dual.pdf' → 'X-2025b'

    三个版本的同源文件清洗后标题一致，因此可以据此聚合成一行。
    """
    t = name
    if suffix and t.lower().endswith(suffix.lower()):
        t = t[:-len(suffix)]
    if not suffix and t.lower().endswith('.pdf'):
        t = t[:-4]
    t = re.sub(r'\.no_watermark$', '', t, flags=re.IGNORECASE)
    t = _HASH_RE.sub('', t)
    return t or name


def _scan_variant(variant):
    """扫描单个版本，返回窗口内的文件列表（按修改时间倒序）。"""
    _, _, dirkey, suffix, url_prefix = variant
    root = _dirs.get(dirkey)
    if not root or not root.exists():
        return []

    cutoff = time.time() - ARCHIVE_WINDOW_HOURS * 3600
    items = []
    try:
        for p in root.iterdir():
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
            items.append({
                'name': p.name,
                'title': _clean_title(p.name, suffix),
                'size': st.st_size,
                'mtime': st.st_mtime,
                'url': '%s/%s' % (url_prefix, quote(p.name)),
            })
    except OSError:
        return []

    items.sort(key=lambda x: x['mtime'], reverse=True)
    return items


def _papers():
    """按原文标题聚合成论文列表，最新修改的排前面。"""
    groups = {}

    for variant in _VARIANTS:
        kind = variant[0]
        for it in _scan_variant(variant)[:_LIMIT]:
            key = it['title']
            g = groups.get(key)
            if g is None:
                g = groups[key] = {
                    'title': key,
                    'mtime': 0.0,
                    'files': {},
                    'orig_title': None,   # 优先用「原文」的标题
                }
            # 同版本重复文件（理论上不该有）取最新的
            old = g['files'].get(kind)
            if old is None or it['mtime'] > old['mtime']:
                g['files'][kind] = it
            if it['mtime'] > g['mtime']:
                g['mtime'] = it['mtime']
            if kind == 'original':
                g['orig_title'] = it['title']

    out = []
    for g in groups.values():
        files = []
        for variant in _VARIANTS:          # 固定顺序：双语 / 中文 / 原文
            kind, label = variant[0], variant[1]
            f = g['files'].get(kind)
            files.append({
                'kind': kind,
                'label': label,
                'available': f is not None,
                'name': f['name'] if f else None,
                'size': f['size'] if f else 0,
                'mtime': f['mtime'] if f else 0,
                'url': f['url'] if f else None,
            })
        out.append({
            'title': g['orig_title'] or g['title'],
            'mtime': g['mtime'],
            'file_count': sum(1 for f in files if f['available']),
            'total_size': sum(f['size'] for f in files),
            'files': files,
        })

    out.sort(key=lambda x: x['mtime'], reverse=True)
    return out[:_LIMIT]


def _flat(kind):
    """旧扁平视图：某版本的全部文件。"""
    items = _scan_variant(_BY_KIND[kind])[:_LIMIT]
    return {
        'kind': kind,
        'label': _BY_KIND[kind][1],
        'total': len(items),
        'limited': len(items) >= _LIMIT,
        'window_hours': ARCHIVE_WINDOW_HOURS,
        'items': items,
    }


@archive_bp.route('/archive')
def archive_page():
    return render_html(_TEMPLATE)


@archive_bp.route('/api/archive/list')
def archive_list():
    raw = (request.args.get('kind') or '').strip().lower()

    # 无参数 / all：论文分组视图（页面就用这个）
    if not raw or raw == 'all':
        papers = _papers()
        return jsonify({
            'window_hours': ARCHIVE_WINDOW_HOURS,
            'total_papers': len(papers),
            'total_files': sum(p['file_count'] for p in papers),
            'papers': papers,
        })

    # 旧别名：translated 合并「双语 + 中文」，upload 等价于 original
    if raw in _LEGACY:
        merged = []
        for k in _LEGACY[raw]:
            merged.extend(_scan_variant(_BY_KIND[k]))
        merged.sort(key=lambda x: x['mtime'], reverse=True)
        return jsonify({
            'kind': raw,
            'label': '译文' if raw == 'translated' else '原文',
            'total': len(merged),
            'limited': len(merged) >= _LIMIT,
            'window_hours': ARCHIVE_WINDOW_HOURS,
            'items': merged[:_LIMIT],
        })

    if raw in _BY_KIND:
        return jsonify(_flat(raw))

    return jsonify({
        'error': 'kind must be one of: dual, mono, original, translated, upload, all'
    }), 400
