# -*- coding: utf-8 -*-
"""模拟浏览器人机验证通道（browser verification fetch）  2026-10-18

定位
----
纯 requests 通道在挂人机验证的源站（Cloudflare 托管挑战 / Turnstile / 行为风控）
上必然拿不到 `citation_pdf_url`。本模块用**真实浏览器内核 + 持久化指纹 + 拟人化
行为**把「等验证通过」这件事做掉，然后把 PDF 取回来。

它只解决「人机验证」这一类拦截：
    ✓ Cloudflare managed challenge（academic.oup.com 等）
    ✓ 站点自有的人机校验页（标题含 Just a moment / Checking your browser）
    ✗ 付费墙（钱的问题，浏览器也拿不到）
    ✗ 网络层封锁（如本机对某域 :80 端口的沙箱拦截）

反检测要点（参考 Selenium 反检测实践的同类原理，移植到 Playwright）
--------------------------------------------------------------------
1. **隐藏 WebDriver 特征**：`navigator.webdriver` 置 undefined；补 chrome 对象、
   plugins、languages、mediaDevices —— 这些是 BotD / Sannysoft 的直接判据。
2. **持久化 profile**：`launch_persistent_context(user_data_dir=...)`，
   指纹与 `cf_clearance` 跨运行一致。首次过后，同站后续文章大多不再弹挑战。
3. **自动化开关**：`--disable-blink-features=AutomationControlled`。
4. **不伪造 UA**：用 Edge 真实 UA（通道自带）。给 Edge 套一个 Chrome UA 会造成
   UA-CH（client hints）自相矛盾，反而更好被识别 —— 这点比文章里更严格。
5. **拟人化**：贝塞尔鼠标轨迹 + 微滚动 + 随机停顿，避免静止指针与匀速移动。
6. **随机视口**：每次从常见分辨率里挑一个，避免固定 1280×720 的机器味。

⚠️ 已知边界（2026-10-17 实测，别再走弯路）
------------------------------------------
OUP 的挑战是**非交互式**托管挑战：iframe 里没有任何复选框，点击无效，
只能「等 + 看起来像人」。若 Cloudflare 升级为交互式（出现复选框），本模块会
用真实坐标兜底点击；再不行就需要有头模式人工点一下（`--headed`）。

用法
----
    python browser_verify_fetch.py --url <文章页> [--out x.pdf] [--timeout 90]
                                   [--headed] [--profile oup] [--engine auto]

退出码：0 = 拿到 PDF；2 = 失败（JSON 里带 status / note）
stdout 最后一行固定 `RESULT_JSON: {...}`，供上层子进程解析。
"""
import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]          # D:/ZSK/geokb
PROFILE_ROOT = BASE / 'state' / 'browser_profiles'
REPORT = BASE / 'state' / 'browser_verify_report.json'

# ── 反检测注入脚本：在每个新文档执行前落地（文章第 7 步的等价物）─────────
STEALTH_JS = r"""
(() => {
  // ① navigator.webdriver：真实浏览器是 **false**（规范定义），不是 undefined。
  //    写成 undefined 会被 Sannysoft 的「WebDriver (New)」判异常（2026-10-18 实测）。
  try {
    Object.defineProperty(navigator, 'webdriver', { get: () => false });
  } catch (e) {}
  // ② 补 chrome 对象（真实 Chrome/Edge 都有）
  try {
    if (!window.chrome) { window.chrome = {}; }
    if (!window.chrome.runtime) { window.chrome.runtime = {}; }
    if (!window.chrome.app) { window.chrome.app = { isInstalled: false }; }
  } catch (e) {}
  // ③ plugins / mimeTypes 非空（无头浏览器常为空数组）
  try {
    if (navigator.plugins && navigator.plugins.length === 0) {
      const fake = [{ name: 'PDF Viewer' }, { name: 'Chrome PDF Viewer' },
                    { name: 'Chromium PDF Viewer' }];
      Object.defineProperty(navigator, 'plugins', { get: () => fake });
    }
  } catch (e) {}
  // ④ 语言环境与 launched locale 对齐
  try {
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
  } catch (e) {}
  // ⑤ mediaDevices：无摄像头/麦克风的桌面常见，但不能整个缺失
  try {
    if (!navigator.mediaDevices) {
      Object.defineProperty(navigator, 'mediaDevices', {
        get: () => ({ enumerateDevices: () => Promise.resolve([]),
                      getUserMedia: () => Promise.reject(new Error('NotAllowedError')) })
      });
    }
  } catch (e) {}
  // ⑥ permissions.query 的 notification 结果归一（headless 常见偏差）
  try {
    const orig = navigator.permissions && navigator.permissions.query;
    if (orig) {
      navigator.permissions.query = (p) => (
        p && p.name === 'notifications'
          ? Promise.resolve({ state: Notification.permission, onchange: null })
          : orig.call(navigator.permissions, p)
      );
    }
  } catch (e) {}
  // ⑦ WebGL：仅在暴露 SwiftShader（软件渲染）时才伪装成 Intel。
  //    本机真实 GPU 是 NVIDIA ANGLE，硬改 Intel 反而造成指纹自相矛盾。
  try {
    const getParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (p) {
      const v = getParam.call(this, p);
      if (typeof v === 'string' && /swiftshader|software|llvmpipe/i.test(v)) {
        if (p === 37445) { return 'Intel Inc.'; }
        if (p === 37446) { return 'Intel Iris OpenGL Engine'; }
      }
      return v;
    };
  } catch (e) {}
})();
"""

LAUNCH_ARGS = [
    # 2026-10-20 移除 `--disable-blink-features=AutomationControlled`：
    # Chrome/Edge 把它列入"不支持的命令行标志"，浏览器顶部会弹一条
    # 「你使用的是不受支持的命令行标志…这会带来稳定性和安全风险」提示条。
    # 这条提示 = 明晃晃的自动化痕迹（真人不会看到），对 Cloudflare 评分是负分；
    # 而且 patchright 已经在协议层抹掉 navigator.webdriver 等信号，根本不靠这个参数。
    # 结论：留着只有坏处没有好处，删除。
    '--disable-features=IsolateOrigins,site-per-process',
    '--lang=zh-CN',
    '--disable-notifications',
]

VIEWPORTS = [(1366, 768), (1440, 900), (1536, 864), (1600, 900), (1920, 1080)]


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# ── 引擎：Patchright 优先（协议层 stealth），否则标准 Playwright ──────────
def get_sync_playwright(engine):
    if engine in ('auto', 'patchright'):
        try:
            from patchright.sync_api import sync_playwright
            log('引擎: Patchright（协议层 stealth）')
            return sync_playwright
        except ImportError:
            if engine == 'patchright':
                log('  [warn] 指定了 patchright 但不可用，回退 Playwright')
    from playwright.sync_api import sync_playwright
    log('引擎: Playwright（JS 层 stealth 补丁）')
    return sync_playwright


# ── profile 互斥锁：同一 user_data_dir 不能被两个浏览器同时打开 ─────────
def _acquire_profile_lock(profile, wait=90, stale=900):
    PROFILE_ROOT.mkdir(parents=True, exist_ok=True)
    lock = PROFILE_ROOT / ('.%s.lock' % profile)
    t0 = time.time()
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, ('%d' % os.getpid()).encode())
            os.close(fd)
            return lock
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > stale:
                    lock.unlink(missing_ok=True)      # 上次异常退出留下的
                    continue
            except OSError:
                pass
            if time.time() - t0 > wait:
                log('  [warn] profile 锁等待超时，改用临时 profile（cookie 不复用）')
                return None
            time.sleep(2)


def _release_profile_lock(lock):
    if lock is not None:
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            pass


# ── 挑战检测 ────────────────────────────────────────────────────────────
def _title(page):
    try:
        return (page.title() or '').strip()
    except Exception:
        return ''


CHALLENGE_TITLES = ('just a moment', 'checking your browser', 'attention required',
                    'verify you are human', 'ddos-guard')
# 出版社自家的「中转页」：挑战刚过时 OUP 会先显示「请稍候…」，
# 此时既不是挑战页也不是文章页（2026-10-18 实测，会被误判为已完成）。
TRANSITION_TITLES = ('请稍候', 'please wait', 'loading', '正在加载')


def challenge_kind(page):
    """返回挑战类型标识；无挑战返回 ''。"""
    t = _title(page).lower()
    for k in CHALLENGE_TITLES:
        if k in t:
            return k
    try:
        if page.query_selector('#challenge-form, #cf-challenge-running, '
                               '#cf-please-wait, #cf-wrapper, .cf-browser-verification'):
            return 'cf-dom'
        # ── Elsevier / ScienceDirect 的 Turnstile 门（2026-10-20 实测）──
        # 该页 **title 仍是 "ScienceDirect" 或中文「请稍候…」**，正文才是
        # "Are you a robot?"，容器是 #captcha-box，里面塞着 cf-turnstile-response
        # 和一个 cf-chl-widget-* iframe。只按标题判会整个漏掉 —— 这就是此前
        # 把 ScienceDirect 误判成「IP 无权、任何指纹 403」的直接原因。
        # ⚠ 必须判**可见性**：文章页 DOM 里也可能残留 hidden 的 #captcha-box，
        # 不加 is_visible 会让每一篇都卡在「假挑战」里直到超时。
        try:
            box = page.query_selector('#captcha-box')
            if box and box.is_visible():
                return 'cf-turnstile-dom'
        except Exception:
            pass
        for f in page.frames:
            if 'challenges.cloudflare.com' in (f.url or ''):
                return 'cf-iframe'
        try:
            body = (page.inner_text('body') or '')[:900].lower()
            if 'are you a robot' in body or 'captcha challenge below' in body:
                return 'robot-gate'
        except Exception:
            pass
    except Exception:
        pass
    return ''


def is_transition(page):
    t = _title(page).lower()
    return any(k in t for k in TRANSITION_TITLES)


def has_article(page):
    """页面是否已经是文章页（有引文元数据）。"""
    try:
        return bool(page.query_selector(
            'meta[name="citation_pdf_url"], meta[name="citation_title"], '
            'meta[name="citation_doi"]'))
    except Exception:
        return False


def _clear_checkbox(page):
    """若出现交互式复选框，用真实坐标点掉（2026-10-18 实测 CF 会间歇切形态）。"""
    hit = False
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
                cx, cy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
                try:
                    page.mouse.move(cx - 60, cy - 30, steps=12)
                    page.mouse.move(cx, cy, steps=10)
                    page.mouse.click(cx, cy)
                    log('    → 出现交互式复选框，已用真实坐标点击')
                    hit = True
                except Exception:
                    pass
                time.sleep(2.5)
                break
        if hit:
            break
    if hit:
        return True
    # ── Turnstile 兜底（ScienceDirect / OUP，2026-10-20）────────────────
    # Turnstile 的复选框画在 iframe 内部的 canvas 上，DOM 里常常查不到
    # input[type=checkbox]。此时只能按 **iframe 元素的真实坐标**点它的
    # 左上角区域 —— 复选框就固定在那个位置（约内边距 30px 处）。
    try:
        cands = page.query_selector_all('iframe[id^=cf-chl-widget], '
                                        'iframe[src*="turnstile"]')
    except Exception:
        cands = []
    for el in (cands or [])[:2]:
        try:
            box = el.bounding_box()
        except Exception:
            box = None
        if not box or box.get('width', 0) < 20:
            continue
        cx, cy = box['x'] + 30, box['y'] + 30
        try:
            page.mouse.move(cx - 70, cy - 40, steps=14)
            page.mouse.move(cx, cy, steps=10)
            page.mouse.click(cx, cy)
            log('    → 检测到 Turnstile，已点 iframe 内复选框坐标')
            hit = True
        except Exception:
            pass
        time.sleep(2.5)
        break
    return hit


def _bezier(p0, p1, p2, p3, n):
    pts = []
    for i in range(n + 1):
        t = i / n
        mt = 1 - t
        pts.append((
            mt ** 3 * p0[0] + 3 * mt ** 2 * t * p1[0] + 3 * mt * t ** 2 * p2[0] + t ** 3 * p3[0],
            mt ** 3 * p0[1] + 3 * mt ** 2 * t * p1[1] + 3 * mt * t ** 2 * p2[1] + t ** 3 * p3[1],
        ))
    return pts


def humanize(page, duration=5.0, vw=1440, vh=900):
    """弧线鼠标 + 微滚动 + 随机停顿（避免静止指针 / 匀速轨迹）。"""
    end = time.time() + duration
    while time.time() < end:
        x0, y0 = random.randint(200, vw - 200), random.randint(140, vh - 220)
        x3, y3 = random.randint(200, vw - 200), random.randint(140, vh - 220)
        c1 = (x0 + random.randint(-240, 240), y0 + random.randint(-180, 180))
        c2 = (x3 + random.randint(-240, 240), y3 + random.randint(-180, 180))
        try:
            for x, y in _bezier((x0, y0), c1, c2, (x3, y3), random.randint(14, 26)):
                page.mouse.move(x, y)
                time.sleep(random.uniform(0.006, 0.026))
            if random.random() < 0.35:
                page.mouse.wheel(0, random.randint(30, 140))
            if random.random() < 0.2:
                time.sleep(random.uniform(0.25, 0.9))
        except Exception:
            return


def wait_until_ready(page, budget):
    """等到「真正的文章页」，而不是「挑战页 / 中转页」。

    三种状态循环出现是常态（实测 OUP）：
        CF 挑战  →  出版社「请稍候…」 → 又一轮 CF 挑战  →  文章页
    所以不能「标题一变就认为通过」，必须以**引文元数据出现**为终态判据；
    对不带 citation meta 的站点，退化判据是「连续 ~3.6s 无挑战且非中转页」。
    """
    t0 = time.time()
    stable = 0
    stuck = 0
    while time.time() - t0 < budget:
        if has_article(page):
            return True
        if challenge_kind(page):
            _clear_checkbox(page)
            humanize(page, duration=random.uniform(3.0, 5.0))
            stable = stuck = 0
            continue
        if is_transition(page):
            stuck += 1
            if stuck % 12 == 0:            # 约 15s 还卡在中转页 → 重载一次
                log('    中转页停留过久，重载…')
                try:
                    page.reload(wait_until='domcontentloaded', timeout=45000)
                except Exception:
                    pass
            time.sleep(1.2)
            humanize(page, duration=1.2)
            stable = 0
            continue
        stable += 1
        if stable >= 3:
            return True                    # 无 citation meta 的站点：稳定即就绪
        time.sleep(1.2)
    return has_article(page)


# ── PDF 候选提取 ────────────────────────────────────────────────────────
PDF_META_RES = [
    re.compile(r'<meta[^>]+name="citation_pdf_url"[^>]+content="([^"]+)"', re.I),
    re.compile(r'<meta[^>]+content="([^"]+)"[^>]+name="citation_pdf_url"', re.I),
]
PDF_LINK_RE = re.compile(
    r'href="([^"]*(?:/doi/pdf/|/article-pdf/|/pdf/|pdfft\?|\.pdf(?:\?|#|")|/file[A-Za-z]*/)[^"]*)"',
    re.I)


def _clean_url(u):
    """把从 HTML 里抓出来的 URL 修干净。

    ⚠️ 2026-10-20 实测踩坑：正则抓 `citation_pdf_url` / href 时，若值是 HTML
    属性，会连带尾巴一起抓住 —— 实际拿到的是
      `...pdfft?md5=xxx&pid=1-s2.0-S0169136826004932-main.pdf" rel=`
    （`"` 与空格都进了 URL）。带着这种脏 URL 请求必然 302 回文章页，
    表现为「反复重定向」，很容易被误判成反爬或没权限。
    """
    if not u:
        return ''
    u = u.replace('&amp;', '&').replace('&#38;', '&').strip()
    # 在第一个明显的属性边界处截断
    for sep in ('"', "'", '<', '>', ' ', '\t', '\n', '\r'):
        i = u.find(sep)
        if i > 0:
            u = u[:i]
    # `rel=` / `target=` 之类没被引号包住的尾巴
    u = re.split(r'\s+(?:rel|target|title|class|id)=', u)[0]
    return u.rstrip('\\').rstrip()


def extract_pdf_url(html, base_url):
    for rx in PDF_META_RES:
        m = rx.search(html)
        if m:
            u = _clean_url(m.group(1))
            if u:
                return u
    m = PDF_LINK_RE.search(html)
    if m:
        u = _clean_url(m.group(1))
        if u.startswith('//'):
            return 'https:' + u
        if u.startswith('/'):
            m2 = re.match(r'(https?://[^/]+)', base_url)
            return (m2.group(1) if m2 else '') + u
        if u.startswith('http'):
            return u
    return ''


def _real_ua(p, channel, headless):
    """预飞：读真实 UA 并把 `HeadlessChrome` 换成 `Chrome`。

    ⚠️ 2026-10-18 实测：无头 Edge 的 UA 是
    `... HeadlessChrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0` ——
    这一条字符串本身就是 Cloudflare / BotD 的强判据，任何 JS 补丁都救不回来。
    只替换标识、保留真实版本号，避免 UA 与 UA-CH（client hints）版本矛盾。
    有头模式 UA 本来就正常，不做处理。
    """
    if not headless:
        return None
    try:
        b = p.chromium.launch(channel=channel, headless=True, args=LAUNCH_ARGS)
        pg = b.new_page()
        ua = pg.evaluate('navigator.userAgent') or ''
        b.close()
        if 'HeadlessChrome' in ua:
            ua = ua.replace('HeadlessChrome', 'Chrome')
        return ua or None
    except Exception as e:
        log('  [warn] UA 预飞失败（沿用默认 UA）: %s' % str(e)[:80])
        return None


FETCH_JS = r"""
async (u) => {
  try {
    const r = await fetch(u, {credentials: 'include', redirect: 'follow',
      headers: {'Accept': 'application/pdf,*/*'}});
    const ct = (r.headers.get('content-type') || '').toLowerCase();
    const buf = await r.arrayBuffer();
    const b = new Uint8Array(buf);
    const head = Array.from(b.slice(0, 5)).map(x => String.fromCharCode(x)).join('');
    let b64 = '';
    if (head === '%PDF-') {
      b64 = await new Promise(res => {
        const fr = new FileReader();
        fr.onload = () => res(String(fr.result).split(',')[1]);
        fr.readAsDataURL(new Blob([buf]));
      });
    }
    const probe = new TextDecoder('utf-8', {fatal: false}).decode(b.slice(0, 4000));
    return {status: r.status, ctype: ct, len: b.length, head: head,
            paywall: /citation_title|get access|purchase|access this article/i.test(probe),
            b64: b64};
  } catch (e) {
    return {status: 0, ctype: '', len: 0, head: String(e).slice(0, 90),
            paywall: false, b64: ''};
  }
}
"""


def download_pdf(ctx, page, pdf_url, budget, referer=''):
    """取 PDF 字节。返回 (status, data, note)。

    实测（2026-10-18）三条路各有坑，所以按「最像人 → 最省事」三级降级：
      ① **页内 fetch()**：同源、带 cf_clearance、用浏览器自己的 TLS 指纹，
         是最不容易被 Cloudflare 拦的一路；正常时一次成功。
      ② `ctx.request.get`：Playwright 走 Node 的 TLS 栈，指纹与 Chrome 不同，
         CFO 会时通时不通 —— 只作兜底。
      ③ `page.goto()` 的 `resp.body()`：PDF 由内置查看器接管，永远只返回
         348 字节的 HTML 壳，**拿不到 PDF 本体**，仅用于建立 clearance。
    另：直链若回落文章页（响应里含 citation_title / get access），那是
    订阅权限问题，不是反爬 —— 归 paywalled，不要当失败重试烧时间。
    """
    # ⓪ 先确保不在挑战里（挑战中发任何请求都是 403）
    if challenge_kind(page):
        if not wait_until_ready(page, budget):
            return 'blocked_captcha', None, '取 PDF 前挑战未通过'

    # ① 页内 fetch
    try:
        r = page.evaluate(FETCH_JS, pdf_url) or {}
    except Exception as e:
        r = {'status': 0, 'head': '%s: %s' % (type(e).__name__, str(e)[:70]),
             'paywall': False, 'b64': ''}
    if r.get('b64'):
        try:
            import base64
            data = base64.b64decode(r['b64'])
            if data[:4] == b'%PDF':
                return 'ok', data, ''
        except Exception:
            pass
    if r.get('paywall'):
        return 'paywalled', None, 'PDF 直链回落文章页（无订阅权限）'
    if r.get('status') == 403 and challenge_kind(page):
        if not wait_until_ready(page, budget):
            return 'blocked_captcha', None, '页内取 PDF 遇挑战未通过'

    # ② 导航到 PDF：既过挑战，也把 clearance 建到该路径上
    # ⚠ 必须用**新标签页**：ScienceDirect 会把当前 tab 换成 PDF 查看器，
    #    原 page 随即失效 → page.goto 抛 TargetClosedError（2026-10-20 实测，
    #    表现为「已到达文章页 112s」之后立刻 err_network）。
    #    且失败**不能 return**，否则 ③ 的 ctx.request 兜底永远没机会跑。
    for _ in range(2):
        resp = None
        pg = None
        try:
            pg = ctx.new_page()
            resp = pg.goto(pdf_url, wait_until='domcontentloaded', timeout=90000)
        except Exception as e:
            log('    PDF 端点导航失败（%s），转 HTTP 兜底…' % type(e).__name__)
            if pg is not None:
                try:
                    pg.close()
                except Exception:
                    pass
            break
        try:
            ctype = ((resp.headers.get('content-type') if resp else '') or '').lower()
            if resp and 'pdf' in ctype:
                try:
                    data = resp.body()
                except Exception:
                    data = b''
                if data and data[:4] == b'%PDF':        # 偶尔能直接拿到
                    return 'ok', data, ''
                break                                # 壳页面 → 走 ③
            if resp and challenge_kind(pg):
                log('    PDF 端点也弹挑战，继续等…')
                if not wait_until_ready(pg, budget):
                    return 'blocked_captcha', None, 'PDF 端点挑战未通过'
                time.sleep(2)
                continue
            try:
                html = resp.body() if resp else b''
            except Exception:
                html = b''
            if b'citation_title' in html or b'citation_pdf_url' in html:
                return 'paywalled', None, 'PDF 直链回落文章页（无订阅权限）'
            return 'not_pdf', None, ('http_%s %s' % (resp.status if resp else '?', ctype))[:70]
        finally:
            if pg is not None:
                try:
                    pg.close()
                except Exception:
                    pass

    # ③ clearance 已建立 → ctx.request 兜底
    try:
        r2 = ctx.request.get(pdf_url, headers={
            'Referer': referer or page.url,
            'Accept': 'application/pdf,*/*',
        }, timeout=180000)
    except Exception as e:
        return 'err_network', None, 'pdf get: %s' % str(e)[:90]
    if r2.status != 200:
        return 'http_%d' % r2.status, None, ''
    data = r2.body()
    if data[:4] == b'%PDF':
        return 'ok', data, ''
    if b'citation_title' in data[:4000]:
        return 'paywalled', None, 'PDF get 回落文章页（无订阅权限）'
    return 'not_pdf', None, (r2.headers.get('content-type') or '')[:60]


# ── 借用本机真实浏览器 profile（2026-10-20）──────────────────────────
# 用户明确要求「用本地真实浏览器」：干净 profile 的 Turnstile 评分偏低，
# 反复点也不过（实测 headless 点十余次仍 blocked_captcha）。真实 profile
# 自带真实指纹、浏览历史、已登录态与既有的 cf_clearance，通过率显著更高。
#
# 不能直接指向真实 user-data-dir —— 浏览器在运行时会独占锁（SingletonLock），
# 第二个进程启动即崩。所以复制一份必要子项到 state/browser_profiles/_real_*/
# 再启动。Edge 的 cookie 用 DPAPI 按「同机同用户」加密，复制后同一账户可解。
REAL_PROFILE_ROOTS = {
    'edge': r'%LOCALAPPDATA%\Microsoft\Edge\User Data',
    'chrome': r'%LOCALAPPDATA%\Google\Chrome\User Data',
}
_REAL_SUBITEMS = [
    'Local State',
    'Default/Preferences',
    'Default/Cookies',
    'Default/Network/Cookies',
    'Default/Web Data',
    'Default/Local Storage',
    'Default/Session Storage',
]


def _copy_with_wait(s, d, wait=120):
    """复制单个子项；被浏览器独占锁住时轮询等待解锁。

    Edge/Chrome 运行期间 `Default/Network/Cookies` 是**独占锁**（copyfile 直接
    Permission denied，copy2 报 WinError 32，连 sqlite immutable 都打不开），
    所以借用真实 profile 必须让用户先把浏览器全部关掉。这里最多等 wait 秒。
    """
    import shutil
    import time as _t
    t0 = _t.time()
    warned = False
    while True:
        try:
            if s.is_dir():
                if d.exists():
                    shutil.rmtree(d, ignore_errors=True)
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(s, d)          # 不用 copy2：元数据写入会被拒
            return True
        except Exception as e:  # noqa: BLE001
            if not warned:
                log('  [lock] %s 被占用，等待浏览器关闭（最多 %ds）…' % (s.name, wait))
                warned = True
            if _t.time() - t0 > wait:
                log('  [warn] %s 等待超时：%s' % (s.name, str(e)[:60]))
                return False
            _t.sleep(2)


def _borrow_real_profile(kind, refresh=True, wait=120):
    """把真实浏览器 profile 的必要子项复制一份，返回副本目录（失败返回 None）。

    只复制 cookie / 存储 / 首选项，不复制缓存与扩展 —— 副本体积可控（几十 MB），
    且足够让 Cloudflare 看到一套连贯的真实浏览记录。
    """
    from pathlib import Path as _P
    root = _P(os.path.expandvars(REAL_PROFILE_ROOTS.get(kind, '')))
    if not root.is_dir():
        log('  [warn] 找不到真实 %s profile：%s' % (kind, root))
        return None
    dst = PROFILE_ROOT / ('_real_' + kind)
    dst.mkdir(parents=True, exist_ok=True)
    copied = 0
    have_cookie = False
    for rel in _REAL_SUBITEMS:
        s = root / rel
        if not s.exists():
            continue
        ok = _copy_with_wait(s, dst / rel, wait)
        if ok:
            copied += 1
            if s.name == 'Cookies':
                have_cookie = True
    log('  已借用真实 %s profile：%d/%d 项 → %s' % (kind, copied, len(_REAL_SUBITEMS), dst))
    # ⚠️ Cookies 是唯一携带登录态的子项，也是唯一被浏览器独占锁住的。
    # 它没复制到时，副本 = 「有浏览历史但没登录」的空壳，跑下去只会得到
    # 误导性的 blocked_captcha（16:22 实测就是这样白跑一轮）。必须硬失败。
    if not have_cookie:
        log('  [abort] Cookie 数据库没能复制到 —— 请完全退出 %s（含托盘/后台进程）后重试' % kind)
        return None
    return dst


def _browser_cookies(ctx, want=('sciencedirect', 'elsevier')):
    """取出与目标站相关的 cookie（含 cf_clearance / SD_REMOTEACCESS / EUID）。"""
    out = {}
    try:
        for c in ctx.cookies():
            dom = (c.get('domain') or '').lower()
            if any(k in dom for k in want):
                out[c['name']] = c['value']
    except Exception:  # noqa: BLE001
        pass
    return out


def _drop_proxy_env():
    """curl_cffi(libcurl) 会读 HTTP(S)_PROXY，而 trust_env/proxies 都拦不住它。
    唯一有效做法是进程内把变量摘掉（GEOKB_USE_PROXY=1 时保留）。"""
    if os.environ.get('GEOKB_USE_PROXY'):
        return {}
    back = {}
    for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy',
              'https_proxy', 'all_proxy'):
        if k in os.environ:
            back[k] = os.environ.pop(k)
    return back


def handoff_http(ctx, page, pdf_url, referer, save=True):
    """把浏览器已建立的 clearance 交给 curl_cffi 直连取 PDF。

    2026-10-20 实测：ScienceDirect 的 pdfft 端点会让 Edge **整个进程退出**，
    不是关标签页 —— 连保活页都挡不住（浏览器的 PDF 查看器接管 + 沙箱重构）。
    浏览器内下载这条路因此不可靠。而 cf_clearance 只绑 IP + UA，这两项在
    handoff 瞬间完全一致，用 curl_cffi 的 chrome 指纹带 cookie 直连反而更稳，
    也天然适合批量复用（一次过验证，有效期内的篇目全走 HTTP）。
    """
    try:
        import curl_cffi.requests as creq
    except Exception as e:  # noqa: BLE001
        return None, 'no_curl_cffi: %s' % str(e)[:50]
    try:
        ua = page.evaluate('navigator.userAgent') or ''
    except Exception:  # noqa: BLE001
        ua = ''
    cks = _browser_cookies(ctx)
    if save and cks:
        try:
            p = BASE / 'state' / 'browser_state'
            p.mkdir(parents=True, exist_ok=True)
            (p / 'cookies_sciencedirect.json').write_text(
                __import__('json').dumps(
                    {'ua': ua, 'ts': time.time(), 'cookies': cks},
                    ensure_ascii=False), encoding='utf-8')
        except Exception:  # noqa: BLE001
            pass
    back = _drop_proxy_env()
    try:
        s = creq.Session(impersonate='chrome')
        s.headers.update({
            'User-Agent': ua or s.headers.get('User-Agent', ''),
            'Accept': 'application/pdf,text/html;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': referer or '',
        })
        for k, v in cks.items():
            try:
                s.cookies.set(k, v, domain='.sciencedirect.com')
            except Exception:  # noqa: BLE001
                pass
        r = s.get(pdf_url, allow_redirects=True, timeout=120)
        body = r.content or b''
        if body[:4] == b'%PDF':
            return body, 'http_%d %sB' % (r.status_code, len(body))
        if b'citation_title' in body[:6000] or b'Get Access' in body[:6000]:
            return None, 'paywalled_http_%d' % r.status_code
        return None, 'http_%d %sB 非PDF(%s)' % (
            r.status_code, len(body), (r.headers.get('content-type') or '')[:40])
    except Exception as e:  # noqa: BLE001
        return None, 'handoff_exc: %s' % str(e)[:80]
    finally:
        for k, v in back.items():
            os.environ[k] = v


def _wait_no_browser(kind, wait=180, kill_first=False):
    """等本机浏览器进程全部退出（真实 profile 被独占，必须先清空）。

    kill_first：先温和终止残留进程再等。Edge 的「启动加速」会在关闭窗口后
    用 `--no-startup-window /prefetch:5` 立刻拉起一个后台实例（2026-10-20
    实测 26 → 12，关窗后 4 分钟内又复活），不清掉就永远等不到 0。
    """
    import time as _t
    pat = {'edge': 'msedge.exe', 'chrome': 'chrome.exe'}.get(kind, 'msedge.exe')
    try:
        import psutil
    except Exception:  # noqa: BLE001
        return True

    def _list():
        out = []
        for pr in psutil.process_iter(['name']):
            try:
                if (pr.info['name'] or '').lower() == pat:
                    out.append(pr)
            except Exception:  # noqa: BLE001
                pass
        return out

    if kill_first:
        ps = _list()
        if ps:
            log('  终止 %d 个残留 %s 进程（你已退出浏览器，这里只是清后台）' % (len(ps), pat))
        for pr in ps:
            try:
                pr.terminate()
            except Exception:  # noqa: BLE001
                pass
        for pr in ps:
            try:
                pr.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    pr.kill()
                except Exception:  # noqa: BLE001
                    pass
        _t.sleep(1)

    t0 = _t.time()
    warned = False
    while True:
        n = len(_list())
        if n == 0:
            if warned:
                log('  [ok] 浏览器进程已清空')
            return True
        if not warned:
            log('  [lock] 还有 %d 个 %s 进程，等待你完全退出 Edge（最多 %ds）…'
                % (n, pat, wait))
            warned = True
        if _t.time() - t0 > wait:
            log('  [abort] 仍有 %d 个 %s 进程未退出' % (n, pat))
            return False
        _t.sleep(2)


def run(args):
    started = time.time()
    out = {'ok': False, 'status': 'init', 'pdf_url': '', 'length': 0,
           'title': '', 'challenge': '', 'elapsed': 0.0, 'note': ''}

    src_py = sys.executable
    out['python'] = src_py
    sync_playwright = get_sync_playwright(args.engine)
    vw, vh = random.choice(VIEWPORTS)
    profile_dir = None
    # ── 真实 profile「原地」模式（2026-10-20 用户要求：直接用本人权限）──
    # 不复制、不搬运，直接把自动化挂到本机真实 User Data 上：
    # 登录态、cf_clearance、指纹、扩展全是本人那一套，最忠实。
    # 代价：必须先完全退出该浏览器，且运行期间不能自己再打开。
    if getattr(args, 'real_live', ''):
        kind = args.real_live
        root = Path(os.path.expandvars(REAL_PROFILE_ROOTS.get(kind, '')))
        if not root.is_dir():
            out['status'] = 'no_real_profile'
            out['note'] = '找不到真实 %s profile: %s' % (kind, root)
            return out
        if not _wait_no_browser(kind, getattr(args, 'wait_unlock', 180),
                                kill_first=getattr(args, 'kill_browser', False)):
            out['status'] = 'browser_busy'
            out['note'] = '真实 %s 仍在运行，无法独占 profile' % kind
            return out
        profile_dir = root
        args.profile = '_live_' + kind
        out['real_live'] = str(root)
        log('  使用真实 %s profile（原地，不复制）：%s' % (kind, root))
    elif getattr(args, 'real_profile', ''):
        # ⚠️ 必须先清进程再复制：Edge 运行期间 Default/Network/Cookies 被独占锁，
        # 复制不到就退化成「有浏览历史、没登录态」的空壳。2026-10-20 因为
        # --kill-browser 只挂在 real-live 分支，这里连空跑两轮，全部结论无效。
        _wait_no_browser(args.real_profile, getattr(args, 'wait_unlock', 120),
                         kill_first=getattr(args, 'kill_browser', False))
        profile_dir = _borrow_real_profile(
            args.real_profile, wait=getattr(args, 'wait_unlock', 120))
        if profile_dir is None:
            # 绝不允许悄悄退化成临时干净 profile —— 那样跑出来的结果没有意义
            out['status'] = 'no_real_cookie'
            out['note'] = ('真实 %s 的 Cookie 数据库没能复制到，'
                           '请完全退出浏览器后重试' % args.real_profile)
            return out
        args.profile = '_real_' + args.real_profile      # 走持久化锁与同一目录
    if profile_dir is None and args.profile:
        profile_dir = PROFILE_ROOT / args.profile
    lock = None
    tmp_ctx = None

    with sync_playwright() as p:
        ua = _real_ua(p, args.channel, not args.headed)
        kwargs = dict(
            channel=args.channel,
            headless=not args.headed,
            args=LAUNCH_ARGS,
            # 2026-10-20：Playwright 默认 chromium_sandbox=False → 会带 --no-sandbox 启动，
            # Edge 顶部就出现「你使用的是不受支持的命令行标志：--no-sandbox」提示条。
            # 这条提示本身是自动化痕迹（真实用户不会看到），且关掉渲染进程沙箱 =
            # 一份可被探测的异常指纹。对 Turnstile 评分不利，所以显式打开沙箱。
            chromium_sandbox=True,
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
            viewport={'width': vw, 'height': vh},
            accept_downloads=True,
        )
        if ua and not getattr(args, 'real_live', ''):
            kwargs['user_agent'] = ua
            out['ua'] = ua
        if getattr(args, 'real_live', ''):
            # 真实 profile 下不要锁视口，用真实窗口尺寸，否则和本人环境不一致
            kwargs.pop('viewport', None)
            kwargs['no_viewport'] = True
        if profile_dir is not None:
            lock = _acquire_profile_lock(args.profile)
            profile_dir.mkdir(parents=True, exist_ok=True)
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir), **kwargs)
            out['profile'] = str(profile_dir)
        else:
            browser = p.chromium.launch(channel=args.channel,
                                        headless=not args.headed, args=LAUNCH_ARGS,
                                        chromium_sandbox=True)
            ctx = browser.new_context(locale='zh-CN', timezone_id='Asia/Shanghai',
                                      viewport={'width': vw, 'height': vh},
                                      accept_downloads=True)
            tmp_ctx = browser
            out['profile'] = '(temp)'
        ctx.add_init_script(STEALTH_JS)
        try:
            ctx.set_extra_http_headers({'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'})
        except Exception:
            pass

        page = ctx.pages[0] if getattr(ctx, 'pages', None) else ctx.new_page()
        # ── 保活页改为「按需创建」（2026-10-20 修正）──────────────────
        # 原先一进来就 new_page() 开一个 about:blank，目的是防
        # `launch_persistent_context` 在最后一个 page 关闭时让整个浏览器退出。
        # 副作用：窗口里凭空多一个空白标签 —— 用户会看到，而且真实用户开一个
        # 文章链接很少是「两个标签」；同时多占一个渲染进程。
        # 现在 PDF 默认走 curl_cffi handoff，浏览器根本不碰 PDF 查看器，
        # 不需要提前钉住 context。只有真要走浏览器内兜底时再补（见下方下载段）。
        try:
            page.goto(args.url, wait_until='domcontentloaded', timeout=args.goto_timeout * 1000)
        except Exception as e:
            out.update(status='err_network', note='%s: %s' % (type(e).__name__, str(e)[:120]))
            _finish(ctx, tmp_ctx, lock, out, started)
            return out

        kind = challenge_kind(page)
        if kind:
            out['challenge'] = kind
            log('  检测到人机验证（%s），拟人化等待（挑战 ↔ 中转页循环）…' % kind)

        budget = max(25, args.timeout - int(time.time() - started))
        if not wait_until_ready(page, budget):
            still = challenge_kind(page)
            out.update(status='blocked_captcha' if still else 'err_timeout',
                       note='%ds 内未到达文章页（挑战=%s，标题=%s）'
                            % (budget, still or '无', _title(page)[:40]),
                       title=_title(page))
            _finish(ctx, tmp_ctx, lock, out, started)
            return out
        log('  ✓ 已到达文章页（%.0fs）' % (time.time() - started))
        if out['challenge']:
            out['challenge'] += ':passed'

        # 就绪后页面可能仍在导航，等一拍再取 DOM
        try:
            page.wait_for_load_state('domcontentloaded', timeout=20000)
        except Exception:
            pass
        time.sleep(random.uniform(0.8, 1.6))

        # 直接落在 PDF 上（url 本身就是 pdf）
        try:
            ctype = page.evaluate("document.contentType || ''") or ''
        except Exception:
            ctype = ''
        if 'pdf' in ctype.lower():
            html = ''
        else:
            try:
                html = page.content()
            except Exception:
                html = ''
        out['title'] = _title(page)

        pdf_url = ''
        if html:
            # 元数据可能比 DOM 晚一拍（实测 OUP 偶发），轮询几次再放弃
            for _ in range(4):
                pdf_url = extract_pdf_url(html, page.url)
                if pdf_url:
                    break
                time.sleep(1.5)
                try:
                    html = page.content()
                except Exception:
                    break
        if not pdf_url and not html:
            pdf_url = page.url                    # 本身就是 PDF
            out['note'] = 'page is pdf'

        if not pdf_url:
            low = (html or '').lower()
            if any(k in low for k in ('purchase', 'subscribe', 'sign in to',
                                      'access this content', 'rent this article')):
                out.update(status='paywalled', note='页面无 PDF 链接（需订阅/购买）')
            else:
                out.update(status='err_parse', note='页面无 citation_pdf_url / PDF 链接')
            _finish(ctx, tmp_ctx, lock, out, started)
            return out

        out['pdf_url'] = pdf_url
        article_url = page.url
        budget = max(20, args.timeout - int(time.time() - started))
        # ── 优先 handoff：浏览器只负责过验证，PDF 交给 HTTP ───────────
        # 依据（2026-10-20 用户实测样本 S0169136826004932）：真实 PDF 落在
        # `pdf.sciencedirectassets.com/.../main.pdf?X-Amz-Signature=...`，
        # 是 5 分钟有效的签名直链，**不走 Cloudflare**。所以只要先拿到
        # pdfft 的 302 目标，curl_cffi 带 cf_clearance + 机构 cookie 直连即可，
        # 不必让浏览器去开 PDF 查看器（那条路会让用户看到重定向、且易被误判）。
        data = None
        hnote = ''
        if not getattr(args, 'no_http_handoff', False):
            data, hnote = handoff_http(ctx, page, pdf_url, article_url)
            if data:
                log('  ✓ handoff 取到 PDF（%s）' % hnote)
                st, note = 'ok', hnote
            else:
                log('  handoff 未成（%s），回退浏览器内下载…' % hnote)
        if data:
            pass
        else:
            # 只有走浏览器内兜底才补保活页：这条路径会让 PDF 查看器接管当前
            # 标签页，一旦它是最后一页，整个浏览器就会退出。
            try:
                _ka = ctx.new_page()
                _ka.goto('about:blank', timeout=8000)
            except Exception:  # noqa: BLE001
                pass
            st, data, note = download_pdf(ctx, page, pdf_url, budget, referer=article_url)
        if st != 'ok' or not data:
            out.update(status=st, note=note)
            _finish(ctx, tmp_ctx, lock, out, started)
            return out

        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_bytes(data)
        out.update(ok=True, status='ok', length=len(data), note='')
        _finish(ctx, tmp_ctx, lock, out, started)
        return out


def _export_cf_cookies(ctx, out):
    """把人机验证拿到的 cf_clearance 存给纯 HTTP 通道复用。

    实测依据（2026-10-20）：浏览器过一次挑战后 ctx.cookies() 里有
    `cf_clearance` / `__cf_bm` / `cf_chl_rc_ni`；同一 IP + 同一 UA 下，
    curl_cffi 带上这些 cookie 请求同站页面可免二次挑战（403 → 200）。

    意义：把「每篇文章都付一次浏览器挑战（70s+）」降为
    「每站每天付一次挑战，其余走 HTTP（5s）」。cookie 有效期约 25 分钟，
    由 antibot_session 侧按 TTL 判定，过期自动不再使用。

    写失败不影响主流程（只是少一个优化）。
    """
    try:
        cookies = ctx.cookies() or []
    except Exception:
        return
    host = ''
    try:
        pages = getattr(ctx, 'pages', None) or []
        if pages:
            from urllib.parse import urlparse
            host = (urlparse(pages[0].url).hostname or '').lower()
    except Exception:
        host = ''
    if not host:
        return
    from urllib.parse import urlparse  # noqa: F401  （保持局部依赖清晰）
    try:
        import importlib.util
        p = Path(__file__).resolve().parent / 'antibot_session.py'
        if not p.is_file():
            return
        spec = importlib.util.spec_from_file_location('_antibot_session_bf', p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        n = m.save_cf_cookies(host, cookies, ua=out.get('ua') or '')
        if n:
            out['cf_cookies_saved'] = n
            log('  已导出 CF cookie %d 个给 HTTP 通道复用（%s）' % (n, host))
    except Exception as e:  # noqa: BLE001
        log('  [warn] CF cookie 导出失败: %s' % str(e)[:80])


def _finish(ctx, browser, lock, out, started):
    out['elapsed'] = round(time.time() - started, 1)
    try:
        _export_cf_cookies(ctx, out)
    except Exception:
        pass
    try:
        ctx.close()
    except Exception:
        pass
    if browser is not None:
        try:
            browser.close()
        except Exception:
            pass
    _release_profile_lock(lock)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', required=True)
    ap.add_argument('--out', default='')
    ap.add_argument('--profile', default='',
                    help='持久化 profile 名（建议按源站：oup/gsw/elsevier）；留空=临时上下文')
    ap.add_argument('--timeout', type=int, default=90, help='人机验证总预算（秒）')
    ap.add_argument('--goto-timeout', type=int, default=60)
    ap.add_argument('--dl-timeout', type=int, default=180)
    ap.add_argument('--headed', action='store_true')
    ap.add_argument('--channel', default='msedge', choices=['msedge', 'chrome', 'chromium'])
    ap.add_argument('--engine', default='auto', choices=['auto', 'patchright', 'playwright'])
    ap.add_argument('--real-profile', default='', choices=['', 'edge', 'chrome'],
                    help='借用本机真实浏览器 profile 的副本（Turnstile 通过率显著更高）')
    ap.add_argument('--real-live', default='', choices=['', 'edge', 'chrome'],
                    help='直接使用本机真实浏览器的 User Data（原地，不复制；需先完全退出浏览器）')
    ap.add_argument('--kill-browser', action='store_true',
                    help='真实 profile 模式下，先终止残留浏览器后台进程（Edge 启动加速会复活）')
    ap.add_argument('--no-http-handoff', action='store_true',
                    help='关闭「过完验证后转 curl_cffi 取 PDF」的优先路径')
    ap.add_argument('--wait-unlock', type=int, default=120,
                    help='借用真实 profile 时等待浏览器关闭的最长秒数')
    ap.add_argument('--json', default='')
    args = ap.parse_args()

    log('=' * 66)
    log('浏览器验证通道  %s  %s' % (datetime.now().strftime('%H:%M:%S'), args.url[:80]))
    try:
        out = run(args)
    except Exception as e:  # noqa: BLE001
        out = {'ok': False, 'status': 'err_engine',
               'note': '%s: %s' % (type(e).__name__, str(e)[:200]), 'elapsed': 0}
    out['url'] = args.url
    log('结果: %s  %s' % (out.get('status'), str(out.get('note'))[:100]))

    if args.json:
        try:
            Path(args.json).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                       encoding='utf-8')
        except OSError:
            pass
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        prev = []
        if REPORT.is_file():
            prev = json.loads(REPORT.read_text(encoding='utf-8') or '[]')[-49:]
        prev.append(out)
        REPORT.write_text(json.dumps(prev, ensure_ascii=False, indent=1), encoding='utf-8')
    except Exception:
        pass

    print('RESULT_JSON: ' + json.dumps(out, ensure_ascii=False))
    return 0 if out.get('ok') else 2


if __name__ == '__main__':
    raise SystemExit(main())
