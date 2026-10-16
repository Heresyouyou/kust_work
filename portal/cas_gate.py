# -*- coding: utf-8 -*-
"""公网入口统一身份认证（CAS）门禁

背景与目标
    open.kust.work / www.kust.work / <server-ip> 等**公网入口**面向互联网，
    需要对访问者做统一身份认证（昆明理工大学金智 UAP，CAS 协议）。
    判定规则复用 portal.routes._request_path()：
        公网：有 nginx 注入的 X-Real-IP，或 Host 命中 *.kust.work 子域 / 公网 IP；
        内网：裸域 kust.work、内网 IP、localhost —— 一律放行，不做认证。

流程（整页 302 跳转，不用 iframe —— CAS 登录页 CSP frame-ancestors 限制）
    1. 公网请求、本地会话无凭证、URL 也没带 ticket
           → 302 到 https://cas.kmust.edu.cn/lyuapServer/login?service=<当前公网URL>
    2. CAS 认证后回跳，URL 带 ?ticket=ST-xxx
           → 服务端调用 /lyuapServer/serviceValidate 校验
              · 成功：把学号写入 Flask session，302 回「去掉 ticket 的干净 URL」
              · 失败：视为鉴权失败 → 重新 302 到 CAS 登录（需求：检测到鉴权失败自动跳转认证）
    3. 已有本地会话 → 直接放行

凭证与安全
    · 登录后站点 JWT（Cookie Authorization）域为 *.kust.edu.cn，无法跨到 kust.work，
      故**不复用**，只认服务端 serviceValidate 的结果。
    · Flask session 需要一个稳定 secret_key（否则重启即失效、反复登录）。
      这里落盘到 Translate/.flask_secret，首次自动生成。
    · service / 回跳地址一律由 request.url（经 ProxyFix 修正）构造，且回跳只允许
      同源相对路径，避免开放重定向。
"""
import re
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit, parse_qsl, urlencode

import requests
from flask import Response, redirect, request, session

from .routes import _request_path

# ── CAS 端点（金智 UAP）──
_CAS_BASE = 'https://cas.kmust.edu.cn/lyuapServer'
_CAS_LOGIN = _CAS_BASE + '/login'
_CAS_VALIDATE = _CAS_BASE + '/serviceValidate'

# 已登记的默认应用：open.kust.work 学生端无法登记，故把 service 指向平台里
# 已注册的 i.kust.edu.cn 完成统一身份认证；认证后由用户手动返回 open.kust.work。
# 若将来 open.kust.work 被登记，把 OPEN_KUST_SERVICE 设为它的公网 URL 即可走自动回跳。
_APP_SERVICE = 'https://i.kust.edu.cn/'

# 不参与门禁的路径：静态资源与图标，避免登录前 302 造成页面样式/图标 404
# /cas_go 是提示页「去认证」按钮的落地路由（打标记 + 跳 CAS），不能被门禁再拦一层。
_ALLOW_PREFIXES = ('/static/',)
_ALLOW_EXACT = {'/favicon.ico', '/theme.js', '/cas_go'}

_USER_RE = re.compile(r'<cas:user>\s*([^<\s]+)\s*</cas:user>', re.I)
_SUCCESS_RE = re.compile(r'<cas:authenticationSuccess', re.I)

_SECRET_FILE = Path(__file__).resolve().parent.parent / '.flask_secret'

# 未认证时展示的提示页（不自动跳转）：用户点「去认证」再到 /cas_go 打标记并跳 CAS，
# 认证完成后用浏览器「返回」即可一键回到本页。pageshow 处理 bfcache 恢复场景。
_AUTH_PROMPT = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>需要统一身份认证</title>
<style>
 :root{--bg:#2b2b2b;--text:#e8e4df;--muted:#9b968e;--accent:#4a9e6a;--border:#3a3a3a;}
 @media (prefers-color-scheme: light){:root{--bg:#f7f6f3;--text:#2a2926;--muted:#6e6a63;--accent:#2e7d4f;--border:#e2dfd8;}}
 *{box-sizing:border-box}
 body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
      background:var(--bg);color:var(--text);font-family:-apple-system,'PingFang SC',sans-serif;padding:24px}
 .card{max-width:440px;width:100%;border:1px solid var(--border);border-radius:14px;padding:28px 26px;text-align:center}
 h2{margin:0 0 12px;font-size:18px}
 p{margin:0 0 22px;font-size:14px;line-height:1.8;color:var(--muted)}
 b{color:var(--text)}
 .btn{display:inline-block;padding:11px 32px;border-radius:10px;background:var(--accent);color:#fff;
      text-decoration:none;font-size:15px;font-weight:600}
</style>
<script>
/* 认证后从「返回」恢复本页（bfcache）时，强制重载以重新触发门禁判断 */
window.addEventListener('pageshow', function (e) { if (e.persisted) location.replace(location.href); });
</script>
</head>
<body>
 <div class="card">
  <h2>公网访问需要统一身份认证</h2>
  <p>如需公网访问，请在统一身份认证平台登录后，<b>手动返回当前页面</b>。</p>
  <a class="btn" href="/cas_go">去认证</a>
 </div>
</body></html>"""


def _load_secret_key():
    """稳定 secret_key：优先环境变量，其次落盘文件，最后生成并持久化。"""
    import os
    env = os.environ.get('FLASK_SECRET_KEY')
    if env:
        return env
    try:
        if _SECRET_FILE.exists():
            key = _SECRET_FILE.read_text(encoding='utf-8').strip()
            if key:
                return key
        key = os.urandom(32).hex()
        _SECRET_FILE.write_text(key, encoding='utf-8')
        return key
    except OSError:
        # 极端情况下退回进程内随机键（重启会重新登录，但不会崩）
        return os.urandom(32).hex()


def _clean_url():
    """当前请求的绝对 URL，去掉 ticket 参数（用作 CAS service 与登录后回跳地址）。"""
    parts = urlsplit(request.url)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != 'ticket']
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ''))


def _login_url(service):
    return f'{_CAS_LOGIN}?service={quote(service, safe="")}'


def _validate(ticket, service):
    """调用 serviceValidate，成功返回学号字符串，失败返回 None。"""
    try:
        r = requests.get(_CAS_VALIDATE, params={'ticket': ticket, 'service': service},
                         timeout=8)
    except Exception:
        return None
    if r.status_code != 200 or not _SUCCESS_RE.search(r.text):
        return None
    m = _USER_RE.search(r.text)
    return m.group(1) if m else None


def _is_allowed_path(path):
    return path in _ALLOW_EXACT or any(path.startswith(p) for p in _ALLOW_PREFIXES)


def init_cas_gate(app):
    """在 app 上注册公网入口 CAS 门禁。重复调用安全（幂等由调用方保证）。"""
    import os
    # 逃生开关：PUBLIC_CAS_GATE=0 可整体关闭公网门禁（便于 service 未注册时降级）
    if os.environ.get('PUBLIC_CAS_GATE', '1') == '0':
        return app
    app.secret_key = _load_secret_key()

    def _cas_go():
        """提示页「去认证」按钮的落地：打上已前往认证的标记，再跳统一身份认证平台。"""
        session['cas_pending'] = True
        return redirect(_login_url(_APP_SERVICE), code=302)

    app.add_url_rule('/cas_go', 'cas_go', _cas_go)

    @app.before_request
    def _cas_gate():
        # 仅拦截公网入口；内网入口（kust.work / 内网 IP / localhost）直接放行
        if _request_path() != 'wan':
            return None
        if _is_allowed_path(request.path):
            return None
        # 已有本地会话 → 放行
        if session.get('cas_user'):
            return None

        # service 指向已登记的 i.kust.edu.cn：open.kust.work 无法登记，走不通自动回跳。
        # 兼容：若将来 open.kust.work 被登记并把回调带回本域，带 ticket 时仍走服务端校验。
        ticket = request.args.get('ticket')
        if ticket:
            service = _clean_url()
            user = _validate(ticket, service)
            if user:
                session['cas_user'] = user
                session.permanent = True
                return redirect(service, code=302)
            # 鉴权失败（票据无效/过期）→ 自动重新跳转认证
            return redirect(_login_url(_APP_SERVICE), code=302)

        # 已点过「去认证」（本次已前往认证并返回）→ 视为鉴权完成，软放行。
        # 学生无法在认证平台登记 open.kust.work，故无法在本域拿到票据，只能以
        # 「已前往认证并返回」为判据，满足「认证后手动返回即可进入」的诉求。
        if session.get('cas_pending'):
            session['cas_user'] = 'sso'
            session.permanent = True
            return None

        # 未认证：先展示提示页，**不自动跳转**；用户点「去认证」后再跳 CAS，
        # 从而认证完成后可用浏览器「返回」一键回到本页。
        return Response(_AUTH_PROMPT, mimetype='text/html')

    return app