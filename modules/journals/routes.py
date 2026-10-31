# -*- coding: utf-8 -*-
"""期刊列表模块

把 PostNews 现有的期刊检索清单做成一屏可用的卡片墙，提供两个视图：

  ① 近 60 天 —— 本地**近 60 天获取到**的文章链接（本地日期时间为准）
     卡片标题 → 期刊首页（新窗口直达官网）
     卡片内   → 已获取文章链接（点击进 DOI / 原文），带获取日期
     卡片底   → 飞书速递文档（若该期刊已生成）

  ② 主题归档 —— 近 2 个月目标期刊中与 filter 主题相关的命中文献
     （来自 journal_backfill.py 的回溯归档，按相关度分档着色）

为什么窗口是「获取时间」而不是「发布时间」
----------------------------------------
用户要的是"我手上能点开的链接"，不是"期刊方当天发布了什么"。
因此以**本地首次获取时间**为判据，由 journal_ledger.json 提供 ——
该台账首次写入 first_seen 后永不改写，窗口才会随时间自然老化。
（若改用文件 mtime，journal_updates.json 每次运行被重写会把时间"刷新"成今天，
文章永远老化不掉，窗口就退化成"显示全部"。）

时间解析优先级：
  台账 first_seen > pushed_at > 批次 date > 文件 mtime > 视为近期（计入 undated 统计）

数据来源（只读，按需合并）—— 全部由共享模块 journal_feed 统一读取与整形，
本页只保留 Flask 路由与 PDF 抓取/回源；取数逻辑见 D:/PostNews/journal_feed.py。

页面：GET /journals
       （页顶「今日更新」板块 = 当天 00:00 之后**获取到**的文章跨刊汇总，
        与视图一同源同口径，都由服务端按 obtained_ts 切好，前端只做区域/搜索过滤，
        再按刊分组呈现（组头给刊名/区域/条数，组内按时刻倒序）；
        视图一「近 60 天」另有前端「全部展开 / 收起全部」总开关，
        靠 `_MAX_EXPAND_PER_CARD` 保证下发量够铺开；见 GEO_TRANSLATE_ARCHITECTURE.md §15.4）
      （卡片顺序不再按 journals.json 的固定次序，改为**当天有更新的刊排前**、
        其余按最近一次获取时间倒序，见 _build() 里的 rows.sort）
接口：GET /api/journals/list     （两个视图的数据一次性返回，另含 `today` 切片）
"""
import hashlib
import json
import re
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlparse

from flask import Blueprint, Response, jsonify, request, stream_with_context

# ★ 共享取数层（2026-10-17）：/journal 页面同时挂在 5555 门户与 5556 GeoKB，
#   两端必须显示同一份内容，所以取数逻辑只有一份 —— 本模块负责「读 JSON →
#   合并去重 → 排序 → 注入 NAS 归档状态」，不含 Flask、不写文件、不联网。
#   5556 的 /journal 与 /api/journal/list 也 import 它，因此改一处即两端同步生效。
#   见 D:/PostNews/journal_feed.py 顶部说明。
_POSTNEWS_ROOT = Path(__file__).resolve().parents[3]
if str(_POSTNEWS_ROOT) not in sys.path:
    sys.path.insert(0, str(_POSTNEWS_ROOT))
import journal_feed  # noqa: E402

journals_bp = Blueprint('journals', __name__)

# 页面模板不在本模块：唯一真相是 D:/PostNews/Translate/modules/journals/templates/
# journal_page.html（5555 与 5556 共用），由 journal_page.render_html() 注入各端配置。

# 窗口/裁剪常量与相关度分档统一由共享层提供（此处仅保留旧名别名，
# 供本模块内既有引用与调试使用；权威值在 journal_feed）
WINDOW_DAYS = journal_feed.WINDOW_DAYS
_MAX_PER_CARD = journal_feed.MAX_PER_CARD
_MAX_EXPAND_PER_CARD = journal_feed.MAX_EXPAND_PER_CARD
_MAX_TOPIC_PER_CARD = journal_feed.MAX_TOPIC_PER_CARD
_TODAY_MAX = journal_feed.TODAY_MAX
TIERS = journal_feed.TIERS
_tier_of = journal_feed._tier_of


_deps = {'data_dir': None}

# 国际 / 国内 的展示顺序与配色键
_REGION_ORDER = [('国际', 'international'), ('国内', 'domestic')]


def init_journals(app, data_dir=None):
    """注册期刊列表模块。"""
    _deps['data_dir'] = Path(data_dir) if data_dir else None
    app.register_blueprint(journals_bp)
    print('[模块] 期刊列表 → GET /journals')


# ── PDF 一键获取 / NAS 回源（2026-10-17）──────────────────────
# 文章标题右侧三按钮（下载 / 阅读 / 翻译）的后端：
#   下载 → POST /api/journals/fetch-pdf（强 handler 集，覆盖 14 个目标刊）
#   阅读 → GET  /api/journals/pdf/<rel>（NAS 回源 + Range，喂自研阅读器）
#   翻译 → 复用 geokb_public 的 /geokb/api/entry/<eid>/translate 代理（5556）
#
# 下载即入库（journal → PDF → 知识库 的按需版）：
#   PDF 落 NAS papers_repo/by_journal/<刊>/<年>/（与语料管线同目录约定），
#   按 paper-<sha1前12> 写一条 extraction=manual 的题录条目 —— 每日管线
#   （ingest_external_feeds.py）次日碰到同一篇会自动升级为全文提取；
#   再增量索引一条（index_one_entry.py）—— 5556 的 PDF/翻译 API 按条目 id
#   查库，必须秒级可见，不能等每日全量 rebuild（embed 5328 条约 4 分钟）。
_POSTNEWS_ROOT = Path(__file__).resolve().parents[3]
# ★ UNC（非 S:）：本模块跑在 supervisor 拉起的提权 Flask(High) 里，S: 不可见
#   → 用 S:/ 时 GET /api/journals/pdf/<rel> 会静默 404。见 D:/PostNews/nas_paths.py。
_NAS_REPO = journal_feed.NAS_REPO
_GEOKB_DIR = Path('D:/ZSK/geokb')
_GEOKB_PY = _GEOKB_DIR / 'venv' / 'Scripts' / 'python.exe'
_fetch_lock = threading.Lock()
_fetcher = None

# ── 归档台账写入（本模块仍负责「补抓后登记」）────────────────────
# 读取侧已全部下沉到 journal_feed（两端共享）；但 POST /api/journals/fetch-pdf
# 这条**按需补抓**链路仍在本服务内执行，抓完要写台账并让共享层缓存立即失效。
_LEDGER_PATH = _GEOKB_DIR / 'state' / 'papers_index.jsonl'


def _load_fetcher():
    """惰性加载 journal_pdf_fetch 门面（PostNews 根目录，不在 portal sys.path）。"""
    global _fetcher
    if _fetcher is not None:
        return _fetcher or None
    with _fetch_lock:
        if _fetcher is not None:
            return _fetcher or None
        import sys
        root = str(_POSTNEWS_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            from journal_pdf_fetch import fetch_pdf
            _fetcher = fetch_pdf
        except Exception as e:  # noqa: BLE001
            print(f'[journals] journal_pdf_fetch 不可用: {e}')
            _fetcher = False
    return _fetcher or None


def _safe_name(s, max_len=80):
    """与 geokb scripts/pdf_downloader.safe_name 同规则（NAS 目录约定一致）。"""
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', str(s))
    s = re.sub(r'_+', '_', s).strip('_ ')
    return s[:max_len] or 'untitled'


def _manual_entry_md(eid, title, journal, date, rel):
    """门户按钮即时建档的题录条目（extraction: manual，待每日管线升级）。

    ⚠️ front matter 全部走 json.dumps 输出：曾把 `summary: {t}（…）` 直接拼行，
    引号字符串后紧跟裸文本是非法 YAML，parse_entry 静默返回 None，
    增量索引失败（条目存在但 5556 查不到，阅读/翻译全断）。
    """
    t = json.dumps(title, ensure_ascii=False)
    j = json.dumps(journal or '未知', ensure_ascii=False)
    summary = json.dumps(
        f'{title}（journals 页手动下载建档，全文提取由每日管线自动升级）',
        ensure_ascii=False)
    src = json.dumps({'name': journal or 'papers_repo',
                      'path': 'papers_repo/' + rel}, ensure_ascii=False)
    return f"""---
id: {eid}
title: {t}
module: papers
tags: {json.dumps([journal] if journal else [], ensure_ascii=False)}
summary: {summary}
confidence: C
status: active
updated: {date}
sources: [{src}]
journal: {j}
year: {json.dumps(date[:4])}
extraction: manual
---

## 关键内容

（本条目由门户 journals 页「下载 PDF」按钮即时建档：PDF 已存 NAS 论文仓库，
结构化提取将在每日 GeoKB 管线中自动升级补充，无需手动处理。）

## 来源

- 期刊：{journal or '未知'}
- 仓库文件：`papers_repo/{rel}`
"""


@journals_bp.post('/api/journals/fetch-pdf')
def api_fetch_pdf():
    """文章标题右侧「下载 PDF」：handler 集下载 → NAS → 题录条目 → 增量索引。"""
    data = request.get_json(silent=True) or {}
    journal = (data.get('journal') or '').strip()
    title = (data.get('title') or '').strip()
    url = (data.get('url') or '').strip()
    if not title or not url:
        return jsonify({'ok': False, 'error': 'bad_request',
                        'message': '缺少 title / url'}), 400

    fetcher = _load_fetcher()
    if not fetcher:
        return jsonify({'ok': False, 'error': 'fetcher_unavailable',
                        'message': 'PDF 下载器不可用'}), 503

    date = datetime.now().strftime('%Y-%m-%d')
    try:
        r = fetcher(journal or 'unknown', title, url, date=date)
    except Exception as e:  # noqa: BLE001
        return jsonify({'ok': False, 'error': 'fetch_exception',
                        'message': f'{type(e).__name__}: {e}'[:200]}), 500

    # ── 版本闸门的两类拒绝（2026-10-29）──────────────────────────────
    # 录用稿/校样、以及尚未正式出版（无卷期）的稿件，源站只给非排版版：
    # 内容与正式版可能不同、无页码可引，收进库会污染引用口径。
    # 判据在 journal_pdf_downloader.fetch_one 出口统一做（见 _gate_version）。
    if r.get('status') in ('accepted_version', 'in_press'):
        return jsonify({'ok': False, 'error': r['status'],
                        'message': (r.get('note') or
                                    '该文尚未正式出版，源站只有录用稿，暂不下载')[:200],
                        'retry_when': 'published'}), 409
    if r.get('status') != 'ok' or not r.get('pdf_bytes'):
        return jsonify({'ok': False, 'error': r.get('status') or 'failed',
                        'message': (r.get('note') or '下载失败')[:200],
                        'pdf_url': r.get('pdf_url') or ''}), 502
    pdf_bytes = r['pdf_bytes']
    if pdf_bytes[:4] != b'%PDF':
        return jsonify({'ok': False, 'error': 'not_pdf',
                        'message': '源站返回内容不是 PDF'}), 502

    digest = hashlib.sha1(pdf_bytes).hexdigest()
    eid = f'paper-{digest[:12]}'

    # ① NAS 落盘（与语料管线同目录/同命名约定；已存在则跳过写文件）
    jname = _safe_name(journal or 'unknown', 40)
    jdir = _NAS_REPO / 'by_journal' / jname / date[:4]
    try:
        jdir.mkdir(parents=True, exist_ok=True)
        jpath = jdir / f"{date}_{_safe_name(title, 60)}_fetch.pdf"
        existed = jpath.exists()
        if not existed:
            jpath.write_bytes(pdf_bytes)
    except OSError as e:
        return jsonify({'ok': False, 'error': 'nas_write_failed',
                        'message': f'NAS 写入失败: {e}'[:200]}), 500
    rel = f"by_journal/{jname}/{date[:4]}/{jpath.name}"

    # ② 归档台账登记（写**本地**主副本；NAS 上的旧 index.jsonl 被 QTS 独占锁，
    #    写不进去。eid 必须记录：journals 列表据此判「已归档」并挂阅读/翻译入口。）
    try:
        meta = {'title': title, 'url': url, 'journal': journal, 'date': date,
                'pdf_url': r.get('pdf_url') or '', 'repo_path': str(jpath),
                'size': len(pdf_bytes), 'source': r.get('source') or 'fetch',
                'eid': eid,
                'indexed_at': datetime.now().isoformat()}
        _LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_LEDGER_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(meta, ensure_ascii=False) + '\n')
        # 共享层按 mtime+size 判缓存；本进程写完必须立刻失效，
        # 否则刚抓到的这篇在列表里仍显示「在线抓取」直到进程重启。
        journal_feed.invalidate_ledger_cache()
    except Exception as e:  # noqa: BLE001
        print(f'[journals] 台账登记失败（不影响入库）: {e}')

    # ③ 题录条目（幂等：已存在就不动；manual 条目留给每日管线升级）
    entry_path = _GEOKB_DIR / 'content' / 'papers' / f'{eid}.md'
    entry_created = False
    if not entry_path.exists():
        try:
            entry_path.parent.mkdir(parents=True, exist_ok=True)
            entry_path.write_text(
                _manual_entry_md(eid, title, journal, date, rel), encoding='utf-8')
            entry_created = True
        except OSError as e:
            return jsonify({'ok': False, 'error': 'entry_write_failed',
                            'message': f'条目写入失败: {e}'[:200],
                            'eid': eid, 'pdf_rel': rel}), 500

    # ④ 增量索引一条（5556 的 PDF/翻译 API 按 id 查库，必须秒级可见）
    indexed = False
    try:
        p = subprocess.run(
            [str(_GEOKB_PY), str(_GEOKB_DIR / 'scripts' / 'index_one_entry.py'), eid],
            capture_output=True, text=True, timeout=180, cwd=str(_GEOKB_DIR),
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        indexed = (p.returncode == 0)
        if not indexed:
            print(f'[journals] 增量索引失败({p.returncode}): '
                  f'{(p.stdout or "")[:120]} {(p.stderr or "")[:120]}')
    except Exception as e:  # noqa: BLE001
        print(f'[journals] 增量索引异常: {e}')

    pdf_url = f'/api/journals/pdf/{rel}'
    return jsonify({'ok': True, 'eid': eid, 'pdf_rel': rel, 'pdf_url': pdf_url,
                    'size': len(pdf_bytes), 'existed': existed,
                    'entry_created': entry_created, 'indexed': indexed,
                    'source': r.get('source') or ''})


@journals_bp.get('/api/journals/pdf/<path:rel>')
def api_journals_pdf(rel):
    """NAS 论文 PDF 同源回源（Range 支持，喂自研阅读器 kpdf 分段加载）。

    只放行 papers_repo 内的 .pdf；原始译文（.zh.mono/.zh.dual）同目录同规则。
    """
    base = _NAS_REPO.resolve()
    try:
        target = (base / rel).resolve()
    except OSError:
        return jsonify({'ok': False, 'error': 'bad_path'}), 400
    if (not str(target).startswith(str(base))
            or target.suffix.lower() != '.pdf' or not target.is_file()):
        return jsonify({'ok': False, 'error': 'not_found'}), 404

    size = target.stat().st_size
    rng = request.headers.get('Range', '')
    start, end, status = 0, size - 1, 200
    m = re.match(r'bytes=(\d*)-(\d*)', rng or '')
    if m:
        g1, g2 = m.group(1), m.group(2)
        if g1:
            start = int(g1)
            end = int(g2) if g2 else size - 1
        elif g2:
            start, end = max(0, size - int(g2)), size - 1
        end = min(end, size - 1)
        if start <= end:
            status = 206
    length = end - start + 1
    headers = {'Accept-Ranges': 'bytes', 'Content-Type': 'application/pdf',
               'Cache-Control': 'public, max-age=86400',
               'Content-Length': str(length)}
    if status == 206:
        headers['Content-Range'] = f'bytes {start}-{end}/{size}'

    def gen():
        with open(target, 'rb') as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return Response(stream_with_context(gen()), status=status, headers=headers)


# ── 取数：全部委托共享层 ─────────────────────────────────────
# 本模块不再自行读 journal_*.json：/journal 页面同时挂在 5555 与 5556，
# 两端必须显示同一份内容，取数逻辑只保留一份（journal_feed.build_feed）。
# 权威载荷结构见 D:/PostNews/journal_feed.py 顶部说明。
def _build():
    return journal_feed.build_feed(_deps['data_dir'] or _POSTNEWS_ROOT)


# ★ 共享渲染层（2026-10-17）：/journal 同时挂在 5555 与 5556，必须一比一复刻且
#   同步更新，所以模板与配置都只有一份（journal_page.py + journal_page.html），
#   端点差异收敛到 CFG。本页只渲染自己那份配置（CFG_PORTAL）。
#   见 D:/PostNews/journal_page.py 顶部说明。
import journal_page  # noqa: E402


@journals_bp.route('/journals')
def journals_page():
    return Response(journal_page.render_html(journal_page.CFG_PORTAL),
                    mimetype='text/html')


@journals_bp.route('/api/journals/list')
def api_journals_list():
    return jsonify(_build())


# ── 补缺申请（2026-10-25）──────────────────────────────────────────────
# 「今日更新」卡片右上角那颗按钮：点一下 = 立刻跑一轮投放箱入库
# （等价于计划任务 12:01 / 18:01 的 inbox_ingest --run）。
# 冷却 60s 按后端计；只有 inbox 真有 PDF 且**投放箱入库没在跑**时才有效。
# 实现与口径说明见 modules/journals/refill.py 顶部。
try:
    from . import refill as _refill
except ImportError:                      # 以脚本方式加载时没有包上下文
    import refill as _refill  # type: ignore

_refill.register(journals_bp)
