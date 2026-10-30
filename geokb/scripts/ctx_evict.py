#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""GeoKB 入库「上下文出栈」工具。

作用：一篇论文 commit 之后，把它在磁盘上一切「可被再次读入上下文」的副本
      （wip 笔记 / payload / 全文缓存 / readable 派生缓存）从热区移到冷区，
      使后续任何一步都无法再把它读回上下文，从而给长跑腾出空间。
      只移动、不删除；需要复核时 restore 可原样拉回。

为什么需要它：agent 的上下文是追加式的，已经进入的 token 无法被任何工具删除。
      唯一可靠的办法是「不让它进来」+「让残留在磁盘上的诱因消失」。

用法：
  python scripts/ctx_evict.py status                    # 热区/冷区统计
  python scripts/ctx_evict.py hot                       # 列出仍在热区、但已入库的篇（理想应为 0）
  python scripts/ctx_evict.py evict <sha12> [...]       # 出栈指定篇
  python scripts/ctx_evict.py evict --committed --limit 30   # 批量出栈已入库篇
  python scripts/ctx_evict.py restore <sha12> [...]     # 需要复核时恢复（verify 报错等）
  python scripts/ctx_evict.py --dry-run evict --committed --limit 5

硬约束：
  - 只对「已生成 content/papers/paper-<sha12>.md」的篇出栈；未入库一律拒绝。
  - 不删除任何文件；移动目标 state/_cold/{wip,text}/，账本 state/ctx_evicted.jsonl。
"""
import argparse
import datetime
import glob
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, 'state')
WIP = os.path.join(STATE, 'agent_ingest_wip')
TEXT = os.path.join(STATE, 'text')
PAPERS = os.path.join(ROOT, 'content', 'papers')
COLD = os.path.join(STATE, '_cold')
COLD_WIP = os.path.join(COLD, 'wip')
COLD_TEXT = os.path.join(COLD, 'text')
LEDGER = os.path.join(STATE, 'ctx_evicted.jsonl')
SHA_RE = r'^[0-9a-f]{12}$'


def sha_from_name(name):
    """从 text/ 或 wip/ 的文件名提取 sha12。
    兼容 0018b5f3....txt / _0018b5f3...._merged.txt / payload-0018b5f3....json"""
    b = name.lstrip('_')
    if b.startswith('payload-'):
        b = b[8:]
    m = re.match(r'^([0-9a-f]{12})', b)
    return m.group(1) if m else None


def imported(sha):
    return os.path.exists(os.path.join(PAPERS, 'paper-%s.md' % sha))


def evicted(sha):
    return os.path.exists(os.path.join(COLD_WIP, sha + '.md')) or \
        os.path.exists(os.path.join(COLD_TEXT, sha + '.txt'))


def hot_files(sha):
    """该篇在热区里所有可被读入上下文的副本"""
    out = []
    p = os.path.join(WIP, sha + '.md')
    if os.path.exists(p):
        out.append(('wip', p))
    for p in glob.glob(os.path.join(WIP, 'payload-%s*.json' % sha)):
        out.append(('wip', p))
    for pat in (sha + '*.txt', '_' + sha + '*.txt'):
        for p in glob.glob(os.path.join(TEXT, pat)):
            out.append(('text', p))
    return out


def all_hot_shas():
    shas = set()
    for d in (WIP, TEXT):
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if not name.endswith(('.md', '.json', '.txt')):
                continue
            s = sha_from_name(name)
            if s:
                shas.add(s)
    return sorted(shas)


def log(rec):
    with open(LEDGER, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def do_evict(sha, dry):
    if not imported(sha):
        print('  SKIP %s 未入库（无 paper-%s.md），拒绝出栈' % (sha, sha))
        return 0
    files = hot_files(sha)
    if not files:
        print('  --   %s 热区已空（之前已出栈）' % sha)
        return 0
    moved = []
    for kind, p in files:
        dst_dir = COLD_WIP if kind == 'wip' else COLD_TEXT
        dst = os.path.join(dst_dir, os.path.basename(p))
        print('  ->   %-5s %s' % (kind, os.path.relpath(p, ROOT)))
        moved.append(os.path.relpath(p, ROOT))
        if dry:
            continue
        os.makedirs(dst_dir, exist_ok=True)
        if os.path.exists(dst):
            os.remove(dst)
        shutil.move(p, dst)
    if not dry:
        log({'ts': datetime.datetime.now().isoformat(timespec='seconds'),
             'sha12': sha, 'moved': len(moved), 'files': moved})
    print('  OK   %s 出栈 %d 个副本' % (sha, len(moved)))
    return len(moved)


def cmd_status():
    hot = all_hot_shas()
    hot_imported = [s for s in hot if imported(s)]
    pending = [s for s in hot if not imported(s)]
    cold = []
    for d in (COLD_WIP, COLD_TEXT):
        if os.path.isdir(d):
            for p in os.listdir(d):
                s = sha_from_name(p)
                if s:
                    cold.append(s)
    cold = sorted(set(cold))
    print('热区篇数 %d  （已入库 %d / 未入库待办 %d）' % (len(hot), len(hot_imported), len(pending)))
    print('冷区篇数 %d' % len(cold))
    if hot_imported:
        print('⚠ 已入库却仍在热区：%d 篇 → %s' % (len(hot_imported), ' '.join(hot_imported[:12])))
    if pending:
        print('待办（正常，勿出栈）：%s' % ' '.join(pending[:12]))


def cmd_hot():
    hot = [s for s in all_hot_shas() if imported(s)]
    if not hot:
        print('热区无已入库残留（干净）')
        return
    for s in hot:
        print('%s  %d 个副本' % (s, len(hot_files(s))))


def cmd_evict(a):
    shas = list(a.shas or [])
    if a.committed or not shas:
        cand = [s for s in all_hot_shas() if imported(s)]
        if a.limit:
            cand = cand[:a.limit]
        shas += cand
    if not shas:
        print('没有可出栈的篇（热区已入库残留为 0）')
        return
    total = 0
    for s in shas:
        total += do_evict(s, a.dry_run)
    print('合计出栈副本 %d 个%s' % (total, '（dry-run 未实际移动）' if a.dry_run else ''))


def cmd_restore(a):
    for sha in a.shas:
        n = 0
        for src_dir, dst_dir in ((COLD_WIP, WIP), (COLD_TEXT, TEXT)):
            if not os.path.isdir(src_dir):
                continue
            for name in os.listdir(src_dir):
                if sha not in name:
                    continue
                os.makedirs(dst_dir, exist_ok=True)
                shutil.move(os.path.join(src_dir, name), os.path.join(dst_dir, name))
                n += 1
        print('  OK   %s 恢复 %d 个副本' % (sha, n))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['status', 'hot', 'evict', 'restore'])
    ap.add_argument('shas', nargs='*')
    ap.add_argument('--committed', action='store_true', help='批量：所有已入库且仍在热区的篇')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    if a.cmd == 'status':
        cmd_status()
    elif a.cmd == 'hot':
        cmd_hot()
    elif a.cmd == 'evict':
        cmd_evict(a)
    else:
        if not a.shas:
            print('restore 需要 sha12')
            return 1
        cmd_restore(a)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (BrokenPipeError, OSError) as e:  # 管道被 head 提前关闭（Windows Errno 22）
        if getattr(e, 'errno', None) in (22, 32):
            os._exit(0)
        raise
