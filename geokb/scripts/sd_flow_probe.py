#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ScienceDirect 全链路对照探针：打开 → 过验证 → 分路径取 PDF。

一次会话内按顺序试，互不干扰，最后出对照报告：

    路径 A  直接下载（citation_pdf_url / pdfft?download=true / 页面下载按钮）
    路径 B1 View PDF 打印（进 viewer 后 CDP printToPDF —— 预期拿到空白，需实测）
    路径 B2 文章页打印（对 HTML 文章页 printToPDF，网页排版）

复用 browser_verify_fetch 的：
    LAUNCH_ARGS / get_sync_playwright / challenge_kind / _clear_checkbox /
    has_article / is_transition

用法：
    python scripts/sd_flow_probe.py --url <文章页> [--minutes 8] [--no-print]

产出：
    state/inbox/*.pdf                      成功的 PDF
    logs/sd_flow_probe_<ts>.json           对照报告
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SCRIPTS = BASE / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from browser_verify_fetch import (  # noqa: E402
    LAUNCH_ARGS, get_sync_playwright, challenge_kind, _clear_checkbox,
    has_article,
)

PROFILE = BASE / 'state' / 'browser_profiles' / '_real_edge'
INBOX = BASE / 'state' / 'inbox'
LOGS = BASE / 'logs'


def log(m):
    print(m, flush=True)


def pii_of(url):
    m = re.search(r'/pii/([A-Z0-9]+)', url or '')
    return m.group(1) if m else ''


def probe_pdf(path):
    """PDF 体检：页数 / 提取字符数 / 是否有文本层。"""
    try:
        import pymupdf
        d = pymupdf.open(str(path))
        pages = d.page_count
        chars = 0
        for i in range(min(pages, 5)):
            chars += len((d[i].get_text() or '').strip())
        d.close()
        return {'pages': pages, 'chars_head5': chars,
                'text_layer': chars > 200,
                'bytes': Path(path).stat().st_size}
    except Exception as e:  # noqa: BLE001
        return {'error': f'{type(e).__name__}: {e}'}


def save_bytes(data: bytes, tag: str, pii: str):
    INBOX.mkdir(parents=True, exist_ok=True)
    name = f'sd_{pii or "unknown"}_{tag}_{datetime.now():%H%M%S}.pdf'
    p = INBOX / name
    p.write_bytes(data)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='https://www.sciencedirect.com/science/'
                                    'article/pii/S0169136823004328')
    ap.add_argument('--verify-timeout', type=int, default=150)
    ap.add_argument('--no-print', action='store_true', help='跳过路径 B')
    ap.add_argument('--manual', action='store_true',
                    help='人工解锁：不自动点复选框，等用户手动过验证后再继续')
    ap.add_argument('--unlock-wait', type=int, default=600,
                    help='人工解锁最长等待秒数（默认 600）')
    args = ap.parse_args()

    pii = pii_of(args.url)
    rep = {'url': args.url, 'pii': pii,
           'ts': datetime.now().isoformat(timespec='seconds'), 'paths': {}}

    if not PROFILE.is_dir():
        log(f'[abort] profile 不存在: {PROFILE}')
        return 2
    LOGS.mkdir(parents=True, exist_ok=True)
    INBOX.mkdir(parents=True, exist_ok=True)

    sync_playwright = get_sync_playwright('auto')
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            channel='msedge',
            headless=False,
            args=LAUNCH_ARGS,
            chromium_sandbox=True,
            accept_downloads=True,
            no_viewport=True,
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # ── 步骤 1：打开文章页 ────────────────────────────────────────
        t0 = time.time()
        log(f'[1] 打开文章页 {args.url}')
        try:
            page.goto(args.url, wait_until='domcontentloaded', timeout=90000)
        except Exception as e:  # noqa: BLE001
            log(f'    goto 异常（继续观察）: {type(e).__name__}: {e}')

        # ── 步骤 2：过验证 ────────────────────────────────────────────
        passed, kinds = False, []
        budget = args.unlock_wait if args.manual else args.verify_timeout
        deadline = time.time() + budget
        if args.manual:
            log(f'[2] 人工解锁模式：请在浏览器窗口里自己过验证，最多等 {budget}s')
            log('    通过后我会自动继续跑 A / B 两条路径，不需要你再说一次')
        last_kind = ''
        while time.time() < deadline:
            k = challenge_kind(page)
            if k:
                if k not in kinds:
                    kinds.append(k)
                    log(f'    挑战: {k}')
                last_kind = k
                if not args.manual:
                    _clear_checkbox(page)
                time.sleep(5)
                continue
            if has_article(page):
                passed = True
                break
            if last_kind:
                log('    挑战消失，等待文章加载 …')
                last_kind = ''
            time.sleep(3)

        ver_sec = round(time.time() - t0, 1)
        rep['verify'] = {'passed': passed, 'kinds': kinds, 'elapsed_sec': ver_sec,
                         'manual': bool(args.manual),
                         'title': (page.title() or '')[:120]}
        log(f'[2] 验证 {"通过" if passed else "未通过"}（{ver_sec}s，挑战={kinds or "无"}）')
        if not passed:
            log('    放弃后续路径（不重复闯关）')
            rep['note'] = 'verify_failed'
            _write(rep)
            _close(ctx)
            return 1

        # ── 步骤 3：采集页面上的 PDF 线索 ────────────────────────────
        links = {}
        try:
            for sel in ('meta[name="citation_pdf_url"]',):
                el = page.query_selector(sel)
                if el:
                    links['citation_pdf_url'] = el.get_attribute('content') or ''
            for a in page.query_selector_all('a[href]'):
                h = a.get_attribute('href') or ''
                if not h:
                    continue
                if 'pdfft' in h or 'article-pdf' in h or 'pdf.sciencedirectassets' in h:
                    key = 'view_or_download'
                    if 'download=true' in h:
                        key = 'download_link'
                    elif '/pdfft' in h:
                        key = 'pdfft_link'
                    txt = (a.inner_text() or '').strip()[:40]
                    links.setdefault(key, []).append({'href': h[:300], 'text': txt})
        except Exception as e:  # noqa: BLE001
            links['error'] = f'{type(e).__name__}: {e}'

        body_head = ''
        try:
            body_head = (page.inner_text('body') or '')[:600]
        except Exception:
            pass
        sub = 'subscribed' if ('subscribed content' in body_head.lower()
                               or 'via kunming' in body_head.lower()) else 'unknown'
        rep['article'] = {'links': links, 'access': sub,
                          'final_url': page.url[:200]}
        log(f'[3] 机构授权: {sub}；PDF 线索 {len(links)} 类')

        # ── 路径 A：直接下载 ─────────────────────────────────────────
        cand_a = []
        if links.get('citation_pdf_url'):
            cand_a.append(('meta.citation_pdf_url', links['citation_pdf_url']))
        for it in (links.get('download_link') or [])[:2]:
            cand_a.append(('page.download_link', it['href']))
        cand_a.append(('constructed_pdfft',
                       f'https://www.sciencedirect.com/science/article/pii/'
                       f'{pii}/pdfft?isDTMRedir=true&download=true'))

        a_rep = {'tried': [], 'ok': False}
        for tag, url in cand_a:
            if not url:
                continue
            if url.startswith('/'):
                url = 'https://www.sciencedirect.com' + url
            item = {'cand': tag, 'url': url[:220]}
            log(f'[A] 试直接下载（{tag}）…')
            try:
                with page.expect_download(timeout=90000) as info:
                    try:
                        page.goto(url, timeout=60000)
                    except Exception:
                        pass        # attachment 响应会让 goto 抛 ERR_ABORTED
                dl = info.value
                dest = INBOX / (dl.suggested_filename or f'sd_{pii}_dl.pdf')
                if dest.suffix.lower() != '.pdf':
                    dest = dest.with_suffix('.pdf')
                dl.save_as(str(dest))
                item.update(status='ok', path=str(dest), **probe_pdf(dest))
                a_rep['ok'] = True
                log(f'    ✓ {item.get("bytes")} bytes, {item.get("pages")} 页, '
                    f'{item.get("chars_head5")} 字符')
            except Exception as e:  # noqa: BLE001
                item.update(status='fail', error=f'{type(e).__name__}: {str(e)[:120]}')
                # 记录落到哪个 URL（区分「无权限」与「CF 拦截」）
                try:
                    item['landed'] = page.url[:200]
                    head = (page.inner_text('body') or '')[:300]
                    item['body_head'] = head
                except Exception:
                    pass
                log(f'    ✗ {item["error"]}')
            a_rep['tried'].append(item)
            if a_rep['ok']:
                break
            time.sleep(3)
        rep['paths']['A_direct_download'] = a_rep

        # ── 路径 B：打印 ────────────────────────────────────────────
        if not args.no_print:
            rep['paths'].update(_print_paths(ctx, page, pii, links, rep))

        _write(rep)
        _close(ctx)

    _summary(rep)
    return 0


def _print_paths(ctx, page, pii, links, rep):
    """B1 = viewer 打印；B2 = 文章页打印。"""
    out = {}

    def cdp_pdf(tag):
        cdp = ctx.new_cdp_session(page)
        try:
            res = cdp.send('Page.printToPDF', {
                'printBackground': True, 'preferCSSPageSize': True,
                'marginTop': 0.3, 'marginBottom': 0.3,
                'marginLeft': 0.3, 'marginRight': 0.3,
            })
        finally:
            try:
                cdp.detach()
            except Exception:
                pass
        import base64
        data = base64.b64decode(res['data'])
        dest = save_bytes(data, tag, pii)
        return dest

    # B1: 进 viewer
    b1 = {'ok': False}
    viewer_url = None
    for it in (links.get('pdfft_link') or []) + (links.get('view_or_download') or []):
        h = it.get('href') or ''
        if '/pdfft' in h:
            viewer_url = h if h.startswith('http') else 'https://www.sciencedirect.com' + h
            break
    if not viewer_url:
        viewer_url = (f'https://www.sciencedirect.com/science/article/pii/{pii}/pdfft')
    log(f'[B1] 打开 viewer: {viewer_url[:120]}')
    try:
        page.goto(viewer_url, timeout=90000)
        time.sleep(8)
        b1['landed'] = page.url[:200]
        b1['title'] = (page.title() or '')[:120]
        try:
            b1['has_embed'] = bool(page.query_selector('embed[type="application/pdf"]'))
        except Exception:
            b1['has_embed'] = None
        dest = cdp_pdf('viewer')
        b1.update(ok=True, path=str(dest), **probe_pdf(dest))
        log(f'    打印结果: {b1.get("pages")} 页 / {b1.get("chars_head5")} 字符')
    except Exception as e:  # noqa: BLE001
        b1.update(status='fail', error=f'{type(e).__name__}: {str(e)[:120]}')
        log(f'    ✗ {b1["error"]}')
    out['B1_viewer_print'] = b1

    # B2: 回文章页打印
    b2 = {'ok': False}
    log('[B2] 回文章页并打印 HTML …')
    try:
        page.goto(rep['url'], wait_until='domcontentloaded', timeout=90000)
        time.sleep(6)
        # 触发懒加载图（打印前把整页滚一遍）
        try:
            page.evaluate("""async () => {
                await new Promise(r => {
                    let y = 0; const step = () => {
                        y += window.innerHeight * 0.8;
                        window.scrollTo(0, y);
                        if (y < document.body.scrollHeight) setTimeout(step, 250);
                        else { window.scrollTo(0,0); setTimeout(r, 1200); }
                    }; step();
                });
            }""")
        except Exception:
            pass
        dest = cdp_pdf('article')
        b2.update(ok=True, path=str(dest), **probe_pdf(dest))
        log(f'    打印结果: {b2.get("pages")} 页 / {b2.get("chars_head5")} 字符')
    except Exception as e:  # noqa: BLE001
        b2.update(status='fail', error=f'{type(e).__name__}: {str(e)[:120]}')
        log(f'    ✗ {b2["error"]}')
    out['B2_article_print'] = b2
    return out


def _write(rep):
    LOGS.mkdir(parents=True, exist_ok=True)
    f = LOGS / f'sd_flow_probe_{datetime.now():%Y%m%d_%H%M%S}.json'
    f.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding='utf-8')
    log(f'[report] {f}')


def _close(ctx):
    try:
        ctx.close()
    except Exception:
        pass


def _summary(rep):
    log('\n=== 对照结果 ===')
    if not rep.get('verify', {}).get('passed'):
        log('验证未通过，无有效结论')
        return
    rows = []
    a = rep['paths'].get('A_direct_download') or {}
    rows.append(('A 直接下载', '成功' if a.get('ok') else '失败',
                 str(a.get('tried', [{}])[-1].get('error', ''))[:80]))
    for k, label in (('B1_viewer_print', 'B1 viewer 打印'),
                     ('B2_article_print', 'B2 文章页打印')):
        v = rep['paths'].get(k) or {}
        rows.append((label, '成功' if v.get('ok') else '失败',
                     f"{v.get('pages', '-')} 页 / {v.get('chars_head5', '-')} 字符"))
    for r in rows:
        log(f'  {r[0]:<16} {r[1]:<4} {r[2]}')


if __name__ == '__main__':
    sys.exit(main())
