"""Anubis 工作量证明（PoW）验证器 —— 纯计算，不用浏览器。

为什么需要它
------------
HAL（hal.science / *.hal.science）2026 年起在 /document 上挂了
TecharoHQ/Anubis：返回一个 12KB 的挑战页，要求客户端算
`sha256(randomData + nonce)` 直到哈希前缀有 N 个 0（默认 difficulty=4），
再带上 cookie 换真文件。这不是图形验证码，是**可确定性求解**的，几十毫秒即可。

协议（实测 hal.science，2026-10-20）
-----------------------------------
1. GET 目标 URL → 200 text/html，正文含 JSON：
   {"rules":{"algorithm":"fast","difficulty":4},
    "challenge":{"id":"...","randomData":"<256 hex>", ...}}
   响应同时下发 cookie `c2sd-an-cookie-verification = id`（版本前缀会变，
   如 `within.website-x-cmd-anubis-*`，所以按后缀匹配）。
2. 找最小 nonce（十进制字符串）使 sha256(randomData + str(nonce)) 以
   '0' * difficulty 开头。
3. GET /api/pass-challenge?id=<id>&response=<hash>&nonce=<n>
       &redir=<原文URL>&elapsedTime=<毫秒>
   → 302，并 Set-Cookie `<prefix>-an-auth`。
4. 重新 GET 原 URL → 真文件。

注意
----
- 挑战与 **User-Agent** 绑定（challenge.metadata["User-Agent"]），
  必须用同一个 session（同 UA、同 cookie jar）走完全程。
- 一个 cookie 对其余路径同样有效，可复用整个 host 的后续请求。
- 只有 html 且含 anubis 标记时才进入求解，正常 PDF 直接返回，不增加延迟。
"""
import hashlib
import json
import re
import time
from urllib.parse import quote

_CH_RE = re.compile(r'(\{\s*"rules"\s*:.*?\})\s*</script>', re.S)
_MARK = ('/.within.website/x/cmd/anubis/', 'anubis')


def looks_like_anubis(body: bytes, ctype: str = '') -> bool:
    if not body or body[:5] == b'%PDF':
        return False
    head = body[:4000].decode('utf-8', 'replace')
    return 'anubis' in head.lower()


def _parse(payload: bytes):
    txt = payload.decode('utf-8', 'replace')
    m = _CH_RE.search(txt)
    if not m:
        return None
    try:
        j = json.loads(m.group(1))
    except Exception:
        return None
    ch = j.get('challenge') or {}
    if not ch.get('randomData') or not ch.get('id'):
        return None
    diff = int((j.get('rules') or {}).get('difficulty')
               or ch.get('difficulty') or 4)
    return {'id': ch['id'], 'data': ch['randomData'], 'difficulty': diff}


def solve(payload: bytes, max_iter=200_000_000):
    """返回 (nonce, hash_str)；解析不出挑战则 None。"""
    p = _parse(payload)
    if not p:
        return None
    data, diff = p['data'], min(p['difficulty'], 8)
    prefix = '0' * diff
    nonce = 0
    while nonce < max_iter:
        h = hashlib.sha256((data + str(nonce)).encode()).hexdigest()
        if h.startswith(prefix):
            return p, nonce, h
        nonce += 1
    return None


def fetch(session, url, timeout=60, allow_redirects=True, max_rounds=2,
          verbose=False):
    """带 Anubis 自动求解的 GET。返回 curl_cffi Response。

    session 必须已固定 impersonate（UA 与挑战绑定）。
    """
    r = session.get(url, timeout=timeout, allow_redirects=allow_redirects)
    for _ in range(max_rounds):
        if not looks_like_anubis(r.content or b'', r.headers.get('content-type') or ''):
            return r
        t0 = time.time()
        got = solve(r.content or b'')
        if not got:
            if verbose:
                print('    [anubis] 挑战页解析失败，放弃')
            return r
        p, nonce, h = got
        root = '/'.join(url.split('/')[:3])
        q = ('%s/.within.website/x/cmd/anubis/api/pass-challenge'
             '?id=%s&response=%s&nonce=%d&redir=%s&elapsedTime=%d'
             % (root, quote(p['id'], safe=''), h, nonce,
                quote(url, safe=''), int((time.time() - t0) * 1000)))
        pr = session.get(q, timeout=30, allow_redirects=False)
        if verbose:
            print('    [anubis] diff=%d nonce=%d pass=%s'
                  % (p['difficulty'], nonce, pr.status_code))
        if pr.status_code not in (200, 302):
            return r
        r = session.get(url, timeout=timeout, allow_redirects=allow_redirects)
    return r


def is_file(resp) -> bool:
    b = resp.content or b''
    return b[:5] == b'%PDF'
