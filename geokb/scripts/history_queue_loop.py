# -*- coding: utf-8 -*-
"""历史清单常驻调度器（多槽位 + 同源站互斥）

为什么是「槽位池」而不是「并发 N 篇」（2026-10-26 用户口径）
----------------------------------------------------------
> 设成 6 个槽位，哪篇下载完就把位置空出来留给下一篇，
> 除非同时卡住全部，至少保证一直有论文在下载。

逐篇串行的毛病是「一篇卡住 = 整条队列停摆」，但把并发直接开到 6 同样不行：
2026-10-20 实测，4 源并行时 gsw 只有 3/117 成功（其余全 403），
串行 `--workers 1` 是 10/10。**被风控的是「同一个出版社的并发」**，不是总并发。

所以做的是**槽位池 + 同源站互斥**：
  · 最多 `--slots` 篇同时在跑（默认 6）；
  · **同一出版社永远只有 1 篇在跑** —— gsw 的下一篇必须等上一篇收工，
    再过完它自己的静默期（60s × --pace-scale）才发；
  · 一篇收工（成功 / 超时被杀 / 失败）立刻把槽位还给池子，补下一篇；
  · 单篇仍在**子进程**里跑，`--item-timeout` 到点杀进程树，父进程不碰网络。

有效并发 = min(槽位数, 池子里不同出版社的数量)。当前池子正好 6 个源站
（gsw / oup / springer / seg / cnki / 中文刊官网），6 槽能跑满；
开到 12 只会让同一出版社出现 2 篇并发 —— 那正是 403 的来源。

节奏控制（三层，缺一不可）
--------------------------
1. **源站静默期** `antibot_session.PACE_GAP × --pace-scale`：gsw 90s、oup 135s、
   springer 18s、中文站 4.5s。派发前用 `pace_wait()` 只读判断，不占槽位干等。
1. **每日额度** `DAILY_MAX`：gsw 40 篇/天、oup 25、SD 60，跨进程共享；
   额度用尽当天不再试（硬试只会抬高 IP 的风控等级）。
1. 全局 `--gap`（默认 10s）：总闸，只用来防止 6 个槽位在同一秒一起发车；
   稳态下每条要跑 30–200s，这个下限基本不生效。

为什么不再用「篇间隔 3 分钟」：那是单源站串行时代的经验值，比 gsw 自己的
60s 更保守、比 springer 的 12s 又没必要。现在各源站按自己的静默期走，
总产能从 ~12 篇/小时提到 ~30–45 篇/小时（gsw/oup 仍受日额度封顶）。

单篇完成语义
------------
下载成功 → 台账（state/papers_index.jsonl）→ **物理移出**待爬清单 →
记入 state/pending_papers_ingest.jsonl（待入库）。**入库不在这里发生**
（ingest_policy 默认关，由 `python scripts/ingest_policy.py --run --limit N` 统一安排）。

单实例：state/history_queue_loop.pid，重复启动直接退出
日志：logs/history_queue_loop.log
停止：杀掉 pid 文件里的进程（守护进程 min_gap 后重新拉起）

启动（后台、无窗口；由 geokb_daemon 的 EXTRA_PROCS 守护）：
    D:/ZSK/geokb/venv/Scripts/python.exe D:/ZSK/geokb/scripts/history_queue_loop.py
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from datetime import datetime
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
SCRIPTS = BASE / 'scripts'
STATE = BASE / 'state'
PID_FILE = STATE / 'history_queue_loop.pid'
START_LOCK = STATE / 'history_queue_loop.startlock'   # O_EXCL 原子启动锁
HEARTBEAT = STATE / 'history_queue_loop.heartbeat'
OP_LOCK = STATE / 'manifest_op.lock'      # park_frozen.py 改清单时持有
OP_LOCK_TTL = 600                          # 超过 10 分钟视为残留锁
LOG = BASE / 'logs' / 'history_queue_loop.log'
MANIFEST = STATE / 'history_manifest.jsonl'

GAP = 10                   # 全局派发下限（秒）：6 个槽位不许在同一秒一起发车
SLOTS = 6                  # 并发槽位数（同源站互斥，超过源站数没有意义）
PACE_SCALE = 1.5           # 源站静默期放大倍数：在实测安全值上留 50% 余量
IDLE = 5 * 60              # 窗口外轮询间隔
EMPTY_SLEEP = 6 * 3600     # 清单空时的探测间隔
STARVE_SLEEP = 30 * 60     # 有行但无可执行篇目（全 SD 暂停 / 全 parked）
PACE_TICK = 20             # 旧的全静默期最小重试间隔；现按 wait2 精确休眠，仅作兜底保留
SLICE = 30                 # 长休眠分段粒度：每 30s 校一次窗口与单实例锁

# 源站熔断：同一出版社连续失败 N 次就先凉一会儿。
# 为什么需要：矿床地质(kcdz) 的 guard_fail、大地构造(dgyk) 的 err_index 会
# **篇篇都失败**。没有熔断的话，6 个槽位里始终有一个被坏源站占着，还会把
# 整池条目推进 30 分钟冷却，看起来像「并发越多、成功越少」。
HOST_FAIL_LIMIT = 3
HOST_PAUSE = 30 * 60
_HOST_FAILS = {}           # host -> 连续失败次数（成功即清零）
_HOST_PAUSE = {}           # host -> 解禁时刻
_HOST_LAST_JOURNAL = {}    # host -> 上一次派发的刊名（用于同源站多刊轮转）


def log(msg):
    line = '[%s] %s' % (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), msg)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


def _load_queue():
    spec = importlib.util.spec_from_file_location('hq', str(SCRIPTS / 'history_queue.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _load_antibot():
    spec = importlib.util.spec_from_file_location(
        'abs_', str(SCRIPTS / 'antibot_session.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _load_freeze():
    """源站冻结表（state/frozen_sources.json）。读失败返回 None，按「没冻结」跑。"""
    try:
        spec = importlib.util.spec_from_file_location(
            'sfz', str(SCRIPTS / 'source_freeze.py'))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    except Exception:
        return None


def acquire_pid():
    """单实例锁：pid 文件存在且进程活着 → 拒绝启动。

    另外拒绝在「清单操作锁」存在时启动：park_frozen.py 正在把冻结站的条目
    移出/放回清单，这时本进程一旦读盘就会拿着旧的内存副本，在 _settle 时
    整份写回，把外部改动覆盖掉。等锁释放（最多 10 分钟）再由守护拉起。
    """
    if OP_LOCK.exists():
        try:
            age = time.time() - OP_LOCK.stat().st_mtime
        except Exception:
            age = 0.0
        holder = -1
        try:
            txt = OP_LOCK.read_text(encoding='utf-8', errors='ignore')
            mm = re.search(r'pid=(\d+)', txt)
            if mm:
                holder = int(mm.group(1))
        except Exception:
            pass
        # 持锁进程若已退出，锁就是崩溃残留（park/restore 中途被打断），
        # 必须立即接管 —— 否则要白等 OP_LOCK_TTL(600s)，期间队列完全停摆。
        stale = holder > 0 and not _alive(holder)
        if age < OP_LOCK_TTL and not stale:
            log('清单正在被外部工具改写（%s，%.0fs 前），本次不启动'
                % (OP_LOCK.read_text(encoding='utf-8', errors='ignore').strip(),
                   age))
            return False
        if stale:
            log('清单操作锁持有者 pid=%s 已退出，判定残留并接管' % holder)
        else:
            log('清单操作锁已残留 %.0f 分钟，视为失效并接管' % (age / 60))
        try:
            OP_LOCK.unlink()
        except Exception:
            pass
    if PID_FILE.exists():
        try:
            old = int(PID_FILE.read_text().strip())
            if _alive(old):
                log('已有实例在跑（pid=%d），退出' % old)
                return False
        except Exception:
            pass
    # 原子抢占启动锁。
    # 为什么 pid 文件那套不够：2026-10-27 实测守护在 18:23:35 同一秒拉起两个
    # 实例，两者都读到空/已死的 pid 文件、都通过检查 → 双实例各持一份清单内存
    # 副本，_settle 时互相覆盖（进度丢失/重复下载，且表面看不出问题）。
    # `O_CREAT|O_EXCL` 由内核保证同一时刻只有一个进程创建成功。
    try:
        fd = os.open(str(START_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        time.sleep(0.3)          # 让抢到锁的那一方把 pid 写进去
        try:
            other = int(START_LOCK.read_text().strip() or 0)
        except Exception:
            other = 0
        if not other or _alive(other):
            log('启动竞争：已有实例（pid=%s）正在启动，退出' % (other or '?'))
            return False
        # 持锁进程已死 = 残留，接管
        log('启动锁残留（pid=%s 已退出），接管' % other)
        try:
            START_LOCK.unlink()
        except OSError:
            pass
        try:
            fd = os.open(str(START_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
        except FileExistsError:
            log('启动竞争失败，退出')
            return False
    PID_FILE.write_text(str(os.getpid()))
    import atexit

    def _drop_start_lock():
        try:
            START_LOCK.unlink()
        except OSError:
            pass
    atexit.register(_drop_start_lock)
    return True


def _alive(pid):
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        return False


def release_pid():
    try:
        if PID_FILE.exists() and int(PID_FILE.read_text().strip()) == os.getpid():
            PID_FILE.unlink()
    except Exception:
        pass


def pending_count():
    """待爬清单总条数（含冷却 / parked）。只用于「清单是不是空的」。"""
    if not MANIFEST.exists():
        return 0
    n = 0
    with open(MANIFEST, encoding='utf-8') as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def counts(q, items=None, _sd=None):
    """(总数, 可执行, 冷却中, 停放)。

    为什么要分开：parked 与冷却中的条目**留在清单里**，只看总条数会误判
    「还有 3966 篇」却一篇都跑不动（2026-10-26 实测：225 篇被风控停放，
    总条数一直不变，日志看不出可执行量在缩水）。
    """
    try:
        if items is None:
            items = q.load_manifest()
        pend, cooling, parked = q.select_ready(items)
        # SD 暂停期：这些条目留在清单里但**不可执行**，算进「可执行」会掩盖真实产能
        try:
            if _sd is None:
                _sd = bool(q.sd_paused())
            if _sd:
                pend = [x for x in pend
                        if not (x['sd'] if 'sd' in x else q.is_sd(
                            x.get('doi', ''), x.get('url', ''), x.get('journal', '')))]
        except Exception:
            pass
        return len(items), len(pend), len(cooling), len(parked)
    except Exception:
        n = len(items) if items is not None else pending_count()
        return n, -1, -1, -1


def beat(note=''):
    """写心跳（外部看门狗只认 mtime，不比时间文本）。"""
    try:
        HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT.write_text('%s %s' % (datetime.now().isoformat(timespec='seconds'), note),
                             encoding='utf-8')
    except Exception:
        pass


def ensure_pid():
    """自愈：pid 文件被外部删/写坏时立刻写回自己。

    守护进程（EXTRA_PROCS）只凭 pid 文件判活，pid 文件一丢就会当成"没在跑"
    而反复拉起新实例（2026-10-18 实测：重启竞态导致 pid 文件短暂消失，
    守护每 2 分钟拉一个新实例）。每轮开头校一次即可闭合这个洞。
    """
    try:
        cur = int(PID_FILE.read_text().strip()) if PID_FILE.exists() else 0
    except Exception:
        cur = 0
    if cur != os.getpid():
        try:
            PID_FILE.write_text(str(os.getpid()))
        except Exception:
            pass


def nap(seconds, q=None, stop_when_closed=False):
    """分段休眠：每 SLICE 秒写回一次 pid 文件。

    为什么不直接 time.sleep(seconds)：
      · pid 文件被外部删掉时要及时写回（守护凭 pid 判活，丢了会重复拉起）；
      · stop_when_closed=True 时（篇间节流）每段后校一次窗口，07:00 一到
        立刻放弃剩余等待，让位给当日更新。
    ⚠️ 窗口外**不要**传 stop_when_closed：那种情况本意就是等，一进来判
    「窗口关闭」会立即返回，主循环退化成忙循环狂打日志（2026-10-20 实测）。
    """
    end = time.time() + max(1, seconds)
    while True:
        ensure_pid()
        beat('nap')
        left = end - time.time()
        if left <= 0:
            return True
        time.sleep(min(SLICE, left))
        if stop_when_closed and q is not None:
            try:
                ok, _why = q.window_ok()
            except Exception:
                ok = True        # 判定本身出错时别把调度器弄死
            if not ok:
                return False


def _host_of(ab, item):
    """条目的限速桶（真实出版社 host；doi.org 会被换算掉）。"""
    try:
        return ab.pace_host(item.get('url', ''), item.get('doi', ''))
    except Exception:
        return ''


def _pick(q, ab, items, busy, last_done, scale, sd_paused, frozen=None):
    """挑下一篇可发的：未被冻结 + 同源站互斥 + 静默期已过 + 未被熔断 + 不在冷却。

    返回 (item, host, why, wait)。why 区分「没活了(starve)」「全被冻结(frozen)」
    与「都在等(pace)」，wait 是最近的解禁时刻还差多少秒，供主循环决定睡多久
    （不必固定 20s 空转）。

    frozen 是 {host: reason}，来自 state/frozen_sources.json。冻结点到即丢，
    **不计入 gate**——否则一个被冻的源站会把自己的静默期算进 wait，
    把整个池子拖成「一直等」。
    """
    pend, _c, _p = q.select_ready(items)
    if not pend:
        return None, '', 'starve', 0
    now = time.time()
    gate = {}                       # host -> 还要等几秒（同一轮内复用，避免重复读文件）
    ready = []                      # [(host, item)] 本轮可发车的候选
    n_frozen = 0
    for it in pend:
        try:
            if sd_paused and (it['sd'] if 'sd' in it else q.is_sd(
                    it.get('doi', ''), it.get('url', ''), it.get('journal', ''))):
                continue
        except Exception:
            pass
        h = _host_of(ab, it)
        if frozen and h in frozen:
            n_frozen += 1
            continue
        if h in busy:
            continue
        if h not in gate:
            try:
                gap = float(ab.gap_for(h)) * float(scale)
            except Exception:
                gap = 60.0
            left = max(0.0, float(last_done.get(h, 0)) + gap - now)
            try:
                left = max(left, float(ab.pace_wait(
                    it.get('url', ''), it.get('doi', ''), scale)))
            except Exception:
                pass
            # 熔断：连续失败的源站先凉一会儿，别让它一直占着槽位把整池拖进冷却
            left = max(left, float(_HOST_PAUSE.get(h, 0)) - now)
            gate[h] = left
        if gate[h] > 0.5:
            continue
        ready.append((h, it))
    if ready:
        # 同源站多刊轮转：同源站互斥，只有一条在跑，所以清单顺序把某个刊排在
        # 最前时，它会长期独占该源站唯一的槽位。实测 JGSA（531 条、多数是
        # 单篇订阅墙）就是这样把 Springer 上 Mineralium Deposita 800 条、
        # CMP 581 条、JES 376 条全部堵在后面 —— 每 3 次失败还熔断 30 分钟。
        # 这里对每个源站优先选「刊物与上次不同」的条目，让好刊与坏刊交替推进。
        order = []
        for h, _x in ready:
            if h not in order:
                order.append(h)
        for h in order:
            pool = [x for hh, x in ready if hh == h]
            # 按刊分组（保持出现顺序），从「上次派发的刊」的下一个开始取，
            # 这样 N 个刊是严格轮转，而不是「JGSA ↔ 排第二的刊」两人转。
            seq, groups = [], {}
            for x in pool:
                j = (x.get('journal') or '')
                if j not in groups:
                    groups[j] = []
                    seq.append(j)
                groups[j].append(x)
            if len(seq) <= 1:
                return pool[0], h, '', 0.0
            prev = _HOST_LAST_JOURNAL.get(h, '')
            nxt = seq[(seq.index(prev) + 1) % len(seq)] if prev in groups else seq[0]
            return groups[nxt][0], h, '', 0.0
    if not gate:
        return None, '', ('frozen' if n_frozen else 'starve'), 0
    return None, '', 'pace', max(0.0, min(gate.values()))


def _settle(q, items, item, host, fut, last_done, spent):
    """一篇收工：成功移出清单，失败按软/硬分级退避。只在本线程调用。"""
    last_done[host] = time.time()
    try:
        r = fut.result()
        if isinstance(r, tuple):
            st, note, err = (tuple(r) + ('', ''))[:3]
        else:
            st, note, err = r, '', ''
    except Exception:
        st = 'fail'
        note = traceback.format_exc(limit=2).replace('\n', ' | ')[-200:]
        err = 'err_exception'
    tag = '[%s]' % (host or '?')

    if st == 'done':
        _HOST_FAILS[host] = 0
        q.append_done({
            'id': item['id'], 'journal': item['journal'], 'title': item['title'],
            'url': item['url'], 'doi': item.get('doi', ''), 'date': item['date'],
            'score': item['score'],
            'finished_at': datetime.now().isoformat(timespec='seconds'),
            # 移出待爬清单后的去向：待入库（默认）/ 已在本轮入库
            'ingest': ('inline' if q.ingest_policy.enabled('history') else 'pending'),
            'note': note,
        })
        items[:] = [x for x in items if x['id'] != item['id']]
        q.save_manifest(items)                 # 立即物理移出，不等整批
        log('★ %s 完成 %.0fs ｜ 剩余 %d ｜ %s' % (tag, spent, len(items), note[:90]))
    elif st == 'skip_sd':
        # 暂停期跳过：不记失败、不消耗重试次数，留在清单里
        log('⏸ %s %s（不计失败）' % (tag, note[:90]))
    else:
        # 唯一的失败记账实现（与单轮模式共用，见 history_queue.apply_failure）
        v = q.apply_failure(item, err, note)
        kind = v['kind']
        # ⚠️ 熔断只统计「可重试」失败。paywalled / 404 / no_fulltext 是确定性结论，
        # 不代表源站不健康；把它们计入，等于让一个付费墙刊每 3 次就熔断整个源站
        # 30 分钟（实测 JGSA 531 条把 Springer 2,400 条正常条目一起拖死）。
        if kind == 'retryable':
            _HOST_FAILS[host] = int(_HOST_FAILS.get(host, 0)) + 1
            trip = (_HOST_FAILS[host] >= HOST_FAIL_LIMIT
                    and _HOST_PAUSE.get(host, 0) <= time.time())
            if trip:
                _HOST_PAUSE[host] = time.time() + HOST_PAUSE
                _HOST_FAILS[host] = 0
        else:
            trip = False
        # ⚠️ 先落盘再打日志：日志只是给人看的，计数才是状态。
        # 之前 log 在前，一旦格式化抛异常（例如条目缺 status 键），
        # save_manifest 就被跳过 —— 失败计数白记，同一个坑会一直踩。
        q.save_manifest(items, backup=False)
        if trip:
            log('⛔ %s 连续失败 %d 次，熔断 %d 分钟（期间不占槽位）'
                % (tag, HOST_FAIL_LIMIT, HOST_PAUSE // 60))
        if kind == 'permanent':
            log('✗ %s 硬失败 %s（%.0fs，第 %d/%d 次，%s）'
                % (tag, err or note[:60], spent, v['attempts'], q.MAX_ATTEMPTS,
                   '已停放（不再自动复活）' if v['parked'] else '下轮重试'))
        elif v['captcha']:
            log('%s %s 人机挑战 %d/%d 次（%.0fs，%s）'
                % ('⛔' if v['newly_parked'] else '⟳', tag, v['captcha'],
                   q.CAPTCHA_MAX, spent,
                   '转人工补缺，不再自动重试' if v['newly_parked']
                   else '冷却 %d 分钟后再复核一次' % (v['cd'] // 60)))
        else:
            log('⟳ %s 软失败 %s（%.0fs，第 %d/%d 次，冷却 %d 分钟%s）'
                % (tag, err or note[:60], spent, v['soft'],
                   q.MAX_SOFT_FAILS, v['cd'] // 60,
                   '；累计过多已停放，%d 小时后可复活' % q.REVIVE_HOURS
                   if v['parked'] else ''))


def main():
    ap = argparse.ArgumentParser(description='历史清单常驻调度器（多槽位 + 同源站互斥）')
    ap.add_argument('--slots', type=int, default=SLOTS,
                    help='并发槽位数（默认 %d）。同一源站永远只有一篇在跑，'
                         '所以超过「池子里有多少个源站」没有意义' % SLOTS)
    ap.add_argument('--item-timeout', type=int, default=300,
                    help='单篇硬超时（秒，默认 300）。到点杀掉子进程、记一次软失败')
    ap.add_argument('--pace-scale', type=float, default=PACE_SCALE,
                    help='源站静默期放大倍数（默认 %.1f）：gsw 60s→90s、oup 90s→135s。'
                         '一旦出现 403 就调大，不必改代码' % PACE_SCALE)
    ap.add_argument('--gap', type=int, default=GAP,
                    help='全局最小派发间隔（秒，默认 0 = 只受源站静默期约束）')
    ap.add_argument('--once', action='store_true', help='所有槽位收工一次就退出')
    args = ap.parse_args()

    slots = max(1, int(args.slots))
    if not acquire_pid():
        return 0
    # 让 history_queue 知道单实例锁由本进程持有（否则它会把本进程当「另一个实例」拒绝）
    os.environ['GEOKB_QUEUE_LOCK'] = '1'
    q = _load_queue()
    ab = _load_antibot()
    sf = _load_freeze()
    items = q.load_manifest(repair=True)       # 父进程独占清单，子进程只报结论
    _ok, _why = q.window_ok()
    log('调度器启动 pid=%d ｜ 槽位=%d ｜ 单篇超时=%ds ｜ 静默期×%.1f ｜ %s'
        % (os.getpid(), slots, args.item_timeout, args.pace_scale, _why))
    try:
        _fz = sf.table_for_dispatch() if sf else {}
        if _fz:
            log('冻结源站 %d 个（不参与派发，条目仍保留）：%s'
                % (len(_fz), ', '.join('%s=%s' % (k, v) for k, v in sorted(_fz.items()))))
    except Exception:
        log('冻结表读取异常（按未冻结继续）：%s'
            % traceback.format_exc(limit=2).replace('\n', ' | ')[-160:])
    beat('start')

    inflight = {}          # future -> (item, host, t0)
    last_done = {}         # host -> 上一篇收工时刻
    last_dispatch = 0.0
    last_maint = 0.0
    pool = ThreadPoolExecutor(max_workers=slots)
    try:
        while True:
            ensure_pid()
            beat('active')
            try:
                ok, why = q.window_ok()
                if not ok:
                    log('静默：%s' % why)
                    if args.once and not inflight:
                        return 0
                    nap(IDLE)
                    continue

                # ── 维护：只在「没有一篇在跑」时做，避免覆盖正在跑的计数 ──
                if time.time() - last_maint > 600 and not inflight:
                    last_maint = time.time()
                    try:
                        fresh = q.load_manifest(repair=True)
                        if len(fresh) != len(items):
                            log('清单被外部更新：%d → %d 条，已重新载入'
                                % (len(items), len(fresh)))
                        items = fresh
                    except Exception:
                        pass
                    try:
                        n_rev = q.revive_parked(items)
                    except Exception:
                        n_rev = 0
                    if n_rev:
                        q.save_manifest(items, backup=False)
                        log('复活已停放（可重试）条目 %d 条' % n_rev)

                tot, exe, cool, park = counts(q, items)
                if tot == 0:
                    log('待爬清单已空，%d 小时后复查' % (EMPTY_SLEEP // 3600))
                    if args.once:
                        return 0
                    nap(EMPTY_SLEEP)
                    continue
                if exe == 0 and not inflight:
                    log('无可执行篇目（冷却 %d ｜ 停放 %d ｜ 总 %d），%d 分钟后复查'
                        % (cool, park, tot, STARVE_SLEEP // 60))
                    if args.once:
                        return 0
                    nap(STARVE_SLEEP)
                    continue

                # ── 填槽：一有空位就补下一篇，同源站绝不并发 ──────────
                sd_paused = bool(q.sd_paused())
                frozen = sf.table_for_dispatch() if sf else {}
                busy = {h for (_it, h, _t) in inflight.values()}
                why2, wait2 = '', 0.0
                while len(inflight) < slots:
                    if args.gap and time.time() - last_dispatch < args.gap:
                        break
                    it, host, why2, wait2 = _pick(q, ab, items, busy, last_done,
                                                  args.pace_scale, sd_paused, frozen)
                    if it is None:
                        break
                    fut = pool.submit(q.process_one_isolated, it, args.item_timeout)
                    inflight[fut] = (it, host, time.time())
                    busy.add(host)
                    _HOST_LAST_JOURNAL[host] = it.get('journal') or ''
                    last_dispatch = time.time()
                    log('▶ [%s] %s | %s（在跑 %d/%d）'
                        % (host, it['journal'], it['title'][:58], len(inflight), slots))

                if not inflight:
                    # 睡多久：pace 状态下必须睡到「最近一个源站解禁」，不要再叠一个
                    # PACE_TICK 下限。原来下限固定 20s，单源站时段（例如 1,165 条
                    # 岩石学报共用 ysxb.ac.cn、间隔 30s）实际间隔被拉到 40–50s，
                    # 吞吐只有理论值的一半。wait2 为 pace 时恒 >0.5s，不会忙循环。
                    _sleep = (STARVE_SLEEP if why2 in ('starve', 'frozen')
                              else min(300.0, max(3.0, float(wait2 or 0) + 0.6)))
                    # 填不进去又没人在跑 = 本轮真的没活可干（冷却 / 静默期 / 全停放 / 全冻结）
                    if why2 == 'frozen':
                        log('可派发的源站已全部冻结（%d 个），剩余条目原地保留；'
                            '解冻：python scripts/source_freeze.py --thaw all'
                            '（%d 分钟后复查）' % (len(frozen), STARVE_SLEEP // 60))
                    elif why2 == 'starve':
                        log('无可派发篇目（冷却 %d ｜ 停放 %d ｜ 冻结 %d 站 ｜ 总 %d），%d 分钟后复查'
                            % (cool, park, len(frozen), tot, STARVE_SLEEP // 60))
                    else:
                        log('槽位全空：源站都在静默期，%d 秒后重试（最近解禁 %ds）'
                            % (int(_sleep), int(wait2 or 0)))
                    if args.once:
                        return 0
                    # 睡到「最近一个源站解禁」为止（最长 5 分钟），不要固定 20s 空转
                    nap(_sleep)
                    continue

                done, _ = futures_wait(list(inflight), timeout=10,
                                       return_when=FIRST_COMPLETED)
                for f in done:
                    it, host, t0 = inflight.pop(f)
                    _settle(q, items, it, host, f, last_done, time.time() - t0)
            except Exception:
                # 单轮异常不许杀死调度器（守护进程不一定可靠）：记下来接着跑
                log('本轮异常（已记录并继续）：%s'
                    % traceback.format_exc(limit=4).replace('\n', ' | ')[-400:])
                nap(30)
    finally:
        pool.shutdown(wait=False)
        release_pid()


if __name__ == '__main__':
    # pythonw 下 sys.stdout 无效，未捕获异常会彻底消失（2026-10-26 实测：
    # 进程 19:34 无声退出、日志一片空白，只能靠 pid 消失才发现）。必须落盘。
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        log('主线异常退出：%s' % traceback.format_exc(limit=8).replace('\n', ' | '))
        sys.exit(1)
