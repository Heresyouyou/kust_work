# -*- coding: utf-8 -*-
"""深挖 sciengine / esf / ddgzyckx 三家失败原因（不碰 geojournals）"""
import sys, re, json, time
sys.path.insert(0, r"D:/PostNews/.workbuddy")
import requests
import journal_pdf_downloader as J

s = J.new_session()

print("=" * 70)
print("A) sciengine: 页面 cookie + api 调用方式分析")
print("=" * 70)
page = "https://www.sciengine.com/sci-open/doi/10.3724/j.issn.1007-2802.20260189"
r = J.fetch(s, page)
print("page status:", r.status_code, "cookies:", dict(s.cookies))
api = "https://www.sciengine.com/sci-open/api/v1/open/file/pdf/00924C65498C4BAFA952222DFC4D77EE"
# 带上页面 cookie 再试
r2 = s.get(api, headers={**J.PDF_HEADERS, "Referer": page}, timeout=60)
print("api with session cookies:", r2.status_code, "len:", len(r2.content),
      "ct:", r2.headers.get("Content-Type", ""), "head:", r2.content[:40])
# 看页面里 pdf 相关的 JS/按钮
for pat in [r'download[Pp]df[^"\']{0,120}', r'api/v1/open/file/[^"\']+',
            r'open/file/pdf/[0-9A-Fa-f]+', r'data-[a-z-]*pdf[a-z-]*="[^"]*"']:
    ms = re.findall(pat, r.text)
    print(f"  pattern {pat[:30]!r}: {len(ms)} hits -> {ms[:5]}")
# 页面里是否有 token / Authorization
for kw in ["token", "Authorization", "X-Auth", "sign", "timestamp"]:
    if kw.lower() in r.text.lower():
        i = r.text.lower().find(kw.lower())
        print(f"  kw {kw!r} ctx: {r.text[max(0,i-80):i+120]!r}")

print()
print("=" * 70)
print("B) esf: 文章页里的下载链接形态")
print("=" * 70)
url = "https://www.earthsciencefrontiers.net.cn/CN/10.13745/j.esf.2014.06.014"
r = J.fetch(s, url)
print("page status:", r.status_code, "cookies:", dict(s.cookies))
for pat in [r'downloadArticleFile\.do[^"\']*', r'href="[^"]*[Pp][Dd][Ff][^"]*"',
            r'attachType=[A-Za-z]+', r'id=(\d+)']:
    ms = re.findall(pat, r.text)
    print(f"  pattern {pat[:40]!r}: {len(ms)} hits -> {ms[:6]}")
# 找 PDF 附件 id 附近上下文
i = r.text.find("downloadArticleFile")
if i > 0:
    print("  ctx:", r.text[max(0, i - 300):i + 200].replace("\n", " ")[:500])
# 试带 cookie + referer 再下
pdf_url = "https://www.earthsciencefrontiers.net.cn/CN/article/downloadArticleFile.do?attachType=PDF&id=4913"
r2 = s.get(pdf_url, headers={**J.PDF_HEADERS, "Referer": url}, timeout=60, allow_redirects=False)
print("pdf GET (no redirect follow):", r2.status_code, "len:", len(r2.content),
      "loc:", r2.headers.get("Location", ""), "ct:", r2.headers.get("Content-Type", ""))
if r2.status_code in (301, 302, 303, 307, 308) and r2.headers.get("Location"):
    loc = r2.headers["Location"]
    r3 = s.get(loc if loc.startswith("http") else "https://www.earthsciencefrontiers.net.cn" + loc,
               headers={**J.PDF_HEADERS, "Referer": url}, timeout=60)
    print("  followed:", r3.status_code, "len:", len(r3.content), "ct:", r3.headers.get("Content-Type", ""),
          "head:", r3.content[:40])

print()
print("=" * 70)
print("C) ddgzyckx: paperonce 索引（带 session）")
print("=" * 70)
try:
    t0 = time.time()
    idx = J._ddgz_build_index(s)
    print("index size:", len(idx), f"({time.time()-t0:.1f}s)")
    t = "柴北缘高压-超高压造山带鱼卡变泥质岩相平衡模拟及白云母40Ar/39Ar定年研究"
    print("hit:", idx.get(J._norm_title(t)))
except Exception as e:
    print("EXC:", type(e).__name__, str(e)[:400])
