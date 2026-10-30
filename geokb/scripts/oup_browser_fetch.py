# -*- coding: utf-8 -*-
"""OUP（academic.oup.com）PDF 抓取 —— Cloudflare 托管挑战专用通道（2026-10-17）

为什么单独一个脚本
------------------
`journal_pdf_downloader.dl_oup` 走纯 requests，而 academic.oup.com 挂了
Cloudflare **托管挑战（managed challenge）**：连续请求后落地页直接 403 +
`Just a moment...`（`server: cloudflare` + `cf-ray` 响应头可证），拿不到
`citation_pdf_url`，于是 27 篇 Journal of Petrology 全部失败。
这**不是路径问题**，是反爬。

2026-10-17 实测诊断（重要，别再走弯路）
--------------------------------------
挑战 iframe 位于 `challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/...`，
标题 "Checking your Browser…"，其 DOM 内 **没有任何复选框**
（`input` / `label` / `.cb-lb` / `#challenge-stage` / `[role=checkbox]` 全部 0 个），
唯一可见元素是 body，bounding box = {x:192, y:304, w:300, h:65.6}。
→ 这是**非交互式**托管挑战：Cloudflare 跑 JS 指纹 + 行为打分自动判定，
  **点击无效**，只能让浏览器「等」并且「看起来像人」。

全网调研（2026-10-17）结论：标准 Playwright 会被 Cloudflare 从 **CDP 协议层**
识别（`Runtime.enable` / `Target.setAutoAttach` 等自动化签名），指纹一致、
无鼠标行为、datacenter/境内 IP 信誉低，三重叠加导致永远过不去。
因此本脚本用三件事把分数拉回来：
  1. **Patchright**（协议层 stealth 补丁的 Playwright 分支）：抹掉
     `navigator.webdriver` 与 CDP 自动化签名；
  2. **持久化 profile**（`launch_persistent_context`）：指纹跨运行一致、
     `cf_clearance` 可复用，不是每次全新的匿名上下文；
  3. **真实鼠标轨迹 + 微滚动**：贝塞尔式移动、随机停顿，避免静止指针。

挑战通过后 `cf_clearance` cookie 留在持久化 context 里，同 context 内
后续文章页多数不再弹挑战。

落盘与台账完全复用既有链路，与 `backfill_all_pdfs.py` 一致：
  - 目录/命名：`pdf_downloader._save_pdf(data, journal, title, date, '_fetch')`
  - 台账：`daily_archive_papers.append_index(meta)`（含 eid，journals 页据此挂按钮）
  - 幂等：`_ledger_get()`（精确 + 前缀兜底），随时可中断续跑

用法
----
    python oup_browser_fetch.py                        # Patchright + 有头 Edge（默认）
    python oup_browser_fetch.py --limit 3              # 先试 3 篇
    python oup_browser_fetch.py --engine playwright    # 回退标准 Playwright
    python oup_browser_fetch.py --list                 # 只看篇目

⚠️ 首次运行会弹出 Edge 窗口显示 Cloudflare 验证。本挑战无复选框可点，
   脚本会边等边模拟鼠标；若 Cloudflare 升级为需人工的形式（图片验证等），
   窗口会停着，人工点一下，脚本检测到标题恢复即自动继续。
"""
import argparse
import hashlib
import json
import math
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]          # D:/ZSK/geokb
POSTNEWS = Path('D:/PostNews')
for p in (str(POSTNEWS), str(POSTNEWS / '.workbuddy')):
    if p not in sys.path:
        sys.path.insert(0, p)

import journal_feed                                     # noqa: E402
from daily_archive_papers import (load_index_keys,       # noqa: E402
                                  append_index, _ledger_get, _norm_title)

USER_DIR = BASE / 'state' / 'oup_browser_profile'       # 持久化 profile（cookie/指纹）
REPORT = BASE / 'state' / 'oup_browser_report.json'
TARGET_SOURCE = 'oup'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36')
LAUNCH_ARGS = ['--disable-blink-features=AutomationControlled',
               '--disable-features=IsolateOrigins,site-per-process',
               '--no-sandbox', '--disable-dev-shm-usage']


def log(msg):
    print(msg, flush=True)


# ─── 引擎选择：Patchright（默认，协议层 stealth）───
def get_playwright(engine):
    if engine == 'patchright':
        try:
            from patchright.sync_api import sync_playwright
            log('引擎: Patchright 1.x（协议层 stealth）')
            return sync_playwright
        except ImportError as e:
            log('  [warn] patchright 不可用（%s），回退标准 Playwright' % e)
    from playwright.sync_api import sync_playwright
    log('引擎: 标准 Playwright（无 stealth 补丁，过挑战概率低）')
    return sync_playwright


# ─── Cloudflare 挑战：等 + 拟人化 ────────────────────────────────────────
def _title(page):
    try:
        return page.title() or ''
    except Exception:
        return ''


def is_challenge(page):
    t = _title(page).lower()
    return 'just a moment' in t or 'checking your browser' in t \
        or 'attention required' in t


def _bezier(p0, p1, p2, p3, n):
    """三阶贝塞尔采样，模拟人手的弧线轨迹（而非瞬移）。"""
    pts = []
    for i in range(n + 1):
        t = i / n
        mt = 1 - t
        x = (mt ** 3 * p0[0] + 3 * mt ** 2 * t * p1[0]
             + 3 * mt * t ** 2 * p2[0] + t ** 3 * p3[0])
        y = (mt ** 3 * p0[1] + 3 * mt ** 2 * t * p1[1]
             + 3 * mt * t ** 2 * p2[1] + t ** 3 * p3[1])
        pts.append((x, y))
    return pts


def humanize(page, duration=6.0):
    """在视口内做一组随机弧线鼠标移动 + 微滚动。"""
    vw, vh = 1280, 860
    t_end = time.time() + duration
    while time.time() < t_end:
        x0 = random.randint(200, vw - 200)
        y0 = random.randint(150, vh - 200)
        x3 = random.randint(200, vw - 200)
        y3 = random.randint(150, vh - 200)
        c1 = (x0 + random.randint(-260, 260), y0 + random.randint(-200, 200))
        c2 = (x3 + random.randint(-260, 260), y3 + random.randint(-200, 200))
        try:
            for (x, y) in _bezier((x0, y0), c1, c2, (x3, y3),
                                  random.randint(14, 26)):
                page.mouse.move(x, y)
                time.sleep(random.uniform(0.006, 0.028))
            if random.random() < 0.35:
                page.mouse.wheel(0, random.randint(30, 130))
            if random.random() < 0.18:
                time.sleep(random.uniform(0.25, 0.9))     # 随机停顿
        except Exception:
            return


def wait_out_challenge(page, budget=210):
    """非交互式托管挑战：没有复选框可点，只能边拟人化边等它自己过。

    同时保留兜底——万一 Cloudflare 退回交互式（出现复选框），照样点掉。
    """
    t0 = time.time()
    clicks = 0
    while time.time() - t0 < budget:
        if not is_challenge(page):
            return True
        # 兜底：若出现交互式复选框，用真实坐标点它
        for f in page.frames:
            if 'challenges.cloudflare.com' not in (f.url or ''):
                continue
            for sel in ('input[type=checkbox]', 'label', '.cb-lb', '#challenge-stage'):
                try:
                    el = f.query_selector(sel)
                    box = el.bounding_box() if el else None
                except Exception:
                    el, box = None, None
                if el and box and box.get('width', 0) > 4:
                    cx = box['x'] + box['width'] / 2
                    cy = box['y'] + box['height'] / 2
                    try:
                        page.mouse.move(cx - 60, cy - 30, steps=12)
                        page.mouse.move(cx, cy, steps=12)
                        page.mouse.click(cx, cy)
                        clicks += 1
                        log('    → 出现交互式复选框，已用真实坐标点击（第 %d 次）' % clicks)
                    except Exception:
                        pass
                    time.sleep(3)
        humanize(page, duration=random.uniform(4.0, 7.0))
    return not is_challenge(page)


def page_html(page, tries=6):
    """取页面 HTML；挑战通过瞬间页面常在导航中，直接 content() 会抛
    "Unable to retrieve content because the page is navigating"。重试 + 退避。"""
    last = None
    for i in range(tries):
        try:
            return page.content()
        except Exception as e:
            last = e
            time.sleep(1.5 + i)
    raise last


def fetch_one_oup(page, ctx, url):
    """返回 (status, pdf_url, note, data)。"""
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=90000)
    except Exception as e:
        return 'err_network', '', 'goto: %s' % type(e).__name__, None

    # 挑战可能反复弹，最多等两轮
    for rnd in range(2):
        if not is_challenge(page):
            break
        log('    落地页在 Cloudflare 挑战中（第 %d 轮），拟人化等待…' % (rnd + 1))
        if not wait_out_challenge(page, budget=180):
            return 'blocked_captcha', page.url, 'Cloudflare 挑战未通过', None
        try:
            page.wait_for_load_state('domcontentloaded', timeout=30000)
        except Exception:
            pass
        time.sleep(2)

    if is_challenge(page):
        return 'blocked_captcha', page.url, 'Cloudflare 挑战未通过', None

    try:
        html = page_html(page)
    except Exception as e:
        return 'err_network', page.url, 'content: %s' % str(e)[:70], None

    m = (re.search(r'name="citation_pdf_url"[^>]+content="([^"]+)"', html)
         or re.search(r'content="([^"]+)"[^>]+name="citation_pdf_url"', html))
    if not m:
        low = html.lower()
        if 'purchase' in low or 'subscribe' in low or 'sign in' in low:
            return 'paywalled', page.url, '无 citation_pdf_url（需订阅）', None
        return 'err_parse', page.url, 'no citation_pdf_url', None
    pdf_url = m.group(1).replace('&amp;', '&')

    try:
        resp = ctx.request.get(pdf_url, headers={'Referer': page.url},
                               timeout=180000)
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


# ─── 收集待抓文章 ────────────────────────────────────────────────────────
def collect_todo():
    feed = journal_feed.build_feed(str(POSTNEWS))
    ledger = journal_feed.load_ledger()
    seen = {}
    for g in feed.get('groups') or []:
        for j in g.get('journals') or []:
            for a in j.get('articles') or []:
                k = a.get('url') or a.get('title')
                if k and k not in seen:
                    a['_jn'] = j.get('name') or j.get('name_cn') or a.get('journal')
                    seen[k] = a
    for a in (feed.get('today') or {}).get('items') or []:
        k = a.get('url') or a.get('title')
        if k and k not in seen:
            a['_jn'] = a.get('journal') or '(today)'
            seen[k] = a

    import journal_pdf_fetch as jpf
    todo, already = [], 0
    for a in seen.values():
        if jpf.source_of(a.get('url') or '') != TARGET_SOURCE:
            continue
        if journal_feed.lookup_ledger(ledger, _norm_title(a.get('title'))) is not None:
            already += 1
            continue
        u = (a.get('url') or '').strip()
        if not u.startswith('http'):
            continue
        todo.append({'title': (a.get('title') or '').strip(),
                     'url': u,
                     'journal': a['_jn'] or a.get('journal') or 'unknown',
                     'date': (a.get('obtained_at') or '')[:10]
                             or datetime.now().date().isoformat()})
    todo.sort(key=lambda x: (x['journal'], x['title']))
    return todo, already, len(seen)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--headless', action='store_true')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--sleep', type=float, default=5.0)
    ap.add_argument('--engine', default='patchright',
                    choices=['patchright', 'playwright'])
    args = ap.parse_args()

    log('=' * 74)
    log('OUP 浏览器通道抓取  %s' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    log('=' * 74)
    todo, already, total = collect_todo()
    log('共享层文章 %d 篇 · %s 已归档 %d · 待抓 %d' % (total, TARGET_SOURCE, already, len(todo)))
    # 防御：依赖缺失会让 source_of 全返回 unknown，从而「0 待抓」被误当成抓完了
    if not todo and not already and total > 0:
        log('!! 警告：共 %d 篇文章却无一被判为 %s —— journal_pdf_downloader '
            '动态加载可能失败（缺 requests 等依赖），请先修依赖再跑' % (total, TARGET_SOURCE))
        return 3
    if args.limit and len(todo) > args.limit:
        todo = todo[:args.limit]
        log('限量: %d' % len(todo))
    if args.list:
        for t in todo:
            log('  [%s] %s | %s' % (t['journal'][:24], t['title'][:52], t['url'][:60]))
        return 0
    if not todo:
        log('无可抓内容')
        return 0

    index_keys = load_index_keys()
    log('台账已有 %d 条' % len(index_keys))
    log('-' * 74)

    sync_playwright = get_playwright(args.engine)
    ok_n = fail_n = skip_n = 0
    fails, by_journal = [], {}

    with sync_playwright() as p:
        # 持久化 context：指纹跨运行一致，cf_clearance 可复用
        USER_DIR.mkdir(parents=True, exist_ok=True)
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(USER_DIR),
            channel='msedge',
            headless=args.headless,
            no_viewport=True,
            args=LAUNCH_ARGS,
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
            viewport={'width': 1280, 'height': 860},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        log('持久化 profile: %s' % USER_DIR)

        # ① 先在 OUP 首页把挑战引出来并等它过，拿 cf_clearance
        log('打开 OUP 首页触发并通过 Cloudflare 挑战…')
        try:
            page.goto('https://academic.oup.com/', wait_until='domcontentloaded',
                      timeout=90000)
        except Exception as e:
            log('  [warn] 首页打开失败: %s' % str(e)[:90])
        if is_challenge(page):
            log('  检测到非交互式托管挑战（无复选框），拟人化等待自动通过…')
            if not wait_out_challenge(page, budget=240):
                log('  [warn] 首页挑战未通过；仍继续逐篇尝试')
            else:
                log('  ✓ 挑战已通过，cf_clearance 已入持久化 profile')
        else:
            log('  ✓ 首页无挑战')
        try:
            page.wait_for_load_state('domcontentloaded', timeout=20000)
        except Exception:
            pass
        humanize(page, duration=3.0)

        t0 = time.time()
        for i, it in enumerate(todo, 1):
            log('  [%d/%d] %-22s %s' % (i, len(todo), it['journal'][:22], it['title'][:46]))
            if _ledger_get(index_keys, it['title']) is not None:
                log('        → 已在 NAS，跳过')
                skip_n += 1
                continue

            # 单篇全程护栏：任何未预期异常只记这一篇失败，不掀翻整轮
            try:
                st, pdf_url, note, data = fetch_one_oup(page, ctx, it['url'])
            except Exception as e:                                  # noqa: BLE001
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

        try:
            ctx.close()
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
            {'ts': datetime.now().isoformat(), 'ok': ok_n, 'skipped': skip_n,
             'fail': fail_n, 'by_journal': by_journal, 'failures': fails},
            ensure_ascii=False, indent=1), encoding='utf-8')
        log('\n报告已存: %s' % REPORT)
    except OSError as e:
        log('  [warn] 报告写入失败: %s' % e)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
