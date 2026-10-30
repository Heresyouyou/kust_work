# -*- coding: utf-8 -*-
"""OUP 抓取 —— 挂载**用户本人日常 Edge** 的通道（2026-10-17）

为什么需要这个脚本
----------------
`oup_browser_fetch.py`（Patchright + 自建持久化 profile）能让 **Cloudflare 首页挑战**
通过，这是标准 Playwright 从未做到的；但文章页随后在 Cloudflare 挑战 ↔ OUP 自家
「请稍候…」之间**无限循环**，300s 到不了正文，reload 无效，`cf_clearance` +
真实 UA 喂纯 requests 仍 403。

剩余唯一没试过的路径：用**用户日常使用的 Edge**（真实 profile：真实 cookie、
浏览历史、扩展、指纹一致性）。这需要带 `--remote-debugging-port` 重启 Edge，
**会中断用户当前的 Edge 会话** —— 因此本脚本只在用户明确同意后才运行。

流程
----
  1. 结束所有 msedge.exe 进程；
  2. 以**默认 profile** + `--remote-debugging-port=9222` 重新启动 Edge
     （不加 --user-data-dir，正是为了让 cookie/历史/扩展原样保留）；
  3. Patchright `connect_over_cdp()` attach，取默认 context（用户的所有 tab 都在，
     **只新建自己的 page，不碰、不关用户 tab**）；
  4. OUP 首页 → 拟人化等挑战 → 逐篇抓 `citation_pdf_url` → `context.request` 下载；
  5. 收尾：只关自己建的 page，Edge 保持运行（调试端口仍开着，用户重开 Edge 即可恢复）。

落盘/台账/幂等与 `backfill_all_pdfs.py` 完全一致（复用同一套函数）。

用法
----
    python oup_real_browser_fetch.py                # 全量 27 篇
    python oup_real_browser_fetch.py --limit 3      # 先试 3 篇
    python oup_real_browser_fetch.py --port 9222    # 自定义调试端口
    python oup_real_browser_fetch.py --keep-edge    # 不自动结束 Edge（已开好端口时）
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# ⚠️ 本机环境设了 HTTP_PROXY=http://127.0.0.1:57129 且没有 NO_PROXY，
# 导致所有本地请求（含 CDP 调试端口探测）都被代理转走，代理自己连不到
# 127.0.0.1:9222 → 返回 502「upstream connect failed / os error 10061」，
# 表现为「调试端口 25s 内未就绪」而 Edge 其实已经起来了。
# 必须在任何网络调用之前把 localhost 加进绕过列表。
os.environ['NO_PROXY'] = '127.0.0.1,localhost,' + os.environ.get('NO_PROXY', '')
os.environ['no_proxy'] = os.environ['NO_PROXY']

BASE = Path(__file__).resolve().parents[1]          # D:/ZSK/geokb
POSTNEWS = Path('D:/PostNews')
for p in (str(POSTNEWS), str(POSTNEWS / '.workbuddy')):
    if p not in sys.path:
        sys.path.insert(0, p)

import journal_feed                                     # noqa: E402
from daily_archive_papers import (load_index_keys,       # noqa: E402
                                  append_index, _ledger_get, _norm_title)
from oup_browser_fetch import (is_challenge, wait_out_challenge,   # noqa: E402
                               humanize, page_html)

REPORT = BASE / 'state' / 'oup_browser_report.json'
EDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
CDP_URL = 'http://127.0.0.1:9222'


def log(msg):
    print(msg, flush=True)


# ─── Edge 重启与 CDP attach ────────────────────────────────────────────
def kill_edge():
    import psutil
    killed = []
    for p in psutil.process_iter(['pid', 'name']):
        try:
            if (p.info['name'] or '').lower() != 'msedge.exe':
                continue
            pid = p.info['pid']
            p.terminate()
            killed.append(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    # 等它们真正退出（Edge 有关联进程树，给足时间）
    deadline = time.time() + 25
    while time.time() < deadline:
        alive = [p.info['pid'] for p in psutil.process_iter(['pid', 'name'])
                 if (p.info['name'] or '').lower() == 'msedge.exe']
        if not alive:
            break
        time.sleep(1)
    log('已结束 Edge 进程 %d 个' % len(killed))


def launch_edge_with_cdp(port):
    import subprocess
    import urllib.request
    log('以默认 profile + 调试端口 %d 重启 Edge…' % port)
    subprocess.Popen([EDGE, '--remote-debugging-port=%d' % port, 'about:blank'],
                     close_fds=True)
    # 显式禁用代理：本机 HTTP_PROXY 会把手伸向 localhost，见文件顶部说明
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = 'http://127.0.0.1:%d/json/version' % port
    for _ in range(45):
        try:
            with opener.open(url, timeout=5) as r:
                j = json.loads(r.read().decode('utf-8', 'ignore'))
                log('  ✓ CDP 已就绪：%s' % str(j.get('Browser'))[:70])
                return True
        except Exception:
            time.sleep(1)
    log('  [warn] 调试端口 45s 内未就绪')
    return False


def fetch_one(page, ctx, url):
    """落地页 → citation_pdf_url → PDF bytes。返回 (status, pdf_url, note, data)。"""
    import re
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=90000)
    except Exception as e:
        return 'err_network', '', 'goto: %s' % type(e).__name__, None

    for rnd in range(2):
        if not is_challenge(page):
            break
        log('    页面在 Cloudflare 挑战中（第 %d 轮），拟人化等待…' % (rnd + 1))
        if not wait_out_challenge(page, budget=200):
            return 'blocked_captcha', page.url, 'Cloudflare 挑战未通过', None
        try:
            page.wait_for_load_state('domcontentloaded', timeout=30000)
        except Exception:
            pass
        time.sleep(2)

    # OUP 自家「请稍候…」中间页：轮询等正文（真实浏览器下应能过）
    html = ''
    t0 = time.time()
    while time.time() - t0 < 180:
        if is_challenge(page):
            if not wait_out_challenge(page, budget=150):
                return 'blocked_captcha', page.url, 'Cloudflare 挑战未通过', None
            continue
        try:
            html = page_html(page, tries=3)
        except Exception:
            time.sleep(2)
            continue
        if 'citation_pdf_url' in html or 'citation_title' in html:
            break
        try:
            t = page.title() or ''
        except Exception:
            t = ''
        if t and '请稍候' not in t and time.time() - t0 > 25:
            log('    标题=%r，仍未取到 citation_pdf_url（%.0fs）' % (t[:40], time.time() - t0))
        humanize(page, duration=3.0)
        time.sleep(2)

    if 'citation_pdf_url' not in html:
        return 'err_parse', page.url, '正文未就绪（请稍候页未消失）', None

    m = (re.search(r'name="citation_pdf_url"[^>]+content="([^"]+)"', html)
         or re.search(r'content="([^"]+)"[^>]+name="citation_pdf_url"', html))
    if not m:
        return 'err_parse', page.url, 'no citation_pdf_url', None
    pdf_url = m.group(1).replace('&amp;', '&')

    try:
        resp = ctx.request.get(pdf_url, headers={'Referer': page.url}, timeout=180000)
    except Exception as e:
        return 'err_network', pdf_url, '%s: %s' % (type(e).__name__, str(e)[:80]), None
    if resp.status != 200:
        return 'http_%d' % resp.status, pdf_url, '', None
    try:
        data = resp.body()
    except Exception as e:
        return 'err_network', pdf_url, 'body: %s' % str(e)[:80], None
    if data[:4] != b'%PDF':
        return 'not_pdf', pdf_url, resp.headers.get('content-type', '')[:60], None
    return 'ok', pdf_url, '', data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--sleep', type=float, default=6.0)
    ap.add_argument('--port', type=int, default=9222)
    ap.add_argument('--keep-edge', action='store_true',
                    help='不自动结束/重启 Edge（端口已由别人开好时用）')
    args = ap.parse_args()

    log('=' * 74)
    log('OUP 抓取 · 挂载用户真实 Edge  %s' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    log('=' * 74)

    todo, already, total = collect = None, None, None
    from oup_browser_fetch import collect_todo
    todo, already, total = collect_todo()
    log('共享层文章 %d 篇 · oup 已归档 %d · 待抓 %d' % (total, already, len(todo)))
    if not todo and not already and total > 0:
        log('!! 警告：共 %d 篇文章却无一被判为 oup —— journal_pdf_downloader 动态加载'
            '可能失败（缺 requests 等依赖），先修依赖再跑' % total)
        return 3
    if args.limit and len(todo) > args.limit:
        todo = todo[:args.limit]
        log('限量: %d' % len(todo))
    if not todo:
        log('无可抓内容')
        return 0

    index_keys = load_index_keys()
    log('台账已有 %d 条' % len(index_keys))
    log('-' * 74)

    # ① 准备带调试端口的 Edge（用户日常 profile）
    if not args.keep_edge:
        kill_edge()
        if not launch_edge_with_cdp(args.port):
            log('无法启动带调试端口的 Edge，终止')
            return 4
        time.sleep(3)

    from patchright.sync_api import sync_playwright
    ok_n = fail_n = skip_n = 0
    fails, by_journal = [], {}

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp('http://127.0.0.1:%d' % args.port)
        except Exception as e:
            log('CDP attach 失败: %s: %s' % (type(e).__name__, str(e)[:120]))
            return 4
        log('✓ 已 attach 到用户 Edge')
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        log('  默认 context，用户 tab 数: %d（不会碰它们）'
            % len([pg for pg in ctx.pages]))
        page = ctx.new_page()

        # ② 首页过挑战
        log('打开 OUP 首页触发并通过 Cloudflare 挑战…')
        try:
            page.goto('https://academic.oup.com/', wait_until='domcontentloaded',
                      timeout=90000)
        except Exception as e:
            log('  [warn] 首页打开失败: %s' % str(e)[:90])
        if is_challenge(page):
            log('  检测到挑战，拟人化等待（真实 profile，通过率应更高）…')
            if not wait_out_challenge(page, budget=280):
                log('  [warn] 首页挑战未通过；仍继续逐篇尝试')
            else:
                log('  ✓ 挑战已通过')
        else:
            log('  ✓ 首页无挑战')
        humanize(page, duration=3.0)

        # ③ 逐篇抓取
        t0 = time.time()
        for i, it in enumerate(todo, 1):
            log('  [%d/%d] %-22s %s' % (i, len(todo), it['journal'][:22], it['title'][:46]))
            if _ledger_get(index_keys, it['title']) is not None:
                log('        → 已在 NAS，跳过')
                skip_n += 1
                continue
            try:
                st, pdf_url, note, data = fetch_one(page, ctx, it['url'])
            except Exception as e:                                      # noqa: BLE001
                st, pdf_url, note, data = ('err_exception', '',
                                           '%s: %s' % (type(e).__name__,
                                                       str(e)[:80]), None)
            if st != 'ok':
                log('        → %s: %s' % (st, note[:90]))
                fails.append({'journal': it['journal'], 'title': it['title'],
                              'url': it['url'], 'status': st, 'note': note})
                fail_n += 1
                time.sleep(args.sleep)
                continue

            import pdf_downloader as pdl
            try:
                jpath = pdl._save_pdf(data, it['journal'] or 'unknown',
                                      it['title'], it['date'], '_fetch')
            except Exception as e:
                log('        → save_err: %s: %s' % (type(e).__name__, str(e)[:70]))
                fails.append({'journal': it['journal'], 'title': it['title'],
                              'url': it['url'], 'status': 'save_err', 'note': str(e)[:90]})
                fail_n += 1
                continue
            actual = Path(jpath).stat().st_size
            if actual != len(data):
                log('        → verify_err: %d != %d' % (actual, len(data)))
                fails.append({'journal': it['journal'], 'title': it['title'],
                              'url': it['url'], 'status': 'verify_err',
                              'note': 'size %d != %d' % (actual, len(data))})
                fail_n += 1
                continue

            meta = {'title': it['title'], 'url': it['url'], 'journal': it['journal'],
                    'date': it['date'], 'pdf_url': pdf_url, 'repo_path': jpath,
                    'size': len(data), 'source': 'fetch',
                    'eid': 'paper-%s' % hashlib.sha1(data).hexdigest()[:12],
                    'indexed_at': datetime.now().isoformat()}
            append_index(meta)
            index_keys[_norm_title(it['title'])] = meta
            ok_n += 1
            by_journal[it['journal']] = by_journal.get(it['journal'], 0) + 1
            log('        ✓ %s（%.1f MB）' % (jpath, len(data) / 1048576))
            time.sleep(args.sleep)

        # 只关自己建的 page；用户 tab 与 Edge 本身保持不动
        try:
            page.close()
            log('已关闭自建 page（用户 tab 未动，Edge 仍在运行）')
        except Exception:
            pass

    dt = time.time() - t0
    log('-' * 74)
    log('完成（%.1f 分钟）：成功 %d · 已存在 %d · 失败 %d' % (dt / 60, ok_n, skip_n, fail_n))
    for j, n in sorted(by_journal.items(), key=lambda x: -x[1]):
        log('  %-34s %d' % (j[:34], n))
    if fails:
        log('\n失败明细：')
        for f in fails:
            log('  - [%s] %s | %s: %s' % (f['journal'][:20], f['title'][:40],
                                          f['status'], str(f['note'])[:60]))
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(
            {'ts': datetime.now().isoformat(), 'mode': 'real-edge-cdp',
             'ok': ok_n, 'skipped': skip_n, 'fail': fail_n,
             'by_journal': by_journal, 'failures': fails},
            ensure_ascii=False, indent=1), encoding='utf-8')
        log('\n报告已存: %s' % REPORT)
    except OSError as e:
        log('  [warn] 报告写入失败: %s' % e)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
