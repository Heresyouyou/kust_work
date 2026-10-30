# -*- coding: utf-8 -*-
"""统一 HTTP 会话工厂：TLS 指纹伪装 + 挑战 Cookie 复用  2026-10-20

背景（实测，`scripts/_antibot_matrix.py` / `logs/antibot_matrix.json`）
----------------------------------------------------------------
同一篇文章页，只换 HTTP 客户端，结果完全不同：

| 客户端 | pubs.geoscienceworld.org | academic.oup.com |
|---|---|---|
| requests（原实现） | **403** | **403** |
| curl_cffi impersonate=chrome | **200 正文页** | **200 正文页** |
| curl_cffi chrome124 / edge101 | 200 | 403 |
| httpx http2 | 200 | 403 |

结论：gsm/oup 的 403 **主因是 TLS 指纹（JA3/JA4）与 HTTP/2 帧序**，不是 IP 被封，
也不是需要人工点复选框。换客户端即可拿到文章页，无需浏览器。

能力边界（已实测，别再重复试）
------------------------------
- 本模块解决：**文章页 / 元数据 / OA 判定** 类 403。
- 本模块解决不了：`/article-pdf/` 路径的 403 —— 那是**订阅墙**：
  浏览器带上 cf_clearance 后 fetch 该路径仍 403（`_antibot_cookie_reuse.py` 实测），
  属权限问题，只能走 OA 渠道或等外部订阅。
- 也解决不了 `:80` 端口被沙箱拦截（geojournals / ddgzyckx）。

用法
----
    from antibot_session import new_session, engine_name
    s = new_session()                 # auto：有 curl_cffi 就用，没有退回 requests
    s = new_session(impersonate='chrome')
    print(engine_name())

    # 挑战 cookie 复用（同 IP + 同 UA 才有效）
    save_cf_cookies(domain, cookies)  # 由 browser_verify_fetch 导出后调用
    s = new_session(cf_domain='pubs.geoscienceworld.org')
"""
import json
import os
import re
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
CF_CACHE = BASE / 'state' / 'cf_cookies.json'
CF_TTL = 25 * 60          # cf_clearance 官方 TTL 30 分钟，取 25 分钟保守值

_ENGINE = None            # 'curl_cffi' | 'requests'


def _want_engine():
    v = (os.environ.get('GEOKB_HTTP_ENGINE') or 'auto').strip().lower()
    return v if v in ('auto', 'curl_cffi', 'requests') else 'auto'


def engine_name():
    """当前选用的 HTTP 引擎（'curl_cffi' / 'requests'）。"""
    global _ENGINE
    if _ENGINE:
        return _ENGINE
    want = _want_engine()
    if want == 'requests':
        _ENGINE = 'requests'
        return _ENGINE
    try:
        import curl_cffi  # noqa: F401
        _ENGINE = 'curl_cffi'
    except Exception:
        _ENGINE = 'requests'
        if want == 'curl_cffi':
            # 显式指定但没装：不静默失败，喊一声便于排查
            print('[antibot] 警告: 指定 curl_cffi 但未安装，降级 requests', flush=True)
    return _ENGINE


# ──────────────────── 挑战 Cookie 缓存 ────────────────────
def _load_cf_cache():
    try:
        return json.loads(CF_CACHE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_cf_cookies(domain, cookies, ua=''):
    """登记某域名的 CF cookie（来自浏览器通道导出）。

    cookies: list[{'name','value','domain',...}] 或 dict
    """
    if not domain or not cookies:
        return 0
    if isinstance(cookies, dict):
        cookies = [{'name': k, 'value': v, 'domain': domain}
                   for k, v in cookies.items()]
    keep = {}
    for c in cookies:
        n = c.get('name') or c.get('Name')
        if not n:
            continue
        if n in ('cf_clearance', '__cf_bm', 'cf_chl_rc_ni', '_cfuvid',
                 'cf_chl_2', 'cf_chl_prog', '__cflb'):
            keep[n] = c.get('value') or c.get('Value') or ''
    if not keep:
        return 0
    d = _load_cf_cache()
    d[domain] = {'ts': time.time(), 'cookies': keep, 'ua': ua}
    try:
        CF_CACHE.parent.mkdir(parents=True, exist_ok=True)
        CF_CACHE.write_text(json.dumps(d, ensure_ascii=False, indent=1),
                            encoding='utf-8')
    except OSError:
        return 0
    return len(keep)


def get_cf_cookies(domain):
    """取未过期的 CF cookie dict；没有则空 dict。"""
    rec = _load_cf_cache().get(domain)
    if not rec:
        return {}
    if time.time() - (rec.get('ts') or 0) > CF_TTL:
        return {}
    return rec.get('cookies') or {}


def _host_of(url):
    try:
        from urllib.parse import urlparse
        return (urlparse(url).hostname or '').lower()
    except Exception:
        return ''


# ────────────── DOI 前缀 → 真出版社 host（2026-10-26）──────────────
# 为什么必须换算：历史清单里 634/849 条的 URL 是 https://doi.org/10.1130/...，
# 若直接按 URL host 查表，全部命中 'doi.org' → gap_for() 匹配不上 → 落到
# PACE_DEFAULT=3s。结果是 gsw 本该 60s、oup 本该 90s 的静默期被整个绕过，
# 以 3 秒间隔连发——这正是 GSW 反复被判 403 的结构性原因（实测已发生）。
# 前缀表与 PostNews/.workbuddy/journal_pdf_downloader.source_of() 保持同源，
# 改一处必须同步另一处。
DOI_HOST = {
    '10.1130': 'pubs.geoscienceworld.org',      # GSA（Geology / GSA Bulletin）
    '10.2138': 'pubs.geoscienceworld.org',      # American Mineralogist
    '10.5382': 'pubs.geoscienceworld.org',      # Economic Geology（SEG，2019 起）
    # 旧卷（2018 及以前）用 10.2113，Crossref 走 chooser 多资源，落地仍是 GSW
    '10.2113': 'pubs.geoscienceworld.org',
    '10.1093': 'academic.oup.com',              # OUP / Journal of Petrology
    '10.1007': 'link.springer.com',             # Springer / Mineralium Deposita
    '10.1016': 'sciencedirect.com',             # Elsevier（暂停期只做归属）
    '10.3724': 'www.sciengine.com',             # 中国科学系列
    '10.16111': 'www.kcdz.ac.cn',               # 矿床地质
    '10.16539': 'dgyk.cbpt.cnki.net',           # 大地构造与成矿学
    '10.18654': 'ysxb.ac.cn',                   # 岩石学报
    '10.3799': 'earth-science.net',             # 地球科学
    # ── 南亚东南亚区域刊（2026-10-27 扩充清单时新增）────────────────
    # 这几个站此前从未访问过，没有风控记录，先按中等偏保守的静默期试。
    # Wiley 两个前缀并入同一个桶：Island Arc / Resource Geology /
    # Acta Geologica Sinica / Geological Journal / Terra Nova 都在
    # onlinelibrary.wiley.com，一套风控，不能当不同源站并发。
    '10.1002': 'onlinelibrary.wiley.com',
    '10.1111': 'onlinelibrary.wiley.com',
    '10.1080': 'www.tandfonline.com',           # Taylor & Francis（Int Geol Rev）
    '10.1017': 'www.cambridge.org',             # Cambridge（Geological Magazine）
    '10.1144': 'www.lyellcollection.org',       # Geol Soc London（JGS）
    # ⚠️ 2026-10-27 实测更正：10.17491（JGSI）的 DOI 解析后**不在 Springer**，
    # 而是跳到 pubs.geoscienceworld.org —— 与 GSA/AmMin/EconGeol 同一套 Cloudflare
    # 风控。必须并入 gsw 桶，否则「冻了 gsw 却还在打 jgsi」就是假冻结。
    '10.17491': 'pubs.geoscienceworld.org',
    # 实测落地在 journal.esdm.go.id（不是 ijog.geologi.esdm.go.id 子域）
    '10.17014': 'journal.esdm.go.id',           # Indonesian J Geoscience（OA）
    # 实测 gsm.org.my 无 www 前缀
    '10.7186': 'gsm.org.my',                    # Bull Geol Soc Malaysia（OA）
    '10.15625': 'vjs.ac.vn',                    # Vietnam J Earth Sciences（OA）
    '10.2465': 'www.jstage.jst.go.jp',          # J Mineral Petrol Sci（J-STAGE）
}
UNKNOWN_DOI_HOST = 'unknown-doi'
PACE_UNKNOWN = 15        # 未知 DOI 前缀：宁慢勿快，不要掉到 PACE_DEFAULT=3

_DOI_RE = re.compile(r'(10\.\d{4,9})/')
_DOI_HOSTS = ('doi.org', 'dx.doi.org', 'www.doi.org')


def pace_host(url_or_host, doi=''):
    """把 URL / DOI / 裸 host 归一成**限速桶的 host**。

    doi.org 只是跳转站，真正被风控的是跳转后的出版社，所以必须换算。
    换算失败（未知前缀）返回 UNKNOWN_DOI_HOST，按 PACE_UNKNOWN 保守限速。
    """
    u = (url_or_host or '').strip()
    h = _host_of(u) if '://' in u else u.lower()
    if h in _DOI_HOSTS or (not h and "10." in (doi or '')):
        m = _DOI_RE.search(u) or _DOI_RE.search(doi or '')
        if m:
            return DOI_HOST.get(m.group(1), UNKNOWN_DOI_HOST)
    return h


# ──────────────────── 会话构造 ────────────────────
def new_session(impersonate=None, cf_domain='', timeout_default=None):
    """建一个带 TLS 指纹伪装（可选）与挑战 cookie（可选）的会话。

    impersonate 默认 'chrome'：实测 gsw/oup 只有最新 chrome 指纹能过
    （chrome124 / edge101 会 403），不要把默认值改成别的。
    """
    imp = impersonate or os.environ.get('GEOKB_IMPERSONATE') or 'chrome'
    eng = engine_name()
    if eng == 'curl_cffi':
        from curl_cffi import requests as cr
        s = cr.Session(impersonate=imp)
        if cf_domain:
            for k, v in get_cf_cookies(cf_domain).items():
                try:
                    s.cookies.set(k, v, domain=cf_domain)
                except Exception:
                    pass
        return s
    import requests
    s = requests.Session()
    if cf_domain:
        for k, v in get_cf_cookies(cf_domain).items():
            try:
                s.cookies.set(k, v, domain=cf_domain)
            except Exception:
                pass
    return s


def new_session_for(url, impersonate=None):
    """按目标 URL 的域名自动带上对应挑战 cookie。"""
    return new_session(impersonate=impersonate, cf_domain=_host_of(url))


def describe():
    """自检信息，供 cron/巡检打印。"""
    d = _load_cf_cache()
    return {
        'engine': engine_name(),
        'impersonate': os.environ.get('GEOKB_IMPERSONATE') or 'chrome',
        'cf_domains': {k: {
            'age_min': round((time.time() - v.get('ts', 0)) / 60, 1),
            'fresh': time.time() - v.get('ts', 0) <= CF_TTL,
            'names': list((v.get('cookies') or {}).keys()),
        } for k, v in d.items()},
    }


# ──────────────────── 按源站节流（跨进程）────────────────────
# 实测依据（logs/gsw_channel_compare.json，2026-10-20）：
# 同一篇文章页，
#   · 单独请求（间隔久）→ curl_cffi 200
#   · 以 1.2s 间隔连发 6 篇 → 4 篇 403
# 说明 TLS 指纹只解决「单次被拦」，**连发照样触发行为风控**。
# 所以真正的解法是「按源站分桶限速 + 403 退避」，而不是换更多伪装。
# 这也正是用户要求的「一次一篇，入库后再爬下一篇」的量化依据。
#
# 2026-10-26 改为**按源站分文件**：state/antibot_pace/<host>.json
# 原来所有源站挤在一个 antibot_pace.json 里，读改写必须加锁；实测 Windows
# 字节锁在本机行为不稳（同进程第二个句柄拿不到锁也不会阻塞，见当日排障），
# 与其依赖它，不如把竞争面直接消掉：一个源站一个文件，写用 tmp→replace 原子替换。
# 顺手也解决了「拿锁时把不同源站串成一条线」的问题。
PACE_DIR = BASE / 'state' / 'antibot_pace'
PACE_FILE = BASE / 'state' / 'antibot_pace.json'      # 旧版单文件，仅作历史留档

# 每源站两次请求的最小间隔（秒）。数字来自实测被挑战的敏感度：
#   gsw/oup 最敏感（Cloudflare 托管挑战），elsevier 次之（IP 权限墙，快了也只多挨 403），
#   中文源站历来 3s 即可。
PACE_GAP = {
    'geoscienceworld.org': 60,
    'academic.oup.com': 90,
    'sciencedirect.com': 15,
    'springer.com': 12,
    'link.springer.com': 12,
    'sciengine.com': 20,
    # 中文源站：原本统一 3s（那是「孤立单发」的旧经验）。改成 6 槽位后同站
    # 间隔只剩 4.5s，比跑了几个月的 180s 激进 40 倍，2026-10-26 观测到
    # 矿床地质 guard_fail 集中出现，先按 20s（×1.5=30s）保守档跑。
    'kcdz.ac.cn': 20,
    'cbpt.cnki.net': 20,
    'geojournals.cn': 20,
    'earthsciencefrontiers': 20,
    'earth-science.net': 20,
    'ysxb.ac.cn': 20,
    # ── 南亚东南亚区域刊（2026-10-27）──────────────────────────────
    # 一律取保守档：这些站没有任何历史成功/拦截数据，先慢后调，
    # 反过来（先快再被拦）会让整个出版社进入 48h 冻结，代价高得多。
    'onlinelibrary.wiley.com': 45,
    'www.tandfonline.com': 60,
    'www.cambridge.org': 60,
    'www.lyellcollection.org': 60,
    # 三个小站都是 OA、无风控，可以快一些
    # （键按实测落地域名写：journal.esdm.go.id / gsm.org.my 无 www）
    'journal.esdm.go.id': 15,
    'gsm.org.my': 15,
    'vjs.ac.vn': 15,
    'www.jstage.jst.go.jp': 15,
}
PACE_DEFAULT = 20

# 每日尝试额度（按源站，跨进程共享）。
# 为什么必须有：2026-10-20 实测 —— 当天在 gsw 上跑了若干轮探测后，
# 即使间隔 20s，4 篇里仍有 3 篇被挑战；而当天更早的单发请求是 200。
# 说明**风控会按 IP 累计升级**，只靠间隔挡不住。
# 硬额度把「一天能试多少」钉死，宁可跨日累积，也不要把 IP 打到黑名单。
DAILY_MAX = {
    'geoscienceworld.org': 40,
    'academic.oup.com': 25,
    'sciencedirect.com': 60,
}
DAILY_MAX_DEFAULT = 0          # 0 = 不限（中文源站不设额度）


def _pace_path(host):
    safe = re.sub(r'[^a-z0-9._-]+', '_', (host or '').lower()).strip('_.') or '_'
    return PACE_DIR / (safe + '.json')


def _pace_read(host):
    """该源站上一次请求的时刻（epoch 秒；0 = 从未请求）。"""
    try:
        d = json.loads(_pace_path(host).read_text(encoding='utf-8'))
        return float(d.get('at') or 0)
    except Exception:
        return 0.0


def _pace_write(host, at):
    p = _pace_path(host)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix('.tmp')
        tmp.write_text(json.dumps({'at': at, 'host': host}), encoding='utf-8')
        os.replace(tmp, p)
    except OSError:
        pass


def gap_for(host):
    """该 host 的最小请求间隔（秒）。接受裸 host 或 URL / DOI 字符串。"""
    h = str(host or '')
    h = pace_host(h) if (not h or '://' in h or h.lower() in _DOI_HOSTS) else h.lower()
    if h == UNKNOWN_DOI_HOST:
        return PACE_UNKNOWN
    for k, v in PACE_GAP.items():
        if k in h:
            return v
    return PACE_DEFAULT


def wait_turn(url_or_host, gap=None):
    """等到该源站的「下一班车」，返回实际等待秒数。

    状态放在 state/antibot_pace/<host>.json —— daily_archive 与 history_queue
    是两个进程，各自 sleep 没用，必须落到共享状态上才真正串行。

    2026-10-26 起改为**预约制**：先算出「本次请求的出发时刻」写回文件，再
    睡到那个时刻。多槽并发时每个槽位拿到互不重叠的时刻，不会同时发车。
    """
    host = pace_host(url_or_host)
    if not host:
        return 0.0
    g = float(gap if gap is not None else gap_for(host))
    now = time.time()
    at = max(now, _pace_read(host) + g)      # 预定的出发时刻
    _pace_write(host, at)
    wait = max(0.0, at - now)
    if wait > 0:
        time.sleep(wait)
    return round(wait, 1)


def pace_wait(url_or_host, doi='', scale=1.0):
    """距该源站下一个可出发时刻还有多少秒（只读，不睡、不写）。

    供调度器在派发前筛掉还在静默期的源站——不要用「派下去让它自己睡」
    来节流，那样槽位会被白白占住。
    """
    host = pace_host(url_or_host, doi)
    if not host:
        return 0.0
    at = _pace_read(host)
    return round(max(0.0, at + float(gap_for(host)) * float(scale) - time.time()), 1)


def backoff(attempt, base=15, cap=120):
    """403/429 的指数退避秒数（含抖动，避免多进程同相位）。"""
    import random
    sec = min(cap, base * (2 ** max(0, attempt - 1)))
    return round(sec * (0.8 + 0.4 * random.random()), 1)


# ──────────────────── 每日额度（跨进程）────────────────────
QUOTA_FILE = BASE / 'state' / 'antibot_quota.json'


def _quota_load():
    try:
        d = json.loads(QUOTA_FILE.read_text(encoding='utf-8'))
    except Exception:
        d = {}
    today = time.strftime('%Y-%m-%d')
    if d.get('_date') != today:
        d = {'_date': today}          # 跨日自动清零
    return d


def _quota_save(d):
    try:
        QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = QUOTA_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(d), encoding='utf-8')
        os.replace(tmp, QUOTA_FILE)      # 原子替换：并发读到半个 JSON 会退化成「已用 0」，等于超额
    except OSError:
        pass


def quota_max(host):
    h = (host or '').lower()
    for k, v in DAILY_MAX.items():
        if k in h:
            return v
    return DAILY_MAX_DEFAULT


def quota_ok(url_or_host, need=1):
    """今天还能不能往这个源站发请求。返回 (ok, 已用, 上限, 说明)。"""
    host = pace_host(url_or_host)
    cap = quota_max(host)
    if not cap:
        return True, 0, 0, 'unlimited'
    d = _quota_load()
    used = int(d.get(host) or 0)
    if used + need > cap:
        return False, used, cap, 'daily quota exhausted (%d/%d)' % (used, cap)
    return True, used, cap, 'ok'


def quota_consume(url_or_host, n=1):
    """记一次（或 n 次）请求额度消耗。"""
    host = pace_host(url_or_host)
    if not quota_max(host):
        return
    d = _quota_load()
    d[host] = int(d.get(host) or 0) + n
    _quota_save(d)




if __name__ == '__main__':
    print(json.dumps(describe(), ensure_ascii=False, indent=1))
