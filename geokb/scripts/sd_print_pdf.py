# -*- coding: utf-8 -*-
"""用「打印渠道」把 ScienceDirect 文章页转成 PDF。

为什么这么做：
    `/pdfft` 端点被 Cloudflare 内容防护拦下（CPE00001 ::CLOUDFLARE_ERROR_1000S_BOX::），
    403 且带 reference number。但**文章页本身是正常打开的**（有 SD_REMOTEACCESS
    机构权限、cf_clearance 也在），所以改走浏览器自己的打印通道：
    在已授权的页面里调 CDP `Page.printToPDF`，由浏览器渲染成 PDF。

代价（必须清楚，不是期刊原始排版）：
    · 版式是网页排版转打印，不是出版社排的期刊 PDF；
    · 图表一般是网页图（够看，但不是矢量原图）；
    · 页码/卷期信息与原文不同。
    好处：**完全绕开被拦的 PDF 端点**，且正文、图表、参考文献都在。

用法：
    venv/Scripts/python.exe scripts/sd_print_pdf.py --url <文章页 URL> --out logs/x.pdf
"""
import argparse
import base64
import json
import re
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'scripts'))

import browser_verify_fetch as bv  # noqa: E402

# 打印前隐藏的页面装饰（导航、广告、推荐、页脚）——只留论文本体
HIDE_CSS = """
header, footer, nav, aside,
.accessbar, .accessbar-utility-component,
#related-articles, .related-articles, #recommended-articles,
.article-recommendations, .publication-cover, .ad, .advertisement,
.cookie-banner, #cookie-banner, .js-cookie-banner,
.PublicationCover, .publication-cover-image, .article-tools,
#article-tools, .download-article, .share-links, .social-share,
.references-toolbar, #references-toolbar { display: none !important; }
body { background: #fff !important; }
article, .Body, #body, section#body { box-shadow: none !important; }
@page { margin: 14mm 12mm; }
"""

META_JS = r"""
() => {
  const g = (n) => { const m = document.querySelector('meta[name="' + n + '"]');
                     return m ? m.getAttribute('content') : ''; };
  const body = document.querySelector('#body, .Body, article, main') || document.body;
  const txt = (body ? body.innerText : '') || '';
  return {
    title: g('citation_title') || document.title,
    doi: g('citation_doi'),
    journal: g('citation_journal_title'),
    date: g('citation_publication_date') || g('citation_date'),
    authors: Array.from(document.querySelectorAll('meta[name="citation_author"]'))
                 .map(m => m.getAttribute('content')).slice(0, 12),
    body_len: txt.length,
    has_refs: /references|参考文献/i.test(txt),
    fig_count: document.querySelectorAll('figure, .figure, img.figure-img').length,
  };
}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', required=True)
    ap.add_argument('--profile', default='sciencedirect_auth')
    ap.add_argument('--out', default=str(BASE / 'logs' / 'sd_print.pdf'))
    ap.add_argument('--challenge-budget', type=int, default=60,
                    help='遇挑战最多等多久；超时立刻放弃，避免把 IP 评分拉更低')
    args = ap.parse_args()

    prof = BASE / 'state' / 'browser_profiles' / args.profile
    rep = {'url': args.url}
    t0 = time.time()

    with bv.get_sync_playwright('patchright')() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(prof), channel='msedge', headless=False,
            no_viewport=True, chromium_sandbox=True, accept_downloads=True,
            locale='zh-CN', timezone_id='Asia/Shanghai', args=bv.LAUNCH_ARGS)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(args.url, wait_until='domcontentloaded', timeout=90000)
            print('[%ds] 落地: %s' % (int(time.time() - t0), page.url[:110]), flush=True)

            if bv.challenge_kind(page):
                print('  遇挑战，最多等 %ds…' % args.challenge_budget, flush=True)
                ok = bv.wait_until_ready(page, args.challenge_budget)
                print('  挑战结果: %s（%ds）' % (ok, int(time.time() - t0)), flush=True)
                if not ok:
                    rep['status'] = 'blocked_captcha'
                    print('  ✗ 挑战未过，按预案放弃（不重试）', flush=True)
                    return _finish(rep, args, ctx, page, t0)

            # 等正文出现（机构权限下应能拿到全文）
            sel = '#body, .Body, article, main'
            try:
                page.wait_for_selector(sel, timeout=30000)
            except Exception:
                print('  [warn] 未等到正文容器', flush=True)
            try:
                page.wait_for_load_state('networkidle', timeout=20000)
            except Exception:
                pass

            # 触发懒加载图片：分段滚到底再回顶
            try:
                for _ in range(6):
                    page.evaluate('window.scrollBy(0, window.innerHeight * 0.9)')
                    page.wait_for_timeout(600)
                page.evaluate('window.scrollTo(0, 0)')
                page.wait_for_timeout(800)
            except Exception:
                pass

            meta = page.evaluate(META_JS)
            rep['meta'] = meta
            print('  标题: %s' % (meta.get('title') or '')[:90], flush=True)
            print('  DOI : %s | 正文 %d 字符 | 图 %d | 有参考文献 %s' % (
                meta.get('doi') or '-', meta.get('body_len') or 0,
                meta.get('fig_count') or 0, meta.get('has_refs')), flush=True)

            # 注入打印样式
            page.add_style_tag(content=HIDE_CSS)

            # CDP printToPDF（有头模式也能用，page.pdf() 在 headed 下不支持）
            cdp = ctx.new_cdp_session(page)
            res = cdp.send('Page.printToPDF', {
                'printBackground': True,
                'paperWidth': 8.27, 'paperHeight': 11.69,   # A4
                'marginTop': 0.55, 'marginBottom': 0.55,
                'marginLeft': 0.47, 'marginRight': 0.47,
                'preferCSSPageSize': False,
                'displayHeaderFooter': False,
            })
            data = base64.b64decode(res['data'])
            outp = Path(args.out)
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_bytes(data)
            print('  已生成 %s（%d 字节，%.1f MB）' % (
                outp.name, len(data), len(data) / 1048576), flush=True)
            rep.update({'status': 'ok', 'out': str(outp), 'bytes': len(data),
                        'magic': data[:4].decode('latin1')})

            # 验收：页数 / 文字量 / DOI 是否出现在 PDF 里
            try:
                import pymupdf
                doc = pymupdf.open(str(outp))
                text = ''.join(pg.get_text() for pg in doc)
                rep['verify'] = {
                    'pages': doc.page_count,
                    'chars': len(text),
                    'has_doi': (meta.get('doi') or 'NONE') in text,
                    'has_title': (meta.get('title') or '')[:30] in text,
                    'head': re.sub(r'\s+', ' ', text[:160]),
                }
                print('  验收: %d 页 / %d 字符 / DOI在文内=%s / 标题在文内=%s' % (
                    doc.page_count, len(text), rep['verify']['has_doi'],
                    rep['verify']['has_title']), flush=True)
                print('  首段: %s' % rep['verify']['head'][:140], flush=True)
                doc.close()
            except Exception as e:  # noqa: BLE001
                print('  [warn] 验收失败: %s' % str(e)[:90], flush=True)

            return _finish(rep, args, ctx, page, t0)
        except Exception as e:  # noqa: BLE001
            rep['status'] = 'err'
            rep['note'] = '%s: %s' % (type(e).__name__, str(e)[:160])
            print('  ✗ 异常:', rep['note'], flush=True)
            return _finish(rep, args, ctx, page, t0)


def _finish(rep, args, ctx, page, t0):
    rep['elapsed'] = round(time.time() - t0, 1)
    try:
        rep['final_url'] = page.url
    except Exception:
        pass
    out = BASE / 'logs' / 'sd_print_report.json'
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding='utf-8')
    print('\nRESULT_JSON:', json.dumps(
        {k: rep[k] for k in ('status', 'elapsed', 'bytes', 'verify')
         if k in rep}, ensure_ascii=False), flush=True)
    time.sleep(1)
    try:
        ctx.close()
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
