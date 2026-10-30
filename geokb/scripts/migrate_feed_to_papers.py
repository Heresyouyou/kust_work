# -*- coding: utf-8 -*-
"""external-feed → papers 标签迁移脚本（PDF 优先，源头治理的补救措施）

背景
----
`ingest_external_feeds.py` 早期只调通用下载器（arXiv / DOI→Unpaywall），
对中文期刊全部落空，导致一批**本可拿到全文**的论文只能以「外部订阅」题录
进库（confidence=C、T4 降权、无 PDF、无知识点）。

2026-10-16 起下载器已逐个期刊完善（12 个出版社 handler）。本脚本用新能力
**回溯迁移**这批存量题录：

  对每条 external-feed 条目：
    1. 取源 URL，尝试下载 PDF（强 handler 集 → 通用下载器）
    2. 下载成功 → 校验是真 PDF 且能抽出正文
    3. 基于原有题录内容 + 全文，DeepSeek 提取 → 写入 papers 标签
       （id 仍用 paper-<sha1前12位>，与语料管线/kpoints 管线约定一致）
    4. 从全文提炼知识点 → 写入 kpoints 标签（parent 指回 papers 条目）
    5. 回写 feed 条目：标记已迁移 + 指向 papers 条目（默认不删除）

设计纪律
--------
- **幂等**：以 PDF 内容 SHA1 为键。papers 条目已存在 → 跳过提取；
  kpoints 已存在 → 跳过。可反复重跑。
- **非破坏**：默认只把 feed 条目标为 `status: deprecated` 并加 migrated_to
  指针，绝不删文件；要真删需显式 `--feed-action delete`。
- **绝不写垃圾**：DeepSeek infra 故障（429/5xx）→ 不标记、留待重跑；
  内容解析失败 → 规则兜底并标 confidence=C，不静默丢条目。
- **先验证再动手**：默认 `--dry-run` 只探测可下载性，不落任何盘。

用法
----
    python migrate_feed_to_papers.py --dry-run            # 只探测，不落盘
    python migrate_feed_to_papers.py --limit 3            # 小批验证
    python migrate_feed_to_papers.py --full               # 全量 + 重建索引
    python migrate_feed_to_papers.py --full --kpoints     # 全量并提炼知识点
    python migrate_feed_to_papers.py --full --feed-action delete   # 迁移后删 feed 条目
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
POSTNEWS = Path(r'D:/PostNews')
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'server'))
sys.path.insert(0, str(POSTNEWS))

import yaml  # noqa: E402

import ingest_policy  # noqa: E402  入库策略（默认只下载，不入库）

from deepseek_client import InfraError, ds_chat, extract_json, load_api_key  # noqa: E402
from pdf_corpus_ingest import (MIN_TEXT_CHARS, ds_extract, extract_text,  # noqa: E402
                               sha1_of, smart_truncate, write_entry,
                               normalize_tags, sentence_safe_summary)

FEED_DIR = BASE / 'content' / 'external-feed'
PAPERS_DIR = BASE / 'content' / 'papers'
KPOINTS_DIR = BASE / 'content' / 'kpoints'
STATE_FILE = BASE / 'state' / 'feed_migration_state.json'
REPORT_DIR = BASE / 'logs'

KP_PROMPT = """你是地矿学科知识抽取器。阅读下面这篇论文全文（每行带行号），抽取其中**独立、可检索的知识点**。

要求：
- 每个知识点是一条独立结论/方法/指标/找矿标志，能单独回答一个问题。
- 最多 6 个，宁缺毋滥；不要复述摘要，不要写"本文研究了……"这类套话。
- 每条 120-260 字，中文，含关键数值与地质对象。
- 每条必须给出证据行号（如 L120-L135）。

输出严格 JSON：{"kps":[{"title":"","body":"","evidence":"L..-L..","tags":["",""]}]}

论文全文：
"""


# ─── 状态 ────────────────────────────────────────────────────────
def load_state():
    if STATE_FILE.exists():
        try:
            return json.load(open(STATE_FILE, encoding='utf-8'))
        except Exception:
            pass
    return {'migrated': {}, 'failed': {}}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _index_py():
    """增量索引用 geokb venv 解释器（server/ingest 需要 jieba/fastembed）。"""
    for p in ('D:/ZSK/geokb/venv/Scripts/python.exe', sys.executable):
        if p and os.path.isfile(p):
            return p
    return sys.executable


def index_one(eid):
    """逐篇增量索引（约 0.6s）。

    2026-10-20：改为「每迁移成功一篇立刻进索引」，而不是跑完统一 rebuild
    （embed 8000+ 条约 5 分钟）。这样即使本轮被 --max-minutes 截断，
    已迁移的条目也已经在库里可检索 —— 即「爬一篇、入库一篇、再爬下一篇」。
    """
    if not eid:
        return False
    try:
        p = subprocess.run(
            [_index_py(), str(BASE / 'scripts' / 'index_one_entry.py'), eid],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=600,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if p.returncode != 0:
            print('      [索引] %s 增量索引失败(rc=%d): %s'
                  % (eid, p.returncode, (p.stderr or p.stdout or '')[-160:].replace('\n', ' ')),
                  flush=True)
        return p.returncode == 0
    except Exception as e:  # noqa: BLE001
        print('      [索引] %s 增量索引异常: %s' % (eid, str(e)[:120]), flush=True)
        return False



# ─── 解析 external-feed 条目 ──────────────────────────────────────
def parse_feed(path):
    raw = open(path, encoding='utf-8').read()
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", raw, re.S)
    if not m:
        return None
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception:
        return None
    if not isinstance(fm, dict) or 'id' not in fm:
        return None
    srcs = fm.get('sources') or []
    url = ''
    if srcs and isinstance(srcs[0], dict):
        url = srcs[0].get('url', '') or ''
    return {
        'id': str(fm.get('id')),
        'title': str(fm.get('title', '')),
        'tags': [str(t) for t in (fm.get('tags') or [])],
        'summary': str(fm.get('summary', '')),
        'url': url,
        'updated': str(fm.get('updated', '')),
        'body': m.group(2).strip(),
        'path': str(path),
    }


# ─── 正文里的结构化片段（研究区域/关键实体/核心方法）────────────────
def _section(body, name):
    m = re.search(r"^##\s*%s\s*\n(.+?)(?=\n##\s|\Z)" % re.escape(name),
                  body, re.S | re.M)
    return m.group(1).strip() if m else ''


def feed_context(entry):
    """从原题录里抽「全文提取可能缺失」的字段，用于补全 papers 条目。"""
    body = entry['body']
    return {
        'study_area': _section(body, '研究区域'),
        'entities': _section(body, '关键实体'),
        'methods': _section(body, '核心方法'),
        'application': _section(body, '应用场景/意义'),
    }


# ─── papers 条目（基于原题录 + 全文）──────────────────────────────
def write_enriched_paper(digest, feed, ctx, data, extraction):
    """写 papers 条目：全文提取为主，原题录内容兜底/补全。"""
    eid = f"paper-{digest[:12]}"
    path = PAPERS_DIR / f"{eid}.md"
    title_zh = (data.get('title_zh') or feed['title'] or '').strip()
    title_en = (data.get('title_en') or '').strip()

    tags = normalize_tags(data.get('tags'))
    for extra in feed['tags']:
        t = str(extra).strip()
        if t and t not in tags:
            tags.append(t)
    for lst_key in ('deposit_types', 'commodities'):
        for t in normalize_tags(data.get(lst_key)):
            if t not in tags:
                tags.append(t)
    tags = tags[:12]

    summary = sentence_safe_summary(data.get('key_content') or feed['summary'])
    confidence = 'B' if extraction == 'llm' else 'C'

    # 原题录里有、全文提取没拿到的字段 → 补进去
    study_area = (data.get('study_area') or '').strip() or ctx['study_area'] or '（未能提取）'
    methods = [str(x).strip() for x in (data.get('core_methods') or []) if str(x).strip()]
    if not methods and ctx['methods'] and ctx['methods'] != '待补充':
        methods = [x.strip() for x in re.split(r'[、,，]', ctx['methods']) if x.strip()]
    apps = [str(x).strip() for x in (data.get('application_scenarios') or []) if str(x).strip()]
    if not apps and ctx['application']:
        apps = [x.strip() for x in re.split(r'[、,，]', ctx['application']) if x.strip()]

    now = datetime.now().strftime('%Y-%m-%d')
    sources = [{'name': '外部订阅原文', 'url': feed['url'], 'accessed': now}]
    sources.append({'name': '本地PDF', 'path': _rel_repo_path(digest), 'accessed': now})

    def md_list(items):
        items = [x for x in (items or []) if x]
        return "\n".join(f"- {x}" for x in items) if items else "- （未能提取）"

    body = f"""## 关键内容

{data.get('key_content') or feed['summary'] or '（未能提取）'}

## 核心方法

{md_list(methods)}

## 研究区域

{study_area}

## 应用场景

{md_list(apps)}

## 来源

- 外部订阅原文：{feed['url']}
- 迁移自题录：`{feed['id']}`（{feed['updated'] or '日期未知'}）
- 英文标题：{title_en or '（无）'}
- 仓库文件：`{_rel_repo_path(digest)}`
"""
    front = f"""---
id: {eid}
title: {json.dumps(title_zh, ensure_ascii=False)}
module: papers
tags: {json.dumps(tags, ensure_ascii=False)}
summary: {json.dumps(summary, ensure_ascii=False)}
confidence: {confidence}
status: active
updated: {now}
sources: {json.dumps(sources, ensure_ascii=False)}
extraction: {extraction}
feed_origin: {feed['id']}
---
"""
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(front + "\n" + body, encoding='utf-8')
    return eid


def _rel_repo_path(digest):
    """papers 条目里引用的仓库相对路径（与语料管线一致的前缀约定）。"""
    from paths import by_journal
    root = Path(by_journal()).parent
    p = _pdf_path_for(digest)
    try:
        rel = os.path.relpath(p, str(root)).replace("\\", "/")
    except Exception:
        rel = str(p).replace("\\", "/")
    if not rel.startswith("papers_repo/"):
        rel = "papers_repo/" + rel.lstrip("./")
    return rel


# ─── PDF 落盘（NAS by_journal）────────────────────────────────────
def _pdf_dir():
    from paths import by_journal
    return Path(by_journal())


def _pdf_path_for(digest):
    return _pdf_dir() / '_feed_migration' / f"{digest[:12]}.pdf"


def save_pdf(data, digest):
    p = _pdf_path_for(digest)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, 'wb') as f:
        f.write(data)
    return str(p)


# ─── kpoints 提炼 ────────────────────────────────────────────────
def paper_meta_for_kp(eid, digest):
    """读回刚写的 papers 条目，取 kpoints 需要的元数据。"""
    p = PAPERS_DIR / f"{eid}.md"
    if not p.exists():
        return {}
    raw = open(p, encoding='utf-8').read()
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", raw, re.S)
    if not m:
        return {}
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception:
        return {}
    return {
        'title': str(fm.get('title', '')),
        'journal': str(fm.get('journal', '')),
        'year': str(fm.get('year', '')),
        'tags': [str(t) for t in (fm.get('tags') or [])][:6],
        'sources': fm.get('sources') or [],
        'confidence': str(fm.get('confidence', 'C')),
    }


def extract_kpoints(digest, eid, api_key, limit=6):
    """从全文提炼知识点写入 kpoints。返回写入条数。

    2026-10-19：本函数原来是 kp_extract.work() 的逐字复制（同提示词、同截断、
    同 write_kp）。现直接委托，避免两份实现漂移（缓存改造后提示词结构已变，
    复制版会立刻失效）。
    去重：`kp-<sha12>-1.md` 已存在即视为抽过，不再花 token。
    """
    from kp_extract import work
    if (BASE / 'content' / 'kpoints' / f"kp-{digest[:12]}-1.md").exists():
        return 0
    txt_path = BASE / 'state' / 'text' / f"{digest}.txt"
    if not txt_path.exists():
        return 0
    try:
        return work(digest, api_key)
    except Exception as e:
        raise InfraError(str(e)) if 'HTTP 5' in str(e) or '429' in str(e) else e


# ─── 回写 feed 条目 ──────────────────────────────────────────────
def mark_feed_migrated(entry, paper_eid, kp_count, action='deprecate'):
    """把题录标记为已迁移。默认只改 frontmatter + 追加说明，不删文件。"""
    p = Path(entry['path'])
    raw = p.read_text(encoding='utf-8')
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", raw, re.S)
    if not m:
        return False
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception:
        return False

    if action == 'delete':
        p.unlink()
        return True

    if action == 'deprecate':
        fm['status'] = 'deprecated'
    fm['migrated_to'] = paper_eid
    fm['migrated_at'] = datetime.now().strftime('%Y-%m-%d')
    head = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, width=100000)
    note = (f"\n## 迁移记录\n\n- 已于 {fm['migrated_at']} 迁移至 papers 条目 "
            f"`{paper_eid}`（PDF 全文 + DeepSeek 提取）\n"
            f"- 提炼知识点 {kp_count} 条 → kpoints 标签\n"
            f"- 本条保留作文献追溯，检索权重已降权\n")
    body = m.group(2).rstrip() + note
    p.write_text("---\n" + head.strip() + "\n---\n" + body + "\n", encoding='utf-8')
    return True


# ─── 单条处理 ────────────────────────────────────────────────────
def process_feed(entry, state, api_key, dry_run=False, want_kpoints=True,
                 feed_action='deprecate'):
    eid_feed = entry['id']
    if eid_feed in state.get('migrated', {}):
        return {'status': 'skipped', 'reason': 'already_migrated'}

    url = entry['url']
    if not url:
        return {'status': 'skipped', 'reason': 'no_url'}

    # ① 探测/下载 PDF
    if dry_run:
        try:
            from journal_pdf_fetch import fetch_pdf
            r = fetch_pdf('external-feed', entry['title'], url, doi=entry.get('doi') or '')
        except Exception as e:
            r = {'status': 'err_probe', 'note': str(e)[:100]}
        ok = r.get('status') == 'ok' and r.get('pdf_bytes')
        return {'status': 'probe', 'downloadable': bool(ok),
                'reason': r.get('status', ''),
                'note': str(r.get('note', ''))[:120]}

    from journal_pdf_fetch import fetch_pdf
    r = fetch_pdf('external-feed', entry['title'], url, doi=entry.get('doi') or '')
    if not (r.get('status') == 'ok' and r.get('pdf_bytes')):
        return {'status': 'no_pdf', 'reason': r.get('status', ''),
                'note': str(r.get('note', ''))[:150]}

    data = r['pdf_bytes']
    if data[:4] != b'%PDF':
        return {'status': 'no_pdf', 'reason': 'not_pdf', 'note': ''}

    # ② 校验 PDF 能抽出正文
    digest = hashlib.sha1(data).hexdigest()
    paper_file = PAPERS_DIR / f"paper-{digest[:12]}.md"
    pdf_path = save_pdf(data, digest)

    # 入库闸门（2026-10-20）：非 daily_poster 链路只下载，不抽全文、不建词条。
    # 下载成果不丢：PDF 已落 NAS，记一条待入库记录等用户统一安排。
    if not ingest_policy.enabled('migrate'):
        ingest_policy.queue_pending({
            'eid': f"paper-{digest[:12]}", 'title': entry.get('title', ''),
            'journal': entry.get('journal', ''), 'pdf': pdf_path,
            'url': url, 'doi': entry.get('doi') or '', 'from': 'migrate',
        })
        return {'status': 'downloaded_only', 'pdf': pdf_path,
                'reason': 'policy:auto_ingest_off'}

    if paper_file.exists():
        paper_eid = f"paper-{digest[:12]}"
        print(f"      papers 条目已存在 {paper_eid}，跳过提取", flush=True)
    else:
        try:
            text = extract_text(pdf_path, digest)
        except Exception as e:
            return {'status': 'failed', 'reason': f'text_extract: {e}'}
        if len(text) < MIN_TEXT_CHARS:
            return {'status': 'needs_ocr', 'chars': len(text)}
        try:
            obj, err = ds_extract(text, digest, api_key)   # 内部已统一 prepare，勿再截断
        except InfraError:
            raise
        if not obj:
            return {'status': 'failed', 'reason': f'ds_extract: {err}'}
        ctx = feed_context(entry)
        paper_eid = write_enriched_paper(digest, entry, ctx, obj, 'llm')
        print(f"      → papers {paper_eid} ✓", flush=True)

    # ③ 知识点提炼
    kp_n = 0
    if want_kpoints:
        existing = list(KPOINTS_DIR.glob(f"kp-{digest[:12]}-*.md")) \
            if KPOINTS_DIR.exists() else []
        if existing:
            kp_n = len(existing)
            print(f"      kpoints 已存在 {kp_n} 条，跳过", flush=True)
        else:
            try:
                kp_n = extract_kpoints(digest, paper_eid, api_key)
                print(f"      → kpoints {kp_n} 条 ✓", flush=True)
            except InfraError:
                raise
            except Exception as e:
                print(f"      [warn] 知识点提炼失败（不影响 papers 入库）: {e}",
                      flush=True)

    # ④ 回写题录 + 登记 NAS 台账
    # ⚠️ 2026-10-18：此前只写 papers 条目不写台账 → journals 页对已迁移文章
    # 仍显示「在线抓取」，external_subscription_sync 也反复列进待办，
    # 而 PDF 其实一直在 NAS（_feed_migration/）。台账是「已归档」判据的
    # 唯一真相（journal_feed.lookup_ledger 认 repo_path 含 papers_repo/），
    # 所以迁移成功必须同时落一条台账，两端才能对上。
    try:
        from daily_archive_papers import append_index
        _body = entry.get('body') or ''
        _md = re.search(r'发布日期\*\*:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})', _body)
        _jm = re.search(r'期刊/来源\*\*:\s*(.+)', _body)
        append_index({
            'title': entry['title'], 'url': url,
            'journal': (_jm.group(1).strip() if _jm else ''),
            'date': (_md.group(1) if _md else entry.get('updated', '')),
            'pdf_url': r.get('pdf_url', ''),
            'repo_path': pdf_path, 'size': len(data),
            'source': 'feed-migration',
            'eid': paper_eid,
            'indexed_at': datetime.now().isoformat(),
        })
    except Exception as _e:
        print(f"      [warn] 台账登记失败（papers 已入库，不影响）: {_e}",
              flush=True)
    mark_feed_migrated(entry, paper_eid, kp_n, action=feed_action)
    state.setdefault('migrated', {})[eid_feed] = {
        'paper': paper_eid, 'kp': kp_n, 'ts': datetime.now().isoformat()}
    return {'status': 'migrated', 'paper': paper_eid, 'kp': kp_n}


# ─── 主流程 ──────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='只探测可下载性，不落盘')
    ap.add_argument('--limit', type=int, default=0, help='只处理前 N 条')
    ap.add_argument('--full', action='store_true', help='全量执行')
    ap.add_argument('--no-kpoints', action='store_true', help='跳过知识点提炼')
    ap.add_argument('--feed-action', default='deprecate',
                    choices=['deprecate', 'keep', 'delete'],
                    help='迁移后对原题录的处理（默认 deprecate 降权保留）')
    ap.add_argument('--rebuild', action='store_true', help='结束后重建索引')
    ap.add_argument('--max-minutes', type=float, default=0,
                    help='本次运行的时间上限（分钟，0=不限）。超时即收尾退出——'
                         '2026-10-19 实况：无限时跑满 7200s 被上层 kill，'
                         '当天新文章的处理被整体推迟。')
    ap.add_argument('--today-first', action='store_true', default=True,
                    help='优先处理最近更新的题录（默认开）')
    args = ap.parse_args()

    deadline = (time.time() + args.max_minutes * 60) if args.max_minutes else 0

    print(f"[{datetime.now().isoformat()}] external-feed → papers 迁移开始"
          f"（dry_run={args.dry_run} max={args.max_minutes or '∞'}min）", flush=True)
    if not FEED_DIR.is_dir():
        print("external-feed 目录不存在，退出")
        return

    entries = []
    for fn in sorted(os.listdir(FEED_DIR)):
        if not fn.endswith('.md'):
            continue
        e = parse_feed(FEED_DIR / fn)
        if e:
            entries.append(e)
    print(f"共 {len(entries)} 条 external-feed 条目", flush=True)
    if args.today_first:
        # 当天新增/最近更新的先跑：题录的 updated 是 ISO 日期，倒序即「最新优先」。
        # 稳定排序 + 空值兜底，避免旧条目没有 updated 时被排到最后以外的乱序。
        entries.sort(key=lambda e: (e.get('updated') or ''), reverse=True)
        print("排序：按 updated 倒序（当天更新的期刊题录优先）", flush=True)
    if args.limit:
        entries = entries[:args.limit]

    state = load_state()
    api_key = None
    if not args.dry_run:
        try:
            api_key = load_api_key()
        except Exception as e:
            print(f"[FATAL] DeepSeek key 不可用: {e}")
            return

    results = []
    infra_streak = 0
    stopped_by_deadline = False
    for i, e in enumerate(entries, 1):
        if deadline and time.time() > deadline:
            stopped_by_deadline = True
            print(f"  [时限] 已达 {args.max_minutes} 分钟上限，收尾退出"
                  f"（剩余 {len(entries) - i + 1} 条留待下一轮）", flush=True)
            break
        print(f"[{i}/{len(entries)}] {e['id']} {e['title'][:44]}", flush=True)
        try:
            r = process_feed(e, state, api_key, dry_run=args.dry_run,
                             want_kpoints=not args.no_kpoints,
                             feed_action=args.feed_action)
            results.append(r)
            infra_streak = 0
            if args.dry_run:
                mark = '✓ 可下载' if r.get('downloadable') else f"✗ {r.get('reason')}"
                print(f"      {mark} {str(r.get('note',''))[:90]}", flush=True)
            elif r.get('status') == 'migrated' and r.get('eid'):
                # 爬一篇 → 入库一篇 → 再爬下一篇（不攒批）
                index_one(r['eid'])
        except InfraError as ex:
            infra_streak += 1
            print(f"      [infra] DeepSeek 故障（{ex}），本条留待重跑", flush=True)
            results.append({'status': 'infra_pending', 'error': str(ex)})
            if infra_streak >= 5:
                print("  [infra] 连续 5 次故障，中止本次运行")
                break
        except Exception as ex:
            print(f"      处理失败: {type(ex).__name__}: {str(ex)[:120]}", flush=True)
            traceback.print_exc(limit=2)
            results.append({'status': 'error', 'error': str(ex)[:200]})

    # 汇总
    def cnt(k):
        return sum(1 for r in results if r.get('status') == k)
    print("\n" + "=" * 60)
    if args.dry_run:
        dl = sum(1 for r in results if r.get('downloadable'))
        print(f"探测完成: 可下载 {dl} / {len(results)}")
    else:
        print(f"迁移 {cnt('migrated')} | 跳过 {cnt('skipped')} | "
              f"无PDF {cnt('no_pdf')} | 需OCR {cnt('needs_ocr')} | "
              f"失败 {cnt('failed')} | 留待重跑 {cnt('infra_pending')} | "
              f"错误 {cnt('error')}")
        kp_total = sum(r.get('kp', 0) for r in results if r.get('status') == 'migrated')
        print(f"新增 papers 条目 {cnt('migrated')} 篇，知识点 {kp_total} 条")
        if stopped_by_deadline:
            print(f"（本轮因 --max-minutes={args.max_minutes} 提前收尾，未处理条目留待下轮）")
        save_state(state)

        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        rep = {'finished_at': datetime.now().isoformat(timespec='seconds'),
               'total': len(entries), 'stopped_by_deadline': stopped_by_deadline,
               'results': results}
        (REPORT_DIR / f"feed_migration_{datetime.now():%Y%m%d_%H%M%S}.json"
         ).write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding='utf-8')

        # 逐篇 index_one() 已把每条写进索引，这里只在**显式要求**时做全量重建
        # （全量 embed 8000+ 条约 5 分钟，不该再挂在日常主链上）。
        if args.rebuild and cnt('migrated'):
            print("重建 GeoKB 索引（显式 --rebuild）...", flush=True)
            try:
                import ingest
                ingest.rebuild(embed=True)
                print("索引重建完成")
            except Exception as ex:
                print(f"索引重建失败: {ex}")

    print(f"[{datetime.now().isoformat()}] 结束", flush=True)


if __name__ == '__main__':
    main()
