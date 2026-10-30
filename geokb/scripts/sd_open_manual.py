#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""打开 ScienceDirect 文章页，把窗口交给用户手动过人机验证。

与 browser_verify_fetch.py **同一套启动方式**（patchright + 真实 Edge +
借用的真实 profile 副本 `state/browser_profiles/_real_edge`，含真实 cookie），
区别只有一点：它不自动闯关、不 handoff、不重试，只做两件事 ——

  1. 把目标页停在用户面前，让真人自己点 Turnstile / 自己点下载；
  2. 监听 download 事件，用户点「下载 PDF」时文件直接落到
     `state/inbox/`，随后交给 `manual_inbox.py` 归档。

用法
----
    python scripts/sd_open_manual.py                     # 默认打开 S0169136823004328
    python scripts/sd_open_manual.py --url <文章页> --minutes 40

退出方式：用户在窗口里关掉浏览器，或时间到，脚本自动退出。
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SCRIPTS = BASE / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from browser_verify_fetch import LAUNCH_ARGS, get_sync_playwright  # noqa: E402

PROFILE = BASE / 'state' / 'browser_profiles' / '_real_edge'
INBOX = BASE / 'state' / 'inbox'
DEFAULT_URL = ('https://www.sciencedirect.com/science/article/pii/'
               'S0169136823004328')


def log(msg):
    print(msg, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default=DEFAULT_URL)
    ap.add_argument('--minutes', type=int, default=40, help='窗口保持时长')
    args = ap.parse_args()

    if not PROFILE.is_dir():
        log(f'[abort] 找不到借用的真实 profile：{PROFILE}')
        log('        先跑一次 browser_verify_fetch.py --real-profile edge 生成副本')
        return 2
    INBOX.mkdir(parents=True, exist_ok=True)

    import pdf_downloader as pdl
    saved = []

    def on_download(dl):
        try:
            raw = dl.suggested_filename or 'sd_manual.pdf'
            name = pdl.safe_name(raw, 90)
            if not name.lower().endswith('.pdf'):
                name += '.pdf'
            dest = INBOX / name
            if dest.exists():
                dest = INBOX / f'{dest.stem}_{datetime.now():%H%M%S}.pdf'
            dl.save_as(str(dest))
            saved.append(dest)
            log(f'  ↓ 已存到收件箱：{dest}')
        except Exception as e:  # noqa: BLE001
            log(f'  [warn] 下载保存失败：{type(e).__name__}: {e}')

    def hook(pg):
        try:
            pg.on('download', on_download)
        except Exception:
            pass

    sync_playwright = get_sync_playwright('auto')
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            channel='msedge',
            headless=False,
            args=LAUNCH_ARGS,
            chromium_sandbox=True,      # 不加 --no-sandbox，避免顶部黄条
            accept_downloads=True,
            no_viewport=True,           # 用真实窗口尺寸，和本人环境一致
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
        )
        ctx.on('page', hook)
        for pg in ctx.pages:
            hook(pg)

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        log(f'[open] {args.url}')
        try:
            page.goto(args.url, wait_until='domcontentloaded', timeout=90000)
        except Exception as e:  # noqa: BLE001
            log(f'  [warn] goto 未完成（不影响手点）：{type(e).__name__}: {e}')
        log(f'[ready] 窗口已交给用户，保持 {args.minutes} 分钟')
        log('        过完验证 → 看到 PDF → 点下载，文件会自动进 state/inbox/')

        deadline = time.time() + args.minutes * 60
        try:
            while time.time() < deadline:
                time.sleep(2.0)
                # 浏览器被用户关掉 → 退出
                alive = False
                try:
                    for _ in ctx.pages:
                        alive = True
                        break
                except Exception:
                    alive = False
                if not alive:
                    log('[exit] 浏览器已关闭')
                    break
                for pg in list(ctx.pages):
                    hook(pg)
                if saved:
                    log(f'[done] 已收 {len(saved)} 个文件')
                    saved.clear()
        except KeyboardInterrupt:
            log('[exit] 手动中断')
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
