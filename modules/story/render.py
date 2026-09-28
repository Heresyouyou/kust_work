# -*- coding: utf-8 -*-
"""Markdown → HTML（不新增任何依赖）

用 Flask 环境里**本来就有**的 markdown-it-py 渲染，不改 requirements：
  · 预设 commonmark，再手开 table / strikethrough ——
    'gfm-like' 预设会连带打开 linkify，而 linkify-it-py 没装，会直接抛异常；
  · html=False：md 里的原始 HTML 一律当文本转义显示（`<sid>` 会原样显示成 <sid>），
    这是刻意的安全默认值 —— 档案内容只走 Markdown 语法；
  · 标题补 id：markdown-it-py 的 anchors 插件未安装，自己给 h2/h3 编锚点，
    供页内目录（TOC）跳转；
  · **硬核小节置顶**：STORY.md 是按「先交代、再展开」写的（汇报定位 → 视觉体系 →
    叙事结构 → …），但读者打开页面最先想核对的恰恰是**数据索引**与**参考文献** ——
    每个数字、每篇引文的出处。所以渲染后把这些小节整块提到正文最前，
    其余小节保持原有相对顺序。标题里的编号（「4. 数据索引」）会顺手去掉，
    避免置顶之后出现「01 02 03 04」式错位。

只做这几件事，不引入插件体系。
"""

import re

from markdown_it import MarkdownIt

_md = MarkdownIt('commonmark', {'html': False, 'linkify': False, 'breaks': False})
_md.enable('table')
_md.enable('strikethrough')

_H_TAG = re.compile(r'<h([23])>(.*?)</h\1>', re.S)
_TAG = re.compile(r'<[^>]+>')
# 表格外面套一层 .tw —— 与站点其它页面同一套样式，窄屏横向滚动而不是压变形
_TABLE = re.compile(r'(<table>.*?</table>)', re.S)

# 文档开头的 h1 与外层 hero 标题重复，正文里去掉它（原文在 /api/story/md/<sid> 里完整可读）
_LEAD_H1 = re.compile(r'\A\s*#\s+.*?(?:\r?\n)')

# ── 置顶小节 ────────────────────────────────────────────────────────────
# 列表顺序 = 置顶后的先后顺序。命中用「包含」判断，所以
# 「## 4. 数据索引（页面上的每个数字都可回溯）」这种带括注/编号的写法照样命中。
SECTION_FIRST = ('数据索引', '参考文献')
_LEAD_NUM = re.compile(r'^\s*\d+\s*[.、)]\s*')

# 按 <h2> 切块：parts[0] 是第一个 h2 之前的内容（引文块等），其余每块含自己的 h2
_SPLIT_H2 = re.compile(r'(?=<h2>)')
_H2_HEAD = re.compile(r'<h2>(.*?)</h2>', re.S)


def _ranker(keys):
    """关键词 → 优先级数字的函数；不在列表里的返回 None。"""
    keys = tuple(k for k in (keys or ()) if k)

    def rank(title):
        for i, kw in enumerate(keys):
            if kw in title:
                return i
        return None

    return rank


def hoist(html, keys=None):
    """把「数据索引 / 参考文献」这类小节整块提到最前，其余保持原相对顺序。

    只动 h2 一级：h3 及其内容跟着自己的 h2 块一起搬，块内结构不变。
    没有命中任何关键词时原样返回，不做无谓重排。
    """
    rank = _ranker(keys if keys is not None else SECTION_FIRST)
    parts = _SPLIT_H2.split(html)
    if len(parts) < 2:
        return html

    head, rest = parts[0], parts[1:]

    def _bare(blk):
        """返回 (去掉序号后的标题, 重写标题后的块)。"""
        m = _H2_HEAD.match(blk)
        if not m:
            return '', blk
        title = _LEAD_NUM.sub('', _TAG.sub('', m.group(1)).strip())
        # 用 lambda 做替换：标题里若出现 \1 之类会被 re.sub 当反向引用
        return title, _H2_HEAD.sub(lambda _m: '<h2>%s</h2>' % title, blk, count=1)

    hot, cold = [], []
    for i, blk in enumerate(rest):
        title, blk = _bare(blk)
        r = rank(title)
        if r is None:
            cold.append(blk)
        else:
            hot.append((r, i, blk))

    if not hot:
        return html

    # 一旦置顶，所有 h2 都去掉「N.」序号 —— 否则会出现 1、2、3、5、6 这种缺号
    hot.sort(key=lambda t: (t[0], t[1]))
    return head + ''.join(b for _, _, b in hot) + ''.join(cold)


def render(text, keys=None):
    """渲染 md → (body_html, toc)

    toc 为 [(锚点, 标题文本), ...]，只收 h2 —— 对应页内目录那一排胶囊。
    keys 逐份汇报可覆盖置顶关键词（stories.py 的 'section_first'）；
    不传则用 SECTION_FIRST。
    """
    if not text:
        return '', []

    html = _md.render(_LEAD_H1.sub('', text, count=1))
    html = _TABLE.sub(r'<div class="tw">\1</div>', html)
    html = hoist(html, keys)

    toc = []
    seq = [0]

    def _tag(m):
        level, inner = m.group(1), m.group(2)
        seq[0] += 1
        hid = 'h%d' % seq[0]
        if level == '2':
            toc.append((hid, _TAG.sub('', inner).strip()))
        return '<h%s id="%s">%s</h%s>' % (level, hid, inner, level)

    return _H_TAG.sub(_tag, html), toc
