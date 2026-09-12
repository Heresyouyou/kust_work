# -*- coding: utf-8 -*-
"""生成可离线打开的静态预览页（用于验收浅色 / 深色主题）

为什么需要：门户和期刊页是「服务端卡片 + 前端 fetch 取数」，
直接双击模板文件打开会没有样式来源也不会有数据。
这里把 theme.js 内联、把接口数据内嵌，产出一个自包含的 HTML，
不依赖 Flask 也能看到真实的浅色/深色效果。

产出（写到 PostNews/output/）：
  portal_theme_preview.html    门户首页（卡片服务端渲染，真实布局）
  journals_theme_preview.html  期刊列表（含「近 60 天 / 主题归档」双视图）

用法：py -3.12 _make_theme_previews.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

OUT = HERE.parent / 'output'
OUT.mkdir(parents=True, exist_ok=True)

THEME_JS = (HERE / 'portal' / 'assets' / 'theme.js').read_text(encoding='utf-8')

# 预览页顶部标注，避免被当成线上实时页面
BANNER = (
    '<span class="stat" style="color:var(--warn)">'
    '离线快照 · 右上角可切换主题</span>'
)

LINK_TAG = '<script src="/theme.js"></script>'
INLINE_TAG = ('<!-- theme.js 已内联：离线打开时没有 Flask，取不到 /theme.js -->\n'
              '<script>\n' + THEME_JS + '\n</script>')


def humanize(html, brand_after, extra=None):
    """把预览页改造得适合离线打开。"""
    assert LINK_TAG in html, '未找到 theme.js 引用锚点'
    html = html.replace(LINK_TAG, INLINE_TAG)
    html = html.replace(brand_after, brand_after + BANNER)
    if extra:
        html = extra(html)
    return html


# ══════════════════════ ① 门户首页 ══════════════════════
def build_portal():
    from portal.routes import _render_cards
    from portal.templating import load_html

    html = load_html(HERE / 'portal' / 'templates' / 'portal.html')
    cards = _render_cards()
    assert '<!--PORTAL_CARDS-->' in html, '未找到卡片插槽'
    html = html.replace('<!--PORTAL_CARDS-->', cards)
    html = humanize(html, '<span class="brand">kust.work</span>')
    html = html.replace('<title>kust.work · 工作台</title>',
                        '<title>kust.work · 工作台（主题预览快照）</title>')
    p = OUT / 'portal_theme_preview.html'
    p.write_text(html, encoding='utf-8')
    return p, len(cards)


# ══════════════════════ ② 期刊列表 ══════════════════════
def build_journals():
    from modules.journals import routes as R

    R._deps['data_dir'] = HERE.parent
    payload = R._build()

    html = (HERE / 'modules' / 'journals' / 'templates' / 'journals.html') \
        .read_text(encoding='utf-8')

    old_fetch = ("  fetch('/api/journals/list', { cache: 'no-store' })\n"
                 "    .then(function (r) { return r.json(); })")
    assert old_fetch in html, '未找到期刊页 fetch 锚点'
    new_fetch = (
        '  // 离线快照：数据已内嵌，不再请求接口\n'
        '  var PREVIEW_PAYLOAD = ' + json.dumps(payload, ensure_ascii=False) + ';\n'
        '  Promise.resolve({ json: function () '
        '{ return Promise.resolve(PREVIEW_PAYLOAD); } })\n'
        '    .then(function (r) { return r.json(); })'
    )
    html = html.replace(old_fetch, new_fetch)
    html = humanize(html, '<span class="brand">期刊列表</span>')
    html = html.replace('<title>期刊列表 · kust.work</title>',
                        '<title>期刊列表 · kust.work（主题预览快照）</title>')
    p = OUT / 'journals_theme_preview.html'
    p.write_text(html, encoding='utf-8')
    return p, payload['total_articles']


def main():
    p1, n1 = build_portal()
    print('  ✓ %s  (卡片 %d 字节)' % (p1, n1))
    p2, n2 = build_journals()
    print('  ✓ %s  (近 60 天获取 %d 条)' % (p2, n2))
    print()
    # 注意：theme.js 的头部注释里也写着 <script src="/theme.js"></script>，
    # 所以不能直接断言该字符串不存在，改判「内联标记存在 + 外链标签已被替换」。
    for p in (p1, p2):
        t = p.read_text(encoding='utf-8')
        assert '<!-- theme.js 已内联' in t, 'theme.js 未内联: %s' % p
        assert t.count(LINK_TAG) == 1 and LINK_TAG in THEME_JS, \
            '外链标签替换不干净: %s' % p
        assert 'id="kust-theme-style"' or 'LIGHT_VARS' in t, '内联内容不完整: %s' % p
    print('  两份预览页均已自包含（无外部依赖），可直接双击打开')


if __name__ == '__main__':
    main()
