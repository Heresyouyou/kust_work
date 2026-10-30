# -*- coding: utf-8 -*-
"""sciengine api 空响应深挖：页面上下文 + 多种调用姿势"""
import sys, re
sys.path.insert(0, r"D:/PostNews/.workbuddy")
import journal_pdf_downloader as J

s = J.new_session()
page = "https://www.sciengine.com/sci-open/doi/10.3724/j.issn.1007-2802.20260189"
r = J.fetch(s, page)
print("page:", r.status_code, "cookies:", dict(s.cookies))

# 页面里 api 链接的上下文
i = r.text.find("api/v1/open/file/pdf")
print("\n--- api 链接上下文 ---")
print(r.text[max(0, i - 600):i + 300].replace("\n", " ")[:900])

# 页面里所有 /sci-open/ 端点
eps = sorted(set(re.findall(r'/sci-open/[a-zA-Z0-9/_\-]+', r.text)))
print("\n--- 页面里的 sci-open 端点 ---")
for e in eps:
    print("  ", e)

# 页面里 <a> 标签含 pdf/download 的
for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>([^<]{0,40})</a>', r.text):
    href, txt = m.group(1), m.group(2).strip()
    if 'pdf' in href.lower() or 'download' in href.lower() or 'PDF' in txt:
        print("  a-tag:", href[:110], "|", txt[:30])

api = "https://www.sciengine.com/sci-open/api/v1/open/file/pdf/00924C65498C4BAFA952222DFC4D77EE"
print("\n--- 多种调用姿势 ---")
tries = [
    ("GET + session cookie + referer", dict(method="GET", headers={"Referer": page})),
    ("GET + XHR 头", dict(method="GET", headers={"Referer": page, "X-Requested-With": "XMLHttpRequest",
                                                  "Accept": "application/json, text/plain, */*"})),
    ("GET + sec-fetch 头", dict(method="GET", headers={"Referer": page, "Sec-Fetch-Dest": "document",
                                                        "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "same-origin"})),
    ("POST + XHR", dict(method="POST", headers={"Referer": page, "X-Requested-With": "XMLHttpRequest",
                                                 "Content-Type": "application/json"}, data=b"{}")),
]
for name, kw in tries:
    try:
        hdrs = {**J.PDF_HEADERS, **kw.get("headers", {})}
        if kw["method"] == "GET":
            r2 = s.get(api, headers=hdrs, timeout=30)
        else:
            r2 = s.post(api, headers=hdrs, data=kw.get("data"), timeout=30)
        print(f"  {name}: {r2.status_code} len={len(r2.content)} ct={r2.headers.get('Content-Type','')!r} "
              f"head={r2.content[:50]!r}")
    except Exception as e:
        print(f"  {name}: EXC {type(e).__name__} {e}")
