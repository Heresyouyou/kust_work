# -*- coding: utf-8 -*-
"""入库策略：**除 daily_poster 外，自动爬取只下载不入库**。

用户口径（2026-10-20 20:31）
---------------------------
> 自动爬取脚本，除了每天的 daily_poster 以外，其他 PDF 只爬取下载到本地，不入库。
> daily_poster 爬到的论文 PDF 需要自动入库，其他 PDF 入库需等我统一安排。

落地语义
--------
「入库」= 调 LLM 抽全文 → 写 `content/papers/*.md` 词条 → 建索引 / 拆知识点。
下载 → 落 NAS → 写台账（`state/papers_index.jsonl`）**不属于入库**，照常进行。

| scope           | 谁在用                                   | 默认 |
|-----------------|------------------------------------------|------|
| `daily_poster`  | 每日管线：`daily_archive_papers.py`（日报海报）+ `ingest_external_feeds.py`（外部订阅余下链路） | ✅ 开 |
| `history`       | `history_queue.py`（历史清单常驻爬取）      | ❌ 关 |
| `migrate`       | `migrate_feed_to_papers.py`               | ❌ 关 |
| `backfill`      | `backfill_all_pdfs.py` / `backfill_en_papers.py` | ❌ 关 |
| `default`       | 其他任何自动链路                            | ❌ 关 |

被关掉的链路的产物不会丢：PDF 照常落 NAS + 台账，同时记一条待入库记录到
`state/pending_papers_ingest.jsonl`（与既有约定同一文件），等用户统一安排时
用 `python scripts/ingest_policy.py --run --limit N` 批量补抽取。

开关文件：`state/ingest_policy.json`
    {"scopes": {"daily_poster": true, "default": false}}
环境变量 `GEOKB_AUTO_INGEST=1/0` 可全局覆盖（1=全开，0=全关，含 daily_poster）。

CLI
----
    python scripts/ingest_policy.py --status
    python scripts/ingest_policy.py --list                 # 待入库队列
    python scripts/ingest_policy.py --run --limit 5        # 手动统一入库
    python scripts/ingest_policy.py --set history=on       # 单链路开关
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_NO_WIN = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW：子孙全继承隐藏控制台
BASE = Path('D:/ZSK/geokb')
STATE = BASE / 'state'
POLICY_FILE = STATE / 'ingest_policy.json'
PENDING = STATE / 'pending_papers_ingest.jsonl'

DEFAULT_SCOPES = {
    'daily_poster': True,   # 唯一允许自动入库的链路（日报海报）
    'history': False,
    'migrate': False,
    'backfill': False,
    'default': False,
}

_cache = {'mtime': None, 'scopes': None}


def _scopes() -> dict:
    try:
        mt = POLICY_FILE.stat().st_mtime if POLICY_FILE.exists() else None
    except Exception:
        mt = None
    if _cache['scopes'] is None or _cache['mtime'] != mt:
        sc = dict(DEFAULT_SCOPES)
        if mt is not None:
            try:
                raw = json.loads(POLICY_FILE.read_text(encoding='utf-8'))
                for k, v in (raw.get('scopes') or {}).items():
                    sc[str(k)] = bool(v)
            except Exception:
                pass
        _cache.update({'mtime': mt, 'scopes': sc})
    return dict(_cache['scopes'])


def enabled(scope: str = 'default') -> bool:
    """该链路是否允许**自动入库**。"""
    env = os.environ.get('GEOKB_AUTO_INGEST', '').strip().lower()
    if env in ('1', 'true', 'yes', 'on'):
        return True
    if env in ('0', 'false', 'no', 'off'):
        return False
    return bool(_scopes().get(scope, _scopes().get('default', False)))


def set_scope(scope: str, flag: bool, who: str = 'cli') -> dict:
    sc = _scopes()
    sc[scope] = bool(flag)
    POLICY_FILE.parent.mkdir(parents=True, exist_ok=True)
    POLICY_FILE.write_text(json.dumps({
        'scopes': sc,
        'updated_at': datetime.now().isoformat(timespec='seconds'),
        'by': who,
        'rule': '仅 daily_poster 自动入库；其余链路只下载，入库由用户统一安排',
    }, ensure_ascii=False, indent=1), encoding='utf-8')
    _cache.update({'mtime': None, 'scopes': None})
    return sc


# ── 待入库队列 ──────────────────────────────────────────────────────────
def queue_pending(meta: dict) -> bool:
    """把「已下载、未入库」的一篇记进队列。按 eid/pdf 去重（幂等）。"""
    rec = {
        'eid': (meta.get('eid') or '').strip(),
        'title': (meta.get('title') or '').strip(),
        'journal': (meta.get('journal') or '').strip(),
        'pdf': (meta.get('pdf') or meta.get('repo_path') or '').strip(),
        'url': (meta.get('url') or '').strip(),
        'doi': (meta.get('doi') or '').strip(),
        'from': (meta.get('from') or 'crawl').strip(),
        'ts': datetime.now().isoformat(timespec='seconds'),
    }
    if not rec['pdf'] and not rec['eid']:
        return False
    key = rec['eid'] or rec['pdf']
    try:
        if PENDING.exists():
            with open(PENDING, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        old = json.loads(line)
                    except Exception:
                        continue
                    if (old.get('eid') or old.get('pdf')) == key:
                        return False
        PENDING.parent.mkdir(parents=True, exist_ok=True)
        with open(PENDING, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        return True
    except OSError:
        return False


def load_pending() -> list:
    out = []
    if not PENDING.exists():
        return out
    with open(PENDING, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def _py() -> str:
    for p in ('D:/ZSK/geokb/venv/Scripts/python.exe', sys.executable):
        if p and os.path.isfile(p):
            return p
    return sys.executable


def run_pending(limit: int = 3, dry: bool = False) -> dict:
    """统一入库：逐条跑 pdf_corpus_ingest --only，成功的移出队列。

    ⚠️ 会调 DeepSeek。只在用户明确安排时执行，且受 09:00–12:00 / 14:00–18:00
    高峰禁令约束（那是对话内主动发起的约束，用户手动执行时自行判断）。
    """
    items = load_pending()
    todo = [x for x in items if x.get('pdf') and Path(x['pdf']).exists()][:limit]
    res = {'queued': len(items), 'picked': len(todo), 'ok': 0, 'fail': 0, 'detail': []}
    if dry:
        res['detail'] = [{'eid': x.get('eid'), 'pdf': x.get('pdf')} for x in todo]
        return res
    done_keys = set()
    for x in todo:
        try:
            r = subprocess.run(
                [_py(), str(BASE / 'scripts' / 'pdf_corpus_ingest.py'), '--only', x['pdf']],
                cwd=str(BASE), capture_output=True, text=True, encoding='utf-8',
                errors='ignore', timeout=900, creationflags=_NO_WIN,
                env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1'})
            out = ((r.stdout or '') + (r.stderr or '')).strip()
            ok = r.returncode == 0
            res['ok' if ok else 'fail'] += 1
            res['detail'].append({'eid': x.get('eid'), 'rc': r.returncode,
                                  'tail': out.splitlines()[-1][:140] if out else ''})
            if ok:
                done_keys.add(x.get('eid') or x.get('pdf'))
        except Exception as e:  # noqa: BLE001
            res['fail'] += 1
            res['detail'].append({'eid': x.get('eid'), 'rc': -1,
                                  'tail': '%s: %s' % (type(e).__name__, str(e)[:120])})
    if done_keys:
        remain = [x for x in items if (x.get('eid') or x.get('pdf')) not in done_keys]
        tmp = PENDING.with_suffix('.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            for x in remain:
                f.write(json.dumps(x, ensure_ascii=False) + '\n')
        os.replace(tmp, PENDING)
        res['remain'] = len(remain)
    res['remain'] = res.get('remain', len(items))
    return res


# ── CLI ────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description='入库策略（自动爬取默认不入库）')
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--list', action='store_true', help='列出待入库队列')
    ap.add_argument('--run', action='store_true', help='手动统一入库（会调 DeepSeek）')
    ap.add_argument('--limit', type=int, default=3)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--set', action='append', default=[], metavar='SCOPE=on|off')
    args = ap.parse_args()

    if args.set:
        for s in args.set:
            k, _, v = s.partition('=')
            sc = set_scope(k.strip(), v.strip().lower() in ('on', '1', 'true', 'yes'))
        print(json.dumps(sc, ensure_ascii=False, indent=1))
        return 0

    if args.run:
        print(json.dumps(run_pending(limit=args.limit, dry=args.dry_run),
                         ensure_ascii=False, indent=1))
        return 0

    if args.list:
        items = load_pending()
        print('待入库 %d 条：' % len(items))
        for x in items[:40]:
            print('  · %-14s %-38s %s'
                  % (x.get('eid', '')[:14], (x.get('journal') or '')[:38],
                     (x.get('title') or '')[:50]))
        if len(items) > 40:
            print('  … 还有 %d 条' % (len(items) - 40))
        return 0

    sc = _scopes()
    print('入库策略（True = 允许自动入库）')
    for k in ('daily_poster', 'history', 'migrate', 'backfill', 'default'):
        print('  %-14s %s%s' % (k, '✅ 开' if sc.get(k) else '❌ 关',
                                '' if k == 'daily_poster' else ''))
    print('待入库队列：%d 条（%s）' % (len(load_pending()), PENDING))
    print('环境覆盖：GEOKB_AUTO_INGEST=%s' % (os.environ.get('GEOKB_AUTO_INGEST') or '(未设)'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
