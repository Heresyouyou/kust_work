# -*- coding: utf-8 -*-
"""本地译文回收 —— 把超过 N 小时的译文三版本搬去 NAS，然后删除本地副本。

独立于 Flask 运行（定时任务直接调它即可，不必等 Web 服务在线）。

用法
    python tools/nas_reaper.py --status              # 只看状态，不动任何文件
    python tools/nas_reaper.py --dry-run             # 预演：会归档，但不删本地
    python tools/nas_reaper.py                       # 正式：归档 + 删除（默认 48h）
    python tools/nas_reaper.py --hours 0             # 无视时间，立即回收全部（测试用）
    python tools/nas_reaper.py --json                # 机器可读输出（给自动化用）

★ 定时执行（Windows 任务计划程序，不依赖任何外部调度服务）
    由 tools/schedule/reap_daily.bat 调用本脚本，每日本地时间 09:00 / 21:00 各跑一次。
    安装：tools/schedule/install_task.bat    卸载：tools/schedule/uninstall_task.bat
    日志：logs/nas_reaper.log（超过 2 MB 自动轮转，保留 3 份）
    本脚本自带运行锁（data/nas_reaper.run.lock），上一次没跑完时不会重入；
    锁带 1 小时陈旧保护 —— 上次进程被强杀留下死锁也能自愈。

★ 安全保证
  删除前逐个校验 NAS 副本：exists() 且 size 完全一致才删本地。
  校验不过的保留在本地并写进 report['kept']，绝不为了腾空间冒险。

★ 幂等
  重复执行无害：NAS 上已有同尺寸副本就跳过传输，本地没有旧文件就什么都不做。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 定时任务环境下 stdout 可能是 cp936，打印中文/emoji 会 UnicodeEncodeError 直接崩掉。
# 这里强制按 UTF-8 输出并对无法编码的字符降级，保证「再难看也不中断回收」。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[union-attr]
    except Exception:
        pass

import nas_archive as N  # noqa: E402

# ── 定时任务相关常量（路径一律基于项目根，绝不受 cwd 影响）──
BASE = Path(__file__).resolve().parent.parent
UPLOADS = BASE / 'uploads'
TRANSLATED = BASE / 'translated'
LOG_PATH = BASE / 'logs' / 'nas_reaper.log'
RUN_LOCK = BASE / 'data' / 'nas_reaper.run.lock'
LOG_MAX_BYTES = 2_000_000   # 单份日志上限 2 MB
LOG_BACKUPS = 3             # 轮转保留份数
RUN_LOCK_TTL = 3600         # 锁超过 1 小时视为陈旧（上次进程被强杀），自动接管


def _mb(n):
    return f'{n / 1e6:.1f} MB'


def _rotate_log():
    """日志超过上限时轮转：.3 丢弃，.2→.3，.1→.2，当前→.1"""
    try:
        if not LOG_PATH.exists() or LOG_PATH.stat().st_size < LOG_MAX_BYTES:
            return
        for i in range(LOG_BACKUPS, 0, -1):
            src = LOG_PATH.with_suffix(f'.log.{i - 1}') if i > 1 else LOG_PATH
            dst = LOG_PATH.with_suffix(f'.log.{i}')
            if i == LOG_BACKUPS and dst.exists():
                dst.unlink()
            if src.exists():
                src.replace(dst)
    except Exception as e:
        print(f'[warn] 日志轮转失败：{e}')


def _write_log(text: str):
    """追加一段带时间戳的运行记录到 logs/nas_reaper.log"""
    try:
        _rotate_log()
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with LOG_PATH.open('a', encoding='utf-8') as f:
            f.write(f'\n──────── {ts} ────────\n{text.rstrip()}\n')
    except Exception as e:
        print(f'[warn] 写日志失败：{e}')


def _acquire_run_lock(force: bool = False) -> bool:
    """进程级防重入：上一次回收没结束就直接跳过。返回 True 表示拿到锁。"""
    try:
        RUN_LOCK.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return True  # 建不了目录也别拦着跑
    try:
        if RUN_LOCK.exists():
            age = time.time() - RUN_LOCK.stat().st_mtime
            if not force and age < RUN_LOCK_TTL:
                print(f'[skip] 上一次回收仍在运行（锁 {age:.0f}s 前创建），本次跳过。'
                      f'确属死锁可加 --force 或删除 {RUN_LOCK}')
                return False
            RUN_LOCK.unlink()
        fd = os.open(str(RUN_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(f'pid={os.getpid()} at={datetime.now().isoformat(timespec="seconds")}')
        return True
    except FileExistsError:
        print('[skip] 另一个回收进程刚抢到锁，本次跳过。')
        return False
    except Exception as e:
        print(f'[warn] 加锁失败（继续运行）：{e}')
        return True


def _release_run_lock():
    try:
        if RUN_LOCK.exists():
            RUN_LOCK.unlink()
    except Exception:
        pass


def print_status():
    s = N.status(upload_dir=UPLOADS, translated_dir=TRANSLATED)
    print('══ NAS 文献仓库状态 ══')
    print(f"  NAS 根目录     : {s['nas_root'] or '（不可达）'}")
    print(f"  NAS 可用       : {'是' if s['nas_available'] else '否 ← 本次不会删除任何本地文件'}")
    print(f"  NAS 存量       : 原文 {s['nas_counts'].get('original')} / "
          f"中文 {s['nas_counts'].get('mono')} / 双语 {s['nas_counts'].get('dual')}")
    print(f"  本地存量       : {s['local_counts'] or {}}")
    print(f"  本地最老译文   : {s['oldest_local_age_hours']} 小时前"
          if s['oldest_local_age_hours'] is not None else '  本地最老译文   : （无）')
    print(f"  索引条目       : {s['index_items']} 条  ({s['index_path']})")
    print(f"  索引更新时间   : {s['index_updated_at']}")
    print(f"  回收阈值       : {s['ttl_hours']} 小时")
    return s


def main():
    ap = argparse.ArgumentParser(description='本地译文 48h 回收到 NAS')
    ap.add_argument('--hours', type=float, default=None,
                    help=f'超过多少小时算过期（默认 {N.DEFAULT_TTL_HOURS}）')
    ap.add_argument('--dry-run', action='store_true', help='只报告，不删除本地文件')
    ap.add_argument('--no-delete', action='store_true', help='只归档到 NAS，不删本地')
    ap.add_argument('--status', action='store_true', help='只看状态，不执行回收')
    ap.add_argument('--json', action='store_true', help='输出 JSON（供自动化解析）')
    ap.add_argument('--force', action='store_true', help='忽略运行锁强制执行')
    ap.add_argument('--no-log', action='store_true', help='不写入 logs/nas_reaper.log')
    args = ap.parse_args()

    if args.status:
        s = print_status()
        if args.json:
            print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0

    out = []

    def say(line=''):
        print(line)
        out.append(line)

    if not _acquire_run_lock(args.force):
        return 0

    rc = 0
    try:
        ttl = N.DEFAULT_TTL_HOURS if args.hours is None else args.hours
        delete = not (args.dry_run or args.no_delete)

        report = N.reap(UPLOADS, TRANSLATED, ttl_hours=ttl,
                        dry_run=args.dry_run, delete=delete)

        if args.json:
            say(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if not report.get('error') else 1

        say('══ 本地译文回收报告 ══')
        say(f"  时间           : {report['started_at']}")
        say(f"  NAS 根目录     : {report['nas_root'] or '（不可达）'}")
        say(f"  回收阈值       : {report['ttl_hours']} 小时")
        say(f"  模式           : {'预演（不删除）' if not delete else '正式（归档后删除）'}")
        if report.get('error'):
            say('')
            say(f"  ⚠️  {report['error']}")
            return 1

        say('')
        say(f"  扫描译文组     : {report['scanned']}")
        say(f"  未过期跳过     : {report['skipped_recent']}")
        say(f"  过期           : {report['expired']}")
        orphans = report.get('orphan_originals') or []
        n_orphan_del = sum(1 for o in orphans if o.get('deleted'))
        say(f"  已删除         : {len(report['deleted'])} 个"
            f"（另清理重复上传的原件 {n_orphan_del} 个），释放 {_mb(report['freed_bytes'])}")
        say(f"  保留本地       : {len(report['kept'])} 个（校验未通过 / 预演）")
        if orphans:
            say(f"  重复原件       : {len(orphans)} 个"
                f"（同一篇文献被重复上传留下的副本）")

        for d in report['deleted']:
            say(f"      🗑  {d['kind']:8s} {d['name']}")
        for o in orphans:
            tag = '🗑' if o.get('deleted') else '⏸'
            say(f"      {tag}  {'original':8s} {o['name']}"
                + ('' if o.get('deleted') else f"  ← {o.get('reason')}"))
        if report['kept']:
            say('  ── 保留原因 ──')
            for k in report['kept']:
                say(f"      ⏸  {k['kind']:8s} {k['name']}  ← {k['reason']}")
        return 0
    except Exception as e:  # 兜底：任何异常都要留下痕迹并释放锁
        say(f'  ❌ 回收异常：{type(e).__name__}: {e}')
        rc = 1
        return rc
    finally:
        if not args.no_log:
            _write_log('\n'.join(out))
        _release_run_lock()


if __name__ == '__main__':
    sys.exit(main())
