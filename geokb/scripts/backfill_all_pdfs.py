# -*- coding: utf-8 -*-
"""期刊论文 PDF **全量补抓**：网站 → NAS papers_repo（2026-10-19）

定位
----
`daily_archive_papers.py` 只处理「当天首次获取」的文章（每日管线用）。
本脚本处理**存量全量**：把 journals 页面上所有「未归档」的文章一次性补齐，
目标是「每篇文章都有对应 PDF 在本地」。

为什么需要它
------------
用户 2026-10-19 要求：journal 页面还有很多期刊要点击「在线抓取」，
把 PDF 都下载下来，尽量保证每篇都有；若只是路径问题导致读取不到，则优化完善。
实测 542 篇文章里仅 12 篇已归档，530 篇待抓，且 530 篇全部有对应 handler。

并发模型（关键设计）
------------------
**按源站分组、组内串行、组间并行**：
- 同源站内部保持 3s 节流 —— 岩石学报等源站对集中连跑会返回非 PDF（实测教训），
  所以**不能**对同一源站并发；
- 不同源站之间互不影响（各自的限流桶独立），并行可把总耗时从
  「530 × (3s + 下载) ≈ 60min」压到「最慢源站 125 篇 ≈ 15min」。
- handler 模块自带 `_log_lock` / `_guard_lock` / `_ddgz_lock`，且默认
  `WORKERS=4`，本身即按并发设计，组间并行不引入新的竞争。

断点续跑
--------
幂等键走 `daily_archive_papers._ledger_get()`（精确 + 前缀兜底，
与门户共享层 `journal_feed.lookup_ledger()` 同规则）。台账已命中的直接跳过，
因此本脚本随时可中断、随时可重跑，不会重复下载、不会生成重复文件。

用法
----
    python backfill_all_pdfs.py                    # 全量补抓
    python backfill_all_pdfs.py --list             # 只列篇目与源站分布（不发请求）
    python backfill_all_pdfs.py --source gsw       # 只补某个源站
    python backfill_all_pdfs.py --journal Geology  # 只补某刊
    python backfill_all_pdfs.py --limit 50         # 限量试跑
    python backfill_all_pdfs.py --workers 4        # 并发源站数（默认 6）
    python backfill_all_pdfs.py --sleep 3.0        # 篇间节流（秒）
"""
import argparse
import json
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]          # D:/ZSK/geokb
POSTNEWS = Path('D:/PostNews')
for p in (str(POSTNEWS), str(POSTNEWS / '.workbuddy')):
    if p not in sys.path:
        sys.path.insert(0, p)

import journal_feed                                    # noqa: E402
import journal_pdf_fetch                               # noqa: E402
from daily_archive_papers import (load_index_keys,      # noqa: E402
                                  append_index, _ledger_get, _norm_title)

REPORT = BASE / 'state' / 'backfill_report.json'


def log(msg):
    print(msg, flush=True)


# ─── 收集待抓文章 ────────────────────────────────────────────────────────
def collect_todo():
    """从共享取数层拿全量文章，去掉已归档的，按 (源站, 期刊) 归组。"""
    feed = journal_feed.build_feed(str(POSTNEWS))
    ledger = journal_feed.load_ledger()
    seen = {}
    for g in feed.get('groups') or []:
        for j in g.get('journals') or []:
            for a in j.get('articles') or []:
                k = a.get('url') or a.get('title')
                if k and k not in seen:
                    a['_jn'] = j.get('name') or j.get('name_cn') or a.get('journal')
                    seen[k] = a
    for a in (feed.get('today') or {}).get('items') or []:
        k = a.get('url') or a.get('title')
        if k and k not in seen:
            a['_jn'] = a.get('journal') or '(today)'
            seen[k] = a

    todo, already = [], 0
    for a in seen.values():
        # 与门户同规则：精确 + 前缀兜底 + URL 二级匹配（救回「两套标题」同文）
        if journal_feed.lookup_ledger(ledger, _norm_title(a.get('title')),
                                      url=a.get('url')) is not None:
            already += 1
            continue
        u = a.get('url') or ''
        if not u.startswith('http'):
            continue
        src = journal_pdf_fetch.source_of(u)
        todo.append({'title': (a.get('title') or '').strip(),
                     'url': u.strip(),
                     'journal': a['_jn'] or a.get('journal') or 'unknown',
                     'date': a.get('obtained_at', '')[:10] or datetime.now().date().isoformat(),
                     'source': src})
    todo.sort(key=lambda x: (x['source'], x['journal'], x['title']))
    return todo, already, len(seen)


# ─── 单篇抓取（与 daily_archive_papers.archive_one 同链路，只是多传 source）──
def fetch_one(item, index_keys, lock):
    key = _norm_title(item['title'])
    with lock:
        if _ledger_get(index_keys, item['title'], url=item.get('url')) is not None:
            return 'skipped', '已在 NAS'

    from journal_pdf_fetch import fetch_pdf
    r = fetch_pdf(item['journal'], item['title'], item['url'], date=item['date'],
                  doi=item.get('doi') or '')
    status, data = r.get('status', ''), r.get('pdf_bytes')
    if status != 'ok' or not data:
        return 'fail', '%s: %s' % (status, str(r.get('note', ''))[:110])

    # 落盘：复用 pdf_downloader._save_pdf（与既有仓目录约定完全一致）
    try:
        import pdf_downloader as pdl
        jpath = pdl._save_pdf(data, item['journal'] or 'unknown',
                              item['title'], item['date'], '_fetch')
    except Exception as e:  # noqa: BLE001
        return 'fail', 'save_err: %s: %s' % (type(e).__name__, str(e)[:70])

    # 抓完立刻自证：文件必须在 NAS 上且字节数吻合，否则登记了也是死链
    try:
        actual = Path(jpath).stat().st_size
    except OSError as e:
        return 'fail', 'verify_err: %s' % e
    if actual != len(data):
        return 'fail', 'verify_err: size %d != %d' % (actual, len(data))

    import hashlib
    meta = {
        'title': item['title'], 'url': item['url'], 'journal': item['journal'],
        'date': item['date'], 'pdf_url': r.get('pdf_url', ''),
        'repo_path': jpath, 'size': len(data),
        'source': r.get('source', 'fetch'),
        # 门户 journals 页据此判「已归档」并挂阅读/翻译入口（eid+pdf_rel）
        'eid': 'paper-%s' % hashlib.sha1(data).hexdigest()[:12],
        'indexed_at': datetime.now().isoformat(),
    }
    with lock:
        append_index(meta)
        index_keys[key] = meta
    return 'ok', jpath


# ─── 单源站工作线程（组内串行 + 节流）────────────────────────────────────
def run_source(src, items, index_keys, lock, cnt, fails, sleep_s, total_ref,
               ok_by_source):
    for i, it in enumerate(items, 1):
        tag = '[%s %d/%d]' % (src, i, len(items))
        try:
            st, note = fetch_one(it, index_keys, lock)
        except Exception as e:  # noqa: BLE001
            st, note = 'fail', '%s: %s' % (type(e).__name__, str(e)[:90])
        with lock:
            cnt[st] = cnt.get(st, 0) + 1
            done = sum(cnt.values())
        mark = {'ok': '✓', 'skipped': '-', 'fail': '×'}[st]
        log('  %s %s %-30s %s' % (tag, mark, it['journal'][:30], it['title'][:44]))
        log('        → %s' % note[:120])
        if st == 'ok':
            with lock:
                ok_by_source[src] += 1
        if st == 'fail':
            with lock:
                fails.append({'source': src, 'journal': it['journal'],
                              'title': it['title'], 'url': it['url'], 'note': note})
        # 同一源站内节流；最后一篇不用歇
        if i < len(items) and st != 'skipped' and sleep_s > 0:
            time.sleep(sleep_s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--list', action='store_true', help='只列篇目与源站分布，不发请求')
    ap.add_argument('--source', default='', help='只补指定源站，逗号分隔')
    ap.add_argument('--journal', default='', help='只补指定期刊（模糊匹配）')
    ap.add_argument('--limit', type=int, default=0, help='总篇数上限（0=不限）')
    ap.add_argument('--workers', type=int, default=6, help='并发源站数（默认 6）')
    ap.add_argument('--sleep', type=float, default=3.0, help='同源站篇间节流秒数')
    args = ap.parse_args()

    log('=' * 74)
    log('期刊 PDF 全量补抓  %s' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    log('=' * 74)

    todo, already, total = collect_todo()
    log('共享取数层文章总数: %d · 已归档跳过: %d · 待抓: %d' % (total, already, len(todo)))

    if args.source:
        want = {s.strip() for s in args.source.split(',') if s.strip()}
        todo = [t for t in todo if t['source'] in want]
        log('按源站过滤后: %d（%s）' % (len(todo), ','.join(sorted(want))))
    if args.journal:
        todo = [t for t in todo if args.journal.lower() in t['journal'].lower()]
        log('按期刊过滤后: %d（含 "%s"）' % (len(todo), args.journal))
    if args.limit and len(todo) > args.limit:
        todo = todo[:args.limit]
        log('限量: %d' % len(todo))

    if not todo:
        log('无可补抓内容 —— 全部已归档或被过滤')
        return 0

    by_src = defaultdict(list)
    for t in todo:
        by_src[t['source']].append(t)
    log('\n源站分布（组内串行、组间并行，workers=%d，节流 %.1fs）:'
        % (args.workers, args.sleep))
    for s, lst in sorted(by_src.items(), key=lambda x: -len(x[1])):
        log('  %-14s %3d 篇' % (s, len(lst)))

    if args.list:
        log('\n--list 模式，不发网络请求。篇目：')
        for t in todo:
            log('  [%s] %-26s %s' % (t['source'], t['journal'][:26], t['title'][:60]))
        return 0

    index_keys = load_index_keys()
    log('\n台账已有 %d 条（命中即跳过，可随时中断续跑）' % len(index_keys))
    log('-' * 74)

    lock = threading.Lock()
    cnt, fails = Counter(), []
    ok_by_source = Counter()
    threads = []
    srcs = sorted(by_src.keys(), key=lambda s: -len(by_src[s]))
    # 源站数可能多于 workers：按 workers 分批起线程，前一批结束再起下一批
    batch, launched = [], 0
    batches = []
    for s in srcs:
        batch.append(s)
        if len(batch) >= args.workers:
            batches.append(batch)
            batch = []
    if batch:
        batches.append(batch)

    t0 = time.time()
    for bi, batch in enumerate(batches, 1):
        if len(batches) > 1:
            log('\n── 第 %d/%d 批源站: %s ──' % (bi, len(batches), ', '.join(batch)))
        for s in batch:
            th = threading.Thread(target=run_source,
                                  args=(s, by_src[s], index_keys, lock, cnt, fails,
                                        args.sleep, [len(todo)], ok_by_source),
                                  name='src-%s' % s, daemon=True)
            th.start()
            threads.append(th)
        for th in threads:
            th.join()
        threads = []

    dt = time.time() - t0
    ok, skip, fail = cnt.get('ok', 0), cnt.get('skipped', 0), cnt.get('fail', 0)
    log('-' * 74)
    # ⚠️ 不要在一个格式串里混用 %.1f 与 %(ok)d 再只传一个 dict：
    #    单个 dict 会被当成映射，%.1f 会尝试把 dict 当数字 → TypeError。
    #    实测踩过（2026-10-19，跑完 40 分钟后在汇总行崩掉，报告没落盘）。
    log('完成（耗时 %.1f 分钟）：成功 %d · 已存在 %d · 失败 %d' % (dt / 60, ok, skip, fail))

    if fails:
        by_reason = Counter()
        for f in fails:
            by_reason[f['note'].split(':')[0][:40]] += 1
        log('\n失败原因 TOP:')
        for r, n in by_reason.most_common(12):
            log('  %-42s %d' % (r, n))
        log('\n失败是常态（源站未上传全文 / 付费墙 / 反爬），记入报告供次日补漏:')
        for f in fails[:20]:
            log('  - [%s] %s | %s' % (f['source'], f['title'][:44], f['note'][:70]))

    report = {
        'ts': datetime.now().isoformat(),
        'elapsed_sec': round(dt, 1),
        'todo_total': len(todo),
        'counts': {'ok': ok, 'skipped': skip, 'fail': fail},
        'by_source_ok': dict(ok_by_source),
        'failures': fails,
    }
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                          encoding='utf-8')
        log('\n报告已存: %s' % REPORT)
    except OSError as e:
        log('  [warn] 报告写入失败: %s' % e)
    return 0


if __name__ == '__main__':
    sys.exit(main())
