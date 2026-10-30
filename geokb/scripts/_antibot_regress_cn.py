# -*- coding: utf-8 -*-
"""中文源站回归：确认 curl_cffi 通道没有把原本能下的源站搞坏。

从台账挑「各中文源站、尚未归档」的各 1 篇，对比 fetch_one 的状态。
"""
import json
import os
import sys
import time

os.environ.setdefault('GEOKB_BROWSER_VERIFY', '0')
sys.path.insert(0, 'D:/PostNews')
sys.path.insert(0, 'D:/ZSK/geokb/scripts')

LEDGER = 'D:/PostNews/journal_ledger.json'
INDEX = 'D:/ZSK/geokb/state/papers_index.jsonl'


def norm(t):
    return ''.join(ch for ch in (t or '').lower() if ch.isalnum())


def archived_titles():
    keys = set()
    try:
        for line in open(INDEX, encoding='utf-8'):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            rp = (r.get('repo_path') or '')
            if 'papers_repo' not in rp:
                continue                    # 题录条目不算已归档
            keys.add(norm(r.get('title')))
    except FileNotFoundError:
        pass
    return keys


def pick():
    import antibot_session as ab
    src_mod = None
    from journal_pdf_fetch import sources  # noqa
    d = json.load(open(LEDGER, encoding='utf-8'))
    have = archived_titles()
    want = {'ysxb': None, 'dqkx': None, 'geojournals': None, 'sciengine': None,
            'esf': None, 'ddgzyckx': None, 'kcdz': None}
    for j, arts in (d.get('journals') or {}).items():
        for a in arts or []:
            u = a.get('url') or ''
            if not u.startswith('http'):
                continue
            if norm(a.get('title')) in have:
                continue
            for k, pat in (('ysxb', 'ysxb.ac.cn'), ('ysxb', 'doi.org/10.18654'),
                           ('dqkx', 'earth-science.net'), ('dqkx', 'doi.org/10.3799'),
                           ('geojournals', 'geojournals.cn'),
                           ('sciengine', 'sciengine'), ('sciengine', 'doi.org/10.3724'),
                           ('esf', 'earthsciencefrontiers'),
                           ('ddgzyckx', 'ddgzyckx'), ('ddgzyckx', 'doi.org/10.16539'),
                           ('kcdz', 'kcdz.ac.cn')):
                if pat in u and want.get(k) is None:
                    want[k] = (j, (a.get('title') or '')[:70], u)
    return [(k, v) for k, v in want.items() if v]


def main():
    import antibot_session as ab
    from journal_pdf_fetch import fetch_pdf
    print('引擎:', ab.describe()['engine'])
    cases = pick()
    print('样本 %d 个源站\n' % len(cases))
    rows = []
    for src, (j, title, url) in cases:
        t0 = time.time()
        r = fetch_pdf(j, title, url)
        data = r.pop('pdf_bytes', None)
        n = len(data) if data else 0
        print('%-12s %-12s %7d B %5.1fs | %s' % (
            src, r.get('status'), n, time.time() - t0, str(r.get('note'))[:70]))
        print('             %s | %s' % (j[:24], title[:50]))
        rows.append({'src': src, 'journal': j, 'title': title, 'url': url,
                     'status': r.get('status'), 'bytes': n,
                     'note': str(r.get('note'))[:200]})
    json.dump(rows, open('D:/ZSK/geokb/logs/antibot_regress_cn.json', 'w',
                         encoding='utf-8'), ensure_ascii=False, indent=1)
    ok = sum(1 for r in rows if r['status'] == 'ok')
    print('\n成功 %d / %d' % (ok, len(rows)))


if __name__ == '__main__':
    sys.exit(main())
