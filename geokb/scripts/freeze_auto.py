# -*- coding: utf-8 -*-
"""按「实际爬取结果」自动判定哪些源站该冻结。

判定只用两个客观来源，不靠猜：
  1. logs/history_queue_loop.log 的窗口期结果行（✓ / ✗ / ⟳）—— 反映**现在**还拦不拦；
  2. state/history_manifest.jsonl 的累计字段（attempts / last_error）—— 反映**历史**存量。

为什么两个都要：
  只看日志 → 今天刚恢复的源站会被误冻（窗口里恰好连败几次）；
  只看清单 → 三周前的老错误还在，早就不拦了反而被冻住。

冻结分类（决定后面怎么做绕路）：
  blocked  风控拦截为主（blocked_captcha / 403 / 429 / cf_challenge）
           → 只能靠浏览器指纹、代理、机构入口绕，靠调间隔没用
  broken   本地 handler 故障为主（err_index / guard_fail / err_parse）
           → 修 handler 就能恢复，冻它是为了别再 4 秒一次空刷
  dead     源头就没全文为主（no_fulltext / not_found / not_pdf）
           → 换源，别在这站上耗

用法：
    python scripts/freeze_auto.py --dry-run          # 先看判成什么，不写盘
    python scripts/freeze_auto.py                    # 写入冻结表
    python scripts/freeze_auto.py --hours 24         # 冻结 24h 后自动失效
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
SCRIPTS = BASE / 'scripts'
STATE = BASE / 'state'
LOGL = STATE / 'history_manifest.jsonl'
QLOG = BASE / 'logs' / 'history_queue_loop.log'

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

RE_LINE = re.compile(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+([✓✗⟳])\s+\[([^\]]+)\]\s+(.*)$')
# 通用捕获，**不再用白名单枚举**：2026-10-27 实测 quota_exhausted / skipped
# 不在旧白名单里，全被归成 'other'，导致 OUP 的 91 次配额用尽被当成故障。
# 新增错误码不必再改这里。
RE_ERR = re.compile(r'(?:软失败|硬失败)\s+([A-Za-z_0-9]+)')

BLOCK_ERRS = {'blocked_captcha', 'http_403', 'cf_challenge', 'captcha', 'http_429'}
BROKEN_ERRS = {'err_index', 'err_parse', 'err_item_timeout', 'guard_fail'}
WALL_ERRS = {'paywalled', 'not_pdf', 'not_pdf_office'}
DEAD_ERRS = {'no_fulltext', 'not_found', '404'}
# 中性状态：既不是成功，也**不是源站故障**，必须从 fail 里剔除。
#   quota_exhausted 本站当日配额用尽（OUP 25 篇/天），根本没发出请求（实测耗时 1s）；
#   skipped         handler 还没接（Wiley/T&F/Cambridge 等），本地能力缺口。
# 2026-10-27 实测：OUP 24h 内 129 次失败里 89 次是 quota_exhausted，
# 被算成 fail 后整站被判「本地故障」——冻它既解决不了配额，也会误伤正常站。
NEUTRAL_ERRS = {'quota_exhausted', 'skipped'}
# err_exception 是「不知道哪一步炸了」，**不参与定性**（只进明细）。
# 实测它在订阅刊上经常是「拿到付费墙页面后解析炸了」，判成本地故障会误导修错地方。
UNKNOWN_ERRS = {'err_exception'}

# SD 已由 state/sd_pause.json 在策略层沉底，这里不重复冻结（否则解冻时两处打架）
SKIP_HOSTS = {'sciencedirect.com'}


def load_freeze_mod():
    spec = importlib.util.spec_from_file_location('sfz2', str(SCRIPTS / 'source_freeze.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def scan_log(hours):
    """窗口内的结果行 → {host: Counter}。"""
    since = datetime.now() - timedelta(hours=hours)
    out = defaultdict(Counter)
    if not QLOG.exists():
        return out
    with open(QLOG, 'r', encoding='utf-8', errors='ignore') as f:
        for ln in f:
            m = RE_LINE.match(ln.rstrip('\n'))
            if not m:
                continue
            try:
                ts = datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S')
            except ValueError:
                continue
            if ts < since:
                continue
            mark, host, rest = m.group(2), m.group(3), m.group(4)
            c = out[host]
            if mark == '✓':
                c['ok'] += 1
                continue
            e = RE_ERR.search(rest)
            k = e.group(1) if e else 'other'
            c['e:' + k] += 1
            if k in NEUTRAL_ERRS:
                # 配额用尽 / handler 未接：不计失败，否则 OUP 这类站会被误判成故障
                c['neutral'] += 1
                continue
            c['fail'] += 1
            if k in BLOCK_ERRS:
                c['blocked'] += 1
    return out


def scan_manifest(frz):
    """清单存量 → {host: Counter}（只统计**试过**的条目）。"""
    out = defaultdict(Counter)
    if not LOGL.exists():
        return out
    with open(LOGL, 'r', encoding='utf-8', errors='ignore') as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                it = json.loads(ln)
            except ValueError:
                continue
            h = frz.host_of(it.get('url', ''), it.get('doi', ''))
            if not h:
                continue
            c = out[h]
            c['n'] += 1
            att = int(it.get('attempts') or 0) + int(it.get('try_count') or 0)
            sf_ = int(it.get('soft_fails') or 0)
            if not att and not sf_:
                continue
            c['tried'] += 1
            e = it.get('last_error') or ''
            for k in BLOCK_ERRS:
                if k in e:
                    c['blocked'] += 1
                    c['e:' + k] += 1
                    break
            else:
                for k in BROKEN_ERRS:
                    if k in e:
                        c['e:' + k] += 1
                        break
                else:
                    for k in WALL_ERRS:
                        if k in e:
                            c['e:' + k] += 1
                            break
                    else:
                        for k in DEAD_ERRS:
                            if k in e:
                                c['e:' + k] += 1
                                break
    return out


def verdict(c, min_fail, max_ok_rate):
    """一个 host 的窗口统计 → (kind, reason, detail) 或 None。

    只有「窗口内失败够多」且「成功率够低」才冻，避免误伤刚恢复的站。
    分类按 KIND_ORDER 固定优先级，不按票数：被风控拦住时，
    窗口里同时出现的 paywalled / not_pdf 都是假信号。
    """
    fail, ok = c['fail'], c['ok']
    if fail < min_fail:
        return None
    total = fail + ok
    ok_rate = ok / float(total) if total else 0.0
    if ok_rate > max_ok_rate:
        return None
    kinds = Counter()
    for k, v in c.items():
        if not k.startswith('e:'):
            continue
        name = k[2:]
        if name == 'other' or name in UNKNOWN_ERRS:
            continue
        if name in BLOCK_ERRS:
            kinds['blocked'] += v
        elif name in BROKEN_ERRS:
            kinds['broken'] += v
        elif name in WALL_ERRS:
            kinds['wall'] += v
        elif name in DEAD_ERRS:
            kinds['dead'] += v
    if not kinds:
        return None
    # 拦截硬优先：被 Cloudflare 拦住时，同窗口里的 paywalled / not_pdf 全是假信号。
    # 门槛 ≥3 次且占失败 20% 以上——偶发一次挑战不该冻整站。
    if kinds.get('blocked', 0) >= max(3, int(0.2 * fail)):
        kind = 'blocked'
    else:
        kinds.pop('blocked', None)
        if not kinds:
            return None
        kind = kinds.most_common(1)[0][0]
    detail = ', '.join('%s×%d' % (k[2:], v) for k, v in c.most_common(16)
                       if k.startswith('e:') and k[2:] not in NEUTRAL_ERRS)
    reason = '%s(窗口 %d 失败/%d 成功, 成功率 %d%%)' % (kind, fail, ok, round(100 * ok_rate))
    return kind, reason, detail


def main(argv=None):
    ap = argparse.ArgumentParser(description='自动判定并冻结持续拦截/持续失败的源站')
    ap.add_argument('--hours', type=float, default=24, help='日志窗口小时数（默认 24）')
    ap.add_argument('--min-fail', type=int, default=5, help='窗口内最少失败次数（默认 5）')
    ap.add_argument('--max-ok-rate', type=float, default=0.2,
                    help='成功率高过这个就不冻（默认 0.2）')
    ap.add_argument('--dry-run', action='store_true', help='只报告，不写冻结表')
    ap.add_argument('--thin', type=int, default=3,
                    help='清单里条目少于这个数的源站不冻（默认 3，防偶发）')
    ap.add_argument('--auto-hours', type=float, default=0,
                    help='冻结后 N 小时自动失效（0 = 长期，等人工绕路）')
    ap.add_argument('--keep', action='store_true',
                    help='同 --prune 相反：明确要求保留全部旧冻结项（默认就保留）')
    ap.add_argument('--prune', action='store_true',
                    help='清掉本次没判中的旧冻结项。⚠️ 默认**不清**：'
                         '已冻结的源站不再产生日志，永远判不中，'
                         '一旦按「没判中」清理就会「解冻→再访问→再被拦」死循环。'
                         '确要清只能清 floor 已过的。')
    ap.add_argument('--min-hours', type=float, default=48,
                    help='最短冻结时长（floor），默认 48 小时。未满一律不许解冻')
    ap.add_argument('--kinds', default='blocked,broken',
                    help='只冻这几类，逗号分隔（默认 blocked,broken）。'
                         'wall/dead 默认不冻：慢站在短窗口里 ok=0 是采样问题'
                         '（实测 OUP 判「订阅墙」的同一小时里成功下了一篇 194s），'
                         '订阅墙冻结也不解决任何问题——得走机构入口。'
                         '确要冻可传 --kinds blocked,broken,wall,dead')
    args = ap.parse_args(argv)
    allow = {k.strip() for k in args.kinds.split(',') if k.strip()}

    frz = load_freeze_mod()
    lg = scan_log(args.hours)
    mf = scan_manifest(frz)

    print('窗口 %.0fh ｜ 日志结果行 %d 站 ｜ 清单 %d 站\n'
          % (args.hours, len(lg), len(mf)))

    rows, hits = [], []
    hosts = set(lg) | set(mf)
    for h in sorted(hosts):
        if h in SKIP_HOSTS or not h or h.startswith('unknown'):
            continue
        c = lg.get(h, Counter())
        v = verdict(c, args.min_fail, args.max_ok_rate)
        n_item = mf.get(h, Counter()).get('n', 0)
        note = ''
        if v is None:
            rows.append((h, n_item, c, None, ''))
            continue
        if n_item and n_item < args.thin:
            rows.append((h, n_item, c, None, '条目过少(%d)' % n_item))
            continue
        kind, reason, detail = v
        rows.append((h, n_item, c, kind, detail))
        if kind not in allow:
            rows[-1] = (h, n_item, c, None, '判为%s，不在冻结范围(--kinds)' % kind)
            continue
        hits.append((h, n_item, c, kind, reason, detail))

    print('%-32s %6s %5s %5s %5s  %s' % ('host', '条目', '成功', '失败', '拦截', '判定'))
    print('-' * 96)
    for h, n_item, c, kind, detail in rows:
        print('%-32s %6s %5d %5d %5d  %s'
              % (h, n_item or '-', c['ok'], c['fail'], c['blocked'],
                 ('⛔ %s %s' % (frz.KIND_CN.get(kind, kind), detail)) if kind else detail or '·'))
        # 清单存量补充提示
        m = mf.get(h, Counter())
        if m.get('tried'):
            print('%-32s %6s %5s %5s %5s  ↳ 历史：试过 %d 篇'
                  % ('', '', '', '', '', m['tried']))

    if not hits:
        print('\n没有需要新冻结的源站。')
        return 0

    print('\n判定结果：')
    for h, n_item, c, kind, reason, detail in hits:
        print('  ⛔ %-30s %-9s %s' % (h, frz.KIND_CN.get(kind, kind), detail))

    if args.dry_run:
        print('\n[dry-run] 未写盘。去掉 --dry-run 生效。')
        return 0

    # 只有明确 --prune 才清理旧项，且**只清最短冻结期已过的**。
    # 理由：已冻结 = 不再派发 = 不再产生日志 = 永远判不中。
    # 若按「本次没判中」自动清，就会解冻 → 立刻再访问 → 再被拦 → 再冻，
    # 永远在 IP 风控的记忆期里打转。
    if args.prune:
        old = frz.load(force=True)
        keep = {x[0] for x in hits}
        for h, rec in old.items():
            if h in keep:
                continue
            left = frz.left_seconds(rec)
            if left > 0:
                print('  保留 %s（最短冻结期未满，剩 %s）' % (h, frz.fmt_left(rec)))
                continue
            print('  清掉旧冻结项 %s（已过最短冻结期且本次未判中）' % h)
            frz.thaw(h, force=True)

    now = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    for h, n_item, c, kind, reason, detail in hits:
        stats = {'n': n_item, 'ok': c['ok'], 'fail': c['fail'],
                 'blocked': c['blocked'], 'window_h': args.hours, 'at': now}
        frz.freeze(h, kind=kind, reason=reason,
                   note='判定依据：' + detail,
                   stats=stats, hours=(args.auto_hours or None),
                   min_hours=args.min_hours)
    print('\n已写入 %s（%d 个源站，最短冻结 %g 小时）'
          % (frz.FREEZE_FILE, len(hits), args.min_hours))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
