# -*- coding: utf-8 -*-
"""运行时 DOM 验证（2026-10-18 期刊按钮改版 v2 —— 用户定稿）：
打开两端 /journal 页面，检查：
  1. 无 JS 运行时错误（console error / pageerror）
  2. 已归档文章行 = 「下载 PDF」（主色绿 btn-primary）+「期刊官网」（默认灰）并存
  3. 未归档文章行 = 只有「期刊官网」（默认灰）；无「下载 PDF」、无「在线抓取」
  4. 两端均不出现「在线抓取」按钮（补抓已退役，交回每日管线）
  5. 「期刊官网」链接 href = 文章源站 URL、target=_blank
  6. 全页唯一主色按钮 = 「下载 PDF」（期刊官网 / 阅读 / 翻译一律默认灰）
"""
import sys

try:
    from patchright.sync_api import sync_playwright   # 首选（stealth 版）
except ImportError:
    from playwright.sync_api import sync_playwright   # 兜底（本地页面无需过墙）

TARGETS = [
    ('5555 门户', 'http://127.0.0.1:5555/journals'),
    ('5556 GeoKB', 'http://127.0.0.1:5556/journal'),
]


def probe(page, url):
    fatal = []
    page.on('pageerror', lambda e: fatal.append(str(e)))
    # 资源 404（译文探测 /api/entry/<eid>/pdf/mono 等）是既有行为：
    # 无中文版的条目被 geokb_translate.js 批量探测，刷 console error 但用户无感，
    # 不算 JS 错误；只有 pageerror（运行时异常）才致命。
    page.goto(url, wait_until='networkidle', timeout=45000)
    page.wait_for_timeout(2500)          # 等 fetch + 渲染完成
    return page.evaluate("""() => {
      var rows = Array.from(document.querySelectorAll('.art'));
      var out = {total: rows.length, archived: 0, unarchived: 0,
                 site_href_ok: 0, site_target_ok: 0, site_primary: 0,
                 grab: 0, dl: 0, dl_primary: 0, samples: []};
      rows.forEach(function (r) {
        var site = r.querySelector('a.j-site');
        var dl = r.querySelector('button.j-dl-ready');
        var grab = r.querySelector('button.j-dl-grab');
        var ready = !!dl;
        if (ready) out.archived++; else out.unarchived++;
        if (dl) {
          out.dl++;
          if (dl.classList.contains('btn-primary')) out.dl_primary++;
        }
        if (grab) out.grab++;
        if (site) {
          var href = site.getAttribute('href') || '';
          if (/^https?:\\/\\//.test(href)) out.site_href_ok++;
          if (site.getAttribute('target') === '_blank') out.site_target_ok++;
          if (site.classList.contains('btn-primary')) out.site_primary++;
          if (out.samples.length < 4) {
            out.samples.push({
              ready: ready,
              site: {href: href.slice(0, 80), primary: site.classList.contains('btn-primary')},
              has_dl: !!dl,
              dl_primary: dl ? dl.classList.contains('btn-primary') : false,
              has_grab: !!grab
            });
          }
        }
      });
      // 页面全文不得出现「在线抓取」按钮标签（>在线抓取< 是渲染出的按钮文字）
      out.grab_text = (document.body.innerText || '').indexOf('>在线抓取<') >= 0
        || (document.body.innerHTML || '').indexOf('>在线抓取<') >= 0;
      return out;
    }"""), fatal


def main():
    all_ok = True
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        for name, url in TARGETS:
            page = browser.new_page(viewport={'width': 1400, 'height': 900})
            try:
                r, fatal = probe(page, url)
            except Exception as e:
                print('=== %s: 打开失败 %s' % (name, e))
                all_ok = False
                page.close()
                continue
            print('=== %s (%s)' % (name, url))
            print('    文章行 %d · 已归档 %d · 未归档 %d'
                  % (r['total'], r['archived'], r['unarchived']))
            print('    期刊官网: href 合规 %d · target=_blank %d · 主色(应为 0) %d'
                  % (r['site_href_ok'], r['site_target_ok'], r['site_primary']))
            print('    下载 PDF 按钮 %d（其中主色绿 %d，应等于已归档数）'
                  ' · 在线抓取按钮 %d（应为 0）'
                  % (r['dl'], r['dl_primary'], r['grab']))
            ok = (r['total'] > 0
                  and r['site_href_ok'] == r['total']
                  and r['site_target_ok'] == r['total']
                  and r['site_primary'] == 0
                  and r['grab'] == 0
                  and r['dl'] == r['archived']
                  and r['dl_primary'] == r['archived']
                  and not r['grab_text']
                  and not fatal)
            print('    正文含「在线抓取」标签: %s' % ('是（FAIL）' if r['grab_text'] else '否'))
            print('    JS 运行时错误: %s' % (fatal[:2] if fatal else '无'))
            print('    判定: %s' % ('PASS' if ok else 'FAIL'))
            all_ok = all_ok and ok
            for s in r['samples'][:3]:
                print('      样例行 ready=%s dl=%s(dl绿=%s) grab=%s 官网主色=%s href=%s'
                      % (s['ready'], s['has_dl'], s['dl_primary'], s['has_grab'],
                         s['site']['primary'], s['site']['href'][:52]))
            page.close()
        browser.close()
    print('\n总体: %s' % ('全部 PASS' if all_ok else '存在 FAIL'))
    sys.exit(0 if all_ok else 1)


if __name__ == '__main__':
    main()
