# -*- coding: utf-8 -*-
"""kust.work · 汇报说明书（/story）注册表

条目有**两个来源**，`all()` 合并后按顺序返回（顺序即标签栏顺序，第一个是 /story 落地页）：

  · `BUILTIN`（本文件）—— 随仓库走的策展条目。加一份要改代码 + 重启 Flask。
      在 docs/ 下建目录 <sid>/，放入：
        STORY.md          可选 —— 正文档案（有它才渲染 md 正文）
        deck.pptx         可选 —— PPT 原文件（文件名任意 .pptx 都认）
        deck/p01.png …    可选 —— 16:9 页图，有它才能开「网页放映版」
        deck_notes.js     可选 —— 放映时的逐页演讲提词（window.STORY_DECK_NOTES）
        story.js          可选 —— 该汇报自己的页面增强脚本（注册为 'js'）
      然后往 BUILTIN 追加一条 dict。

  · `store`（stories.json）—— **运行期条目**，由 /story/admin 像发文一样新增，
      不用改代码、不用重启。字段与 BUILTIN 完全一致。

两条硬规矩（两边都适用）：
  · STORY.md 与 PPT **至少有一样** —— 只有 PPT 也成立（那就只有放映入口，没有正文）；
  · 一份汇报就是一个目录，目录名 = id = 标签栏顺序里的那条。
"""

from pathlib import Path
import time

from . import store

_DIR = Path(__file__).resolve().parent
DOCS = _DIR / 'docs'

# 页图文件名约定：p01.png / p02.png …（与 deck.html 的 BASE + 'pNN.png' 对应）
DECK_PAGE_PREFIX = 'p'


# ══════════ 内置条目（策展，随仓库走）══════════
# 改这里要重启 Flask。运行期新增的条目走 store（stories.json），见文件头说明。
BUILTIN = [
    # ═══════════════ 01 组会汇报 · 近期学习与工作进展 ═══════════════
    {
        'id': 'groupmeeting-2026-09',
        'tab': '组会汇报',
        'tab_note': '21 页 · 11 min',
        'brand': '汇报说明书',
        'eyebrow': '课题组组会 · 2026-09',
        'title': '组会汇报：近期学习总结 + 工作进展',
        'subtitle': (
            '一次组会、两个部分、连续讲述，合计约 11 分钟。'
            '第一部分用 5 篇中文综述 + 文献库中的钨矿论文搭出学科骨架，'
            '并最终落回云南南温河钨矿与滇东南 W–Sn 带（3 分钟 / 8 页）；'
            '第二部分用真实截图与实测数字讲清 kust.work 从哪来、能干什么（8 分钟 / 11 页）。'
        ),
        'meta': '21 页 · 约 11 分钟',
        'tags': ['第一部分 · 钨矿研究现状', '第二部分 · kust.work 平台'],
        'md': 'STORY.md',
        'pptx': '组会汇报-近期学习与工作进展.pptx',
        # p01.png…p21.png 由 tools/pptx_to_deck.py 生成（LibreOffice → PDF → PyMuPDF 栅格化）
        'deck_pages': 21,
        # 硬核小节置顶：STORY.md 里写在 §4 的「数据索引」会被提到正文最前。
        # 不写也行 —— 默认就是这个顺序（render.SECTION_FIRST）；写出来便于日后微调。
        'section_first': ['数据索引', '参考文献'],
    },

    # ═══════════════ 02 斑岩铜矿研读（原 /story 首发版）═══════════════
    {
        'id': 'porphyry-zircon',
        'tab': '斑岩铜矿',
        'tab_note': '15 页 · 5 min',
        'brand': '汇报说明书',
        'eyebrow': '学术文献研读 · Mineralium Deposita · 2026 · s00126-025-01356-w',
        'title': '大型与小型安第斯斑岩铜矿的成因分野',
        'subtitle': (
            '智利北部海岸科迪勒拉四个早白垩世斑岩铜矿（含矿）与两套无矿对照岩石的'
            '系统锆石 U–Pb + 微量元素研究。核心问题：矿床规模的差异由什么控制？'
        ),
        'meta': 'Mineralium Deposita · 2026',
        'tags': ['顶刊研读 · IF 5.0', '锆石微量元素 · 肥力指标'],
        'md': 'STORY.md',
        'pptx': None,          # 原始 .pptx 未入库，只有 15 张页图
        'deck_pages': 15,
        'view': 'md',          # 自定义版式片段：templates/body/porphyry-zircon.html
        'body': 'porphyry',
        'js': 'story.js',
    },
]


# ── 查询辅助 ─────────────────────────────────────────────────────────────

def _enrich(s):
    """给运行期条目补上「实时算得出来」的字段（存盘时不落，避免和目录对不上）。

    目前只有 deck_pages —— 页图数随时可能因为重建而变，写死在 JSON 里必然过期。
    """
    if 'deck_pages' not in s:
        n = len(deck_files(s))
        if n:
            s = dict(s, deck_pages=n)
    return s


def all():
    """全部汇报（内置在前，运行期条目在后；顺序即标签顺序）。

    id 撞车时内置优先 —— 内置条目是策展过的，不该被一个同名的新条目顶掉。
    """
    out = list(BUILTIN)
    seen = {s['id'] for s in out}
    for s in store.all():
        if s.get('id') in seen:
            continue
        out.append(_enrich(s))
    return out


def get(sid):
    """按 id 取一条，找不到返回 None。"""
    for s in BUILTIN:
        if s['id'] == sid:
            return s
    s = store.find(sid)
    return _enrich(s) if s else None


def is_builtin(sid):
    """内置条目（/story/admin 里只读：改它要动代码）。"""
    return any(s['id'] == sid for s in BUILTIN)


def first():
    """标签栏第一份 —— /story 的落地页。"""
    items = all()
    return items[0] if items else None


# ── 运行期条目的增删改（/story/admin 用）─────────────────────────────────
# 收在这里而不是让 admin.py 直接碰 store：条目字段的规矩（id 白名单、目录名=id）
# 只有本模块清楚，外面少一层知情就是少一处漂移。

def add_custom(entry, **kw):
    """新增一条运行期条目（字段先过 store 白名单）。"""
    return store.add(store.clean(entry), **kw)


def update_custom(sid, patch):
    """改一条运行期条目的部分字段；内置条目 / 不存在的一律返回 None。"""
    if is_builtin(sid):
        return None
    return store.update(sid, patch)


def drop_custom(sid):
    """摘掉一条运行期条目；内置条目 / 不存在的一律返回 None。"""
    if is_builtin(sid):
        return None
    return store.remove(sid)


def suggest_id(prefix='story'):
    """挑一个没被占用的 id（内置与运行期都算占用）。"""
    sid = store.suggest_id(prefix)
    while get(sid):
        sid = '%s-%d' % (sid, int(time.time()))
    return sid


def check_id(sid):
    """id 合法性（格式 + 保留字 + 是否已被占用）。返回 (是否合法, 原因)。"""
    ok, why = store.check_id(sid)
    if not ok:
        return ok, why
    if get(sid):
        return False, 'id「%s」已被占用' % sid
    return True, ''


def deck_story():
    """第一份带页图的汇报 —— 旧链接 /story/deck 重定向到它。"""
    for s in all():
        if s.get('deck_pages'):
            return s
    return None


def doc_dir(story):
    """该汇报的档案目录 docs/<sid>/。"""
    return DOCS / story['id']


def md_path(story):
    """STORY.md 路径（缺失返回 None）。"""
    name = story.get('md')
    if not name:
        return None
    p = doc_dir(story) / name
    return p if p.is_file() else None


def pptx_path(story):
    """PPT 原文件路径。注册表没写就按目录里的 .pptx 自动认一个。"""
    d = doc_dir(story)
    name = story.get('pptx')
    if name:
        p = d / name
        return p if p.is_file() else None
    if not d.is_dir():
        return None
    found = sorted(x for x in d.glob('*.pptx') if not x.name.startswith('~$'))
    return found[0] if found else None


def deck_files(story):
    """该汇报的页图（p01.png …），按页码升序；没有则空列表。"""
    d = doc_dir(story) / 'deck'
    if not d.is_dir():
        return []
    files = [x for x in d.glob(DECK_PAGE_PREFIX + '*.png') if x.is_file()]

    def key(p):
        n = p.stem[len(DECK_PAGE_PREFIX):]
        return (0, int(n)) if n.isdigit() else (1, 0)

    return sorted(files, key=key)


def deck_base(story):
    """页图基路径 —— deck.html 里 BASE + 'pNN.png' 拼出来的。"""
    return '/api/story/asset/%s/deck/' % story['id']


def js_path(story):
    """该汇报自己的页面增强脚本路径（可选）。"""
    name = story.get('js')
    if not name:
        return None
    p = doc_dir(story) / name
    return p if p.is_file() else None


def notes_path(story):
    """演讲提词文件路径（可选）。"""
    p = doc_dir(story) / 'deck_notes.js'
    return p if p.is_file() else None


def body_path(story):
    """自定义正文片段路径（可选）—— 有它就不渲染 md 正文，改用片段。

    片段是 templates/body/<body>.html，与外壳同套 CSS，供版式复杂的汇报使用。
    """
    name = story.get('body')
    if not name:
        return None
    p = _DIR / 'templates' / 'body' / ('%s.html' % name)
    return p if p.is_file() else None


def human_size(path):
    """文件体积的可读文本（KB / MB）；取不到返回 None。"""
    if not path:
        return None
    try:
        n = path.stat().st_size
    except OSError:
        return None
    if n >= 1048576:
        return '%.2f MB' % (n / 1048576)
    return '%.1f KB' % (n / 1024)
