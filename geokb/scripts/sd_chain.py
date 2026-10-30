# -*- coding: utf-8 -*-
"""ScienceDirect 全链路探测（按 PII）

    python scripts/sd_chain.py --pii S0169136826004932

链路：
  A. curl_cffi(chrome) + profile 真 cookie → 文章页 HTML（取 md5 / pid）
  B. /science/article/pii/<PII>/pdf?md5=..&pid=..            （出版社直链）
  C. /science/article/pii/<PII>/pdfft?isDTMRedir=true         （下载重定向）
  D. /sdfe/pdf/download/eid/1-s2.0-<PII>/first-page-pdf       （首页预览）
  E. api.elsevier.com/content/article/pii/<PII>?httpAccept=.. （官方 API）
"""
import argparse
import re
import sys
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
sys.path.insert(0, str(BASE / 'scripts'))
INBOX = BASE / 'state' / 'inbox'
INBOX.mkdir(parents=True, exist_ok=True)

from browser_verify_fetch import get_sync_playwright, LAUNCH_ARGS  # noqa
from sso_keeper import PROFILE  # noqa
from curl_cffi import requests as creq  # noqa


def get_jar():
    sync_playwright = get_sync_playwright('auto')
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), channel='msedge', headless=True,
            args=LAUNCH_ARGS, chromium_sandbox=True)
        jar = {c['name']: c['value'] for c in ctx.cookies()
               if 'elsevier' in c.get('domain', '') or 'sciencedirect' in c.get('domain', '')}
        ctx.close()
    return jar


def classify(r):
    b = r.content
    if b[:4] == b'%PDF':
        return 'PDF'
    t = b.decode('utf-8', 'ignore')
    for k, tag in (('CLOUDFLARE_ERROR_1000S_BOX', 'CF-1000S'),
                   ('Are you a robot', 'ROBOT'),
                   ('problem providing the content', 'CF-PROBLEM'),
                   ('Access to subscribed content', 'SUBSCRIBED')):
        if k in t:
            return tag
    return '-'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pii', default='S0169136826004932')
    args = ap.parse_args()
    PII = args.pii
    A = 'https://www.sciencedirect.com/science/article/pii/' + PII

    jar = get_jar()
    s = creq.Session(impersonate='chrome', trust_env=False)
    for k, v in jar.items():
        s.cookies.set(k, v, domain='.sciencedirect.com')
    H = {'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
         'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'}

    print('cookie: %d | 关键 %s' % (len(jar), [k for k in ('EUID', 'SD_REMOTEACCESS',
                                                           'cf_clearance') if k in jar]))

    # ── A ────────────────────────────────────────────────────────────────
    r = s.get(A, headers=H, timeout=60)
    html = r.content.decode('utf-8', 'ignore')
    print('A 文章页      %s %7d %s' % (r.status_code, len(r.content), classify(r)))
    m_md5 = re.search(r'"pdfDownload":\{.*?"md5":"([0-9a-f]{32})"', html)
    m_pid = re.search(r'"pdfDownload":\{.*?"pid":"([^"]+)"', html)
    md5 = m_md5.group(1) if m_md5 else None
    pid = m_pid.group(1) if m_pid else None
    oa = 'creativecommons.org/licenses' in html
    print('   md5=%s pid=%s CC=%s' % (md5, pid, oa))

    H2 = {'Accept': 'application/pdf,text/html;q=0.9,*/*;q=0.8', 'Referer': A}
    cands = []
    if md5 and pid:
        cands.append(('B /pdf', A + '/pdf?md5=%s&pid=%s' % (md5, pid)))
        cands.append(('C /pdfft+md5', A + '/pdfft?md5=%s&pid=%s&isDTMRedir=true&download=true' % (md5, pid)))
    cands.append(('C2 /pdfft', A + '/pdfft?isDTMRedir=true&download=true'))
    cands.append(('D first-page', 'https://www.sciencedirect.com/sdfe/pdf/download/eid/1-s2.0-%s/first-page-pdf' % PII))

    for tag, u in cands:
        try:
            rr = s.get(u, headers=H2, timeout=120)
            cls = classify(rr)
            print('%-13s %s %7d %-11s %s' % (tag, rr.status_code, len(rr.content), cls,
                                             str(rr.url)[:60]))
            if cls == 'PDF':
                out = INBOX / ('%s_%s.pdf' % (tag.split()[1].strip('/'), PII))
                out.write_bytes(rr.content)
                print('   ✅ 保存 %s（%.1f MB）' % (out.name, len(rr.content) / 1048576))
        except Exception as e:
            print('%-13s ERR %s' % (tag, type(e).__name__))

    # ── E: Elsevier API ──────────────────────────────────────────────────
    print('E Elsevier API')
    for acc, label in (('application/pdf', 'pdf'), ('text/xml', 'xml')):
        u = 'https://api.elsevier.com/content/article/pii/%s?httpAccept=%s' % (PII, acc)
        try:
            rr = creq.get(u, impersonate='chrome', timeout=60,
                          headers={'Accept': acc})
            cls = classify(rr)
            print('   %-4s %s %7d %-10s %s' % (label, rr.status_code, len(rr.content), cls,
                                               rr.text[:70].replace('\n', ' ') if cls != 'PDF' else ''))
            if cls == 'PDF':
                out = INBOX / ('api_%s.pdf' % PII)
                out.write_bytes(rr.content)
                print('   ✅ 保存 %s（%.1f MB）' % (out.name, len(rr.content) / 1048576))
        except Exception as e:
            print('   %-4s ERR %s' % (label, type(e).__name__))


if __name__ == '__main__':
    main()
