# -*- coding: utf-8 -*-
"""运行期新增的汇报条目 —— 落在模块目录下的 stories.json

两种条目的分工：

  · `stories.py` 的 `BUILTIN`：随仓库走的**策展条目**。改它要改代码、要重启 Flask。
    适合「定稿的、要进 git 的」汇报（组会汇报、斑岩研读）。

  · 本模块的 `stories.json`：**运行期条目**。由 `/story/admin` 像发文一样新增，
    改完立刻生效（`stories.all()` 每次请求读盘，mtime 变了就重读）。

为什么用 JSON 而不是 sqlite：
    条目数是十几条量级，且这份文件要能直接进 git 看 diff ——
    「这一份是什么时候加的、标签叫什么」在 diff 里一眼可见，比查库方便。

写盘一律走「临时文件 + os.replace」，避免断电/并发写留半个文件。
"""
import errno
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

_DIR = Path(__file__).resolve().parent
STORE = _DIR / 'stories.json'

# 与路由冲突的 id（/story/admin、/story/deck 都是静态规则，占了就没法再当汇报 id）
RESERVED = ('admin', 'deck', 'index', 'api', 'assets', 'static', 'new', 'edit')

# id 只允许小写字母数字与 . _ -；首字符必须是字母或数字（避免 '..' 之类）
_ID_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{0,47}$')

# 一条汇报里允许落盘到 JSON 的字段（白名单 —— 表单里多传的东西一律丢掉）
FIELDS = ('id', 'tab', 'tab_note', 'brand', 'eyebrow', 'title', 'subtitle',
          'meta', 'tags', 'md', 'pptx', 'deck_pages', 'section_first',
          'created_at', 'note')

_lock = threading.Lock()
_cache = {'mtime': None, 'items': []}


# ── 读 ──────────────────────────────────────────────────────────────────

def _read():
    """按 mtime 缓存的读取。文件不存在视为空列表（不是错误）。"""
    try:
        mtime = STORE.stat().st_mtime
    except OSError:
        return []

    if _cache['mtime'] == mtime:
        return _cache['items']

    try:
        raw = STORE.read_text(encoding='utf-8')
        items = json.loads(raw) if raw.strip() else []
        if not isinstance(items, list):
            items = []
    except (OSError, ValueError) as e:
        # 文件坏了也不能让整个 /story 挂掉：退回空列表并在控制台说明
        print('[story.store] stories.json 解析失败，按空处理：%s' % e)
        items = []

    items = [s for s in items if isinstance(s, dict) and s.get('id')]
    _cache['mtime'] = mtime
    _cache['items'] = items
    return items


def all():
    """全部运行期条目（原样列表，顺序即标签追加顺序）。"""
    return list(_read())


def find(sid):
    for s in _read():
        if s.get('id') == sid:
            return s
    return None


def has(sid):
    return find(sid) is not None


# ── 写 ──────────────────────────────────────────────────────────────────

def _write(items):
    """原子写：同目录临时文件 → os.replace，避免读到半个 JSON。"""
    payload = json.dumps(items, ensure_ascii=False, indent=2) + '\n'
    fd, tmp = tempfile.mkstemp(dir=str(_DIR), prefix='.stories-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(payload)
        os.replace(tmp, STORE)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    _cache['mtime'] = None          # 失效缓存：下一次读一定重新解析
    _cache['items'] = []


def clean(entry):
    """只留白名单字段，并把 None/空串剔掉（免得 JSON 里一堆 null）。"""
    out = {}
    for k in FIELDS:
        if k not in entry:
            continue
        v = entry[k]
        if v is None or v == '' or v == []:
            continue
        out[k] = v
    return out


def add(entry, *, front=False):
    """新增一条。id 重复直接报错（调用方负责挑 id）。"""
    entry = clean(entry)
    sid = entry.get('id')
    valid, why = check_id(sid)
    if not valid:
        raise ValueError(why)

    with _lock:
        items = _read()
        if any(s.get('id') == sid for s in items):
            raise ValueError('id 已存在：%s' % sid)
        entry.setdefault('created_at', time.strftime('%Y-%m-%d %H:%M:%S'))
        items.insert(0, entry) if front else items.append(entry)
        _write(items)
    return entry


def update(sid, patch):
    """改一条已有条目的部分字段；返回更新后的条目（找不到返回 None）。"""
    patch = clean(patch)
    patch.pop('id', None)           # id 不可改（它就是目录名）
    with _lock:
        items = _read()
        hit = None
        for s in items:
            if s.get('id') == sid:
                s.update(patch)
                hit = s
                break
        if hit is None:
            return None
        _write(items)
    return hit


def remove(sid):
    """摘掉一条；返回被摘掉的条目（找不到返回 None）。"""
    with _lock:
        items = _read()
        hit = None
        keep = []
        for s in items:
            if s.get('id') == sid and hit is None:
                hit = s
            else:
                keep.append(s)
        if hit is None:
            return None
        _write(keep)
    return hit


# ── 校验 ────────────────────────────────────────────────────────────────

def check_id(sid):
    """返回 (是否合法, 原因)。当目录名用，所以规则比一般 slug 严。"""
    if not sid:
        return False, 'id 不能为空'
    if not _ID_RE.match(sid):
        return False, ('id 只能用 1–48 位小写字母、数字与 . _ -，且以字母或数字开头'
                       '（它就是目录名 docs/<id>/）')
    if sid in RESERVED:
        return False, 'id「%s」与系统路由保留字冲突，换一个' % sid
    return True, ''


def suggest_id(prefix='story'):
    """给一个不冲突的候选 id：<prefix>-YYYYMMDD-HHMM，重复则顺延 -2/-3。"""
    base = '%s-%s' % (prefix, time.strftime('%Y%m%d-%H%M'))
    if not has(base):
        return base
    for i in range(2, 100):
        cand = '%s-%d' % (base, i)
        if not has(cand):
            return cand
    return '%s-%d' % (base, int(time.time()))


def _errno_hint(e):
    """不同步盘/只读盘时给个人话提示（STORE 在 D: 上一般不会触发）。"""
    if getattr(e, 'errno', None) in (errno.EACCES, errno.EPERM, errno.EROFS):
        return 'stories.json 不可写（%s）—— 检查目录权限' % STORE
    return str(e)
