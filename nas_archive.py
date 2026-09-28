# -*- coding: utf-8 -*-
"""NAS 文献仓库 —— 译文三版本归档 / 本地索引检索 / 48h 本地空间回收

★ 这个模块解决什么问题
  本地磁盘放不下所有译文，但译文又不能真的丢。于是把 NAS 当**文献仓库**：

      S:\\Translator_PDFs\\uploads\\     原文
      S:\\Translator_PDFs\\translated\\  中文版
      S:\\Translator_PDFs\\duo\\         双语对照版

  本地只留「最近 48 小时」的活文件，旧的搬去 NAS 后删除，
  之后再有人上传同名文献，从 NAS 取回来即可 —— 不必重翻。

★ 三条链路
  1. 归档 archive()     翻译完成 → 三版本复制到 NAS → 写本地索引
  2. 检索 lookup()      本地没有 → 查索引 → 命中则 restore() 回本地
  3. 回收 reap()        本地文件 >48h → 归档 → **逐个校验 NAS 副本** → 删本地

★ 为什么检索要用本地 JSON 索引
  每次扫 NAS 目录（网络往返 + 可能上千个文件）太慢。索引只记文件名与元数据，
  是纯本地的小 JSON，命中后再按记录去 NAS 取文件 —— 一次 stat 都不用扫全目录。

★ 为什么 restore 是「拷回本地」而不是「直接从 NAS 发给前端」
  翻译队列与译文归档只认本地目录（既有铁律），前端 URL 也全是 /translated/xxx.pdf。
  拷回本地后 URL 完全一致 —— 前端 0 改动，队列/归档逻辑 0 改动。

★ 删除的铁律
  **绝不在确认 NAS 上有同尺寸的副本之前删除任何本地文件。**
  回收时逐文件 copy → 校验 exists() 且 size 一致 → 才删。
  校验不通过的保留在本地并在报告里标出来，宁可占空间也不能丢数据。

★ 路径优先级
  盘符 S: 优先（与用户心智一致，且实测可读写）；UNC 兜底
  （驱动器映射是「每登录会话」的，若服务被计划任务/系统会话拉起则看不到 S:）。
  可用环境变量 NAS_PDF_ROOT 强制指定。

配置：环境变量 NAS_PDF_ROOT（可选）、NAS_TTL_HOURS（默认 48）
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

logger = None  # 由 init_logging() 注入；未注入时用模块级 logging
import logging
logger = logging.getLogger('nas_archive')


BASE = Path(__file__).parent.resolve()
DATA_DIR = BASE / 'data'
INDEX_PATH = DATA_DIR / 'nas_index.json'
LOCK_PATH = DATA_DIR / 'nas_index.lock'

# ── NAS 根目录候选（按顺序探测，第一个 is_dir() 的胜出）──
_ENV_ROOT = os.environ.get('NAS_PDF_ROOT', '').strip()
ROOT_CANDIDATES = ([Path(_ENV_ROOT)] if _ENV_ROOT else []) + [
    Path('S:/Translator_PDFs'),
    Path('//<nas-host>/<share>'),
]

# 三个版本 → NAS 子目录名（与手工建好的目录一致，不要改）
SUBDIRS = {'original': 'uploads', 'mono': 'translated', 'dual': 'duo'}
KINDS = ('original', 'mono', 'dual')

DEFAULT_TTL_HOURS = float(os.environ.get('NAS_TTL_HOURS', '48') or 48)

INDEX_VERSION = 1

# BabelDOC / 双语版的文件名后缀，按「长在前」顺序匹配
_SUFFIXES = ('.no_watermark.zh.mono.pdf', '.zh.mono.pdf', '.zh.dual.pdf', '.zh.pdf', '.pdf')
_HASH_RE = re.compile(r'[_]{1,2}[a-f0-9]{8}$', re.IGNORECASE)


# ══════════════════════════ 命名工具 ══════════════════════════

def strip_suffix(name: str) -> str:
    """去掉 PDF 版本后缀，保留 base stem。

    'a_1a2b3c4d.no_watermark.zh.mono.pdf' → 'a_1a2b3c4d'
    'a_1a2b3c4d.zh.dual.pdf'              → 'a_1a2b3c4d'
    'a_1a2b3c4d.pdf'                      → 'a_1a2b3c4d'
    """
    s = Path(name).name
    low = s.lower()
    for suf in _SUFFIXES:
        if low.endswith(suf):
            return s[: -len(suf)]
    return Path(s).stem


def base_stem(name: str) -> str:
    """含 hash 的 base stem —— 三个版本靠它互相关联。"""
    s = strip_suffix(name)
    return s.replace('.no_watermark', '').replace('no_watermark', '').strip('._ ')


def norm_stem(name: str) -> str:
    """归一化 stem（去掉 8 位 hash）—— 索引的 key。

    同一个文献无论上传多少次（每次新 hash），归一化后都收敛到同一个 key。
    这也和 web_app._find_cache 的去 hash 规则保持一致。
    """
    s = base_stem(name)
    prev = None
    while prev != s:                      # 可能连着两个 hash（重复上传又归档）
        prev = s
        s = _HASH_RE.sub('', s)
    return s.strip('._ ')


def dual_name_for(mono_path) -> str:
    """mono 文件名 → 对应 dual 文件名。"""
    n = Path(mono_path).name
    for suf in ('.no_watermark.zh.mono.pdf', '.zh.mono.pdf'):
        if n.lower().endswith(suf):
            return n[: -len(suf)] + '.zh.dual.pdf'
    return Path(n).stem + '.zh.dual.pdf'


# ══════════════════════════ NAS 根目录 ══════════════════════════

_root_cache = {'path': None, 'at': 0.0}
_ROOT_TTL = 60.0     # 探测结果缓存 60 秒；负结果也缓存，避免反复卡在不可达 UNC 上


def root(refresh: bool = False):
    """返回可用的 NAS 根目录 Path，全部不可达返回 None。"""
    now = time.time()
    if not refresh and _root_cache['path'] is not None:
        try:
            if _root_cache['path'].is_dir():
                return _root_cache['path']
        except Exception:
            pass
        if now - _root_cache['at'] < _ROOT_TTL:
            return None                    # 负结果缓存期内不再重复探测

    for cand in ROOT_CANDIDATES:
        try:
            if cand.is_dir():
                _root_cache['path'] = cand
                _root_cache['at'] = now
                return cand
        except Exception as e:
            logger.debug('NAS 候选不可用 %s: %s', cand, e)
    _root_cache['path'] = None
    _root_cache['at'] = now
    return None


def available(refresh: bool = False) -> bool:
    return root(refresh=refresh) is not None


def nas_dir(kind: str):
    """kind → NAS 上的目标目录（不存在则创建）。"""
    r = root()
    if r is None:
        return None
    d = r / SUBDIRS[kind]
    try:
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception as e:
        logger.warning('创建 NAS 目录失败 %s: %s', d, e)
        return None


# ══════════════════════════ 索引读写 ══════════════════════════

def _empty_index():
    return {'version': INDEX_VERSION, 'updated_at': None, 'items': {}}


def load_index() -> dict:
    try:
        if INDEX_PATH.exists():
            with open(INDEX_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get('items'), dict):
                return data
    except Exception as e:
        logger.warning('索引读取失败，按空索引处理: %s', e)
    return _empty_index()


def _save_index_atomic(data: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_PATH.with_suffix('.json.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, INDEX_PATH)


class _IndexLock:
    """跨进程互斥（Flask 线程与定时回收脚本可能同时写索引）。

    用目录的原子创建做锁，带过期兜底 —— 持有者崩溃不会永久锁死。
    """

    def __init__(self, timeout: float = 30.0, stale: float = 300.0):
        self.timeout = timeout
        self.stale = stale
        self.held = False

    def __enter__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + self.timeout
        while True:
            try:
                LOCK_PATH.mkdir(exist_ok=False)
                self.held = True
                return self
            except FileExistsError:
                try:
                    age = time.time() - LOCK_PATH.stat().st_mtime
                    if age > self.stale:                 #  stale lock → 抢占
                        LOCK_PATH.rmdir()
                        continue
                except Exception:
                    try:
                        LOCK_PATH.rmdir()
                    except Exception:
                        pass
                if time.time() > deadline:
                    logger.warning('索引锁等待超时，继续（可能并发写）')
                    return self
                time.sleep(0.2)

    def __exit__(self, *exc):
        if self.held:
            try:
                LOCK_PATH.rmdir()
            except Exception:
                pass
            self.held = False
        return False


def _now_iso():
    return datetime.now().isoformat(timespec='seconds')


# ══════════════════════════ 归档 ══════════════════════════

def _copy_to_nas(src: Path, kind: str):
    """把单个文件复制进 NAS 对应子目录，返回 (ok, nas_path_or_None, 说明)。

    已存在且大小一致 → 视为成功，不重复传（幂等）。
    """
    if src is None or not Path(src).exists():
        return False, None, '源文件不存在'
    src = Path(src)
    d = nas_dir(kind)
    if d is None:
        return False, None, 'NAS 目录不可用'
    dst = d / src.name
    try:
        ssize = src.stat().st_size
        if dst.exists() and dst.stat().st_size == ssize:
            return True, dst, '已存在（大小一致，跳过）'
        tmp = dst.with_name(dst.name + '.part')
        shutil.copy2(str(src), str(tmp))
        if not tmp.exists() or tmp.stat().st_size != ssize:
            try:
                tmp.unlink()
            except Exception:
                pass
            return False, None, '复制后大小不一致'
        os.replace(str(tmp), str(dst))     # 原子落位，避免半截文件被当成完整副本
        return True, dst, '已归档'
    except Exception as e:
        return False, None, f'{type(e).__name__}: {e}'


def archive(orig_path=None, mono_path=None, dual_path=None, *,
            display_name: str = '', source: str = 'translate') -> dict:
    """把三版本归档到 NAS 并写入索引。

    返回 {ok, stem, files:{kind:{name,size,note}}, missing:[...], error}
    ⚠️ 任何一步失败都不抛异常 —— NAS 可能没挂载，绝不能拖垮翻译主流程。
    """
    out = {'ok': False, 'stem': None, 'files': {}, 'missing': [], 'error': None,
           'source': source}
    try:
        if not available():
            out['error'] = 'NAS 不可达'
            return out

        ref = mono_path or dual_path or orig_path
        if ref is None:
            out['error'] = '没有可归档的文件'
            return out
        stem = norm_stem(Path(ref).name)
        if not stem:
            out['error'] = '无法解析 stem'
            return out
        out['stem'] = stem

        srcs = {'original': orig_path, 'mono': mono_path, 'dual': dual_path}
        copied = {}
        for kind in KINDS:
            p = srcs.get(kind)
            if not p:
                out['missing'].append(kind)
                continue
            ok, dst, note = _copy_to_nas(Path(p), kind)
            if ok:
                copied[kind] = {'name': Path(dst).name,
                                'size': Path(p).stat().st_size,
                                'note': note}
            else:
                out['missing'].append(kind)
                copied[kind] = {'name': Path(p).name, 'size': 0, 'note': note}
        out['files'] = copied
        out['ok'] = 'mono' in copied and copied['mono'].get('size', 0) > 0

        # ── 更新索引 ──
        with _IndexLock():
            idx = load_index()
            item = idx['items'].get(stem) or {}
            files = item.get('files') or {}
            for kind, meta in copied.items():
                if meta.get('size'):
                    files[kind] = {'name': meta['name'], 'size': meta['size'],
                                   'at': _now_iso()}
            item.update({
                'stem': stem,
                'display': display_name or item.get('display') or (Path(ref).name),
                'base': base_stem(Path(ref).name),
                'files': files,
                'updated_at': _now_iso(),
            })
            if 'archived_at' not in item:
                item['archived_at'] = _now_iso()
            if source:
                item['source'] = source
            idx['items'][stem] = item
            idx['updated_at'] = _now_iso()
            _save_index_atomic(idx)
        return out
    except Exception as e:
        out['error'] = f'{type(e).__name__}: {e}'
        logger.warning('archive 异常: %s', e)
        return out


# ══════════════════════════ 检索 / 恢复 ══════════════════════════

def lookup(name: str, *, require=('mono',)):
    """按文件名查索引（纯本地 JSON，不碰网络）。

    返回条目 dict 或 None。require 里列出的版本必须都在，否则视为未命中。
    """
    if not name:
        return None
    stem = norm_stem(name)
    if not stem:
        return None
    idx = load_index()
    item = idx['items'].get(stem)
    if not item:
        return None
    files = item.get('files') or {}
    if any(k not in files for k in require):
        return None
    return item


def restore(item: dict, upload_dir, translated_dir, *,
            kinds=('mono', 'dual', 'original')) -> dict:
    """把索引条目的文件从 NAS 取回本地，返回恢复结果。

    ⚠️ 必须拷回本地：队列与归档只读本地，前端 URL 也是 /translated/xxx.pdf。
    文件名沿用 NAS 上的名字（含原 hash），这样再次归档时天然幂等。
    """
    res = {'ok': False, 'restored': [], 'missing': [], 'error': None}
    try:
        if not available():
            res['error'] = 'NAS 不可达'
            return res
        r = root()
        files = (item or {}).get('files') or {}
        upload_dir, translated_dir = Path(upload_dir), Path(translated_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)
        translated_dir.mkdir(parents=True, exist_ok=True)

        for kind in kinds:
            meta = files.get(kind)
            if not meta:
                continue
            fname = meta.get('name')
            if not fname:
                continue
            src = r / SUBDIRS[kind] / fname
            dst = (upload_dir if kind == 'original' else translated_dir) / fname
            try:
                if dst.exists() and dst.stat().st_size == meta.get('size'):
                    res['restored'].append({'kind': kind, 'name': fname, 'note': '本地已有'})
                    continue
                if not src.exists():
                    res['missing'].append({'kind': kind, 'name': fname, 'note': 'NAS 上不存在'})
                    continue
                tmp = dst.with_name(dst.name + '.part')
                shutil.copy2(str(src), str(tmp))
                if not tmp.exists() or tmp.stat().st_size != src.stat().st_size:
                    try:
                        tmp.unlink()
                    except Exception:
                        pass
                    res['missing'].append({'kind': kind, 'name': fname, 'note': '拷贝大小不一致'})
                    continue
                os.replace(str(tmp), str(dst))
                res['restored'].append({'kind': kind, 'name': fname,
                                        'size': dst.stat().st_size})
            except Exception as e:
                res['missing'].append({'kind': kind, 'name': fname,
                                       'note': f'{type(e).__name__}: {e}'})
        res['ok'] = any(x['kind'] == 'mono' for x in res['restored'])
        return res
    except Exception as e:
        res['error'] = f'{type(e).__name__}: {e}'
        return res


# ══════════════════════════ 48h 回收 ══════════════════════════

def _group_versions(translated_dir: Path, upload_dir: Path):
    """把本地文件按 base stem 聚成「一文献三版本」。

    以 mono 为锚（没有译文的不算「已翻译文献」，不参与回收）。
    返回 [(base, {'mono':Path,'dual':Path|None,'original':Path|None})]
    """
    groups = {}
    for mono in sorted(translated_dir.glob('*.zh.mono.pdf')):
        if not mono.is_file():
            continue
        b = base_stem(mono.name)
        g = groups.setdefault(b, {'mono': mono, 'dual': None, 'original': None})
        d = translated_dir / dual_name_for(mono.name)
        g['dual'] = d if d.is_file() else None
        o = upload_dir / f'{b}.pdf'
        g['original'] = o if o.is_file() else None
    return groups


def reap(upload_dir, translated_dir, *, ttl_hours: float = DEFAULT_TTL_HOURS,
         dry_run: bool = False, delete: bool = True) -> dict:
    """回收本地超过 ttl_hours 的译文（三版本一起搬走再删）。

    ★ 安全顺序：copy → 逐个校验 NAS 副本 exists 且 size 一致 → 才删本地。
      校验没过的保留本地并记进 kept，绝不为了腾空间冒险。

    delete=False 时只归档不删（等价于「预搬迁」）。
    返回报告 dict，可直接 JSON 化。
    """
    report = {
        'started_at': _now_iso(), 'ttl_hours': ttl_hours, 'dry_run': dry_run,
        'nas_root': None, 'scanned': 0, 'expired': 0,
        'archived': [], 'deleted': [], 'kept': [], 'skipped_recent': 0,
        'orphan_originals': [], 'freed_bytes': 0, 'error': None,
    }
    try:
        upload_dir, translated_dir = Path(upload_dir), Path(translated_dir)
        if not available():
            report['error'] = 'NAS 不可达，跳过回收（本地文件保持不动）'
            return report
        r = root()
        report['nas_root'] = str(r)

        groups = _group_versions(translated_dir, upload_dir)
        report['scanned'] = len(groups)
        cutoff = time.time() - ttl_hours * 3600

        for base, g in groups.items():
            # 三个版本里「最新」的 mtime 作为最后活跃时间：
            # 只要有一个版本近期被碰过（例如刚从 NAS 取回），整组都不回收。
            mtimes = [p.stat().st_mtime for p in g.values() if p is not None]
            if not mtimes:
                continue
            newest = max(mtimes)
            if newest > cutoff:
                report['skipped_recent'] += 1
                continue

            report['expired'] += 1
            stem = norm_stem(base)

            # ── 1. 归档（幂等：NAS 上已有同尺寸副本就跳过传输）──
            ar = archive(g['original'], g['mono'], g['dual'],
                         display_name=f'{base}.pdf', source='reap')
            report['archived'].append({'stem': stem, 'ok': ar.get('ok'),
                                       'missing': ar.get('missing')})

            # ── 2. 逐版本校验 + 删除 ──
            for kind in KINDS:
                p = g.get(kind)
                if p is None:
                    continue
                nas_file = r / SUBDIRS[kind] / p.name
                try:
                    ok = nas_file.exists() and nas_file.stat().st_size == p.stat().st_size
                except Exception as e:
                    ok = False
                    logger.debug('校验失败 %s: %s', nas_file, e)
                if not ok:
                    report['kept'].append({'stem': stem, 'kind': kind,
                                           'name': p.name,
                                           'reason': 'NAS 副本缺失或大小不一致'})
                    continue
                size = p.stat().st_size
                if dry_run or not delete:
                    report['kept'].append({'stem': stem, 'kind': kind, 'name': p.name,
                                           'reason': 'dry-run/未开启删除', 'size': size})
                    continue
                try:
                    os.remove(str(p))
                    report['deleted'].append({'stem': stem, 'kind': kind,
                                              'name': p.name, 'size': size})
                    report['freed_bytes'] += size
                except Exception as e:
                    report['kept'].append({'stem': stem, 'kind': kind, 'name': p.name,
                                           'reason': f'删除失败 {type(e).__name__}: {e}'})

        # ── 3. 孤儿原件清理 ──
        #    同一篇文献每次上传都会生成新的 {stem}_{新hash}.pdf，
        #    而 NAS 索引里只保留一份原件 —— 这些"重复上传的副本"会永远赖在
        #    uploads/ 里，不清理的话本地空间根本腾不出来。
        #    判定条件缺一不可（宁可留着也绝不误删）：
        #      a. 该文献在索引里有条目（说明 NAS 上确实已有存档）
        #      b. NAS 上的原件确实存在
        #      c. 本地文件大小与 NAS 原件**完全一致**
        idx_now = load_index()
        for p in sorted(upload_dir.glob('*.pdf')):
            if not p.is_file():
                continue
            try:
                if p.stat().st_mtime > cutoff:
                    continue
                item = idx_now['items'].get(norm_stem(p.name))
                if not item:
                    continue
                meta = (item.get('files') or {}).get('original')
                if not meta:
                    continue
                size = p.stat().st_size
                if meta.get('size') != size:
                    continue
                if not (r / SUBDIRS['original'] / meta['name']).exists():
                    continue
            except Exception as e:
                logger.debug('孤儿原件判定跳过 %s: %s', p.name, e)
                continue

            if dry_run or not delete:
                report['orphan_originals'].append(
                    {'name': p.name, 'size': size, 'reason': 'dry-run/未开启删除'})
                continue
            try:
                os.remove(str(p))
                report['orphan_originals'].append({'name': p.name, 'size': size,
                                                   'deleted': True})
                report['freed_bytes'] += size
            except Exception as e:
                report['orphan_originals'].append(
                    {'name': p.name, 'reason': f'删除失败 {type(e).__name__}: {e}'})

        report['finished_at'] = _now_iso()
        return report
    except Exception as e:
        report['error'] = f'{type(e).__name__}: {e}'
        logger.warning('reap 异常: %s', e)
        return report


# ══════════════════════════ 状态 ══════════════════════════

def status(upload_dir=None, translated_dir=None) -> dict:
    """给 CLI / 排查用的概览。"""
    s = {
        'nas_root': None, 'nas_available': False, 'index_path': str(INDEX_PATH),
        'index_items': 0, 'nas_counts': {}, 'local_counts': {},
        'oldest_local_age_hours': None, 'ttl_hours': DEFAULT_TTL_HOURS,
    }
    try:
        r = root(refresh=True)
        s['nas_available'] = r is not None
        s['nas_root'] = str(r) if r else None
        if r is not None:
            for kind in KINDS:
                d = r / SUBDIRS[kind]
                try:
                    s['nas_counts'][kind] = len([p for p in d.glob('*.pdf') if p.is_file()])
                except Exception:
                    s['nas_counts'][kind] = None
    except Exception as e:
        s['nas_error'] = str(e)

    idx = load_index()
    s['index_items'] = len(idx.get('items', {}))
    s['index_updated_at'] = idx.get('updated_at')

    if translated_dir:
        td = Path(translated_dir)
        try:
            monos = [p for p in td.glob('*.zh.mono.pdf') if p.is_file()]
            duals = [p for p in td.glob('*.zh.dual.pdf') if p.is_file()]
            s['local_counts'] = {'mono': len(monos), 'dual': len(duals)}
            if monos:
                oldest = min(p.stat().st_mtime for p in monos)
                s['oldest_local_age_hours'] = round((time.time() - oldest) / 3600, 1)
        except Exception:
            pass
    if upload_dir:
        try:
            s['local_counts']['original'] = len(
                [p for p in Path(upload_dir).glob('*.pdf') if p.is_file()])
        except Exception:
            pass
    return s
