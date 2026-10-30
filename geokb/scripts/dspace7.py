"""DSpace 7 REST：从记录页拿到真正的 PDF 直链。

为什么需要
----------
DSpace 7 的 `/handle/<h>` 页面是 JS 应用，静态 HTML 里**没有** PDF 链接
（也没有 citation_pdf_url），光靠正则抓页面只能得到 landing_no_pdf。
但它同时暴露了 HAL 风格的 REST API，四步就能拿到文件：

  1. GET {root}/server/api/pid/find?id=hdl:<handle>       → item（含 uuid / _links.bundles）
  2. GET {root}/server/api/core/items/<uuid>/bundles      → 找 name == "ORIGINAL"
  3. GET <bundle>/bitstreams                              → 找 *.pdf 的 bitstream
  4. GET <bitstream>/content                              → 真 PDF（application/pdf）

实测 helda.helsinki.fi（2026-10-20）：4 次请求、约 2 秒，拿到 3MB 原文，
bitstream 名就是 `s00126-025-01423-2.pdf`，可直接做身份锚点。

用法
----
    s = curl_cffi.Session(impersonate='chrome', trust_env=False)
    hit = find_pdf(s, 'https://helda.helsinki.fi/handle/10138/629161')
    # -> {'pdf_url': ..., 'name': 'xxx.pdf', 'item': '标题'} 或 None
"""
import re
from urllib.parse import quote, urlparse

_HANDLE_RX = re.compile(r'/(?:handle|xmlui/handle|jspui/handle)/([^/?#]+/[^/?#]+)')
_UUID_RX = re.compile(r'/items/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', re.I)


def _root(url):
    p = urlparse(url)
    return '%s://%s' % (p.scheme, p.netloc)


def find_pdf(sess, landing_url, timeout=25, verbose=False):
    root = _root(landing_url)
    item = None
    try:
        m = _HANDLE_RX.search(landing_url)
        if m:
            r = sess.get('%s/server/api/pid/find?id=hdl:%s' % (root, m.group(1)),
                         timeout=timeout)
            if r.status_code == 200 and 'json' in (r.headers.get('content-type') or ''):
                item = r.json()
        if item is None:
            m2 = _UUID_RX.search(landing_url)
            if m2:
                r = sess.get('%s/server/api/core/items/%s' % (root, m2.group(1)),
                             timeout=timeout)
                if r.status_code == 200 and 'json' in (r.headers.get('content-type') or ''):
                    item = r.json()
    except Exception:
        return None
    if not item:
        return None
    bundles = (((item.get('_links') or {}).get('bundles') or {}).get('href'))
    if not bundles:
        return None
    try:
        bj = sess.get(bundles, timeout=timeout).json()
    except Exception:
        return None
    for bun in (bj.get('_embedded') or {}).get('bundles') or []:
        if (bun.get('name') or '').upper() != 'ORIGINAL':
            continue
        href = ((bun.get('_links') or {}).get('bitstreams') or {}).get('href')
        if not href:
            continue
        try:
            sj = sess.get(href, timeout=timeout).json()
        except Exception:
            continue
        for bs in (sj.get('_embedded') or {}).get('bitstreams') or []:
            name = (bs.get('name') or '')
            content = ((bs.get('_links') or {}).get('content') or {}).get('href')
            if not content:
                continue
            if name.lower().endswith('.pdf') or 'pdf' in (bs.get('bundleName') or '').lower():
                if verbose:
                    print('    [dspace7] %s → %s' % (name, content[:70]))
                return {'pdf_url': content, 'name': name,
                        'item': item.get('name') or ''}
    return None


def maybe(url):
    """这个 URL 像不像 DSpace（handle 形态或 /items/<uuid>）。"""
    return bool(_HANDLE_RX.search(url or '') or _UUID_RX.search(url or ''))
