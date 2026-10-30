# -*- coding: utf-8 -*-
"""把 journals 页「PDF 抓取失败」的论文同步为 external-feed（外部订阅）题录。

背景
----
journals 页 60 天窗口约 380 篇待抓文章，源站直连大量撞付费墙 / Cloudflare。
用户要求：抓取失败的论文先以「外部订阅」标签资源进知识库，
之后每天管线用 CrossRef / Unpaywall 等渠道尝试补下载，
下一篇入一篇到 papers 标签并拆 kpoint。

分工
----
- 本脚本：只负责「写题录」（幂等、可反复跑）
- `migrate_feed_to_papers.py`：负责「下载 → 入库 papers → 拆 kpoint → 回写题录」
- `journal_pdf_oa.py`：提供 CrossRef/Unpaywall/OpenAlex/S2 四渠道候选
- `journal_pdf_downloader.fetch_one`：源站 handler 失败后自动 fallback 到 OA 渠道
  （因此 migrate 与 journals 页「在线抓取」按钮都自动获得 OA 能力）

题录格式与 parse_feed() 约定一致：frontmatter 必须有 id/title/sources[0].url。

用法
----
    python external_subscription_sync.py --list      # 干跑：只看会写什么
    python external_subscription_sync.py             # 写入题录
    python external_subscription_sync.py --prune     # 顺带报告已迁移/已归档的题录
"""
import argparse
import hashlib
import os
import re
import sys
from datetime import datetime
from pathlib import Path

GEokb = Path('D:/ZSK/geokb')
POSTNEWS = Path('D:/PostNews')
sys.path.insert(0, str(POSTNEWS))
sys.path.insert(0, str(POSTNEWS / '.workbuddy'))
sys.path.insert(0, str(GEokb / 'scripts'))

import journal_feed  # noqa: E402
import journal_pdf_downloader as pdl  # noqa: E402
from journal_pdf_oa import doi_of  # noqa: E402

FEED_DIR = GEokb / 'content' / 'external-feed'
WINDOW_TAG = '外部订阅'


def _eid(url, title):
    """题录 id：URL 优先（更稳定），无 URL 用标题。"""
    key = (url or '').strip() or (title or '').strip()
    return 'feed-' + hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]


def collect_articles():
    """共享层全量文章（groups + today 去重）。"""
    feed = journal_feed.build_feed(str(POSTNEWS))
    seen = {}
    for g in feed['groups']:
        for j in g['journals']:
            for a in (j.get('articles') or []):
                seen[a.get('url') or a.get('title')] = a
    for a in feed['today'].get('items') or []:
        seen.setdefault(a.get('url') or a.get('title'), a)
    return list(seen.values())


def existing_ids():
    """已存在的题录 id → (status, migrated_to)。"""
    out = {}
    if not FEED_DIR.is_dir():
        return out
    for fn in os.listdir(FEED_DIR):
        if not fn.endswith('.md'):
            continue
        raw = (FEED_DIR / fn).read_text(encoding='utf-8', errors='ignore')
        m = re.match(r'^---\n(.*?)\n---', raw, re.S)
        if not m:
            continue
        fm = {}
        for line in m.group(1).splitlines():
            if ':' in line and not line.startswith(' '):
                k, _, v = line.partition(':')
                fm[k.strip()] = v.strip().strip('"\'')
        out[fm.get('id', '')] = (fm.get('status', ''), fm.get('migrated_to', ''), fn)
    return out


def build_entry(article):
    """把一篇文章转成 external-feed 题录 dict。"""
    url = (article.get('url') or '').strip()
    title = (article.get('title') or '').strip()
    journal = article.get('journal') or ''
    date = article.get('date') or ''
    doi = doi_of(url)
    summary = (article.get('summary') or article.get('abstract') or '').strip()
    if len(summary) > 600:
        summary = summary[:597] + '...'

    tags = ['论文', WINDOW_TAG]
    if journal:
        tags.append(journal)
    for t in (article.get('tags') or [])[:4]:
        if t and t not in tags:
            tags.append(str(t))

    today = datetime.now().strftime('%Y-%m-%d')
    src = {
        'name': journal or '期刊更新',
        'url': url,
        'accessed': today,
    }
    if doi:
        src['doi'] = doi

    body_lines = [
        '## 来源',
        '- **类型**: paper',
        '- **期刊/来源**: %s' % (journal or '未知'),
        '- **原始标题**: %s' % title,
        '- **原始 URL**: %s' % (url or '（无）'),
        '- **DOI**: %s' % (doi or '（未提取到）'),
        '- **发布日期**: %s' % (date or '未知'),
        '- **收录日期**: %s' % today,
        '',
        '## 摘要',
        summary or '（源站未提供摘要）',
        '',
        '## 补抓说明',
        '- 本篇来自 journals 页 60 天更新窗口，源站直连下载 PDF 失败'
        '（付费墙 / 反爬 / 未上传全文）。',
        '- 每日管线会通过 CrossRef / Unpaywall / OpenAlex / Semantic Scholar'
        ' 等渠道尝试获取 OA 全文；成功后迁移至 papers 标签并提炼知识点。',
    ]
    return {
        'id': _eid(url, title),
        'title': title,
        'tags': tags,
        'summary': summary or title,
        'url': url,
        'doi': doi,
        'journal': journal,
        'date': date,
        'today': today,
        'source': src,
        'body': '\n'.join(body_lines),
    }


def render(entry):
    """题录 → markdown 文本（frontmatter 手写，避免 yaml 转义歧义）。"""
    fm = [
        '---',
        'id: %s' % entry['id'],
        'title: %s' % _yaml_str(entry['title']),
        'module: external-feed',
        'tags: [%s]' % ', '.join(_yaml_str(t) for t in entry['tags']),
        'summary: %s' % _yaml_str(entry['summary']),
        'confidence: C',
        'status: active',
        'updated: %s' % entry['today'],
        'sources: [{"name": %s, "url": %s, "accessed": %s%s}]' % (
            _yaml_str(entry['source']['name']), _yaml_str(entry['source']['url']),
            _yaml_str(entry['source']['accessed']),
            (', "doi": %s' % _yaml_str(entry['source']['doi']))
            if entry['source'].get('doi') else ''),
        '---',
        '',
        entry['body'],
        '',
    ]
    return '\n'.join(fm)


def _yaml_str(s):
    """YAML 双引号字符串转义。"""
    s = str(s or '')
    s = s.replace('\\', '\\\\').replace('"', '\\"')
    s = s.replace('\n', ' ').replace('\r', ' ')
    return '"%s"' % s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--list', action='store_true', help='干跑：只统计不写盘')
    ap.add_argument('--limit', type=int, default=0, help='最多处理前 N 篇')
    ap.add_argument('--prune', action='store_true',
                    help='报告已归档/已迁移的题罗（不删文件）')
    args = ap.parse_args()

    arts = collect_articles()
    todo = [a for a in arts if not a.get('archived')]
    print('journals 窗口文章 %d 篇 · 未归档（抓取失败）%d 篇' % (len(arts), len(todo)))
    if args.limit:
        todo = todo[:args.limit]

    have = existing_ids()
    print('external-feed 已有题录 %d 条' % len(have))

    written = skipped_exist = skipped_migrated = 0
    new_entries = []
    for a in todo:
        e = build_entry(a)
        if not e['url']:
            continue
        prev = have.get(e['id'])
        if prev is not None:
            status, migrated = prev[0], prev[1]
            if status == 'deprecated' or migrated:
                skipped_migrated += 1
            else:
                skipped_exist += 1
            continue
        new_entries.append(e)
        if not args.list:
            FEED_DIR.mkdir(parents=True, exist_ok=True)
            (FEED_DIR / ('%s.md' % e['id'])).write_text(
                render(e), encoding='utf-8')
            have[e['id']] = ('active', '', '')
        written += 1

    print('新增题录 %d · 已存在（跳过）%d · 已迁移（跳过）%d'
          % (written, skipped_exist, skipped_migrated))

    if args.list and new_entries:
        import collections
        c = collections.Counter(e['journal'] or '?' for e in new_entries)
        print('\n将新增的题录按期刊分布：')
        for j, n in c.most_common():
            print('  %-28s %d' % (j[:28], n))
        print('\n样例（前 3 条）：')
        for e in new_entries[:3]:
            print('  %s | %s | %s' % (e['id'], e['doi'] or '无DOI',
                                      e['title'][:48]))

    if args.prune:
        # 现在已归档但题录还在 active 的（源站直连后来成功的）
        led = journal_feed.load_ledger()
        stale = []
        for a in arts:
            if not a.get('archived'):
                continue
            eid = _eid(a.get('url'), a.get('title'))
            prev = have.get(eid)
            if prev and prev[0] == 'active' and not prev[1]:
                stale.append((eid, (a.get('title') or '')[:50]))
        print('\n[prune] 已归档但题录仍 active 的: %d 条'
              '（migrate 跑完后会自动标记 migrated，此处仅提示）' % len(stale))
        for eid, t in stale[:10]:
            print('   ', eid, t)


if __name__ == '__main__':
    main()
