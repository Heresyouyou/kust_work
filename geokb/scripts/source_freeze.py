# -*- coding: utf-8 -*-
"""源站冻结表 —— 把「反复被拦 / 反复失败」的源站从派发池里摘出去。

为什么要单独一张表（而不是直接改清单）：
  · 清单条目一条都不能丢。冻结只影响**派发**，条目原样留在 history_manifest.jsonl，
    解冻即恢复，不需要重建清单。
  · 冻结是**跨进程共享**的状态（调度器常驻 + 手动 CLI + 后续绕路脚本都要看），
    所以落在 state/frozen_sources.json，不能只放内存。

三种冻结原因，对应后面的三种处理方式：
  blocked  源站风控拦截（Cloudflare 挑战 / 403 / 429）→ 绕路：浏览器指纹、代理、机构入口
  broken   本地 handler 故障（err_index / guard_fail / err_parse）→ 修 handler 再解冻
  dead     源头就没有可用全文（no_fulltext / not_pdf 占绝对多数）→ 换源，别硬试

判定口径一律走 antibot_session.pace_host()，也就是**限速桶 host**：
GSW 三刊（GSA / AmMin / EconGeol）虽然域名不同，风控是同一套，必须合并成一个桶，
否则会出现「冻结了 geoscienceworld 却还在打 minsocam」的假冻结。
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(BASE, 'state')
FREEZE_FILE = os.path.join(STATE, 'frozen_sources.json')

KINDS = ('blocked', 'wall', 'broken', 'dead', 'manual')
KIND_CN = {
    'blocked': '风控拦截',
    'wall': '订阅墙',
    'broken': '本地故障',
    'dead': '源头无全文',
    'manual': '人工冻结',
}
KIND_HINT = {
    'blocked': '绕爬虫：浏览器指纹 / 代理 / 机构 IP，调间隔无效',
    'wall': '绕订阅：需要机构登录态或走图书馆入口，不是爬虫问题',
    'broken': '修 handler 后直接解冻，冻它是为了别再空刷',
    'dead': '源头没有可下全文，考虑换源',
    'manual': '人工指定',
}

_cache = {'mtime': 0.0, 'data': {}}

# 最短冻结时长（小时）。为什么是 48：风控对 IP 有记忆期，短暂停会被延续。
# 2026-10-26 实测 gsw 按 60s 间隔跑了几轮后，即使停半小时再上，仍在第一篇就被挑战；
# 而此前单发（间隔数分钟、当天首次）是 200。说明「停一会儿」不消账，
# 必须让该站彻底静默足够长，48h 是用户明确要求的底线。
# ⚠️ 这是**下限**，不是到期自动解冻：到点后仍需人工确认绕路方案再 --thaw。
MIN_FREEZE_HOURS = 48


# ─────────────────────────── 读 ───────────────────────────
def _ts(v, default=0.0):
    """把 until / floor 这类字段安全转成时间戳。坏值按「没有」处理。"""
    try:
        return float(v) if v else default
    except (TypeError, ValueError):
        return default


def left_seconds(rec):
    """最短冻结期还剩多久（秒）。已过或没有 floor 返回 0。"""
    return max(0.0, _ts(rec.get('floor')) - time.time())


def load(force=False):
    """返回 {host: {...}}。按 mtime 失效，调度器可以每轮无脑调。"""
    global _cache
    try:
        mt = os.path.getmtime(FREEZE_FILE)
    except OSError:
        _cache = {'mtime': 0.0, 'data': {}}
        return {}
    if not force and mt == _cache['mtime']:
        return _cache['data']
    data = {}
    try:
        with open(FREEZE_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        data = raw.get('hosts') or {}
        if not isinstance(data, dict):
            data = {}
        # 剔掉**已经彻底到期**的记录（until 过了 且 floor 也过了）。
        # ⚠️ 有 floor（最短冻结期）挡着时不能剔：until 只是「自动重试」的时间点，
        # 冻满 48h 之前一律按冻结处理，否则等于自己给自己开后门。
        now = time.time()
        expired = []
        for h, rec in list(data.items()):
            if not isinstance(rec, dict):
                expired.append(h)
                continue
            if _ts(rec.get('floor')) > now:      # 最短冻结期未到 → 一定保留
                continue
            until = _ts(rec.get('until'))
            if until and now > until:
                expired.append(h)
        for h in expired:
            data.pop(h, None)
    except (OSError, ValueError) as e:
        # 读坏了不许把调度器带崩：当作「没冻结」，但要在日志里留痕
        print('[source_freeze] 冻结表读取失败，按未冻结处理：%s' % e)
        data = {}
    _cache = {'mtime': mt, 'data': data}
    return data


def is_frozen(host, table=None):
    """(bool, reason)。host 必须是 pace_host() 归一后的值。

    判定顺序很关键：**先看最短冻结期，再看 until**。
    只要有 floor 且没到点，就是冻结，任何 until 都不能让它提前解冻。
    """
    t = load() if table is None else table
    rec = t.get(host)
    if not rec:
        return False, ''
    why = rec.get('reason') or rec.get('kind') or 'frozen'
    now = time.time()
    if _ts(rec.get('floor')) > now:
        return True, why + '｜最短冻结期未满，剩 %s' % fmt_left(rec)
    until = _ts(rec.get('until'))
    if until and now > until:
        return False, ''
    return True, why


def fmt_left(rec):
    """把剩余时间写成「31h12m」这种。"""
    s = left_seconds(rec)
    if s <= 0:
        return '0'
    h, m = int(s // 3600), int(s % 3600 // 60)
    return '%dh%02dm' % (h, m) if h else '%dm' % m


def host_of(url='', doi=''):
    """条目 → 限速桶 host（与派发、限速用同一套映射）。"""
    try:
        # 本文件可能被 importlib 动态加载（调度器就这么干），
        # 这时 scripts 目录不在 sys.path 里，得自己补上才能 import 兄弟模块。
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        from antibot_session import pace_host
        return pace_host(url, doi)
    except Exception:
        try:
            from urllib.parse import urlparse
            return (urlparse(url or '').netloc or '').lower()
        except Exception:
            return ''


# ─────────────────────────── 写 ───────────────────────────
def _save(hosts, note=''):
    os.makedirs(STATE, exist_ok=True)
    payload = {
        'version': 1,
        'updated': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'note': note or '源站冻结表：这里列的 host 不参与派发，条目仍保留在清单里',
        'hosts': hosts,
    }
    tmp = FREEZE_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, FREEZE_FILE)
    _cache['mtime'] = 0.0
    return payload


def freeze(host, kind='manual', reason='', note='', stats=None, hours=None,
           min_hours=None):
    """冻结一个源站。重复冻结会**覆盖**旧记录（用来更新原因/统计）。

    min_hours 写 floor（最短冻结期），默认 MIN_FREEZE_HOURS。
    floor 一旦写入，除非 --force，thaw 会被拒绝——这是防止「停了半天又上去
    再被拦」的唯一硬约束。重复冻结时 floor **只延长不缩短**（取较晚的那个），
    免得一次 auto 重跑把已经冻了 40h 的站重置成刚冻。
    """
    host = (host or '').strip().lower()
    if not host:
        raise ValueError('host 不能为空')
    hosts = dict(load(force=True))
    old = hosts.get(host) or {}
    mh = MIN_FREEZE_HOURS if min_hours is None else float(min_hours)
    now = time.time()
    rec = {
        'kind': kind if kind in KINDS else 'manual',
        'reason': reason or old.get('reason') or '',
        'note': note or old.get('note') or '',
        'since': old.get('since') or datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'updated': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'stats': stats if stats is not None else (old.get('stats') or {}),
        'floor': max(_ts(old.get('floor')), now + mh * 3600) if mh > 0
                 else _ts(old.get('floor')),
    }
    if hours:
        rec['until'] = now + float(hours) * 3600
    elif old.get('until'):
        rec['until'] = old['until']
    hosts[host] = rec
    _save(hosts)
    return hosts[host]


def thaw(host, force=False):
    """解冻；host='all' 清空整表。返回被移除的 host 列表。

    ⚠️ 最短冻结期（floor）未满时**拒绝**，除非 force=True。
    这是用户 2026-10-26 明确要求的：冻不满 48h 就回去，仍然会被拦。
    """
    hosts = dict(load(force=True))
    now = time.time()
    if (host or '').strip().lower() in ('all', '*'):
        if not force:
            blocked = {h: fmt_left(r) for h, r in hosts.items()
                       if _ts(r.get('floor')) > now}
            if blocked:
                raise RuntimeError(
                    '以下源站最短冻结期未满，拒绝解冻：%s（确要解冻加 --force）'
                    % ', '.join('%s 剩 %s' % (h, v) for h, v in blocked.items()))
        gone = sorted(hosts.keys())
        hosts = {}
    else:
        h = (host or '').strip().lower()
        rec = hosts.get(h)
        if rec and not force and _ts(rec.get('floor')) > now:
            raise RuntimeError(
                '%s 最短冻结期未满（剩 %s），拒绝解冻；确要解冻加 --force'
                % (h, fmt_left(rec)))
        gone = [h] if h in hosts else []
        hosts.pop(h, None)
    _save(hosts)
    return gone


def table_for_dispatch():
    """派发用：{host: reason} 的扁平表，避免每轮重复解析。"""
    out = {}
    for h, rec in load().items():
        ok, why = is_frozen(h)
        if ok:
            out[h] = why
    return out


# ─────────────────────────── 展示 ───────────────────────────
def describe(host, rec):
    kind = rec.get('kind') or 'manual'
    st = rec.get('stats') or {}
    bits = []
    if st:
        if st.get('n') is not None:
            bits.append('条目%s' % st['n'])
        if st.get('fail') is not None:
            bits.append('失败%s' % st['fail'])
        if st.get('ok') is not None:
            bits.append('成功%s' % st['ok'])
        if st.get('blocked') is not None:
            bits.append('拦截%s' % st['blocked'])
    left = left_seconds(rec)
    tag = ('⏳ 剩 %s' % fmt_left(rec)) if left > 0 else '✅ 可解冻'
    return '%-30s %-10s %-12s %-22s %s' % (
        host, KIND_CN.get(kind, kind), tag, rec.get('reason') or '-',
        ('(' + ' / '.join(bits) + ')') if bits else '')


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='源站冻结表')
    ap.add_argument('--list', action='store_true', help='列出冻结的源站')
    ap.add_argument('--freeze', metavar='HOST', help='冻结某源站')
    ap.add_argument('--thaw', metavar='HOST', help="解冻（all = 全部解冻）")
    ap.add_argument('--kind', default='manual', choices=list(KINDS) + ['manual'])
    ap.add_argument('--reason', default='')
    ap.add_argument('--note', default='')
    ap.add_argument('--hours', type=float, default=0,
                    help='until：N 小时后允许重新尝试（不影响 floor）')
    ap.add_argument('--min-hours', type=float, default=MIN_FREEZE_HOURS,
                    help='floor：最短冻结时长，默认 %g 小时，未满不许解冻'
                         % MIN_FREEZE_HOURS)
    ap.add_argument('--force', action='store_true',
                    help='无视最短冻结期强制解冻（危险：极易再次被拦）')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--hint', action='store_true',
                    help='按冻结原因给出后续绕路方向')
    args = ap.parse_args(argv)

    if args.thaw:
        try:
            gone = thaw(args.thaw, force=args.force)
        except RuntimeError as e:
            print('拒绝：%s' % e)
            return 3
        print('已解冻：%s' % (', '.join(gone) if gone else '（本来就没冻结）'))
        return 0

    if args.freeze:
        rec = freeze(args.freeze, args.kind, args.reason, args.note,
                     hours=args.hours, min_hours=args.min_hours)
        print('已冻结 %s（%s），最短冻结 %s 至 %s'
              % (args.freeze, KIND_CN.get(rec['kind']), fmt_left(rec),
                 datetime.fromtimestamp(rec['floor']).strftime('%Y-%m-%d %H:%M')
                 if rec.get('floor') else '-'))
        return 0

    hosts = load(force=True)
    if args.json:
        print(json.dumps(hosts, ensure_ascii=False, indent=2))
        return 0
    if not hosts:
        print('冻结表为空（%s）' % FREEZE_FILE)
        return 0
    print('冻结 %d 个源站（%s）\n' % (len(hosts), FREEZE_FILE))
    for h, rec in sorted(hosts.items()):
        print('  ' + describe(h, rec))
        if rec.get('note'):
            print('      %s' % rec['note'])
        if args.hint:
            print('      → %s' % KIND_HINT.get(rec.get('kind'), ''))
    if not args.hint:
        print('\n（加 --hint 看每类该怎么绕；解冻：--thaw <host> 或 --thaw all）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
