#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""校园统一身份认证（CAS）自动登录 + 会话保活守护。

背景
----
ScienceDirect 的 PDF 下载权限分两层：
  · IP 级授权（校园网内自动获得）—— 只能看文章页，PDF 请求一律 302 回文章页；
  · 用户级授权（CARSI / Shibboleth 登录后获得）—— PDF 真正开放。
本脚本负责把第二层拿下来，并让它长期有效。

真实链路（2026-10-20 从 Edge 历史还原，已验证）：
    ScienceDirect
      → auth.elsevier.com/ShibAuth/institutionLogin?entityID=https://sdauth.sciencedirect.com/
      → idplib.kust.edu.cn/idp/profile/SAML2/Redirect/SSO      (Shibboleth IdP)
      → cas.kmust.edu.cn/lyuapServer/login                      (金智 LyuAP，账号登录 tab)
      → 认证成功 → SAML Response → 回 Elsevier

登录页结构（实测）：
    #userName  账号         #password  密码
    #captcha   验证码（算术题，如 "0 + 8 ="，填 8）
    button:has-text("登 录")

凭据
----
    state/.sso_cred.json   {"username": "...", "password": "..."}
    （脚本只读，不打印、不落日志。文件权限请自行收紧。）

用法
----
    python scripts/sso_keeper.py --check            # 只检查当前会话是否有效
    python scripts/sso_keeper.py --login            # 执行一次登录
    python scripts/sso_keeper.py --loop --hours 6   # 常驻：每 6h 检查，失效自动重登
    python scripts/sso_keeper.py --login --headed   # 显示浏览器窗口（排错用）

profile
-------
    固定使用 state/browser_profiles/sciencedirect_auth
    （patchright 自建的 profile，cookie 密钥归本实例所有，可重复使用；
      绝不复用用户真实 Default profile —— 那边是 App-Bound 加密，复制即失效）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SCRIPTS = BASE / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from browser_verify_fetch import LAUNCH_ARGS, get_sync_playwright  # noqa: E402

PROFILE = BASE / 'state' / 'browser_profiles' / 'sciencedirect_auth'
CRED = BASE / 'state' / '.sso_cred.json'
STATE = BASE / 'state' / 'sso_keeper_state.json'
LOGDIR = BASE / 'logs'

# entityID 必须是学校 IdP 自己的标识（history 实测可用），
# 用 sdauth.sciencedirect.com 会停在选择机构页、不跳 CAS。
SSO_ENTRY = ('https://auth.elsevier.com/ShibAuth/institutionLogin'
             '?entityID=https%3A%2F%2Fidplib.kust.edu.cn%2Fidp%2Fshibboleth'
             '&appReturnURL=https%3A%2F%2Fid.elsevier.com%2Fas%2FTJBSbzxo9Z'
             '%2Fresume%2Fas%2Fauthorization.ping&oneTrust=false')
TEST_ARTICLE = ('https://www.sciencedirect.com/science/article/pii/'
                'S0169136823004328')
CAS_HOST = 'cas.kmust.edu.cn'

# SD 自己的机构登录页：会带上新鲜的 RelayState/appReturnURL，
# 走完后 SAML 能真正把凭证投递给 SD（直连 auth.elsevier.com 会停在“抱歉”页）。
SD_INSTITUTION_LOGIN = ('https://www.sciencedirect.com/user/institution/login'
                        '?targetURL=%2Fscience%2Farticle%2Fpii%2FS0169136823004328')


def log(msg):
    line = '[%s] %s' % (datetime.now().strftime('%H:%M:%S'), msg)
    print(line, flush=True)
    try:
        LOGDIR.mkdir(parents=True, exist_ok=True)
        with open(LOGDIR / 'sso_keeper.log', 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def load_cred():
    if not CRED.exists():
        log(f'[abort] 缺少凭据文件：{CRED}')
        log('        内容格式：{"username": "学/工号", "password": "密码"}')
        return None
    try:
        d = json.loads(CRED.read_text(encoding='utf-8'))
    except Exception as e:
        log(f'[abort] 凭据文件解析失败：{e}')
        return None
    u, pw = (d.get('username') or '').strip(), d.get('password') or ''
    if not u or not pw:
        log('[abort] 凭据缺 username 或 password')
        return None
    return u, pw


# ─── 验证码：算术题识别 ──────────────────────────────────────────────────
_OCR = None


def _ocr():
    global _OCR
    if _OCR is None:
        import ddddocr
        _OCR = ddddocr.DdddOcr(show_ad=False)
    return _OCR


def _find_captcha_el(page):
    """在 #captcha 输入框附近找验证码图片（img / canvas）。"""
    js = """() => {
        const inp = document.querySelector('#captcha');
        if (!inp) return null;
        const r = inp.getBoundingClientRect();
        let best = null, bestD = 1e9;
        document.querySelectorAll('img,canvas').forEach(el => {
            const b = el.getBoundingClientRect();
            if (b.width < 25 || b.width > 260 || b.height < 15 || b.height > 120) return;
            const d = Math.abs((b.top + b.bottom) / 2 - (r.top + r.bottom) / 2);
            if (d < bestD && d < 60) { bestD = d; best = el; }
        });
        return best;
    }"""
    return page.evaluate_handle(js).as_element()


def _solve_captcha(page):
    """截图验证码 → OCR → 算出算术结果。返回 (答案, 识别原文)。"""
    el = _find_captcha_el(page)
    if el is None:
        return None, ''
    try:
        png = el.screenshot()
    except Exception as e:
        log(f'  验证码截图失败：{type(e).__name__}')
        return None, ''
    try:
        raw = _ocr().classification(png)
    except Exception as e:
        log(f'  OCR 失败：{type(e).__name__}: {e}')
        return None, ''
    txt = (raw or '').replace(' ', '')
    # 归一化常见误识别
    txt = (txt.replace('×', '*').replace('x', '*').replace('X', '*')
              .replace('÷', '/').replace('=', '').strip())
    m = re.search(r'(-?\d+)\s*([+\-*/])\s*(-?\d+)', txt)
    if not m:
        log(f'  OCR 原文={raw!r} → 未解析出算式')
        return None, raw
    a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
    if op == '+':
        ans = a + b
    elif op == '-':
        ans = a - b
    elif op == '*':
        ans = a * b
    else:
        ans = a // b if b else None
    if ans is None:
        return None, raw
    log(f'  验证码 {a} {op} {b} = {ans}（OCR 原文 {raw!r}）')
    return str(ans), raw


# ─── 登录 ────────────────────────────────────────────────────────────────
def _goto_account_tab(page):
    for sel in ('text=账号登录', 'li:has-text("账号登录")',
                '.el-tabs__item:has-text("账号登录")'):
        try:
            el = page.query_selector(sel)
        except Exception:
            el = None
        if el:
            try:
                el.click()
                page.wait_for_timeout(1200)
                return True
            except Exception:
                pass
    return False


def do_login(page, cred, max_captcha_retry=4):
    user, pwd = cred
    # 两个入口：SD 自己的机构登录页会生成**新鲜**的 SAML state（首选）；
    # auth.elsevier.com 直连可无 state 走通前半段（兜底）。
    entries = [SD_INSTITUTION_LOGIN, SSO_ENTRY]
    reached_cas = False
    for entry in entries:
        log(f'[login] 打开入口 → {entry[:70]}')
        try:
            page.goto(entry, wait_until='domcontentloaded', timeout=60000)
        except Exception as e:
            log(f'  goto 异常（继续）：{type(e).__name__}')

        deadline = time.time() + 75
        _tick = 0
        while time.time() < deadline:
            u = page.url or ''
            if CAS_HOST in u:
                reached_cas = True
                break
            if 'sciencedirect.com/science' in u and 'institutionLogin' not in u:
                log('[login] 已经是登录态，无需重登')
                return True
            _tick += 1
            if _tick % 5 == 0:
                try:
                    _ti = (page.title() or '')[:40]
                except Exception:
                    _ti = '?'
                log(f'  [wait] {u[:70]} | {_ti}')
            time.sleep(2)
        if reached_cas:
            break
        # 入口被拦（SD 错误页 / CF）→ 换下一个
        log(f'  该入口未到 CAS（{page.url[:70]}），换下一个')

    log(f'[login] 已到 CAS：{page.url[:110]}')
    _goto_account_tab(page)

    for attempt in range(1, max_captcha_retry + 1):
        try:
            page.fill('#userName', user)
            page.fill('#password', pwd)
        except Exception as e:
            log(f'  填表失败：{type(e).__name__}: {e}')
            return False

        ans, raw = _solve_captcha(page)
        if not ans:
            log(f'  [{attempt}] 验证码没解出来，刷新重试')
            try:
                page.reload(wait_until='domcontentloaded')
                page.wait_for_timeout(2500)
                _goto_account_tab(page)
            except Exception:
                pass
            continue

        try:
            page.fill('#captcha', ans)
        except Exception as e:
            log(f'  填验证码失败：{e}')
            return False

        # 勾「记住我」，让 CAS 侧会话更持久
        try:
            cb = page.query_selector('#rememberMe')
            if cb and not cb.is_checked():
                cb.check()
        except Exception:
            pass

        try:
            page.click('button:has-text("登 录")')
        except Exception:
            try:
                page.keyboard.press('Enter')
            except Exception:
                pass
        log(f'  [{attempt}] 已提交，等待跳转 …')

        # 等离开 CAS
        d2 = time.time() + 45
        while time.time() < d2:
            u = page.url or ''
            if CAS_HOST not in u:
                break
            time.sleep(1.5)
        u = page.url or ''
        if CAS_HOST in u:
            log(f'  [{attempt}] 仍在 CAS（可能验证码错或密码错），重试')
            try:
                page.reload(wait_until='domcontentloaded')
                page.wait_for_timeout(2500)
                _goto_account_tab(page)
            except Exception:
                pass
            continue

        log(f'[login] 已离开 CAS → {u[:110]}')
        # 等 SAML 走完回到 SD
        d3 = time.time() + 60
        while time.time() < d3:
            if 'sciencedirect.com' in (page.url or ''):
                break
            time.sleep(2)
        page.wait_for_timeout(2500)
        ok = verify_session(page, quiet=True)
        log(f'[login] 校验结果：{"成功" if ok else "未通过"}')
        return ok

    log('[fail] 验证码连续失败，放弃本轮')
    return False


# ─── 会话校验 ────────────────────────────────────────────────────────────
def verify_session(page, quiet=False):
    """打开测试文章页，判断是否拿到用户级访问（而非仅 IP 授权）。"""
    try:
        page.goto(TEST_ARTICLE, wait_until='domcontentloaded', timeout=60000)
    except Exception as e:
        if not quiet:
            log(f'  校验页打开异常：{type(e).__name__}')
        return False
    page.wait_for_timeout(3000)

    info = page.evaluate("""() => {
        const t = document.body ? document.body.innerText.slice(0, 1500) : '';
        const meta = document.querySelector('meta[name="citation_pdf_url"]');
        const link = document.querySelector('a#pdfLink, a[href*="/pdfft"]');
        return {
            url: location.href,
            has_pdf_meta: !!(meta && meta.content),
            pdf_meta: meta ? meta.content.slice(0, 200) : '',
            has_pdf_link: !!link,
            pdf_href: link ? (link.getAttribute('href') || '').slice(0, 200) : '',
            subscribed: /subscribed content|via kunming/i.test(t),
            signed_in: !document.querySelector('a[href*="user/login"]'),
            title: document.title.slice(0, 90)
        };
    }""")
    if not quiet:
        log(f'  页面：{info.get("title")!r}')
        log(f'  citation_pdf_url = {info.get("pdf_meta") or "无"}')
        log(f'  PDF 链接 = {info.get("pdf_href") or "无"}')
        log(f'  机构授权字样 = {info.get("subscribed")}')
    ok = bool(info.get('has_pdf_meta') or info.get('has_pdf_link'))
    return ok


def save_state(**kw):
    try:
        cur = {}
        if STATE.exists():
            cur = json.loads(STATE.read_text(encoding='utf-8'))
        cur.update(kw)
        cur['updated'] = datetime.now().isoformat(timespec='seconds')
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(cur, ensure_ascii=False, indent=2),
                         encoding='utf-8')
    except Exception:
        pass


def run_once(action, headed=False, cred=None):
    sync_playwright = get_sync_playwright('auto')
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), channel='msedge', headless=not headed,
            args=LAUNCH_ARGS, chromium_sandbox=True, no_viewport=True,
            locale='zh-CN', timezone_id='Asia/Shanghai')
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            if action == 'check':
                ok = verify_session(page)
                log(f'[check] {"会话有效" if ok else "会话失效"}')
                save_state(last_check=datetime.now().isoformat(timespec='seconds'),
                           valid=ok)
                return 0 if ok else 1
            ok = do_login(page, cred)
            save_state(last_login=datetime.now().isoformat(timespec='seconds'),
                       valid=ok)
            return 0 if ok else 1
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--login', action='store_true')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--loop', action='store_true')
    ap.add_argument('--hours', type=float, default=6.0)
    ap.add_argument('--headed', action='store_true')
    args = ap.parse_args()

    if not PROFILE.is_dir():
        log(f'[abort] profile 不存在：{PROFILE}')
        return 2
    LOGDIR.mkdir(parents=True, exist_ok=True)

    if args.check:
        return run_once('check', headed=args.headed)
    if args.login:
        cred = load_cred()
        if not cred:
            return 2
        return run_once('login', headed=args.headed, cred=cred)
    if args.loop:
        cred = load_cred()
        if not cred:
            return 2
        log(f'[loop] 每 {args.hours}h 检查一次，失效自动重登')
        while True:
            rc = run_once('check', headed=args.headed)
            if rc != 0:
                log('[loop] 会话失效 → 重新登录')
                run_once('login', headed=args.headed, cred=cred)
            time.sleep(max(300, int(args.hours * 3600)))
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
