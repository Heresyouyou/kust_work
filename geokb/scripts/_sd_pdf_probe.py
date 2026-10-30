# -*- coding: utf-8 -*-
"""ScienceDirect 取 PDF 定向诊断。

目的：把「验证过了但 403」这件事拆成三个可证的真问题：
  A. 文章页里到底有没有 View PDF / Download PDF（还是 Get Access = 无权限）
  B. pdfft 链接点击后，真正下发的签名直链（pdf.sciencedirectassets.com）
  C. 那个签名直链用同 context 的 cookie 去 GET，能否拿到 %PDF

复用 browser_verify_fetch 的挑战识别与等待逻辑，不重复实现。
"""
import argparse
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'scripts'))

import browser_verify_fetch as bv  # noqa: E402

PROBE_JS = r"""
() => {
  const out = {url: location.href, title: document.title};
  const q = (s) => Array.from(document.querySelectorAll(s));
  out.pdfft = q('a[href*="pdfft"], a[href*="pdffile"], a[href*="main.pdf"]').slice(0, 8)
      .map(a => ({href: a.getAttribute('href'), text: (a.innerText || '').trim().slice(0, 40),
                  aria: a.getAttribute('aria-label'), cls: a.className}));
  // View PDF 通常是个 <a>/<button>/<span>，文本匹配后取外层可点元素的 outerHTML
  const hit = q('a, button, span, div').filter(e => {
      const t = (e.innerText || '').trim();
      return t.length < 30 && /^(view|download)\s*pdf$/i.test(t);
  });
  out.pdf_elems = hit.slice(0, 6).map(e => ({
      tag: e.tagName, cls: String(e.className).slice(0, 60),
      href: e.getAttribute('href') || (e.closest('a') ? e.closest('a').getAttribute('href') : ''),
      html: e.outerHTML.slice(0, 220)}));
  const meta = document.querySelector('meta[name="citation_pdf_url"]');
  out.citation_pdf_url = meta ? meta.getAttribute('content') : '';
  const body = (document.body ? document.body.innerText : '') || '';
  out.flags = {
    view_pdf: /view\s*pdf|查看\s*pdf/i.test(body),
    download_pdf: /download\s*pdf|下载\s*pdf/i.test(body),
    get_access: /get\s*access|获取访问|purchase\s*pdf/i.test(body),
    institutional: /subscribed content|access through your|institution/i.test(body),
    open_archive: /open\s*archive/i.test(body),
  };
  const m = body.match(/.{0,80}(subscribed content|Kunming University|昆明理工).{0,80}/i);
  out.banner = m ? m[0].replace(/\s+/g, ' ').trim() : '';
  return out;
}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', required=True)
    ap.add_argument('--profile', default='sciencedirect_auth')
    ap.add_argument('--timeout', type=int, default=260)
    ap.add_argument('--out', default=str(BASE / 'logs' / 'sd_pdf_probe.json'))
    args = ap.parse_args()

    prof = BASE / 'state' / 'browser_profiles' / args.profile
    rep = {'url': args.url, 'profile': str(prof)}
    assets, downloads = [], []

    with bv.get_sync_playwright('patchright')() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(prof), channel='msedge', headless=False,
            no_viewport=True, chromium_sandbox=True, accept_downloads=True,
            locale='zh-CN', timezone_id='Asia/Shanghai',
            args=bv.LAUNCH_ARGS)
        ctx.on('request', lambda r: assets.append(r.url)
              if 'sciencedirectassets.com' in r.url else None)
        ctx.on('download', lambda d: downloads.append(d.url or d.suggested_filename))

        def _resp(r):
            u = r.url or ''
            if 'pdfft' in u or 'pdf' in u.split('?')[0][-12:]:
                try:
                    ctype = (r.headers.get('content-type') or '')[:40]
                except Exception:
                    ctype = ''
                resp_log.append('%s %s %s' % (r.status, ctype, u[:110]))

        resp_log = []
        ctx.on('response', _resp)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(args.url, wait_until='domcontentloaded', timeout=90000)
            t0 = time.time()
            if bv.challenge_kind(page):
                print('检测到挑战，等待通过…', flush=True)
                bv.wait_until_ready(page, args.timeout)
                print('等待结束，用时 %ds' % int(time.time() - t0), flush=True)
            # 文章页是 JS 渲染，View PDF 按钮晚于 domcontentloaded 出现
            try:
                page.wait_for_selector('a[href*="pdfft"], a[href*="main.pdf"]', timeout=25000)
                print('pdfft 链接已出现', flush=True)
            except Exception:
                print('等待 pdfft 链接超时，继续看页面状态', flush=True)
            try:
                page.wait_for_load_state('networkidle', timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(3000)
            print('落地 URL:', page.url, flush=True)
            print('标题:', bv._title(page)[:100], flush=True)
            info = page.evaluate(PROBE_JS)
            rep['page'] = info
            print('\n[页面状态] 用时 %ds' % int(time.time() - t0))
            print('  flags:', json.dumps(info['flags'], ensure_ascii=False))
            print('  banner:', info['banner'][:160])
            print('  citation_pdf_url:', (info['citation_pdf_url'] or '')[:150])
            print('  pdfft 链接 %d 个:' % len(info['pdfft']))
            for l in info['pdfft'][:5]:
                print('    ', json.dumps(l, ensure_ascii=False)[:150])
            print('  View PDF 元素 %d 个:' % len(info.get('pdf_elems') or []))
            for e in (info.get('pdf_elems') or [])[:4]:
                print('    ', json.dumps(e, ensure_ascii=False)[:200])

            # B: 优先点真实的 View PDF 元素（带 referer 与同源上下文）
            target = ''
            for l in info['pdfft']:
                h = l.get('href') or ''
                if 'pdfft' in h or 'main.pdf' in h:
                    target = h
                    break
            if not target and info['citation_pdf_url']:
                target = info['citation_pdf_url']
            rep['target'] = target
            print('\n[点击取链] target=%s' % (target[:110] or '(无)'), flush=True)

            clicked = False
            if info.get('pdf_elems'):
                try:
                    with ctx.expect_page(timeout=40000) as pop:
                        page.locator('a[href*="pdfft"], a[href*="main.pdf"]').first.click()
                    np = pop.value
                    try:
                        np.wait_for_load_state('domcontentloaded', timeout=40000)
                    except Exception:
                        pass
                    print('  ✓ 点击后新页:', np.url[:170], flush=True)
                    rep['popup_url'] = np.url
                    clicked = True
                    try:
                        ct = np.evaluate('document.contentType')
                        has_emb = np.evaluate(
                            "!!document.querySelector('embed[type=\"application/pdf\"], "
                            "object[type=\"application/pdf\"]')")
                        body_txt = np.evaluate(
                            "(document.body ? document.body.innerText : '').slice(0,200)")
                    except Exception as e:  # noqa: BLE001
                        ct, has_emb, body_txt = '(读取失败 %s)' % type(e).__name__, False, ''
                    print('     contentType=%r pdf_viewer=%s' % (ct, has_emb), flush=True)
                    print('     body: %s' % re.sub(r'\s+', ' ', body_txt)[:180], flush=True)
                    rep['popup_ct'] = str(ct)
                    rep['popup_pdf_viewer'] = bool(has_emb)
                    rep['popup_body'] = body_txt[:200]
                except Exception as e:  # noqa: BLE001
                    print('  点击未产生新页（%s），等网络请求…' % type(e).__name__, flush=True)
                    clicked = True
                time.sleep(6)
            elif target:
                href = target if target.startswith('http') else (
                    'https://www.sciencedirect.com' + target)
                page.evaluate('(u) => { location.href = u }', href)
                time.sleep(8)
                print('  直接跳转后 URL:', page.url[:170], flush=True)
                rep['popup_url'] = page.url

            print('\n[assets 请求] %d 条' % len(assets), flush=True)
            for u in assets[:6]:
                print('  ', u[:170])
            print('\n[pdfft 响应] %d 条' % len(resp_log), flush=True)
            for line in resp_log[:8]:
                print('  ', line)
            rep['resp_log'] = resp_log[:10]
            rep['assets'] = assets[:20]
            rep['downloads'] = downloads[:10]
            if downloads:
                print('  下载事件:', downloads[:5])

            # C: 用同 context 的 cookie 直接 GET 签名直链
            cand = [u for u in assets if '.pdf' in u.lower()]
            if not cand and rep.get('popup_url', '').startswith('http'):
                cand = [rep['popup_url']]
            if cand:
                u = cand[0]
                print('\n[验证直链] %s' % u[:130], flush=True)
                try:
                    r = ctx.request.get(u, timeout=90000)
                    body = r.body()
                    print('  http=%s len=%d magic=%r' % (
                        r.status, len(body), body[:4]), flush=True)
                    rep['direct'] = {'status': r.status, 'len': len(body),
                                     'magic': body[:8].decode('latin1')}
                    if body[:4] == b'%PDF':
                        outp = BASE / 'logs' / 'sd_probe_direct.pdf'
                        outp.write_bytes(body)
                        print('  ✓ 已落盘', outp, flush=True)
                        rep['saved'] = str(outp)
                except Exception as e:  # noqa: BLE001
                    print('  直链请求失败:', type(e).__name__, str(e)[:90], flush=True)
        finally:
            Path(args.out).write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                                      encoding='utf-8')
            print('\n报告已写:', args.out, flush=True)
            time.sleep(2)
            try:
                ctx.close()
            except Exception:
                pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
