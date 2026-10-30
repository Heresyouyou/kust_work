# -*- coding: utf-8 -*-
"""
论文 PDF 下载器。
输入：title + url（DOI/arXiv/出版商URL）
输出：本地 PDF 路径 或 失败原因。
策略：
  1. arXiv → 直接 https://arxiv.org/pdf/<id>
  2. DOI → 用 crossref 解析 DOI，再尝试 Unpaywall / 出版商 PDF 直链
  3. 非 OA 则只记录 metadata，不下载

失败时返回 metadata 字典，供后续统一仓库索引。
"""
import json
import os
import re
import ssl
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote, unquote

from paths import repo as _repo  # noqa: E402

REPO = Path(_repo())
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/pdf,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def safe_name(s, max_len=80):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', str(s))
    s = re.sub(r'_+', '_', s).strip('_ ')
    return s[:max_len] or 'untitled'


def _http(url, accept='application/json', timeout=20, raw=False):
    h = dict(HEADERS)
    h['Accept'] = accept
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
        data = resp.read()
        return data if raw else data.decode('utf-8', errors='replace')


def _save_pdf(data, journal, title, date, suffix=""):
    REPO.mkdir(parents=True, exist_ok=True)
    (REPO / 'by_journal').mkdir(exist_ok=True)
    jdir = REPO / 'by_journal' / safe_name(journal, 40) / date[:4]
    jdir.mkdir(parents=True, exist_ok=True)
    base = f"{date}_{safe_name(title, 60)}{suffix}.pdf"
    jpath = jdir / base
    with open(jpath, 'wb') as f:
        f.write(data)
    # ⚠️ 2026-10-16：by_date 影子目录已停用（内容与 by_journal 全量重复，
    # 且早已停止同步）。唯一事实源是 by_journal，不再双写。
    # 旧目录已改名 by_date.deprecated 观察一周。
    return str(jpath)


def _read_index():
    idx = REPO / 'index.jsonl'
    seen = set()
    if idx.exists():
        with open(idx, encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    seen.add(rec.get('url', ''))
                    seen.add(rec.get('title', ''))
    return seen


def _append_index(meta):
    with open(REPO / 'index.jsonl', 'a', encoding='utf-8') as f:
        f.write(json.dumps(meta, ensure_ascii=False) + '\n')


def _extract_arxiv_id(url):
    m = re.search(r'arxiv\.org/(?:abs|pdf)/(\d+\.\d+|[a-z\-]+/\d+)', url, re.I)
    if m:
        return m.group(1)
    m = re.search(r'(\d{4}\.\d{4,5})', url)
    if m:
        return m.group(1)
    return None


def _extract_doi(url):
    m = re.search(r'10\.\d{4,9}/[^\s"<>]+', url)
    if m:
        return m.group(0).rstrip('.,')
    return None


def download(title, url, journal='unknown', date=None, force=False):
    """
    尝试下载 PDF。返回 dict：
      {'ok': True, 'pdf_path': ..., 'meta': {...}} 或
      {'ok': False, 'reason': ..., 'meta': {...}}
    """
    date = date or time.strftime('%Y-%m-%d')
    seen = _read_index()
    if not force and (url in seen or title in seen):
        return {'ok': False, 'reason': 'already_indexed', 'meta': None}

    # 1. arXiv
    arxiv_id = _extract_arxiv_id(url)
    if arxiv_id:
        pdf_url = f'https://arxiv.org/pdf/{arxiv_id}.pdf'
        try:
            data = _http(pdf_url, accept='application/pdf', timeout=25, raw=True)
            if len(data) > 50000:
                jpath = _save_pdf(data, journal, title, date, '_arxiv')
                meta = {
                    'title': title, 'url': url, 'journal': journal, 'date': date,
                    'pdf_url': pdf_url, 'repo_path': jpath,
                    'size': len(data), 'source': 'arxiv', 'indexed_at': time.strftime('%Y-%m-%dT%H:%M:%S')
                }
                _append_index(meta)
                return {'ok': True, 'pdf_path': jpath, 'meta': meta}
        except Exception as e:
            pass

    # 2. DOI → CrossRef 元数据 + 直链
    doi = _extract_doi(url)
    if doi:
        try:
            cr_url = f'https://api.crossref.org/works/{quote(doi, safe="")}'
            cr = json.loads(_http(cr_url, timeout=20))
            item = cr.get('message', {})
            links = item.get('link', []) or item.get('URL', [])
            pdf_url = None
            # 优先找 license URL 或 OA link
            for link in links:
                if isinstance(link, dict):
                    if link.get('content-type') == 'application/pdf' or 'pdf' in link.get('URL', '').lower():
                        pdf_url = link['URL']
                        break
            # 尝试 unpaywall（需要 email）
            if not pdf_url:
                try:
                    up_url = f'https://api.unpaywall.org/v2/{doi}?email=youyou@example.com'
                    up = json.loads(_http(up_url, timeout=15))
                    best = up.get('best_oa_location', {})
                    if best and best.get('url_for_pdf'):
                        pdf_url = best['url_for_pdf']
                except Exception:
                    pass
            if pdf_url:
                try:
                    data = _http(pdf_url, accept='application/pdf', timeout=25, raw=True)
                    if len(data) > 50000:
                        jpath = _save_pdf(data, journal, title, date, '_doi')
                        meta = {
                            'title': title, 'url': url, 'journal': journal, 'date': date,
                            'pdf_url': pdf_url, 'doi': doi, 'repo_path': jpath,
                            'size': len(data), 'source': 'doi', 'indexed_at': time.strftime('%Y-%m-%dT%H:%M:%S')
                        }
                        _append_index(meta)
                        return {'ok': True, 'pdf_path': jpath, 'meta': meta}
                except Exception:
                    pass
        except Exception:
            pass

    # 3. 非 OA：只把元数据返回给调用方，**不写台账**（2026-10-21 用户口径）。
    #
    # 旧行为：这里 `_append_index(meta)` 写一条 source=metadata_only、repo_path=None
    # 的记录，等于「没下到 PDF 也入库」。后果是 knowledge base 里混进只有题录的
    # 条目，journals 页判「已归档」时还要在四处代码里专门绕开它。
    # 现口径：当天没拿到 PDF 的论文一律不入库，由人工下载后经
    # scripts/inbox_ingest.py 投放补入。故此处不再落任何记录。
    meta = {
        'title': title, 'url': url, 'journal': journal, 'date': date,
        'doi': doi, 'repo_path': None,
        'size': 0, 'source': 'metadata_only', 'indexed_at': time.strftime('%Y-%m-%dT%H:%M:%S')
    }
    return {'ok': False, 'reason': 'not_open_access', 'meta': meta}


def download_batch(items, delay=0.5):
    """items: list of {title, url, journal, date}"""
    results = []
    for it in items:
        r = download(it.get('title', ''), it.get('url', ''), it.get('journal', 'unknown'), it.get('date'))
        results.append(r)
        time.sleep(delay)
    return results


if __name__ == '__main__':
    # 简单测试
    print(download(
        title="A test arXiv paper",
        url="https://arxiv.org/abs/2401.12345",
        journal="arXiv",
        date="2024-01-15"
    ))
