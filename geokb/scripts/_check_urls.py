# -*- coding: utf-8 -*-
"""外链可达性检测：对审计底表中的唯一外部 URL 做并发探测。
方法：HEAD → 失败则 GET(带 Range)；跟随重定向；记录最终状态码/URL/类型。
"""
import json
import os
import ssl
import concurrent.futures as cf
import urllib.request
import urllib.error

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT = os.path.join(BASE, "state", "source_audit.json")
OUT = os.path.join(BASE, "state", "url_check.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def probe(url, timeout=20):
    """返回探测结果 dict。"""
    result = {"url": url, "ok": False, "status": None, "final_url": None,
              "content_type": None, "error": None, "method": None}
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method,
                                     headers={"User-Agent": UA,
                                              "Accept": "*/*",
                                              "Range": "bytes=0-1023" if method == "GET" else "bytes=0-0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                result.update(ok=r.status < 400, status=r.status,
                              final_url=r.geturl(),
                              content_type=r.headers.get("Content-Type"),
                              method=method)
                return result
        except urllib.error.HTTPError as e:
            result.update(status=e.code, final_error=None, method=method,
                          final_url=(e.url or url))
            # 403/405 对 HEAD 常见（服务器不允许 HEAD），换 GET 再试
            if e.code in (403, 405, 501):
                continue
            result.update(error=f"HTTP {e.code}")
            return result
        except Exception as e:
            result.update(error=f"{type(e).__name__}: {e}"[:160], method=method)
            continue
    return result


def main():
    audit = json.load(open(AUDIT, encoding="utf-8"))
    urls = list(audit["ext_url_counts"].keys())
    print(f"待检测唯一 URL: {len(urls)}")
    results = {}
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(probe, u): u for u in urls}
        for fut in cf.as_completed(futs):
            r = fut.result()
            results[r["url"]] = r
            flag = "OK " if r["ok"] else "FAIL"
            print(f"[{flag}] {r['status']} {r['url'][:90]}")
    json.dump(results, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    ok = sum(1 for r in results.values() if r["ok"])
    print(f"\n可达 {ok} / {len(results)}；不可达 {len(results) - ok}")


if __name__ == "__main__":
    main()
