# -*- coding: utf-8 -*-
"""爬取进度快照（历史清单爬取 + 待入库队列 + 调度器状态）。

单一真源，三个消费者共用：
  · 5556 `GET /api/crawl/progress`  → JSON（AI / 外部脚本）
  · 5555 `/geokb/progress`          → 渲染成可视化页面
  · 命令行 `python scripts/crawl_status.py` → 人读摘要

为什么单独成模块：进度页要同时看「待爬清单 / 已完成 / 待入库 / 调度器
此刻在干什么 / SD 是否暂停」，这些分散在 6 个文件里。把拼装逻辑收在一处，
页面和 API 就不会各算一套、各说一个数。
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
STATE = BASE / 'state'
MANIFEST = STATE / 'history_manifest.jsonl'
DONE = STATE / 'history_done.jsonl'
PENDING = STATE / 'pending_papers_ingest.jsonl'
LEDGER = STATE / 'papers_index.jsonl'
PID_FILE = STATE / 'history_queue_loop.pid'
LOOP_LOG = BASE / 'logs' / 'history_queue_loop.log'
SD_PAUSE = STATE / 'sd_pause.json'
INGEST_POLICY = STATE / 'ingest_policy.json'

GAP_SEC = 180                 # 与 history_queue_loop.GAP 一致（3 分钟/篇）
NIGHT_HOURS = 12.9            # 入库模式的每日可跑时长（18:05 - 07:00）
FULL_DAY_HOURS = 24.0         # 只下载模式的每日可跑时长（全天候）
_CACHE = {'ts': 0.0, 'data': None}
TTL = 8                       # 秒；页面 15s 轮询，8s 缓存足够且不会看旧数据

_TS_RE = re.compile(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s*(.*)$')
_WAIT_RE = re.compile(r'等\s*(\d+)\s*分\s*(\d+)\s*秒后爬下一篇')
_LEFT_RE = re.compile(r'待爬清单剩余\s*(\d+)\s*篇')
# 「本篇完成：用时 551s」/「本篇未完成：用时 246s」—— 取真实单篇耗时，
# 用来估 ETA。只按 gap(180s) 估会严重低估：实测一篇常跑 4–9 分钟。
_SPENT_RE = re.compile(r'本篇(?:完成|未完成)：用时\s*(\d+)s')


# ── 小工具 ──────────────────────────────────────────────────────────────
def _iter_jsonl(path):
    if not path.exists():
        return
    try:
        with open(path, encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except OSError:
        return


def _pid_alive(pid):
    if not pid or pid <= 0:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        pass
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _tail(path, nbytes=180000, nlines=600):
    if not path.exists():
        return []
    try:
        with open(path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - nbytes))
            data = f.read().decode('utf-8', 'ignore')
        return data.splitlines()[-nlines:]
    except OSError:
        return []


# ── 待爬清单 ────────────────────────────────────────────────────────────
def _is_sd_row(r):
    """SD 判据的**零依赖**版本（不 import sd_policy，5556 进程的 sys.path 里没有 scripts）。"""
    if 'sd' in r:
        return bool(r['sd'])
    if (r.get('doi') or '').lower().startswith('10.1016/'):
        return True
    u = (r.get('url') or '').lower()
    return any(h in u for h in ('sciencedirect.com', 'linkinghub.elsevier.com'))


def manifest_stats():
    total = pending_any = pending_free = sd_all = parked = 0
    by_j, by_reach = {}, {}
    for r in _iter_jsonl(MANIFEST):
        total += 1
        is_sd = _is_sd_row(r)
        if is_sd:
            sd_all += 1
        if (r.get('status') or 'pending') == 'parked' or int(r.get('attempts', 0) or 0) >= 3:
            parked += 1
            continue
        pending_any += 1
        if is_sd:
            continue                      # SD 暂停期不消费，不进「可执行」与期刊分布
        pending_free += 1
        j = r.get('journal') or '未知'
        by_j[j] = by_j.get(j, 0) + 1
        rc = r.get('reach') or 'other'
        by_reach[rc] = by_reach.get(rc, 0) + 1

    sd_paused = False
    try:
        sd_paused = bool(json.loads(SD_PAUSE.read_text(encoding='utf-8')).get('paused'))
    except Exception:
        pass

    return {
        'total': total,
        'pending': pending_any,                     # 待爬（不含 parked）
        'parked': parked,
        'sd': sd_all,
        'sd_waiting': pending_any - pending_free,   # 待爬里被 SD 暂停挡住的
        # 可执行 = 待爬 - 被 SD 挡住的（SD 未暂停时全部可执行）
        'runnable': pending_free if sd_paused else pending_any,
        'by_journal': sorted(({'journal': k, 'n': v} for k, v in by_j.items()),
                             key=lambda x: -x['n']),
        'by_reach': sorted(({'reach': k, 'n': v} for k, v in by_reach.items()),
                           key=lambda x: -x['n']),
    }


# ── 已完成 / 待入库 ─────────────────────────────────────────────────────
def done_stats(hours=12):
    total = today = last24 = pending_mode = 0
    now = datetime.now()
    d0 = now.date()
    buckets = {}
    for i in range(hours):
        h = (now - timedelta(hours=i)).strftime('%H')
        buckets[h] = 0
    recent = []
    for r in _iter_jsonl(DONE):
        total += 1
        if r.get('ingest') == 'pending':
            pending_mode += 1
        fs = r.get('finished_at') or ''
        try:
            t = datetime.fromisoformat(fs)
        except Exception:
            continue
        if t.date() == d0:
            today += 1
        if (now - t).total_seconds() <= 86400:
            last24 += 1
        hh = t.strftime('%H')
        if hh in buckets:
            buckets[hh] += 1
        recent.append((t, r))

    recent.sort(key=lambda x: x[0], reverse=True)
    return {
        'total': total,
        'today': today,
        'last_24h': last24,
        'pending_mode': pending_mode,
        'hourly': [{'hour': (now - timedelta(hours=i)).strftime('%H'),
                    'n': buckets.get((now - timedelta(hours=i)).strftime('%H'), 0)}
                   for i in range(hours - 1, -1, -1)],
        'recent': [{
            'title': (r.get('title') or '')[:80],
            'journal': r.get('journal') or '',
            'finished_at': (r.get('finished_at') or '')[5:16].replace('T', ' '),
            'url': r.get('url') or '',
        } for _t, r in recent[:12]],
    }


def pending_ingest():
    items = list(_iter_jsonl(PENDING))
    return {
        'total': len(items),
        'items': [{
            'title': (x.get('title') or '')[:80],
            'journal': x.get('journal') or '',
            'ts': (x.get('ts') or '')[5:16].replace('T', ' '),
            'from': x.get('from') or '',
            'url': x.get('url') or '',
        } for x in items[-8:]][::-1],
    }


def ledger_count():
    n = 0
    for _ in _iter_jsonl(LEDGER):
        n += 1
    return n


# ── 调度器状态 ──────────────────────────────────────────────────────────
def loop_state():
    pid = 0
    try:
        pid = int(PID_FILE.read_text(encoding='utf-8').strip())
    except Exception:
        pass
    alive = _pid_alive(pid)

    last_ts, last_msg = None, ''
    state, detail, next_dt = 'unknown', '', None
    spent_samples = []
    for line in _tail(LOOP_LOG):
        ms = _SPENT_RE.search(line)
        if ms:
            spent_samples.append(int(ms.group(1)))
        m = _TS_RE.match(line)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S')
        except Exception:
            continue
        msg = m.group(2).strip()
        last_ts, last_msg = ts, msg
        if '调度器启动' in msg:
            state, detail, next_dt = 'starting', msg, None
        elif '开始爬一篇' in msg:
            mm = _LEFT_RE.search(msg)
            state = 'crawling'
            detail = ('正在爬取（清单剩余 %s 篇）' % mm.group(1)) if mm else '正在爬取'
            next_dt = None
        elif '本篇完成' in msg:
            state, detail, next_dt = 'finished', msg, None
        elif '本篇未完成' in msg:
            state, detail, next_dt = 'retry', msg, None
        elif '本篇异常' in msg:
            state, detail, next_dt = 'error', msg[:160], None
        elif '后爬下一篇' in msg:
            mm = _WAIT_RE.search(msg)
            state = 'waiting'
            if mm:
                sec = int(mm.group(1)) * 60 + int(mm.group(2))
                next_dt = ts + timedelta(seconds=sec)
                detail = '等 %s 分 %s 秒后爬下一篇' % (mm.group(1), mm.group(2))
            else:
                detail = msg
        elif '静默' in msg:
            state, detail, next_dt = 'silent', msg, None
        elif '无可执行篇目' in msg:
            state, detail, next_dt = 'starve', msg, None
        elif '待爬清单已空' in msg:
            state, detail, next_dt = 'empty', msg, None

    if not alive:
        state, detail, next_dt = 'stopped', '调度进程未运行', None

    labels = {
        'starting': '启动中', 'crawling': '正在爬取', 'finished': '刚完成一篇',
        'retry': '本篇失败待重试', 'error': '异常', 'waiting': '节流等待中',
        'silent': '静默让位', 'starve': '无可执行篇目', 'empty': '清单已空',
        'stopped': '已停止', 'unknown': '未知',
    }
    age = int((datetime.now() - last_ts).total_seconds()) if last_ts else None

    # 实测单篇周期 = 中位下载耗时 + 节流间隔。样本不足 2 篇时只按节流估。
    tail_spent = spent_samples[-12:]
    if len(tail_spent) >= 2:
        import statistics
        med = statistics.median(tail_spent)
        cycle = int(med) + GAP_SEC
    else:
        med = 0
        cycle = GAP_SEC

    return {
        'running': alive,
        'pid': pid,
        'state': state,
        'state_label': labels.get(state, state),
        'detail': detail,
        'last_event_at': last_ts.isoformat(timespec='seconds') if last_ts else None,
        'last_event_ts': int(last_ts.timestamp()) if last_ts else None,
        'last_event_age_sec': age,
        # next_at 是给人看的字符串；next_ts 给前端算倒计时（避免解析无时区 ISO）
        'next_at': next_dt.isoformat(timespec='seconds') if next_dt else None,
        'next_ts': int(next_dt.timestamp()) if next_dt else None,
        'gap_sec': GAP_SEC,
        'cycle_sec': cycle,
        'median_spent_sec': med,
        'samples': len(tail_spent),
    }


def policy_state():
    sd = {}
    try:
        sd = json.loads(SD_PAUSE.read_text(encoding='utf-8'))
    except Exception:
        pass
    sc = {}
    try:
        sc = (json.loads(INGEST_POLICY.read_text(encoding='utf-8')) or {}).get('scopes') or {}
    except Exception:
        pass
    return {
        'sd_paused': bool(sd.get('paused')),
        'sd_since': sd.get('since') or '',
        'sd_resume_hint': sd.get('resume_hint') or '',
        'ingest_daily_poster': bool(sc.get('daily_poster', True)),
        'ingest_history': bool(sc.get('history', False)),
    }


# ── 快照 ────────────────────────────────────────────────────────────────
def snapshot(use_cache=True):
    now = time.time()
    if use_cache and _CACHE['data'] and now - _CACHE['ts'] < TTL:
        return _CACHE['data']

    man = manifest_stats()
    done = done_stats()
    pend = pending_ingest()
    loop = loop_state()
    pol = policy_state()

    runnable = man['runnable']
    cycle = loop['cycle_sec']
    eta_h = runnable * cycle / 3600.0
    # 只下载不入库 = 不调 DeepSeek → 全天候跑，ETA 按 24h/天折算；
    # 一旦开启 history 入库（会调 API），退回夜间窗口 12.9h/天。
    all_day = not pol['ingest_history']
    run_h = FULL_DAY_HOURS if all_day else NIGHT_HOURS
    data = {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'manifest': man,
        'done': done,
        'pending_ingest': pend,
        'ledger': {'papers': ledger_count()},
        'loop': loop,
        'policy': pol,
        'eta': {
            'hours': round(eta_h, 1),
            'days': round(eta_h / run_h, 1) if eta_h else 0,
            'nights': round(eta_h / run_h, 1) if eta_h else 0,   # 兼容旧字段名，语义同 days
            'run_hours': run_h,
            'all_day': all_day,
            'gap_sec': GAP_SEC,
            'night_hours': NIGHT_HOURS,
            'cycle_sec': cycle,
            'cycle_min': round(cycle / 60.0, 1),
            'spent_min': round(loop['median_spent_sec'] / 60.0, 1),
            'samples': loop['samples'],
        },
        'progress': {
            'done_total': done['total'],
            'remaining': man['total'],
            'all': done['total'] + man['total'],
            'pct': round(done['total'] * 100.0 / max(1, done['total'] + man['total']), 1),
        },
    }
    _CACHE.update({'ts': now, 'data': data})
    return data


def main():
    import argparse
    ap = argparse.ArgumentParser(description='爬取进度快照')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    d = snapshot(use_cache=False)
    if args.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0
    m, dn, pi, lp, pg = d['manifest'], d['done'], d['pending_ingest'], d['loop'], d['progress']
    print('总进度  %d / %d（%.1f%%）' % (pg['done_total'], pg['all'], pg['pct']))
    print('待爬    %d 篇（可执行 %d ｜ SD 暂停 %d ｜ 停放 %d）'
          % (m['pending'], m['runnable'], m['sd'], m['parked']))
    print('已完成  %d 篇（今日 %d ｜ 近 24h %d）' % (dn['total'], dn['today'], dn['last_24h']))
    print('待入库  %d 篇（入库策略 history=%s）'
          % (pi['total'], 'on' if d['policy']['ingest_history'] else 'off'))
    print('台账    %d 篇已归档' % d['ledger']['papers'])
    print('调度器  %s（pid=%s）｜ %s' % (lp['state_label'], lp['pid'], lp['detail'][:70]))
    eta = d['eta']
    mode = ('全天候（只下载，不调 API）' if eta['all_day']
            else '夜间窗口 %sh/天（入库模式）' % eta['run_hours'])
    print('预计    %s 分钟/篇（下载 %s + 节流 %d）→ %s 小时 ≈ %s 天 ｜ %s'
          % (eta['cycle_min'], eta['spent_min'], eta['gap_sec'] // 60,
             eta['hours'], eta['days'], mode))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
