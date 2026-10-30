#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PDF 身份核验（独立小工具，供 PostNews / GeoKB 通过子进程调用）

作用：判断一个 PDF 是否**确实是目标这篇文献**，而不是同名/近似/综述/勘误。
判据（按可信度顺序）：
  1. DOI 命中：把正文前 N 页的文本压平（去空格与标点）后包含同格式 DOI 串；
  2. 标题命中：标题归一化后与正文前 N 页做**包含匹配** + difflib 比率。
只做「是不是这篇」的判定，不做模糊推荐。

用法：
  python pdf_identity.py --path a.pdf --doi 10.1016/j.oregr.2020.103456 --title "..."
输出：单行 JSON
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys


def _flat(s: str) -> str:
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', (s or '').lower())


def norm_doi(s: str) -> str:
    s = re.sub(r'^(https?://)?(dx\.)?doi\.org/', '', (s or '').strip(), flags=re.I)
    s = re.sub(r'^doi:\s*', '', s, flags=re.I)
    return s.strip().rstrip('.,;)').lower()


def head_text(path: str, pages: int = 3) -> tuple[str, int]:
    import pymupdf
    doc = pymupdf.open(path)
    n = doc.page_count
    parts = []
    for i in range(min(pages, n)):
        parts.append(doc[i].get_text() or '')
    doc.close()
    return '\n'.join(parts), n


def check(path: str, doi: str = '', title: str = '', pages: int = 3) -> dict:
    out = {'path': path, 'ok': False, 'doi_in_pdf': False,
           'title_ratio': 0.0, 'reason': ''}
    try:
        txt, n = head_text(path, pages)
    except Exception as e:
        out['reason'] = 'open_failed: %r' % (e,)
        return out
    out['pages'] = n
    if not (txt or '').strip():
        out['reason'] = 'no_text_layer'
        return out

    flat = _flat(txt)
    d = norm_doi(doi)
    if d:
        # DOI 里可能被排版插入换行/连字符，压平后两侧一起比
        if _flat(d) in flat:
            out.update(ok=True, doi_in_pdf=True, reason='doi_match')
            return out

    nt = _flat(title)
    if nt and len(nt) >= 20:
        key = nt[:80]
        if key in flat:
            out.update(ok=True, title_ratio=1.0, reason='title_contained')
            return out
        # 退化情形：标题被排版拆行/换大小写 → 用滑动窗口近似
        win = len(key) + 40
        best = 0.0
        for i in range(0, max(1, min(len(flat), 4000) - len(key) + 1), 40):
            seg = flat[i:i + win]
            r = difflib.SequenceMatcher(None, key, seg[:len(key)]).ratio()
            if r > best:
                best = r
        out['title_ratio'] = round(best, 3)
        if best >= 0.92:
            out.update(ok=True, reason='title_ratio')
            return out

    out['reason'] = 'no_match' if d else 'no_match_no_doi'
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--path', required=True)
    ap.add_argument('--doi', default='')
    ap.add_argument('--title', default='')
    ap.add_argument('--pages', type=int, default=3)
    a = ap.parse_args()
    print(json.dumps(check(a.path, a.doi, a.title, a.pages), ensure_ascii=False))


if __name__ == '__main__':
    main()
