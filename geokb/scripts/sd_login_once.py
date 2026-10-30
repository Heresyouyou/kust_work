# -*- coding: utf-8 -*-
"""ScienceDirect 一次性授权：在受控 profile 里完成机构认证，之后长期复用。

为什么需要这个脚本（2026-10-20 实测结论）：

  Edge/Chrome 127+ 的 cookie 全部变成 `v20`（App-Bound Encryption），密钥绑定
  在浏览器安装路径 + 原 profile 上。把 profile 复制到别处，Edge 解不开就**直接
  丢弃** —— 实测 469 条只剩 18 条，`SD_REMOTEACCESS`（机构远程访问凭证）消失。
  所以「复制真实 profile 带登录态」这条路被浏览器安全设计堵死，不是配置问题。

  正确做法相反：让认证**发生在这个 profile 里**。你在下面的窗口里走一次
  学校/CARSI 认证，浏览器会用正确的密钥把 cookie 写进这个 profile，
  以后所有抓取都复用这一份，不必再碰你日常的 Edge。

用法：
    venv/Scripts/python.exe scripts/sd_login_once.py            # 默认 profile
    venv/Scripts/python.exe scripts/sd_login_once.py --profile sciencedirect_auth

完成后 cookie 持久化在 state/browser_profiles/<profile>/，由
browser_verify_fetch.py --profile <profile> 复用。
"""
import argparse
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'scripts'))

# 2026-10-20 实测更正：昆工对 ScienceDirect 走的是 **SD_REMOTEACCESS 远程访问**，
# 不是 CARSI 的 cra-ch-clearance（那个 cookie 从头到尾没出现过）。
# 只认 SD_REMOTEACCESS + sd_session_id 两个即可判定"机构权限已就位"。
KEY_COOKIES = ('SD_REMOTEACCESS', 'sd_session_id')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--profile', default='sciencedirect_auth')
    ap.add_argument('--url', default='https://www.sciencedirect.com/')
    ap.add_argument('--timeout', type=int, default=900,
                    help='等待你完成认证的最长秒数（默认 900 = 15 分钟）')
    ap.add_argument('--channel', default='msedge')
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    prof = BASE / 'state' / 'browser_profiles' / args.profile
    prof.mkdir(parents=True, exist_ok=True)
    print('=' * 66)
    print('ScienceDirect 一次性机构认证')
    print('  profile :', prof)
    print('  说明    : 在弹出的窗口里完成学校认证（CARSI / 校园网登录）')
    print('            认证成功后本脚本自动识别并保存，直接关窗口不用管')
    print('=' * 66, flush=True)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(prof),
            channel=args.channel,
            headless=False,
            no_viewport=True,
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
            accept_downloads=True,
            args=['--disable-blink-features=AutomationControlled'],
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(args.url, wait_until='domcontentloaded', timeout=90000)
        except Exception as e:  # noqa: BLE001
            print('打开首页失败：%s' % str(e)[:100])

        t0 = time.time()
        found = {}
        last = ''
        while time.time() - t0 < args.timeout:
            try:
                cks = {c['name']: c for c in ctx.cookies()}
            except Exception:
                print('  [warn] 浏览器已关闭，退出')
                break
            found = {k: cks[k] for k in KEY_COOKIES if k in cks}
            msg = '已识别: %s' % (', '.join(sorted(found)) or '（暂无）')
            if msg != last:
                print('  [%4ds] %s' % (int(time.time() - t0), msg), flush=True)
                last = msg
            if len(found) >= len(KEY_COOKIES):
                print('  ✓ 机构凭证已就位')
                break
            time.sleep(5)

        # 保存一份可读快照，便于排查（不用于注入）
        try:
            snap = {
                'ts': time.time(),
                'profile': str(prof),
                'keys': {k: v.get('value') for k, v in found.items()},
                'all_names': sorted({c['name'] for c in ctx.cookies()}),
            }
            (BASE / 'state' / 'browser_state').mkdir(parents=True, exist_ok=True)
            (BASE / 'state' / 'browser_state' / 'sd_auth_state.json').write_text(
                json.dumps(snap, ensure_ascii=False, indent=1), encoding='utf-8')
        except Exception:  # noqa: BLE001
            pass

        ok = len(found) >= len(KEY_COOKIES)
        print()
        if ok:
            print('结果: 成功。后续用 --profile %s 复用，不必再认证。' % args.profile)
        else:
            print('结果: 未检测到机构凭证。可能认证未完成，或该校走 IP 授权（无需 cookie）。')
        print('3 秒后关闭浏览器…', flush=True)
        time.sleep(3)
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
