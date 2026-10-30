# -*- coding: utf-8 -*-
"""把已冻结源站的条目**物理移出**爬取清单，存进备份；需要时再放回。

为什么不只是「冻结不派发」：
  用户 2026-10-26 要求 —— 冻结要真把这些刊从列表里拿走。
  留在清单里虽然不派发，但调度器每轮仍会遍历它们、日志里仍会出现，
  日后一次误操作（改判定、手滑解冻）就会立刻恢复高频访问。移出是物理隔离。

为什么不能直接改文件：
  调度器父进程把清单**整份读进内存**，每次 `_settle` 都 `save_manifest(items)`
  全量写回。外部改盘 = 被它用旧内存覆盖回去，而且看起来「改了却没生效」。
  所以本工具的做法是：
     建操作锁 state/manifest_op.lock
       → 等 2s 让在跑的进程暴露
       → terminate 调度器并确认死透（守护看到锁不会拉起）
       → 改清单（原子写 + 带时间戳的备份）
       → 删锁（守护 10s 内自动拉起，读到的就是新清单）

安全约束：
  · 坏行（解析失败）原样留在清单里，绝不丢；
  · 备份文件按 id 去重，重复 --run 幂等；
  · restore 受 floor 约束：最短冻结期未满一律拒绝（--force 可破，危险）；
  · 每一步都先写 tmp + fsync 再 replace，断电不会拿到半个文件。

用法：
    python scripts/park_frozen.py --list                 # 看备份里有什么
    python scripts/park_frozen.py --run                  # 把冻结站移出清单
    python scripts/park_frozen.py --run --dry-run        # 只看会移多少
    python scripts/park_frozen.py --restore all          # 全部放回（受 floor 约束）
    python scripts/park_frozen.py --restore pubs.geoscienceworld.org
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
STATE = BASE / 'state'
SCRIPTS = BASE / 'scripts'
MANIFEST = STATE / 'history_manifest.jsonl'
PARKED = STATE / 'history_manifest_frozen.jsonl'
PARKED_META = STATE / 'history_manifest_frozen.meta.json'
OP_LOCK = STATE / 'manifest_op.lock'
BACKUP_DIR = STATE / 'backup'

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import source_freeze as frz       # noqa: E402


# ─────────────────────────── 基础 ───────────────────────────
def now_s():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def read_rows(path):
    """→ (good, bad_lines)。坏行原样保留，绝不丢。"""
    good, bad = [], []
    if not path.exists():
        return good, bad
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            s = line.rstrip('\n')
            if not s.strip():
                continue
            try:
                o = json.loads(s)
                if not isinstance(o, dict) or not o.get('id'):
                    raise ValueError('无 id')
                good.append(o)
            except Exception:
                bad.append(s)
    return good, bad


def atomic_write(path, rows, bad_lines=(), stamp=True):
    """原子写 jsonl。stamp=True 时先留一份带时间戳的备份。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if stamp and path.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        dst = BACKUP_DIR / ('%s.%s.jsonl' % (path.stem,
                                             datetime.now().strftime('%Y%m%d-%H%M%S')))
        if not dst.exists():
            with open(path, 'r', encoding='utf-8', errors='ignore') as src, \
                    open(dst, 'w', encoding='utf-8', newline='\n') as out:
                for ln in src:
                    out.write(ln)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        for x in rows:
            f.write(json.dumps(x, ensure_ascii=False) + '\n')
        for b in bad_lines:
            f.write(b + '\n')
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp, path)


# ─────────────────────────── 操作锁 ───────────────────────────
def take_lock():
    """建操作锁。已有未失效的锁 → 拒绝（可能另一个工具在改清单）。"""
    try:
        fd = os.open(str(OP_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            age = time.time() - OP_LOCK.stat().st_mtime
            who = OP_LOCK.read_text(encoding='utf-8', errors='ignore').strip()
        except Exception:
            age, who = 0, '?'
        if age < 600:
            raise RuntimeError('已有清单操作在进行（%s，%ds 前），不要并发，'
                               '确认为残留可删 %s' % (who or '?', int(age), OP_LOCK))
        print('[warn] 发现 %.0f 分钟前的残留锁，接管' % (age / 60))
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write('pid=%d at=%s' % (os.getpid(), now_s()))
    return True


def drop_lock():
    try:
        if OP_LOCK.exists():
            OP_LOCK.unlink()
    except Exception:
        pass


# ─────────────────────────── 调度器控制 ───────────────────────────
def queue_procs():
    try:
        import psutil
    except ImportError:
        return []
    out = []
    for p in psutil.process_iter(['pid', 'cmdline']):
        try:
            cl = ' '.join(p.info['cmdline'] or [])
        except Exception:
            continue
        if 'history_queue_loop.py' in cl:
            out.append(p)
    return out


def stop_queue():
    """terminate 调度器并确认死透。守护因操作锁存在不会立刻拉起。"""
    ps = queue_procs()
    if not ps:
        return 0
    for p in ps:
        try:
            p.terminate()
        except Exception:
            pass
    t0 = time.time()
    while time.time() - t0 < 15:
        if not queue_procs():
            return len(ps)
        time.sleep(0.5)
    for p in queue_procs():
        try:
            p.kill()
        except Exception:
            pass
    time.sleep(1)
    return len(ps)


# ─────────────────────────── 移出 ───────────────────────────
def do_park(dry_run=False):
    frozen = frz.table_for_dispatch()
    if not frozen:
        print('冻结表为空，没有要移出的源站。')
        return 0
    rows, bad = read_rows(MANIFEST)
    old_park, park_bad = read_rows(PARKED)
    have = {x['id'] for x in old_park}

    move, keep = {}, []
    for it in rows:
        h = frz.host_of(it.get('url', ''), it.get('doi', ''))
        if h in frozen:
            move.setdefault(h, []).append(it)
        else:
            keep.append(it)
    n_move = sum(len(v) for v in move.values())

    print('清单 %d 条（坏行 %d）｜ 冻结站 %d 个' % (len(rows), len(bad), len(frozen)))
    for h, v in sorted(move.items(), key=lambda x: -len(x[1])):
        dup = sum(1 for x in v if x['id'] in have)
        print('  移出 %-30s %4d 条%s' % (h, len(v), ('（其中 %d 条备份里已有）' % dup) if dup else ''))
    print('  清单保留 %d 条' % len(keep))
    if not n_move:
        print('没有匹配条目，未改动。')
        return 0
    if dry_run:
        print('\n[dry-run] 未改动任何文件。')
        return 0

    # 备份按 id 去重（幂等）：重复跑不会让备份膨胀
    add = [x for v in move.values() for x in v if x['id'] not in have]
    atomic_write(PARKED, old_park + add, park_bad)
    atomic_write(MANIFEST, keep, bad)

    meta = {}
    if PARKED_META.exists():
        try:
            meta = json.loads(PARKED_META.read_text(encoding='utf-8'))
        except Exception:
            meta = {}
    for h, v in move.items():
        m = meta.get(h) or {}
        m['entries'] = int(m.get('entries', 0)) + len([x for x in v if x['id'] not in have])
        m['last_park'] = now_s()
        m['since'] = m.get('since') or now_s()
        meta[h] = m
    PARKED_META.write_text(json.dumps(
        {'updated': now_s(), 'parked_total': len(old_park) + len(add), 'hosts': meta},
        ensure_ascii=False, indent=2), encoding='utf-8')

    print('\n✅ 已移出 %d 条 → %s' % (len(add), PARKED))
    print('   清单现 %d 条；备份共 %d 条' % (len(keep), len(old_park) + len(add)))
    return 0


# ─────────────────────────── 放回 ───────────────────────────
def targets_for(host):
    """restore 目标 host 列表。"""
    if host in ('all', '*'):
        parked, _ = read_rows(PARKED)
        if not parked:
            return []
        return sorted({t for t in (frz.host_of(x.get('url', ''), x.get('doi', ''))
                                   for x in parked) if t})
    return [host.strip().lower()]


def check_floor(targets, force=False):
    """最短冻结期预检 → 未满的 [(host, 剩余)]。force 时恒为空。

    必须**在停调度器之前**跑：否则每次被拒都要白停一次队列。
    """
    if force:
        return []
    hosts = frz.load(force=True)
    out = []
    for t in targets:
        rec = hosts.get(t)
        if rec and frz.left_seconds(rec) > 0:
            out.append((t, frz.fmt_left(rec)))
    return out


def do_restore(host='all', force=False, dry_run=False):
    hosts = frz.load(force=True)
    parked, bad = read_rows(PARKED)
    if not parked:
        print('备份为空（%s）%s' % (PARKED, '，坏行 %d' % len(bad) if bad else ''))
        return 0

    targets = targets_for(host)
    refuse = check_floor(targets, force)
    if refuse:
        print('拒绝放回（最短冻结期未满）：')
        for t, l in refuse:
            print('  %-30s 剩 %s' % (t, l))
        print('\n确要强行放回：加 --force（会立刻恢复对该站的访问，极易再次被拦）')
        return 3

    tset = set(targets)
    back = [x for x in parked
            if frz.host_of(x.get('url', ''), x.get('doi', '')) in tset]
    stay = [x for x in parked
            if frz.host_of(x.get('url', ''), x.get('doi', '')) not in tset]
    if not back:
        print('备份里没有这些源站的条目：%s' % ', '.join(targets))
        return 0

    cur, cur_bad = read_rows(MANIFEST)
    have = {x['id'] for x in cur}
    add = [x for x in back if x['id'] not in have]
    print('放回 %d 条（其中 %d 条清单里已有，跳过）→ %s'
          % (len(add), len(back) - len(add), ', '.join(targets)))
    print('  清单 %d → %d 条 ｜ 备份 %d → %d 条'
          % (len(cur), len(cur) + len(add), len(parked), len(stay)))
    if dry_run:
        print('\n[dry-run] 未改动任何文件。')
        return 0
    if add:
        atomic_write(MANIFEST, cur + add, cur_bad)
    atomic_write(PARKED, stay, bad)
    print('✅ 已放回。冻结表未动——如确认该站可正常访问，再 --thaw 清记录。')
    return 0


# ─────────────────────────── 查看 ───────────────────────────
def do_list():
    parked, bad = read_rows(PARKED)
    hosts = frz.load(force=True)
    print('备份文件 %s' % PARKED)
    if not parked:
        print('  空')
    else:
        from collections import Counter
        c = Counter(frz.host_of(x.get('url', ''), x.get('doi', '')) for x in parked)
        print('  共 %d 条（坏行 %d）\n' % (len(parked), len(bad)))
        print('  %-32s %6s   %s' % ('host', '条数', '冻结状态'))
        for h, v in c.most_common():
            rec = hosts.get(h)
            stat = ('⏳ 剩 %s' % frz.fmt_left(rec)) if rec else '（不在冻结表）'
            print('  %-32s %6d   %s' % (h, v, stat))
    meta = {}
    if PARKED_META.exists():
        try:
            meta = json.loads(PARKED_META.read_text(encoding='utf-8'))
            print('\n  最近操作：%s' % meta.get('updated', '?'))
        except Exception:
            pass
    print('\n清单 %d 条' % len(read_rows(MANIFEST)[0]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description='冻结源站条目移出 / 放回')
    ap.add_argument('--run', action='store_true', help='把冻结站的条目移出清单')
    ap.add_argument('--restore', metavar='HOST', nargs='?', const='all',
                    help="放回（默认 all）")
    ap.add_argument('--list', action='store_true', help='看备份内容')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force', action='store_true',
                    help='无视最短冻结期强行放回（危险）')
    ap.add_argument('--no-stop', action='store_true',
                    help='不自动停调度器（默认会停；停了才改得动清单）')
    args = ap.parse_args(argv)

    if args.list or (not args.run and not args.restore):
        return do_list()

    # 放回：先做 floor 预检，不合格就地退出，**不要**为此白停一次调度器
    if args.restore and not args.force:
        refuse = check_floor(targets_for(args.restore), force=False)
        if refuse:
            print('拒绝放回（最短冻结期未满）：')
            for t, l in refuse:
                print('  %-30s 剩 %s' % (t, l))
            print('\n确要强行放回：加 --force（会立刻恢复对该站的访问，极易再次被拦）')
            return 3

    want_write = not args.dry_run
    if want_write:
        try:
            take_lock()
        except RuntimeError as e:
            print('拒绝：%s' % e)
            return 4
        stopped = 0
        if not args.no_stop:
            time.sleep(2)          # 让正在跑的进程暴露出来，再一起停
            stopped = stop_queue()
            print('已停调度器 %d 个进程（守护因操作锁暂不拉起）' % stopped)
        else:
            left = queue_procs()
            if left:
                drop_lock()
                print('拒绝：调度器还在跑（pid=%s），它会把清单覆盖回去。'
                      '去掉 --no-stop 让它自动停，或先手动停。'
                      % ', '.join(str(p.pid) for p in left))
                return 5
    try:
        if args.run:
            return do_park(dry_run=args.dry_run)
        return do_restore(args.restore, force=args.force, dry_run=args.dry_run)
    finally:
        if want_write:
            drop_lock()
            print('（操作锁已释放，守护进程 10s 内会拉起调度器）')


if __name__ == '__main__':
    raise SystemExit(main())
