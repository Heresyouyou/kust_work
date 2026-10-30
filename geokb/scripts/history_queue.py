# -*- coding: utf-8 -*-
"""历史文献清单消费器（逐篇爬取 → 只下载 → 移出待爬清单 → 进待入库队列）

一次运行 = 一轮，默认只处理 **1 篇**（`--limit` 可加）。清单由
`history_manifest.py` **一次性**生成，本脚本只消费、不重新生成；
当天新更新的期刊由既有管线（journal_auto_push / daily_run）负责，互不干扰。

节流（2026-10-20 用户口径）
--------------------------
> 不用 18 分钟一轮，爬取阶段直接爬完一篇文章等 3 分钟爬下一篇，
> 下载成功就从待爬清单里移出到另一个待入库清单，但不执行入库。
> 这样不会触发过于频繁的验证。

因此默认 `--gap 180`：篇与篇之间**硬等 3 分钟**（从上一次请求结束算起），
把单 IP 的请求密度压到出版社风控的安全区。手动批量排障时可 `--gap 5`。


时段策略（2026-10-21 改：只下载阶段全天候）
----------------------------------------
  · **只下载不入库**（当前默认，`ingest_policy` 的 history scope 关）
    → **全天候可跑**，不受任何时段限制。不调 DeepSeek 就没有理由卡时段。
  · 仅当 history scope **打开**（恢复自动入库、会调 API）时，才回到：
    18:05 ~ 次日 07:00 允许；07:00~09:00 让位当天更新；09:00~18:05 禁止。
  `--force` 可越过时段限制（仅手动排障用）。

单篇流水线（任一步失败即不计完成，保留在清单里等下一轮）
--------------------------------------------------------
  ① 若台账已有该文 PDF → 跳过下载，直接进 ③（避免重复下载）
  ② fetch_pdf（handler → OA → 浏览器验证）→ 落 NAS（pdf_downloader._save_pdf）
  ③ 登记台账（daily_archive_papers.append_index，含 eid）
  ④ 入库闸门（2026-10-20）：`ingest_policy.enabled('history')` 默认 **关** ——
     只下载、不入库，记一条 `state/pending_papers_ingest.jsonl` 后即算完成；
     只有显式开启时才继续 ⑤ 入库 ⑥ 拆知识点。
  ⑤ 入库：pdf_corpus_ingest.py --only <pdf>（DeepSeek 提取 papers 词条）
  ⑥ 拆知识点：kp_extract.py --sha <sha1>
  ⑦ 全绿 → 追加到 history_done.jsonl，并从 history_manifest.jsonl 物理移除
     （ScienceDirect 条目恒排最后；`state/sd_pause.json` 暂停期整批跳过）

用法
----
    python history_queue.py                     # 只下载模式：全天候爬 1 篇
    python history_queue.py --limit 8           # 连爬 8 篇（篇间隔 3 分钟）
    python history_queue.py --limit 8 --gap 5   # 排障：篇间隔压到 5 秒
    python history_queue.py --status            # 只看清单进度
    python history_queue.py --dry-run           # 只列本轮会处理哪些
    python history_queue.py --force             # 忽略时段限制（排障）

返回码：0 正常 / 2 不在允许时段（未执行，仅入库模式）/ 3 清单为空 /
        4 已有实例持锁（不并发）

鲁棒性契约（2026-10-26，「被打断也要能自己收尾、清单不能坏」）
--------------------------------------------------------------
1. **单篇硬超时**：每篇在**子进程**里跑（`--item-timeout`，默认 300s），
   卡在验证页/浏览器驱动上就杀掉子进程、记一次软失败，整轮继续。
   父进程不做网络动作，因此不会跟着卡死。
2. **逐篇落盘**：开始前写 `try_started/try_count`，每篇结束立刻原子回写清单
   （tmp + fsync + replace）。任何时刻被强杀，最多只丢「当前这一篇」的结论，
   进度、失败计数、冷却时间都不会回退。
3. **失败分级**：`blocked_captcha`/`err_*`/`http_5xx` 属**软失败** → 退避
   30min / 2h / 6h / 12h（不消耗硬配额）；`paywalled`/`not_found`/`no_fulltext`
   属**硬失败** → 3 次停放。停放满 12 小时的可重试条目自动复活。
4. **清单自检**：读盘时丢弃半行/重复 id，原件另存 `history_manifest.corrupt-*`；
   整份不可读则从 `history_manifest.jsonl.bak` 恢复。
5. **单实例锁**：`state/history_queue.lock`（pid + 6h 陈旧判定）。调度器与手动/
   agent 直跑不允许并发，避免把 IP 打进出版社风控。
6. **时间预算**：`--budget N` 到点先落盘再退出，方便被外部（工具超时、agent
   额度中断）掐断时留一个干净的断点。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
SCRIPTS = BASE / 'scripts'
STATE = BASE / 'state'
MANIFEST = STATE / 'history_manifest.jsonl'
MANIFEST_BAK = STATE / 'history_manifest.jsonl.bak'
DONE_FILE = STATE / 'history_done.jsonl'
QUEUE_STATE = STATE / 'history_queue_state.json'
LOCK_FILE = STATE / 'history_queue.lock'
LOG = BASE / 'logs' / 'history_queue.jsonl'
LEDGER = STATE / 'papers_index.jsonl'
PAPERS_DIR = BASE / 'content' / 'papers'
KP_DIR = BASE / 'content' / 'kpoints'

PY = sys.executable
_NO_WIN = 0x08000000 if os.name == 'nt' else 0    # CREATE_NO_WINDOW：不弹黑窗
MAX_ATTEMPTS = 3          # 硬失败（订阅墙 / 404 / 无全文）次数上限
MAX_SOFT_FAILS = 12       # 软失败（风控 / 网络 / 驱动异常）累计上限，超过才停放
# 人机挑战（Cloudflare/CF challenge）单独计数：2 次即转人工补缺。
# 为什么不是 1 次：CF 有随机 js-challenge 与网络抖动，单次失败可能是偶发，
#   一次就推给人工会把本可自动下载的文章误判（人工每篇 1–2 分钟，代价高且不可自动挽回）。
# 为什么不是沿用 12 次软失败曲线：人机挑战是 IP/站点级状态，重复撞墙只会
#   加深 IP 标记；实测 GSW 撞 9 次仍 0 成功，白占槽位数小时。
CAPTCHA_MAX = 2
REVIVE_HOURS = 12         # 停放后自动复活冷却（小时）
SOFT_COOLDOWN = (1800, 7200, 21600, 43200)   # 软失败退避：30min / 2h / 6h / 12h
GAP_DEFAULT = 180      # 篇间硬等待（秒）= 3 分钟，单 IP 请求节奏 ≈ 3 分钟/篇

# 依赖两个已存在的实现目录（不要复制代码，直接复用）：
#   D:/PostNews             → journal_pdf_fetch（handler + OA 多渠道 + 浏览器验证）
#   D:/ZSK/geokb/scripts    → pdf_downloader._save_pdf / daily_archive_papers.append_index
for _p in ('D:/PostNews', str(BASE / 'scripts')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ingest_policy                          # noqa: E402  入库策略：默认「只下载不入库」
from sd_policy import is_sd, paused as sd_paused   # noqa: E402  ScienceDirect 判据 + 暂停开关

WIN_START = '18:05'
WIN_END = '07:00'
GUARD_START = '07:00'     # 当天更新优先窗口起点
GUARD_END = '09:00'       # 当天更新优先窗口终点


# pythonw.exe（无控制台）下 sys.stdout 句柄无效，裸 print 会抛
# OSError: [Errno 22] Invalid argument，直接打断整轮爬取。
# 这里统一兜住：能打印就打印，不能就只落文件（2026-10-18 实测踩坑）。
_builtin_print = print


def print(*a, **kw):        # noqa: A001  仅本模块内生效
    try:
        _builtin_print(*a, **kw)
    except Exception:
        pass


def log(msg):
    line = '[%s] %s' % (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), msg)
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'ts': datetime.now().isoformat(timespec='seconds'),
                                'msg': msg}, ensure_ascii=False) + '\n')
    except Exception:
        pass


# ── 失败分级：决定「还能不能再试」────────────────────────────────────────
# 为什么要分（2026-10-26）：
#   `blocked_captcha`（Cloudflare 风控）被当成硬失败时，GSW 的 Am Min / Geology
#   会在 3 次内被整批 parked —— 实测 225 篇这样停放，队列看着「还剩 3966」，
#   实际可执行篇目在持续缩水；而 `paywalled` 再试一百次也一样。
#   前者应**退避等待**（不消耗硬配额），后者才该放弃。
_RETRYABLE = ('blocked_captcha', 'err_', 'http_5', 'http_429', 'http_408',
              'timeout', 'skipped', 'rate_limit', 'unknown_source')
_PERMANENT = ('paywalled', 'not_found', 'no_fulltext', 'not_pdf_office',
              'http_404', 'http_410',
              # 2026-10-27：录用稿/校样不是正式排版版，重试也不会变正式版，
              # 只能等出版社排期后条目被重新派发。
              'accepted_version',
              # 2026-10-29：首页无卷期 + in press（尚未正式出版，源站只有录用稿）。
              # 同属「等排期」性质，重试只会白撞反爬；停放 12h 后自动复活再探。
              'in_press')


def classify_failure(err):
    """把 fetch_pdf 的 status 归到 retryable / permanent（未知按可重试）。"""
    s = (err or '').strip().lower()
    for p in _PERMANENT:
        if s.startswith(p):
            return 'permanent'
    for p in _RETRYABLE:
        if s.startswith(p):
            return 'retryable'
    return 'retryable'          # 宁慢勿丢：拿不准就当可重试


def cooldown_sec(soft_fails):
    i = min(max(0, int(soft_fails) - 1), len(SOFT_COOLDOWN) - 1)
    return SOFT_COOLDOWN[i]


def apply_failure(item, err, note):
    """**失败记账的唯一实现**（常驻调度器与单轮模式共用，规则只写这里）。

    2026-10-27 踩坑：这段逻辑原本在 history_queue.main() 与
    history_queue_loop._settle() 各写了一份，改了一处另一处不生效 ——
    实测「人机挑战 2 次转人工」「配额对齐次日 00:05」都只在死代码里。
    所以统一到这里：只改 item、不做 IO、不打日志，调用方负责落盘与输出。

    返回 dict：
      kind           permanent / retryable
      attempts       硬失败次数（permanent 分支）
      soft           软失败累计次数
      captcha        人机挑战次数（独立计数）
      cd             本次冷却秒数（0 = 无冷却）
      newly_parked   本次是否刚刚停放（调用方据此决定是否提示「不再重试」）
      parked         结束时是否处于停放状态
      error_text     写进 last_error 的「错误码: 详情」
    """
    kind = classify_failure(err)
    e = (err or '').strip()
    # 记「错误码 + 详情」：manual 页分桶靠错误码前缀（blocked_captcha /
    # paywalled / no_fulltext），人看的是详情；只存 note 会让分桶判错。
    item['last_error'] = ('%s: %s' % (e, note) if e and note else (e or note or ''))[:200]
    item['last_try'] = datetime.now().isoformat(timespec='seconds')
    out = {'kind': kind, 'attempts': 0, 'soft': 0, 'captcha': 0, 'cd': 0,
           'newly_parked': False, 'parked': False, 'error_text': item['last_error']}

    if kind == 'permanent':
        # 订阅墙 / 404 / 无全文：再试一百次也一样，计硬次数
        item['attempts'] = int(item.get('attempts', 0)) + 1
        out['attempts'] = item['attempts']
        if item['attempts'] >= MAX_ATTEMPTS:
            item['status'] = 'parked'
            item['parked_at'] = item['last_try']
            item['park_kind'] = 'permanent'
            out['newly_parked'] = True
        out['parked'] = item.get('status') == 'parked'
        return out

    # 风控 / 网络 / 驱动异常：只退避，不消耗硬配额
    item['soft_fails'] = int(item.get('soft_fails', 0)) + 1
    out['soft'] = item['soft_fails']

    if e.startswith('blocked_captcha'):
        # 人机挑战：2 次即转人工补缺，独立计数、不进 12 次曲线。
        # 第 1 次只退避 30 分钟（复核一次，排除 CF 随机挑战/网络抖动）。
        item['captcha_fails'] = int(item.get('captcha_fails', 0)) + 1
        out['captcha'] = item['captcha_fails']
        if item['captcha_fails'] >= CAPTCHA_MAX:
            item['status'] = 'parked'
            item['parked_at'] = item['last_try']
            item['park_kind'] = 'captcha'
            item['next_try_at'] = ''
            out['newly_parked'] = True
        else:
            out['cd'] = cooldown_sec(1)          # 30 分钟
            item['next_try_at'] = _iso_in(out['cd'])
    else:
        if e.startswith('quota_exhausted') or 'quota_exhausted' in (note or ''):
            # 日配额用尽 → 对齐到次日 00:05（零点重置 + 5 分钟缓冲），
            # 不走退避曲线，避免白等一整天里的后半夜（实测白等 7.7 小时）。
            out['cd'] = secs_until_tomorrow(5 * 60)
        else:
            out['cd'] = cooldown_sec(item['soft_fails'])
        item['next_try_at'] = _iso_in(out['cd'])
        if item['soft_fails'] >= MAX_SOFT_FAILS:
            item['status'] = 'parked'
            item['parked_at'] = item['last_try']
            item['park_kind'] = 'retryable'
            out['newly_parked'] = True

    out['parked'] = item.get('status') == 'parked'
    return out


def secs_until_tomorrow(extra=0):
    """到次日 00:00 的秒数（+extra 缓冲）。

    用于「日配额用尽」这类**按自然日重置**的状态：它不是源站故障，
    走退避曲线会白等 —— 实测 19:46 用尽 → 12h 退避 → 次日 07:46 才重试，
    而配额 00:00 就已恢复，等于浪费 7 小时 40 分。
    """
    now = datetime.now()
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((nxt - now).total_seconds()) + int(extra))


def _iso_in(seconds):
    return (datetime.now() + timedelta(seconds=seconds)).isoformat(timespec='seconds')


# ── 单实例锁（防「调度器 + 手动/agent 直跑」并发把出版社打成风控）──────────
# 并发是反爬最大的杀手（4 源并行 gsw 3/117 全 403，串行 10/10）。
# 调度器在进程内调 main() 时会带 GEOKB_QUEUE_LOCK=1，表示锁由它持有。
def _pid_alive(pid):
    if not pid:
        return False
    try:
        import psutil
        return psutil.pid_exists(int(pid)) and psutil.Process(int(pid)).status() != 'zombie'
    except Exception:
        try:
            os.kill(int(pid), 0)
            return True
        except Exception:
            return False


def acquire_lock(break_lock=False):
    """返回 (ok, holder_pid)。陈旧锁（>6h 或进程已死）自动接管。"""
    if os.environ.get('GEOKB_QUEUE_LOCK') == '1':
        try:
            LOCK_FILE.write_text(str(os.getpid()))
        except Exception:
            pass
        return True, os.getpid()
    try:
        holder = int(LOCK_FILE.read_text().strip()) if LOCK_FILE.exists() else 0
        stale = (not LOCK_FILE.exists()) or (time.time() - LOCK_FILE.stat().st_mtime > 6 * 3600)
    except Exception:
        holder, stale = 0, True
    if holder and holder != os.getpid() and _pid_alive(holder) and not stale and not break_lock:
        return False, holder
    try:
        LOCK_FILE.write_text(str(os.getpid()))
    except Exception:
        pass
    return True, os.getpid()


def release_lock():
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text().strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except Exception:
        pass


# ── 时段 ────────────────────────────────────────────────────────────────
_LEDGER = Path('D:/PostNews/journal_ledger.json')
_PAPERS_INDEX = BASE / 'state' / 'papers_index.jsonl'
_backlog_cache = {'ts': 0, 'n': -1, 'age': 0.0}

# 让位宽限（小时）：当天欠账里最老的一篇超过这个年龄，就不再让位。
# 为什么必须有：有些条目（如 doi.org 落地页的中文刊英文文）天长日久也抓不到，
# 若只按「欠账 > 0」让位，历史队列会**整夜一条都跑不了**（2026-10-20 实测：
# 2 篇《矿物岩石地球化学通报》欠账 13 小时，18:05 起全程静默）。
BACKLOG_GRACE_H = 4.0


def _norm_title(s):
    return ''.join(ch for ch in (s or '').lower() if ch.isalnum())


def today_backlog(ttl=300):
    """当天更新的期刊文章里，还有多少篇**没进 NAS**，以及最老那篇几小时了。

    返回 `(n, oldest_age_h)`。为什么要年龄：有些条目先天抓不到（doi.org 落地页
    的中文刊英文文等），只按「欠账 > 0」让位会让历史队列**永久停摆**
    （2026-10-20：2 篇欠账 13 小时，18:05 起全程静默）。所以 window_ok
    拿年龄兜底：超过 BACKLOG_GRACE_H 就不再让位。

    为什么要它（2026-10-20 用户要求「当天更新的期刊优先」）：
    历史清单的窗口是 18:05-次日 07:00，若白天当天新文章没抓完，
    夜里历史队列会跟它们抢同一批源站的限流额度，结果两边都慢。
    当天有欠账时历史爬取主动让位，把带宽和风控额度留给当日更新。

    只认「有 PDF 的台账条目」（repo_path 含 papers_repo）——纯题录条目
    不算已归档，这条规则与 daily_archive_papers._has_pdf 一致。
    """
    if ttl and time.time() - _backlog_cache['ts'] < ttl and _backlog_cache['n'] >= 0:
        return _backlog_cache['n'], _backlog_cache['age']
    n = 0
    oldest = ''          # ISO 字符串可直接比较，取最小即最老
    try:
        if not _LEDGER.exists():
            return 0, 0.0
        have = set()
        if _PAPERS_INDEX.exists():
            with open(_PAPERS_INDEX, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if 'papers_repo' not in (r.get('repo_path') or ''):
                        continue        # 题录不算已归档
                    have.add(_norm_title(r.get('title')))
        today = datetime.now().date()
        d = json.load(open(_LEDGER, encoding='utf-8'))
        for _j, arts in (d.get('journals') or {}).items():
            for a in arts or []:
                if not isinstance(a, dict):
                    continue
                fs = a.get('first_seen') or ''
                try:
                    day = datetime.fromisoformat(fs).date()
                except Exception:
                    continue
                if day != today:
                    continue
                if _norm_title(a.get('title')) in have:
                    continue
                # SD 暂停期：Elsevier 的当日新文**不再计入欠账**，否则它们永远
                # 归档不了 → 欠账恒 > 0 → 历史队列每轮都让位，全站静默停摆。
                if is_sd(a.get('doi', ''), a.get('url', ''), _j):
                    continue
                n += 1
                if fs and (not oldest or fs < oldest):
                    oldest = fs
    except Exception:
        n = 0
        oldest = ''
    age = 0.0
    if oldest:
        try:
            age = max(0.0, (datetime.now() - datetime.fromisoformat(oldest)).total_seconds() / 3600.0)
        except Exception:
            age = 0.0
    _backlog_cache['ts'] = time.time()
    _backlog_cache['n'] = n
    _backlog_cache['age'] = age
    return n, age


def window_ok(now=None):
    """返回 (ok, reason)。

    ⚠️ 2026-10-21 用户口径：**只下载不入库 = 不碰 DeepSeek API → 全天候爬取**。
    时段限制的唯一理由是「入库要调 LLM，高峰时段禁用」（09:00-12:00 /
    14:00-18:00）。既然当前阶段只落 NAS + 台账、词条与知识点都留给用户统一
    安排，就完全没有时段约束，也不给当日更新让位（跨进程 PACE_GAP 会自然
    排队，历史队列 3 分钟才取一篇，抢不走多少额度）。

    一旦把 `ingest_policy` 的 history scope 打开（恢复自动入库），下面的
    夜间窗口 + 让位逻辑立即生效。
    """
    if not ingest_policy.enabled('history'):
        return True, '只下载不入库（不调 DeepSeek）→ 全天候爬取，不受时段限制'

    t = (now or datetime.now()).strftime('%H:%M')
    if GUARD_START <= t < GUARD_END:
        return False, '%s 属当天更新优先窗口（07:00-09:00），历史爬取让位' % t
    if t >= WIN_START or t < WIN_END:
        # 当天欠账闸门：哪怕进了夜间窗口，只要当天的新文章还没抓完，历史也让路。
        # 但只让到宽限为止 —— 先天抓不到的条目不能把历史队列整夜锁死。
        n, age_h = today_backlog()
        if n > 0 and age_h < BACKLOG_GRACE_H:
            return False, ('当天更新仍有 %d 篇未归档（最老 %.1f 小时），历史爬取让位'
                           '（把限流额度留给当日）' % (n, age_h))
        if n > 0:
            return True, ('%s 在历史空闲窗口；当天欠账 %d 篇已超 %.0f 小时宽限，不再让位'
                          % (t, n, BACKLOG_GRACE_H))
        return True, '%s 在历史空闲窗口（18:05-次日07:00）' % t
    return False, '%s 不在空闲窗口（18:05-次日07:00）' % t


# ── 清单读写 ────────────────────────────────────────────────────────────
OA_CACHE = STATE / 'oa_thirdparty.jsonl'


def oa_repo_dois():
    """返回「第三方仓储有直连副本」的 DOI 集合（来自 oa_thirdparty 盘点）。

    用途：这些篇目**不用碰出版社风控**，直接从机构库/HAL/GFZ 拿 PDF，
    成功率远高于走 handler，所以排到批次前面先消耗掉。
    缓存缺失或字段不全时返回空集，排序逻辑自动退化为原顺序。
    """
    s = set()
    if not OA_CACHE.exists():
        return s
    try:
        with open(OA_CACHE, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get('usable') == 'repo' and r.get('doi'):
                    s.add(str(r['doi']).lower())
    except Exception:
        return set()
    return s


def load_manifest(repair=False):
    """读清单；repair=True 时自检修复（丢弃坏行/重复 id，必要时从 .bak 或备份恢复）。

    为什么要自检：清单是唯一进度载体，被强杀（额度/网络/工具超时）时可能留下
    半行 JSON。半行只该丢那一行，不该让整份清单读不出来。
    """
    if not MANIFEST.exists():
        if MANIFEST_BAK.exists():
            try:
                import shutil
                shutil.copy2(MANIFEST_BAK, MANIFEST)
                print('[自检] 清单缺失，已从 .bak 恢复（%d 字节）' % MANIFEST_BAK.stat().st_size)
            except Exception:
                return []
        else:
            return []
    out, seen, bad = [], set(), 0
    try:
        raw = MANIFEST.read_text(encoding='utf-8', errors='ignore').splitlines()
    except Exception:
        raw = []
    for line in raw:
        line = line.strip()
        if not line:
            continue
        try:
            x = json.loads(line)
        except Exception:
            bad += 1
            continue
        if not isinstance(x, dict) or not x.get('id') or x['id'] in seen:
            bad += 1
            continue
        seen.add(x['id'])
        out.append(x)

    # 整份读不出来（0 条）但有备份 → 回退备份
    if not out and MANIFEST_BAK.exists():
        try:
            for line in MANIFEST_BAK.read_text(encoding='utf-8', errors='ignore').splitlines():
                line = line.strip()
                if not line:
                    continue
                x = json.loads(line)
                if isinstance(x, dict) and x.get('id'):
                    out.append(x)
            if out:
                print('[自检] 主清单不可读，已从 .bak 恢复 %d 条' % len(out))
                save_manifest(out, backup=False)
                return out
        except Exception:
            pass

    if repair and bad:
        try:
            import shutil
            bak = STATE / ('history_manifest.corrupt-%s' % datetime.now().strftime('%Y%m%d-%H%M%S'))
            shutil.copy2(MANIFEST, bak)
            print('[自检] 丢弃 %d 行坏行/重复 id，保留 %d 条（原件备份 %s）'
                  % (bad, len(out), bak.name))
            save_manifest(out, backup=True)
        except Exception as e:
            print('[自检] 修复写盘失败（不影响本轮）: %s' % str(e)[:80])
    return out


_last_backup = {'ts': 0}


def save_manifest(items, backup=True):
    """原子落盘（tmp → fsync → replace），并按需留一份 .bak。

    为什么要 fsync：清单是 3,900+ 条的**唯一进度载体**，断电/强制结束进程时
    只写进页缓存会用半个文件换掉好文件。为什么要 .bak：写坏了还能退回上一版，
    比让 4,000 条题录归零便宜得多。
    """
    tmp = MANIFEST.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        for x in items:
            f.write(json.dumps(x, ensure_ascii=False) + '\n')
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    if backup and MANIFEST.exists() and time.time() - _last_backup['ts'] > 600:
        try:
            import shutil
            shutil.copy2(MANIFEST, MANIFEST_BAK)
            _last_backup['ts'] = time.time()
        except Exception:
            pass
    os.replace(tmp, MANIFEST)


def append_done(rec):
    with open(DONE_FILE, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


# ── 台账索引（判「已下载」，含分隔符归一）────────────────────────────────
def _norm(p):
    return (p or '').replace('\\', '/').lower()


def _nk(s):
    return re.sub(r'\W+', '', (s or '').lower())[:100]


def ledger_index():
    """返回 {title_key: meta}，只收有 PDF 的条目（PDF 覆盖题录）。"""
    idx = {}
    if not LEDGER.exists():
        return idx
    for line in LEDGER.read_text(encoding='utf-8', errors='ignore').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except Exception:
            continue
        p = _norm(m.get('repo_path'))
        if 'papers_repo/' not in p or not p.endswith('.pdf'):
            continue
        k = _nk(m.get('title'))
        if k:
            idx[k] = m
    return idx


# ── 单篇处理 ────────────────────────────────────────────────────────────
def run_ingest(pdf_path):
    """入库：pdf_corpus_ingest --only。返回 (ok, note)"""
    try:
        env = dict(os.environ)
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUNBUFFERED'] = '1'
        p = subprocess.run([PY, str(SCRIPTS / 'pdf_corpus_ingest.py'), '--only', str(pdf_path)],
                           cwd=str(BASE), capture_output=True, text=True,
                           encoding='utf-8', errors='ignore', timeout=420, env=env,
                           creationflags=_NO_WIN)
        out = (p.stdout or '') + (p.stderr or '')
        if p.returncode == 0 and ('[DONE] 无待处理篇目' in out or 'llm ✓' in out or 'fallback' in out):
            return True, out.strip().splitlines()[-1][:120] if out.strip() else 'ok'
        return False, 'ingest rc=%s %s' % (p.returncode, out.strip()[-160:].replace('\n', ' '))
    except subprocess.TimeoutExpired:
        return False, 'ingest timeout 420s'
    except Exception as e:
        return False, 'ingest %s: %s' % (type(e).__name__, str(e)[:100])


def run_kp(sha):
    """拆知识点：kp_extract --sha。返回 (ok, note)

    ⚠️ 判据只看**产物**（kp-<sha12>-*.md 数量）与 returncode，不看子进程文案：
    kp_extract 的输出是 GBK，被当 utf-8 读会变成乱码（"新增知识点 6" →
    "֪ʶ6"），按中文关键词匹配会**每篇都判失败**（2026-10-18 实测：
    3 篇各拆出 6 条知识点却全部记 fail，清单减不掉）。
    """
    sha12 = sha[:12]
    before = kp_count(sha12)
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUNBUFFERED'] = '1'
    try:
        p = subprocess.run([PY, str(SCRIPTS / 'kp_extract.py'), '--sha', sha],
                           cwd=str(BASE), capture_output=True, text=True,
                           encoding='utf-8', errors='ignore', timeout=300, env=env,
                           creationflags=_NO_WIN)
        out = (p.stdout or '') + (p.stderr or '')
        after = kp_count(sha12)
        # 0 条知识点是**合法结论**（不可复用），不能按「有文件才算成功」判失败。
        # kp_extract 会打印机器可读行 KP_RESULT sha12=… n=…（2026-10-23）。
        if p.returncode == 0 and ('KP_RESULT' in out or after > 0
                                  or after > before or 'ALREADY_DONE' in out):
            return True, 'kp=%d' % after
        return False, 'kp rc=%s try=%s after=%s %s' % (
            p.returncode, before, after, out.strip()[-140:].replace('\n', ' '))
    except subprocess.TimeoutExpired:
        return False, 'kp timeout 300s'
    except Exception as e:
        return False, 'kp %s: %s' % (type(e).__name__, str(e)[:100])


def kp_count(sha12):
    return len(list(KP_DIR.glob('kp-%s-*.md' % sha12)))


def run_index(eids):
    """增量写索引（entries + FTS5 + 向量），秒级可见。

    ⚠️ 不要每篇都跑 rebuild_index.py：全库重建实测 5 分钟（2026-10-18），
    一轮只下 4 篇却要等 5 分钟，产能直接腰斩。`index_one_entry` 与 rebuild
    的插入逻辑逐行同源（同一 embed 模型 / tokenize / struct.pack），单条
    ~0.6s，同一进程内循环还能复用已加载的库。
    """
    eids = [e for e in eids if e]
    if not eids:
        return 0
    try:
        sys.path.insert(0, str(SCRIPTS))
        import index_one_entry as ioe
        old = sys.argv
        ok = 0
        for e in eids:
            sys.argv = ['index_one_entry.py', e]
            try:
                if ioe.main() == 0:
                    ok += 1
            except SystemExit as ex:
                if ex.code == 0:
                    ok += 1
            except Exception as ex:
                log('    [warn] 索引 %s 失败: %s' % (e, str(ex)[:80]))
        sys.argv = old
        return ok
    except Exception as ex:
        log('    [warn] 增量索引不可用（回退到全量重建）: %s' % str(ex)[:100])
        return -1


def kp_ids(sha12):
    return [p.stem for p in sorted(KP_DIR.glob('kp-%s-*.md' % sha12))]


def process_one(item, idx, dry=False):
    """返回 (status, note, err)。status ∈ done / skipped_existing / fail / dry

    err 是 fetch_pdf 的原始 status（blocked_captcha / paywalled / err_exception…），
    供上层 `classify_failure()` 判「可重试还是该放弃」。
    """
    from journal_pdf_fetch import fetch_pdf
    import pdf_downloader as pdl
    from daily_archive_papers import append_index

    title, url, journal, date = item['title'], item['url'], item['journal'], item['date']
    doi = item.get('doi') or ''
    meta0 = idx.get(_nk(title))

    if dry:
        return ('dry', '已下载' if meta0 else '需下载', '')

    pdf_path, sha, size = '', '', 0
    if meta0:                                  # ① 已有 PDF
        rel = (meta0.get('repo_path') or '').replace('\\', '/')
        cand = Path(rel)
        if not cand.exists():
            cand = Path('S:/GeoKB') / rel.split('GeoKB/', 1)[-1] if 'GeoKB/' in rel else cand
        if cand.exists():
            pdf_path = str(cand)
            sha = hashlib.sha1(cand.read_bytes()).hexdigest()
            size = cand.stat().st_size
            log('    ↳ 台账已有 PDF，跳过下载：%s' % cand.name[:60])

    if not pdf_path:
        # 上次已是确定性结论（付费墙 / 无全文 / 无此文章）时，跳过 OA 兜底：
        # OA 四渠道按 DOI 查是**确定性**的，几小时内重查结果不变，白花 ~65s。
        # 实测 JGSA 一类付费墙刊每条重试都要多等一分钟，几百条就是十几个小时槽位。
        _prev = (item.get('last_error') or '').split(':')[0].strip()
        _skip_oa = _prev in ('paywalled', 'no_fulltext', 'not_found',
                             'http_404', 'not_pdf_office')
        r = fetch_pdf(journal, title, url, date=date, doi=doi, skip_oa=_skip_oa)
        if r.get('status') == 'paused_sd':
            # 兜底：SD 暂停时不要把它记成失败（否则 3 次就 parked）
            return ('skip_sd', 'SD 已暂停，跳过', '')
        if r.get('status') != 'ok' or not r.get('pdf_bytes'):
            return ('fail', '%s: %s' % (r.get('status'), str(r.get('note', ''))[:110]),
                    str(r.get('status') or 'unknown'))
        data = r['pdf_bytes']
        # ── 版本闸门（2026-10-27）：录用稿/校样不落盘、不入库 ──────────
        # GSW 的 "Just Accepted" 等录用稿，首页带
        #   "This is the peer-reviewed, final accepted version ... in press"
        # 全篇带行号、无卷期页码、内容与正式版可能不同 → 收进来会污染引用。
        # 判定只扫第 1 页、只用强判据（详见 scripts/pdf_version_guard.py）。
        try:
            import pdf_version_guard as _vg
            _vr = _vg.check_bytes(data)
        except Exception:  # noqa: BLE001
            _vr = {'version': 'other', 'evidence': []}
        if _vr.get('version') == 'accepted':
            return ('fail', 'accepted_version: 录用稿/校样，非正式排版版（%s）'
                    % ','.join(_vr.get('evidence') or [])[:80],
                    'accepted_version')
        sha = hashlib.sha1(data).hexdigest()
        size = len(data)
        pdf_path = pdl._save_pdf(data, journal or 'unknown', title, date, '_hist')

        meta = {
            'title': title, 'url': url, 'journal': journal, 'date': date,
            'doi': item.get('doi') or '',
            'pdf_url': r.get('pdf_url', ''),
            'repo_path': pdf_path, 'size': size,
            'source': r.get('source', 'fetch'),
            'eid': 'paper-%s' % sha[:12],
            'indexed_at': datetime.now().isoformat(timespec='seconds'),
        }
        append_index(meta)
        idx[_nk(title)] = meta
        log('    ✓ 落盘 %s（%.1f MB，来源 %s）' % (pdf_path, size / 1e6, r.get('source')))

    sha12 = sha[:12]
    paper_eid = 'paper-%s' % sha12

    # ── 入库闸门（2026-10-20 用户口径）──────────────────────────────
    # 自动爬取只负责「下载到本地 + 写台账」；抽全文 / 建词条 / 建索引 /
    # 拆知识点一律等用户统一安排。完成下载即算本条爬取完成（移出清单），
    # 同时记一条待入库记录，避免成果丢失。
    if not ingest_policy.enabled('history'):
        queued = ingest_policy.queue_pending({
            'eid': paper_eid, 'title': title, 'journal': journal,
            'pdf': pdf_path, 'url': url, 'doi': item.get('doi') or '',
            'from': 'history',
        })
        log('    ⇢ 只下载模式：已入待入库队列（%s）' % ('新增' if queued else '已存在'))
        return ('done', '已下载，待入库 %s' % Path(pdf_path).name, '')

    # ④ 入库
    if (PAPERS_DIR / ('%s.md' % paper_eid)).exists():
        log('    ↳ papers 词条已存在，跳过入库')
    else:
        ok, note = run_ingest(pdf_path)
        if not ok:
            return ('fail', 'ingest: %s' % note, 'err_ingest')
        log('    ✓ 入库：%s' % note)
    # 索引：词条立即可查（秒级）
    if run_index([paper_eid]) == 0:
        pass

    # ⑤ 拆知识点
    n = kp_count(sha12)
    if n > 0:
        log('    ↳ 已有 %d 个知识点，跳过拆分' % n)
    else:
        ok, note = run_kp(sha)
        if not ok:
            return ('fail', 'kp: %s' % note, 'err_kp')
        n = kp_count(sha12)
        log('    ✓ 拆知识点：%d 条（%s）' % (n, note))
        if n == 0:
            # 模型没给出知识点：不算完成，下轮重试；连续 3 次转 parked
            return ('fail', 'kp 抽取为空（模型未返回可用知识点）', 'err_kp_empty')

    # 索引：知识点同样即时可查
    run_index(kp_ids(sha12))

    return ('done', '%d kp | %s' % (n, pdf_path), '')


def revive_parked(items):
    """把「停放够久 + 属可重试失败」的条目放回待爬。

    为什么需要：风控/网络是一次性的，停放只是「现在别撞」。没有复活机制，
    一次 Cloudflare 封禁就会把整批篇目永久判死（2026-10-26：225 篇 GSW 被
    parked，队列可执行量持续缩水）。permanent（订阅墙/404）不自动复活。

    例外：park_kind='captcha' 也不自动复活 —— 那类已经 2 次人机挑战失败、
    交给人工补缺处理了，自动复活只会再去撞同一堵墙。
    """
    cutoff = _iso_in(-REVIVE_HOURS * 3600)
    n = 0
    for x in items:
        if x.get('status') != 'parked':
            continue
        if x.get('park_kind') in ('permanent', 'captcha'):
            continue
        when = x.get('parked_at') or x.get('last_try') or ''
        if not when or when > cutoff:
            continue
        x['status'] = 'pending'
        x['attempts'] = 0
        x['soft_fails'] = 0
        x['next_try_at'] = ''
        x.pop('parked_at', None)
        x.pop('park_kind', None)
        n += 1
    return n


def select_ready(items, include_parked=False):
    """按「可执行 / 冷却中 / 停放」三分，供排队与进度展示共用。"""
    now_iso = datetime.now().isoformat(timespec='seconds')
    pend, cooling, parked = [], [], []
    for x in items:
        if x.get('status') == 'parked':
            (pend if include_parked else parked).append(x)
            continue
        if int(x.get('attempts', 0)) >= MAX_ATTEMPTS:
            parked.append(x)
            continue
        nt = x.get('next_try_at') or ''
        if nt and nt > now_iso:
            cooling.append(x)
            continue
        pend.append(x)
    return pend, cooling, parked


def _kill_tree(pid):
    """连同子孙一起杀（浏览器驱动常是孙进程，只杀父会留僵尸占着端口）。"""
    try:
        import psutil
        p = psutil.Process(pid)
        for c in p.children(recursive=True):
            try:
                c.kill()
            except Exception:
                pass
        p.kill()
    except Exception:
        try:
            os.kill(pid, 9)
        except Exception:
            pass


def process_one_isolated(item, timeout):
    """在**子进程**里跑单篇，超时直接杀掉。

    为什么必须隔离（2026-10-26 用户口径「被打断也要因超时自动退出」）：
    单篇下载会走浏览器验证，卡在验证页时 Python 侧没有任何超时能兜住；
    父进程一旦跟着卡死，整轮不再前进，清单也停在半路。放进子进程后，
    超时 = 杀掉子进程并记一次软失败，队列继续下一篇，清单始终完整。

    timeout<=0 时退化为进程内直跑（排障用）。
    """
    if not timeout or timeout <= 0:
        return process_one(item, ledger_index())
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUNBUFFERED'] = '1'
    env.pop('GEOKB_QUEUE_LOCK', None)     # 子进程不碰单实例锁
    cmd = [PY, str(Path(__file__).resolve()), '--single-item',
           json.dumps(item, ensure_ascii=False)]
    try:
        p = subprocess.Popen(cmd, cwd=str(BASE), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                             errors='ignore', env=env, creationflags=_NO_WIN)
    except Exception as e:
        return 'fail', '子进程启动失败 %s: %s' % (type(e).__name__, str(e)[:80]), 'err_spawn'
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(p.pid)
        try:
            p.communicate(timeout=15)
        except Exception:
            pass
        return ('fail', '单篇超时 %ds，已强制终止子进程' % timeout, 'err_item_timeout')
    for ln in reversed((out or '').splitlines()):
        if ln.startswith('@@RESULT@@'):
            try:
                d = json.loads(ln[len('@@RESULT@@'):])
                return d.get('st', 'fail'), d.get('note', ''), d.get('err', '')
            except Exception:
                break
    tail = (out or '').strip().replace('\n', ' | ')[-180:]
    return ('fail', '子进程无结果 rc=%s %s' % (p.returncode, tail), 'err_child')


def run_single(item):
    """--single-item：只跑一篇，结果打一行 @@RESULT@@（父进程解析）。"""
    try:
        r = process_one(item, ledger_index())
        st, note, err = (tuple(r) + ('', ''))[:3] if isinstance(r, tuple) else (r, '', '')
    except Exception as e:
        st, note, err = 'fail', '%s: %s' % (type(e).__name__, str(e)[:140]), 'err_exception'
    print('@@RESULT@@ ' + json.dumps({'st': st, 'note': str(note)[:300], 'err': err},
                                    ensure_ascii=False), flush=True)
    return 0


# ── 主流程 ──────────────────────────────────────────────────────────────
def main():
    # 子进程模式：只跑一篇并把结果吐给父进程（不给单实例锁 / 不读清单）
    if len(sys.argv) > 2 and sys.argv[1] == '--single-item':
        try:
            return run_single(json.loads(sys.argv[2]))
        except Exception as e:
            print('@@RESULT@@ ' + json.dumps(
                {'st': 'fail', 'note': '参数解析失败 %s' % str(e)[:120], 'err': 'err_badarg'},
                ensure_ascii=False), flush=True)
            return 1

    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=1, help='本轮最多处理几篇（默认 1 篇）')
    ap.add_argument('--gap', type=int, default=GAP_DEFAULT,
                    help='篇间硬等待秒数（默认 %d 即 3 分钟；0 = 不等，排障用）' % GAP_DEFAULT)
    ap.add_argument('--status', action='store_true', help='只看清单进度')
    ap.add_argument('--dry-run', action='store_true', help='只列本轮会处理哪些')
    ap.add_argument('--force', action='store_true', help='忽略时段限制')
    ap.add_argument('--include-parked', action='store_true', help='连 parked 的一起重试')
    ap.add_argument('--include-sd', action='store_true',
                    help='强制包含 ScienceDirect（默认沉底；暂停期默认跳过）')
    ap.add_argument('--no-prefer-oa', action='store_true',
                    help='不优先处理「第三方仓储有副本」的篇目（默认优先）')
    ap.add_argument('--rebuild-all', action='store_true',
                    help='本轮结束后全量重建索引（约 5 分钟，默认不做）')
    ap.add_argument('--budget', type=int, default=0,
                    help='本轮时间预算（秒，0=不限）。到点先落盘再退出，防被外部掐断丢进度')
    ap.add_argument('--break-lock', action='store_true',
                    help='抢占单实例锁（默认发现已有实例在跑就退出，避免并发触发风控）')
    ap.add_argument('--no-revive', action='store_true',
                    help='不自动复活已停放的可重试条目')
    ap.add_argument('--item-timeout', type=int, default=300,
                    help='单篇硬超时（秒，默认 300）。到点杀掉子进程、记一次软失败，'
                         '保证整轮不会卡死在一篇上；0 = 不隔离（排障）')
    args = ap.parse_args()

    # 只在真正要跑的时候才改写清单（--status / --dry-run 保持只读）
    items = load_manifest(repair=not (args.status or args.dry_run))
    if not items:
        print('清单为空或不存在：%s（先跑 history_manifest.py）' % MANIFEST)
        return 3

    pend, cooling, parked = select_ready(items, args.include_parked)

    # ── ScienceDirect 一律沉底；暂停期整批跳过 ──────────────────────────
    # 2026-10-20：SD 的 /pdf /pdfft 被 CF 内容防护(1000S)硬拦，重试只加深
    # IP 标记。清单里 SD 必须排在所有源站之后，暂停期干脆不消费。
    _sd_paused = False if args.include_sd else sd_paused()
    n_sd = 0
    if pend:
        for x in pend:
            if 'sd' not in x:
                x['sd'] = is_sd(x.get('doi', ''), x.get('url', ''), x.get('journal', ''))
        n_sd = sum(1 for x in pend if x.get('sd'))
        if _sd_paused:
            pend = [x for x in pend if not x.get('sd')]
            print('SD 暂停中：跳过 %d 篇，可执行剩 %d 篇' % (n_sd, len(pend)))

    if args.status:
        by_st = {}
        for x in items:
            by_st[x.get('status', '?')] = by_st.get(x.get('status', '?'), 0) + 1
        print('清单总数 %d ｜ 可执行 %d ｜ 冷却中 %d ｜ 停放 %d'
              % (len(items), len(pend), len(cooling), len(parked)))
        print('状态分布：', by_st)
        if cooling:
            _nx = min((x.get('next_try_at') or '') for x in cooling)
            print('最早可重试 %s（%d 篇在风控/网络退避中，不消耗重试次数）'
                  % (_nx, len(cooling)))
        _n_sd = 0
        for x in items:
            _flag = x['sd'] if 'sd' in x else is_sd(
                x.get('doi', ''), x.get('url', ''), x.get('journal', ''))
            _n_sd += 1 if _flag else 0
        print('其中 ScienceDirect %d 篇 ｜ SD 抓取：%s'
              % (_n_sd, '已暂停 ⏸（不消费）' if sd_paused() else '运行中 ▶（排在最后）'))
        print('入库策略：%s' % ('自动入库开' if ingest_policy.enabled('history')
                              else '只下载不入库（进待入库队列）'))
        try:
            print('待入库清单：%d 条（state/pending_papers_ingest.jsonl）'
                  % len(ingest_policy.load_pending()))
        except Exception:
            pass
        print('节流：篇间隔 %d 分 %d 秒' % (args.gap // 60, args.gap % 60))
        if DONE_FILE.exists():
            n_done = sum(1 for _ in open(DONE_FILE, encoding='utf-8'))
            print('已完成（累计移出） %d' % n_done)
        for x in pend[:10]:
            print('  · [%d] %-38s %s  %s' % (x['score'], x['journal'], x['date'], x['title'][:56]))
        return 0

    ok, why = window_ok()
    print('时段判定：%s' % why)
    if not ok and not args.force:
        print('未执行（--force 可强制）')
        return 2

    # ── 单实例锁：调度器与手动/agent 直跑不允许并发 ─────────────────────
    # 并发是反爬最大的杀手（2026-10-20 实测 4 源并行 gsw 3/117 全 403）。
    ok_lock, holder = (True, 0) if args.dry_run else acquire_lock(args.break_lock)
    if not ok_lock:
        print('已有队列实例在跑（pid=%s），本次不并发执行。' % holder)
        print('（并发只会叠加请求密度、把 IP 打进出版社风控；确实要抢加 --break-lock）')
        return 4

    # 停放够久的可重试条目放回队列（风控是一时的，不该永久判死）
    n_rev = 0 if (args.no_revive or args.dry_run) else revive_parked(items)
    if n_rev:
        save_manifest(items, backup=False)
        log('复活停放 %d 篇（停放 ≥%d 小时且属可重试失败）' % (n_rev, REVIVE_HOURS))
        pend, cooling, parked = select_ready(items, args.include_parked)
        if _sd_paused:
            pend = [x for x in pend if not x.get('sd')]

    # 第三方仓储有副本的先跑（不碰出版社风控，成功率最高）
    if not getattr(args, 'no_prefer_oa', False):
        oa = oa_repo_dois()
        if oa:
            pend.sort(key=lambda x: (0 if str(x.get('doi') or '').lower() in oa else 1))
            n_oa = sum(1 for x in pend[:args.limit]
                       if str(x.get('doi') or '').lower() in oa)
            print('OA 优先：本批 %d/%d 篇有第三方仓储副本' % (n_oa, min(args.limit, len(pend))))

    # SD 永远排最后（稳定分区，不破坏上面的 OA/清单顺序）。
    if pend and any(x.get('sd') for x in pend):
        pend.sort(key=lambda x: 1 if x.get('sd') else 0)

    batch = pend[:args.limit]
    if not batch:
        print('无待办（可执行 0，停放 %d）' % len(parked))
        return 3

    print('本轮处理 %d 篇：' % len(batch))
    for x in batch:
        print('  · [%d] %-38s %s  %s' % (x['score'], x['journal'], x['date'], x['title'][:58]))
    if args.dry_run:
        return 0

    # 单篇在子进程里跑，父进程不预读台账（省 1~2s/轮，也避免两处状态不一致）
    done_ids, results = set(), []
    t_start = time.time()
    for i, item in enumerate(batch, 1):
        if args.budget and (time.time() - t_start) > args.budget:
            log('已用满本轮预算 %d 秒，余下篇目留给下一轮（进度已落盘）' % args.budget)
            break
        log('─' * 58)
        log('[%d/%d] %s | %s' % (i, len(batch), item['journal'], item['title'][:70]))
        # 先落盘「我开始试了」：进程被强杀（额度/网络/工具超时）时，
        # 下一轮从这个痕迹能看出是同一篇反复被杀，而不是凭空少了一篇。
        item['try_started'] = datetime.now().isoformat(timespec='seconds')
        item['try_count'] = int(item.get('try_count', 0)) + 1
        save_manifest(items, backup=False)
        try:
            # 子进程 + 硬超时：单篇卡在验证页/驱动上也不会拖住整轮
            r = process_one_isolated(item, args.item_timeout)
            if isinstance(r, tuple):
                st, note, err = (tuple(r) + ('', ''))[:3]
            else:                              # 兼容旧式两值返回
                st, note, err = r, '', ''
        except Exception as e:
            import traceback
            st, note, err = ('fail', '%s: %s' % (type(e).__name__, str(e)[:140]),
                             'err_exception')
            log('    ✗ 异常：%s' % traceback.format_exc(limit=2).replace('\n', ' | ')[:220])

        if st == 'done':
            done_ids.add(item['id'])
            append_done({
                'id': item['id'], 'journal': item['journal'], 'title': item['title'],
                'url': item['url'], 'doi': item.get('doi', ''), 'date': item['date'],
                'score': item['score'], 'finished_at': datetime.now().isoformat(timespec='seconds'),
                # 移出待爬清单后的去向：待入库（默认）/ 已在本轮入库
                'ingest': ('inline' if ingest_policy.enabled('history') else 'pending'),
                'note': note,
            })
            items[:] = [x for x in items if x['id'] != item['id']]
            save_manifest(items)               # 立即物理移出，不等整批结束
            log('    ★ 完成并移出清单')
        elif st == 'skip_sd':
            # 暂停期跳过：不记失败、不消耗重试次数，留在清单里
            log('    ⏸ %s（不计失败）' % note)
        else:
            # 唯一的失败记账实现（与调度器共用，见 apply_failure 注释）
            v = apply_failure(item, err, note)
            if v['kind'] == 'permanent':
                log('    ✗ 硬失败 %s（第 %d/%d 次，%s）'
                    % (err or note, v['attempts'], MAX_ATTEMPTS,
                       '已停放（不再自动复活）' if v['parked'] else '下轮重试'))
            elif v['captcha']:
                log('    %s 人机挑战 %d/%d 次（%s）'
                    % ('⛔' if v['newly_parked'] else '⟳', v['captcha'], CAPTCHA_MAX,
                       '转人工补缺，不再自动重试' if v['newly_parked']
                       else '冷却 %d 分钟后再复核一次' % (v['cd'] // 60)))
            else:
                log('    ⟳ 软失败 %s（第 %d/%d 次，冷却 %d 分钟后重试%s）'
                    % (err or note, v['soft'], MAX_SOFT_FAILS, v['cd'] // 60,
                       '；累计过多已停放，%d 小时后可复活' % REVIVE_HOURS
                       if v['parked'] else ''))
            save_manifest(items, backup=False)   # 每篇立刻回写：崩了也不丢计数
        results.append((item['journal'], st, note[:80]))

        if i < len(batch) and args.gap > 0:
            log('    等 %d 秒后继续下一篇（节流，防人机验证）' % args.gap)
            time.sleep(args.gap)

    # ⚠️ 清单就是进度本身：完成篇在循环里已就地移除，这里不能再重新 load，
    # 否则本轮 attempts / 冷却时间 / 移除记录全部丢失（2026-10-18 踩过）。
    remain = items
    save_manifest(remain)

    st = {}
    if QUEUE_STATE.exists():
        try:
            st = json.loads(QUEUE_STATE.read_text(encoding='utf-8'))
        except Exception:
            st = {}
    today = datetime.now().strftime('%Y-%m-%d')
    if st.get('date') != today:
        st = {'date': today, 'count': 0}
    st['count'] = st.get('count', 0) + len(done_ids)
    st['last_run'] = datetime.now().isoformat(timespec='seconds')
    st['last_done'] = len(done_ids)
    st['remaining'] = len(remain)
    QUEUE_STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding='utf-8')

    n_ok = sum(1 for _, s, _ in results if s == 'done')
    log('=' * 58)
    log('本轮完成 %d / %d，清单剩余 %d（今日累计 %d）'
        % (n_ok, len(batch), len(remain), st['count']))

    # 每轮把「人读清单」刷新成当前剩余（2.5MB 写盘，1 秒级，值得）
    if done_ids:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                'history_manifest', str(SCRIPTS / 'history_manifest.py'))
            hm = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(hm)
            stats = json.loads((STATE / 'history_manifest_stats.json')
                               .read_text(encoding='utf-8'))
            stats['todo'] = len(remain)
            by_j = {}
            for x in remain:
                by_j[x['journal']] = by_j.get(x['journal'], 0) + 1
            stats['by_journal'] = by_j
            (STATE / 'history_manifest_stats.json').write_text(
                json.dumps(stats, ensure_ascii=False, indent=1), encoding='utf-8')
            hm.write_html(remain, stats)
        except Exception as e:
            log('[warn] 清单 HTML 刷新失败（不影响队列）: %s' % str(e)[:90])

    # 索引已在每篇入库/拆 kp 后走 `index_one_entry` 增量写入（秒级可查）。
    # `--rebuild-all` 才做全库重建（约 5 分钟），用于结构性修复后的兜底。
    if n_ok and args.rebuild_all:
        try:
            log('全量重建 GeoKB 索引…')
            p = subprocess.run([PY, str(SCRIPTS / 'rebuild_index.py')],
                               cwd=str(BASE), capture_output=True, text=True,
                               encoding='utf-8', errors='ignore', timeout=1800,
                               creationflags=_NO_WIN)
            tail = ((p.stdout or '') + (p.stderr or '')).strip().splitlines()[-2:]
            log('索引重建 rc=%s | %s' % (p.returncode, ' / '.join(tail)))
        except Exception as e:
            log('索引重建失败（不影响已入库内容）: %s: %s' % (type(e).__name__, str(e)[:100]))
    return 0


if __name__ == '__main__':
    try:
        rc = main()
    finally:
        # 无论正常结束、异常还是被上层 return 打断，都释放单实例锁，
        # 否则下一次运行会被自己上一轮的 pid 挡住 6 小时。
        release_lock()
    sys.exit(rc)
