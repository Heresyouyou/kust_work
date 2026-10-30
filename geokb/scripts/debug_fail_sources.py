# -*- coding: utf-8 -*-
"""逐源复现 backfill 失败，定位是网络问题还是路径问题"""
import sys, json, re, time
sys.path.insert(0, r"D:/PostNews/.workbuddy")
import requests
import journal_pdf_downloader as J

s = J.new_session()

def show(tag, r):
    print(f"    {tag}: status={r.status_code} ct={r.headers.get('Content-Type','')!r} "
          f"len={len(r.content)} head={r.content[:60]!r}")

print("=" * 70)
print("1) geojournals 地质学报")
print("=" * 70)
url = "https://www.geojournals.cn/dzxb/ch/reader/view_abstract.aspx?file_no=2026134&flag=1"
try:
    r = J.fetch(s, url)
    show("abstract page", r)
    pdf_url = J.get_meta(r.text, "citation_pdf_url")
    print("    citation_pdf_url =", repr(pdf_url))
    print("    NO_FULLTEXT in page:", J.NO_FULLTEXT in r.text)
    if pdf_url:
        m = re.match(r"(https?://)([^/]+)(/.*)?$", pdf_url)
        direct = (m.group(1) + "geo.ijournals.cn" + (m.group(3) or "")) if m else pdf_url
        print("    direct =", direct)
        try:
            r2 = J.fetch(s, direct, referer=r.url, headers=J.PDF_HEADERS)
            show("direct GET", r2)
        except requests.RequestException as e:
            print("    direct GET EXC:", e)
        st, content = J.guard_get(s, pdf_url, referer=r.url)
        print(f"    guard_get(pdf_url) -> {st} len={len(content) if isinstance(content,(bytes,bytearray)) else content}")
except Exception as e:
    print("    EXC:", e)

print()
print("=" * 70)
print("2) sciengine 矿物岩石地球化学通报")
print("=" * 70)
url = "https://doi.org/10.3724/j.issn.1007-2802.20260189"
m = re.search(r"(10\.\d{4,9}/[^\s?#]+)", url)
doi = m.group(1)
page = f"https://www.sciengine.com/sci-open/doi/{doi}"
print("    doi =", doi, " page =", page)
try:
    r = J.fetch(s, page)
    show("sci-open page", r)
    m2 = re.search(r'https://www\.sciengine\.com/sci-open/api/v1/open/file/pdf/[0-9A-Fa-f]+', r.text)
    print("    api url found:", bool(m2), m2.group(0) if m2 else "")
    if m2:
        api = m2.group(0)
        r2 = J.fetch(s, api, referer=page, headers=J.PDF_HEADERS)
        show("api GET", r2)
        print("    resp headers:", dict(list(r2.headers.items())[:12]))
except Exception as e:
    print("    EXC:", type(e).__name__, e)

print()
print("=" * 70)
print("3) esf 地学前缘")
print("=" * 70)
url = "https://www.earthsciencefrontiers.net.cn/CN/10.13745/j.esf.2014.06.014"
try:
    r = J.fetch(s, url)
    show("article page", r)
    pdf_url = J.get_meta(r.text, "citation_pdf_url")
    print("    citation_pdf_url =", repr(pdf_url))
    if pdf_url:
        r2 = J.fetch(s, pdf_url, referer=r.url, headers=J.PDF_HEADERS)
        show("pdf GET", r2)
except Exception as e:
    print("    EXC:", type(e).__name__, e)

print()
print("=" * 70)
print("4) ddgzyckx 大地构造与成矿学 (paperonce 索引)")
print("=" * 70)
try:
    idx = J._ddgz_build_index()
    print("    index size:", len(idx))
    for t in ["柴北缘高压-超高压造山带鱼卡变泥质岩相平衡模拟及白云母40Ar/39Ar定年研究"]:
        print("    hit:", idx.get(J._norm_title(t)))
except Exception as e:
    print("    EXC:", type(e).__name__, str(e)[:300])
