#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""手动下载 PDF 收件箱：本地 PDF → NAS papers_repo → 台账 →（可选）入库

⚠️ 2026-10-21 起已被 `scripts/inbox_ingest.py` 取代，**不要再用本脚本**
------------------------------------------------------------------------
新脚本的差别（用户新口径要求）：
  · 目录改为 `D:/ZSK/geokb/inbox/papers`（本脚本的 state/inbox 仍在兼容扫描）
  · 覆盖**所有来源**（不止 SD），按 DOI 前缀 / ISSN / 刊名 / 台账四级识别期刊
  · 期刊目录名从台账反推，自动继承 `OGR` / `LITHOS` 等历史缩写，不造平行目录
  · 完整入库链路（摘要 → 知识点 → 索引），本脚本只有 --ingest 单步
  · 不再写 upgraded_from（不做题录升级）
定时任务：`GeoKB-InboxIngest`，每天 12:01 / 18:01。
本文件保留仅为兼容旧引用，新代码请走 inbox_ingest.py。

--- 以下为原说明 ---

背景
----
ScienceDirect 等站点用 Cloudflare Turnstile 拦自动化，但**真人浏览器点一下就过**。
用户手动下载后把 PDF 丢进收件箱，本脚本接手全部机械动作：

    身份核验 → 落 NAS（沿用既有 by_journal 目录约定）
    → 登记台账（eid = paper-<sha1前12>，与 pdf_corpus_ingest 同源）
    → 可选调用 pdf_corpus_ingest.py --only 抽知识点入库

用法
----
    python scripts/manual_inbox.py                 # 扫描 state/inbox/*.pdf 并归档
    python scripts/manual_inbox.py --dry-run       # 只判定，不落盘
    python scripts/manual_inbox.py --file a.pdf --doi 10.1016/j.oregeorev.2023.105678 \\
        --journal "Ore Geology Reviews" --date 2023
    python scripts/manual_inbox.py --ingest        # 归档后立刻入库

⚠️ `--ingest` 会调用 DeepSeek：09:00–11:59 / 14:00–17:59 禁止使用。
   本脚本**不设时间闸门**（业务链路必须全天可用），遵守规则靠调用方自觉。

退出码：0 全部成功/无待处理；1 有失败条目。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_NO_WIN = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW：子孙全继承隐藏控制台
BASE = Path(__file__).resolve().parents[1]
SCRIPTS = BASE / 'scripts'
sys.path.insert(0, str(SCRIPTS))

INBOX = BASE / 'state' / 'inbox'
DONE = INBOX / 'done'
FAILED = INBOX / 'failed'
INDEX = BASE / 'state' / 'papers_index.jsonl'
POSTNEWS = Path('D:/PostNews')

DOI_RE = re.compile(r'10\.\d{4,9}/[-._;()/:A-Za-z0-9]+', re.I)
YEAR_RE = re.compile(r'\b(19[7-9]\d|20[0-4]\d)\b')


# ─── 基础工具 ───────────────────────────────────────────────────────────────
def norm_doi(s: str) -> str:
    s = re.sub(r'^(https?://)?(dx\.)?doi\.org/', '', (s or '').strip(), flags=re.I)
    s = re.sub(r'^doi:\s*', '', s, flags=re.I)
    return s.strip().rstrip('.,;)]}>').lower()


def norm_title(s: str) -> str:
    """与 daily_archive_papers._norm_title / journal_feed.norm_title 同规则。"""
    return re.sub(r'\W+', '', (s or '').lower())


def sha1_file(p, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(p, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def head_text(path, pages: int = 3) -> str:
    import pymupdf
    doc = pymupdf.open(path)
    try:
        parts = [doc[i].get_text() or '' for i in range(min(pages, doc.page_count))]
    finally:
        doc.close()
    return '\n'.join(parts)


def pdf_meta(path):
    import pymupdf
    doc = pymupdf.open(path)
    try:
        return dict(doc.metadata or {}), doc.page_count
    finally:
        doc.close()


def guess_doi(text: str) -> str:
    """从正文前几页抽 DOI：取出现次数最多者（页眉/页脚会重复出现）。"""
    hits = [norm_doi(m) for m in DOI_RE.findall(text or '')]
    hits = [h for h in hits if len(h) > 8 and not h.endswith('/')]
    if not hits:
        return ''
    from collections import Counter
    return Counter(hits).most_common(1)[0][0]


def guess_year(text: str, fallback: str) -> str:
    ys = YEAR_RE.findall(text or '')
    if not ys:
        return fallback
    from collections import Counter
    return Counter(ys).most_common(1)[0][0]


# ─── 台账 ──────────────────────────────────────────────────────────────────
def load_ledger():
    rows = []
    if INDEX.exists():
        for line in open(INDEX, encoding='utf-8'):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def has_pdf(meta) -> bool:
    rp = str((meta or {}).get('repo_path') or '')
    return 'papers_repo/' in rp.replace('\\', '/')


def lookup(rows, doi='', title='', url=''):
    """按 DOI → 标题键 → URL(含 DOI/PII) 顺序查台账，返回首个命中。"""
    d = norm_doi(doi)
    if d:
        for r in rows:
            if norm_doi(r.get('doi') or '') == d:
                return r
        for r in rows:
            if d and d in norm_doi(r.get('url') or ''):
                return r
    k = norm_title(title)
    if k:
        for r in rows:
            if norm_title(r.get('title') or '') == k:
                return r
        if len(k) >= 70:
            cands = [r for r in rows
                     if norm_title(r.get('title') or '')[:80] == k[:80]]
            if len(cands) == 1:
                return cands[0]
    if url:
        u = url.lower().rstrip('/')
        for r in rows:
            if (r.get('url') or '').lower().rstrip('/') == u:
                return r
    return None


def journal_names():
    """已知期刊名（用于从 PDF 首页猜 journal）。"""
    names = set()
    led = POSTNEWS / 'journal_ledger.json'
    if led.exists():
        try:
            d = json.loads(led.read_text(encoding='utf-8'))
            names.update((d.get('journals') or {}).keys())
        except Exception:
            pass
    for r in load_ledger():
        j = (r.get('journal') or '').strip()
        if j and j != 'unknown':
            names.add(j)
    return sorted(names)


def guess_journal(text: str, names) -> str:
    low = (text or '').lower()
    for n in sorted(names, key=len, reverse=True):
        if n and n.lower() in low:
            return n
    return ''


# ─── 归档 ──────────────────────────────────────────────────────────────────
def archive_one(pdf: Path, rows, names, args, dry_run=False):
    """返回 (status, note)。status ∈ ok / dup / fail"""
    try:
        digest = sha1_file(pdf)
    except Exception as e:
        return 'fail', f'read_err: {e}'

    try:
        text = head_text(pdf)
        meta, pages = pdf_meta(pdf)
    except Exception as e:
        return 'fail', f'pdf_open_err: {type(e).__name__}: {e}'

    doi = norm_doi(args.doi) or guess_doi(text)
    title = (args.title or meta.get('title') or '').strip()
    # 元数据标题常常是文件名/空串，退化到首页第一行
    if len(title) < 12:
        first = [ln.strip() for ln in text.splitlines() if len(ln.strip()) > 12]
        title = first[0] if first else pdf.stem

    hit = lookup(rows, doi=doi, title=title, url=args.url)

    if hit and has_pdf(hit):
        return 'dup', f"已在 NAS: {hit.get('repo_path', '')[-70:]}"

    journal = (args.journal or (hit or {}).get('journal') or guess_journal(text, names)
               or 'manual')
    date = str(args.date or (hit or {}).get('date') or '')[:4] or guess_year(
        text, str(datetime.now().year))
    url = args.url or (hit or {}).get('url') or ''
    eid = f'paper-{digest[:12]}'

    # 身份核验：给了 DOI 就必须在 PDF 里能找到
    if doi:
        import pdf_identity
        chk = pdf_identity.check(str(pdf), doi=doi, title='')
        if not chk.get('doi_in_pdf'):
            return 'fail', f'身份核验失败: PDF 前 3 页找不到 DOI {doi}'

    if dry_run:
        return 'ok', f'[dry] doi={doi or "-"} journal={journal} date={date} pages={pages}'

    data = pdf.read_bytes()
    try:
        import pdf_downloader as pdl
        jpath = pdl._save_pdf(data, journal, title, date, '_manual')
    except Exception as e:
        return 'fail', f'save_err: {type(e).__name__}: {str(e)[:70]}'

    rec = {
        'title': title, 'url': url, 'journal': journal, 'date': date,
        'pdf_url': url, 'repo_path': jpath, 'size': len(data),
        'source': 'manual_download', 'eid': eid,
        'indexed_at': datetime.now().isoformat(timespec='seconds'),
    }
    if doi:
        rec['doi'] = doi
    # 台账已有题录条目（metadata_only）→ 就地补 PDF 字段，避免两套记录
    if hit and not has_pdf(hit):
        rec['upgraded_from'] = hit.get('source') or 'metadata_only'
    try:
        INDEX.parent.mkdir(parents=True, exist_ok=True)
        with open(INDEX, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    except Exception as e:
        return 'fail', f'ledger_err: {e}（PDF 已落盘 {jpath}）'

    rows.append(rec)
    return 'ok', f'{jpath} | eid={eid}'


def ingest_one(eid: str, jpath: str):
    """单篇入库（会调 DeepSeek）。成功后补索引，使 5556 立即可见。"""
    r = subprocess.run([sys.executable, str(SCRIPTS / 'pdf_corpus_ingest.py'),
                        '--only', jpath],
                       cwd=str(BASE), capture_output=True, text=True,
                       encoding='utf-8', errors='replace', creationflags=_NO_WIN,
                       env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    ok = r.returncode == 0
    tail = (r.stdout or '').strip().splitlines()[-3:]
    if ok:
        subprocess.run([sys.executable, str(SCRIPTS / 'index_one_entry.py'), eid],
                       cwd=str(BASE), capture_output=True, text=True,
                       encoding='utf-8', errors='replace', creationflags=_NO_WIN,
                       env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    return ok, ' / '.join(tail)[:140]


def move_to(path: Path, dst_dir: Path):
    """归档后挪走原件，避免重复扫描。失败不致命（有 digest 幂等在）。"""
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
        target = dst_dir / path.name
        if target.exists():
            target = dst_dir / f'{path.stem}_{datetime.now():%H%M%S}{path.suffix}'
        shutil.move(str(path), str(target))
        return str(target)
    except Exception as e:
        return f'(move_failed: {e})'


def main():
    ap = argparse.ArgumentParser(description='手动下载 PDF 收件箱')
    ap.add_argument('--file', default='', help='单个 PDF 路径（默认扫描 inbox）')
    ap.add_argument('--doi', default='')
    ap.add_argument('--title', default='')
    ap.add_argument('--journal', default='')
    ap.add_argument('--date', default='')
    ap.add_argument('--url', default='')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--ingest', action='store_true', help='归档后立即入库（会调 DeepSeek）')
    args = ap.parse_args()

    if args.file:
        files = [Path(args.file)]
    else:
        INBOX.mkdir(parents=True, exist_ok=True)
        files = sorted(p for p in INBOX.glob('*.pdf')
                       if not p.name.startswith('~$'))
        if not files:
            print(f'收件箱为空：{INBOX}')
            print('把手动下载的 PDF 放进去，再重跑本脚本。')
            return 0

    rows = load_ledger()
    names = journal_names()
    print(f'[inbox] 待处理 {len(files)} 个，台账 {len(rows)} 条，已知期刊 {len(names)} 个')

    bad = 0
    for p in files:
        st, note = archive_one(p, rows, names, args, dry_run=args.dry_run)
        flag = {'ok': '✓', 'dup': '=', 'fail': '✗'}.get(st, '?')
        print(f'  {flag} {p.name[:48]:<48} {st:<4} {note}')

        if st == 'fail':
            bad += 1
            if not args.dry_run:
                print(f'     → 原件留在收件箱: {p}')
            continue
        if args.dry_run:
            continue

        if st == 'ok' and args.ingest:
            eid = note.split('eid=')[-1] if 'eid=' in note else ''
            jpath = note.split(' | ')[0]
            if eid:
                ok, tail = ingest_one(eid, jpath)
                print(f'     → 入库 {"成功" if ok else "失败"}: {tail}')

        moved = move_to(p, DONE if st == 'ok' else DONE / 'dup')
        if moved.startswith('('):
            print(f'     → {moved}')

    print(f'[inbox] 完成：失败 {bad} 个')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
