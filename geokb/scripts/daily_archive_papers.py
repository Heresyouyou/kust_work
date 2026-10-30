"""当日期刊论文自动归档：网站 → NAS papers_repo（2026-10-17）

背景（用户语义）：
    「下载 PDF」按钮 = **NAS → 用户本地**；
    网站 → NAS 这一段必须由**每日管线自动完成**，不能依赖用户点按钮。

本脚本的定位：
    journal_checker 抓完期刊列表、写完 journal_updates.json 后由 daily_run
    subprocess 调用，把「当天首次获取到的文章」逐篇抓 PDF 落 NAS 并登记台账。
    只处理**当天**（本地日期 first_seen），历史存量由 ingest_external_feeds
    的 journal_pushed 通道与本脚本 --backfill 兜底。

关键约束（踩过的坑）：
    · 源站限流：岩石学报等对集中连跑会返回非 PDF —— 篇间必须 sleep（默认 3s），
      且单篇失败不阻塞后续（下载失败是常态，记台账 status 后继续）。
    · 幂等：台账 index.jsonl 里已有同 title+url 的 ok 记录就跳过，可安全重跑。
    · SMB 偶发 Permission denied：台账登记容错，不影响 PDF 落盘。
    · 绝不写知识库条目：本脚本只管「文件层」；条目入库由 ingest_external_feeds
      （题录或全文）与 index_one_entry.py（增量索引）负责，避免职责重叠。

用法：
    python daily_archive_papers.py                # 当天
    python daily_archive_papers.py --days 7       # 近 7 天（补漏）
    python daily_archive_papers.py --list         # 只列篇目与命中状态（不发网络请求）
    python daily_archive_papers.py --limit 20     # 限量（默认 200，防单日爆量）

⚠️ `--dry-run` 仍会真下载（fetch_pdf 是副作用函数，无法干跑），
   只用于验证「落盘 + 登记」链路；要纯看列表请用 `--list`。
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]          # D:/ZSK/geokb
POSTNEWS = Path('D:/PostNews')
sys.path.insert(0, str(POSTNEWS))
sys.path.insert(0, str(BASE / 'scripts'))

import ingest_policy                                   # noqa: E402  入库策略
from sd_policy import is_sd, paused as sd_paused       # noqa: E402  ScienceDirect 判据/开关

LEDGER = POSTNEWS / 'journal_ledger.json'
UPDATES = POSTNEWS / 'journal_updates.json'
REPO = Path('S:/GeoKB/papers_repo')
# 台账主副本放**本地**：NAS 上的 S:/GeoKB/papers_repo/index.jsonl 会被 QTS 侧
# 独占锁定（Python open(...,'a') 与 os.replace 均返回 PermissionError/Error 5），
# 台账是纯展示/索引用途，没必要放在 SMB 上冒写入风险。
# 首次迁移由 migrate_ledger_to_local.py 完成；此处的 _read_ledger 会自动兜底读旧位置。
LOCAL_INDEX = BASE / 'state' / 'papers_index.jsonl'
LEGACY_INDEX = REPO / 'index.jsonl'
INDEX_JSONL = LOCAL_INDEX
STATE = BASE / 'index' / 'daily_archive_state.json'
# 本轮新下载成功的 PDF 清单（供后续「只入库当天新抓论文」精确筛选）
LAST_RUN_FILE = BASE / 'state' / 'daily_archive_last.json'

# URL 二级索引（2026-10-18）：load_index_keys() 时构建，
# _ledger_get(url=...) 用它救回「两套标题」的同文，避免重复抓取。
_url_index_keys = {}

SLEEP_BETWEEN = 3.0        # 篇间节流（秒）——源站限流的教训
DEFAULT_LIMIT = 200


def log(msg):
    print(msg, flush=True)


def _norm_title(s):
    """跨源同文判定键，与门户共享层 `journal_feed.norm_title` 保持同一规则。

    ⚠️ 2026-10-19 修复：原先这里也做了 `[:80]` 截断，与门户同源问题 ——
    台账里存的是源站列表给的截断简称（'...phosphate-rich systems'），
    而 journals 文章是全称（'...systems: integrated apatite in-situ...'），
    截断后两侧键不同 → 幂等判定失效，会把 NAS 上已有的论文再抓一遍，
    生成重复文件。现在保留完整键，截断简称的兜底见 `_ledger_get()`。
    """
    return re.sub(r'\W+', '', (s or '').lower())


def _has_pdf(meta):
    """台账条目是否代表「NAS 上真有 PDF」。

    ⚠️ 2026-10-17 修复：台账里混有 GeoKB 入库链路写的**纯题录**条目
    （`source: metadata_only`，repo_path 为空、size=0），它们用于检索但没有 PDF。
    旧版 `_ledger_get()` 只看键是否存在，于是把这些题录当成「已归档」而跳过 ——
    实测 28 条题录中有 14 条落在 journals 60 天窗口内，页面显示「在线抓取」，
    补抓却又因「已在 NAS」永不下载，两边互相等，形成静默死角。
    """
    rp = str((meta or {}).get('repo_path') or '')
    return 'papers_repo/' in rp.replace('\\', '/')


def _ledger_get(index_keys, title, url=None):
    """按标题查台账，带回退（与 journal_feed.lookup_ledger 同规则）。

    1) 精确命中（且真有 PDF，见 _has_pdf）
    2) 前缀互为包含（键长 ≥79 才触发，即原本会被旧 `[:80]` 截断的长度），
       且候选唯一 + 真有 PDF —— 救回「全称 vs 截断简称」的同文，避免重复抓取。
    3) URL 二级匹配（2026-10-18，与 journal_feed.lookup_ledger 同步）：
       台账 title（文章页 citation_title）与期刊列表题录可能是两套标题
       （中英文混排/改题），实测 8 篇 sciengine 因此被反复重下。
    """
    k = _norm_title(title)
    if k:
        m = index_keys.get(k)
        if m is not None:
            # 精确命中即同一篇：没 PDF 就是真没归档，不再拿前缀兜底去配别篇
            return m if _has_pdf(m) else None
        if len(k) >= 79:
            cands = [kk for kk in index_keys
                     if len(kk) >= 40 and (kk.startswith(k) or k.startswith(kk))
                     and _has_pdf(index_keys[kk])]
            if len(cands) == 1:
                return index_keys[cands[0]]
    if url:
        m = _url_index_keys.get(_norm_title(url))
        if m is not None and _has_pdf(m):
            return m
    return None


# ─── 台账（决定幂等）─────────────────────────────────────────────────────
def _iter_index_lines():
    """依次读「本地台账 + NAS 旧台账」（旧台账只读，作为迁移前的兜底数据源）。"""
    for p in (LOCAL_INDEX, LEGACY_INDEX):
        if not p.exists():
            continue
        try:
            with open(p, encoding='utf-8', errors='replace') as f:
                for line in f:
                    yield line
        except OSError as e:
            log(f'  [warn] 台账 {p.name} 读取失败（跳过）: {e}')


def load_index_keys():
    """{norm_title: 最新 meta}。本地台账优先，旧 NAS 台账只作兜底补充。

    ⚠️ 2026-10-17：同一键可能出现多行（先 PDF 归档、后题录入库，或反之）。
    旧版「后者覆盖前者」会让题录遮蔽掉 PDF 条目 → 误判未归档 → 重复下载。
    现在**有 PDF 的条目优先保留**，两条都有 PDF 时仍取后者（后者更新）。

    同时构建 URL 二级索引 `_url_index_keys`（2026-10-18）：
    norm_title(url) -> meta，供 `_ledger_get(url=...)` 做标题变体匹配。
    """
    out = {}
    url_idx = {}
    for line in _iter_index_lines():
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except Exception:
            continue
        k = _norm_title(m.get('title'))
        if not k:
            continue
        prev = out.get(k)
        if prev is not None and _has_pdf(prev) and not _has_pdf(m):
            continue                      # 别让题录覆盖掉真 PDF 条目
        out[k] = m
        uk = _norm_title(m.get('url'))
        if uk:
            prev_u = url_idx.get(uk)
            if prev_u is not None and _has_pdf(prev_u) and not _has_pdf(m):
                continue                  # 同上：PDF 条目优先
            url_idx[uk] = m
    global _url_index_keys
    _url_index_keys = url_idx
    return out


def append_index(meta):
    """登记台账（写本地副本）。PDF 已落盘时，台账失败不影响主流程。"""
    try:
        LOCAL_INDEX.parent.mkdir(parents=True, exist_ok=True)
        with open(LOCAL_INDEX, 'a', encoding='utf-8') as f:
            f.write(json.dumps(meta, ensure_ascii=False) + '\n')
        return True
    except Exception as e:
        log(f'  [warn] 台账登记失败（PDF 已落盘，不影响）: {e}')
        return False


def load_state():
    try:
        return json.loads(STATE.read_text(encoding='utf-8'))
    except Exception:
        return {'days': {}}


def save_state(st):
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2),
                         encoding='utf-8')
    except Exception as e:
        log(f'  [warn] 状态写入失败: {e}')


# ─── 采集当日文章 ────────────────────────────────────────────────────────
def collect(days, limit):
    """从 journal_ledger 的 first_seen 判定「最近 N 天首次获取」，journal_updates
    只作补充（更新轮里出现但台账尚未落 first_seen 的）。

    优先级（2026-10-20 用户要求「当天更新优先」）：
      prio 0 = 当天首次获取（最新一批，最新鲜，最该先抓）
      prio 1 = 近 N 天内的较早日期
      prio 2 = 无日期的存量（journal_updates 补充）
    排序 = 先按 prio 升序，再按日期倒序。Python sort 稳定，故 prio 内部仍保日期序。
    """
    cutoff = (datetime.now() - timedelta(days=days - 1)).date()
    today = datetime.now().date()
    items, seen = [], set()

    if LEDGER.exists():
        d = json.loads(LEDGER.read_text(encoding='utf-8'))
        for jname, arts in (d.get('journals') or {}).items():
            for a in arts or []:
                if not isinstance(a, dict):
                    continue
                ts = a.get('first_seen') or ''
                try:
                    day = datetime.fromisoformat(ts).date()
                except Exception:
                    continue
                if day < cutoff:
                    continue
                key = _norm_title(a.get('title'))
                if not key or key in seen:
                    continue
                seen.add(key)
                items.append({
                    'title': (a.get('title') or '').strip(),
                    'url': (a.get('url') or '').strip(),
                    'journal': jname,
                    'date': day.isoformat(),
                    'prio': 0 if day == today else 1,
                })

    # journal_updates 补充（有更新但台账 first_seen 缺失/过期的存量条目）
    if UPDATES.exists():
        try:
            u = json.loads(UPDATES.read_text(encoding='utf-8'))
            for jname, blk in (u or {}).items():
                if jname.startswith('_') or not isinstance(blk, dict):
                    continue
                for a in blk.get('new_articles') or []:
                    if not isinstance(a, dict):
                        continue
                    key = _norm_title(a.get('title'))
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    items.append({'title': (a.get('title') or '').strip(),
                                  'url': (a.get('url') or '').strip(),
                                  'journal': jname, 'date': 'updates', 'prio': 2})
        except Exception as e:
            log(f'  [warn] journal_updates 解析跳过: {e}')

    items.sort(key=lambda x: x['date'], reverse=True)   # 先日期倒序（稳定）
    items.sort(key=lambda x: x.get('prio', 1))          # 再按优先级（稳定，不打乱上一步）

    # ── ScienceDirect：暂停期不抓，恢复后也排最后 ──────────────────────
    # 2026-10-20：SD 的 /pdf /pdfft 被 CF 内容防护(1000S)硬拦，反复试只加深
    # IP 标记；且 OGR/Lithos 日更量大，会把当天 limit 名额全吃掉。
    for x in items:
        x['sd'] = is_sd(x.get('doi', ''), x.get('url', ''), x.get('journal', ''))
    n_sd = sum(1 for x in items if x['sd'])
    if n_sd and sd_paused():
        items = [x for x in items if not x['sd']]
        log('  [SD 暂停] 跳过 %d 篇 Elsevier/ScienceDirect，本批剩 %d 篇' % (n_sd, len(items)))
    elif n_sd:
        items.sort(key=lambda x: 1 if x['sd'] else 0)

    return items[:limit]


def _index_py():
    """跑增量索引要用的解释器。

    ⚠️ 必须用 geokb venv：index_one_entry → server/ingest 需要 jieba/fastembed，
    系统 Python 没有（实测 `ModuleNotFoundError: No module named 'jieba'`，
    于是逐篇入库会静默变成 rc=1）。
    """
    for p in ('D:/ZSK/geokb/venv/Scripts/python.exe', sys.executable):
        if p and os.path.isfile(p):
            return p
    return sys.executable


def _ingest_one(meta):
    """把刚归档的一篇「立刻」推进知识库，返回说明字符串。

    用户语义（2026-10-20）：「一次爬一篇，入库后再爬下一篇」。
    这样做的好处不只是礼貌——批量的最大风险是**攒一堆再处理**，
    一旦中途被源站限流或进程被杀，已抓到的全都白抓。

    两档落地：
      · 条目已存在（别处已 LLM 提取过）→ 立刻增量索引（~0.6s），秒级可检索；
      · 条目不存在 → 记入 state/pending_papers_ingest.jsonl，由管线在
        DeepSeek 低谷窗口补抽取。**绝不在此处调 LLM**（高峰时段铁律）。
    """
    eid = meta.get('eid') or ''
    if not eid:
        return 'no-eid'
    # 策略闸门（2026-10-20）：daily_poster 是**唯一**允许自动入库的链路。
    # 其它链路（history / migrate / external / backfill）一律只下载，
    # 由各自脚本记 pending_papers_ingest.jsonl，等用户统一安排。
    if not ingest_policy.enabled('daily_poster'):
        ingest_policy.queue_pending({
            'eid': eid, 'title': meta.get('title', ''),
            'journal': meta.get('journal', ''), 'pdf': meta.get('repo_path', ''),
            'url': meta.get('url', ''), 'doi': meta.get('doi', ''),
            'from': 'daily_poster(off)',
        })
        return 'queued(policy)'
    entry = None
    for mod in ('papers', 'external-feed'):
        p = BASE / 'content' / mod / f'{eid}.md'
        if p.is_file():
            entry = p
            break
    if entry is not None:
        try:
            import subprocess
            r = subprocess.run(
                [_index_py(), str(BASE / 'scripts' / 'index_one_entry.py'), eid],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=600,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return 'indexed' if r.returncode == 0 else f'index_rc{r.returncode}'
        except Exception as e:  # noqa: BLE001
            return f'index_err:{type(e).__name__}'
    # 条目尚未生成 → 排队，等低谷窗口补抽取
    try:
        q = BASE / 'state' / 'pending_papers_ingest.jsonl'
        rec = {'eid': eid, 'title': meta.get('title', ''),
               'journal': meta.get('journal', ''), 'pdf': meta.get('repo_path', ''),
               'url': meta.get('url', ''), 'ts': datetime.now().isoformat()}
        with open(q, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        return 'queued'
    except OSError as e:
        return f'queue_err:{type(e).__name__}'


# ─── 单篇抓取 ────────────────────────────────────────────────────────────
def archive_one(item, index_keys, dry_run=False):
    """返回 (status, note)。status ∈ skipped / ok / fail"""
    if _ledger_get(index_keys, item['title'], url=item.get('url')) is not None:
        return 'skipped', '已在 NAS'

    if not item['url'].startswith('http'):
        return 'fail', '无有效 URL'

    # --list 模式：只判定，不发网络请求
    if dry_run == 'list':
        return 'todo', item['url'][:96]

    from journal_pdf_fetch import fetch_pdf

    r = fetch_pdf(item['journal'], item['title'], item['url'],
                  date=item['date'], doi=item.get('doi') or '')
    status = r.get('status', '')
    data = r.get('pdf_bytes')

    if status != 'ok' or not data:
        return 'fail', f"{status}: {str(r.get('note', ''))[:90]}"

    # ── 版本闸门（2026-10-27）：录用稿/校样不落盘、不入库 ──────────────
    # GSW "Just Accepted" 一类的 accepted manuscript：全篇行号、无卷期页码、
    # "the published version is subject to change" → 收进来会污染引用页码。
    # 只扫第 1 页 + 只用强判据，避免参考文献里的 "in press" 误伤
    # （见 scripts/pdf_version_guard.py 的实测说明）。
    try:
        import pdf_version_guard as _vg
        _vr = _vg.check_bytes(data)
    except Exception:  # noqa: BLE001
        _vr = {'version': 'other', 'evidence': []}
    if _vr.get('version') == 'accepted':
        return 'fail', ('accepted_version: 录用稿/校样，非正式排版版（%s）'
                        % ','.join(_vr.get('evidence') or [])[:70])

    if dry_run == 'dry':
        return 'ok', f"[dry] {len(data)} bytes from {r.get('source')}"

    # 落盘：复用 pdf_downloader._save_pdf（与既有仓目录约定完全一致）
    try:
        import pdf_downloader as pdl
        jpath = pdl._save_pdf(data, item['journal'] or 'unknown',
                              item['title'], item['date'], '_fetch')
    except Exception as e:
        return 'fail', f'save_err: {type(e).__name__}: {str(e)[:70]}'

    meta = {
        'title': item['title'], 'url': item['url'], 'journal': item['journal'],
        'date': item['date'], 'pdf_url': r.get('pdf_url', ''),
        'repo_path': jpath, 'size': len(data),
        'source': r.get('source', 'fetch'),
        # 门户 journals 页据此判「已归档」并挂阅读/翻译入口（eid+pdf_rel）
        'eid': f'paper-{hashlib.sha1(data).hexdigest()[:12]}',
        'indexed_at': datetime.now().isoformat(),
    }
    append_index(meta)
    index_keys[_norm_title(item['title'])] = meta
    return 'ok', jpath


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=1, help='最近 N 天（默认 1 = 当天）')
    ap.add_argument('--limit', type=int, default=DEFAULT_LIMIT)
    ap.add_argument('--list', action='store_true',
                    help='只列篇目与命中状态，不发网络请求')
    ap.add_argument('--dry-run', action='store_true',
                    help='仍真下载但不落盘（验证下载链路）')
    ap.add_argument('--no-sleep', action='store_true', help='调试用，关闭节流')
    ap.add_argument('--serial-ingest', action='store_true',
                    help='逐篇入库：每抓成一篇立刻推进知识库，再抓下一篇（默认开）')
    ap.add_argument('--no-serial-ingest', action='store_true',
                    help='关闭逐篇入库（仅在需要纯文件层归档时用）')
    ap.add_argument('--retry-failed', type=int, default=1,
                    help='失败项在**本轮末尾**补试几次（默认 1，设为 0 关闭）。'
                         '当天的新文章若因瞬时限流失败，等到次日会掉出当日窗口。')
    args = ap.parse_args()
    serial = not args.no_serial_ingest

    mode = 'list' if args.list else ('dry' if args.dry_run else 'run')
    log('=' * 62)
    log('当期刊自动归档  %s  days=%d limit=%d  mode=%s'
        % (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
           args.days, args.limit, mode))
    log('=' * 62)

    items = collect(args.days, args.limit)
    log(f'候选文章 {len(items)} 篇')
    if not items:
        log('无可归档内容（当日无新增）')
        return 0

    index_keys = load_index_keys()
    log(f'台账已有 {len(index_keys)} 条，将跳过其中命中项')
    log('-' * 62)

    cnt = {'ok': 0, 'skipped': 0, 'fail': 0, 'todo': 0}
    ok_paths = []            # 本轮真正新下载成功、已落盘的 PDF 绝对路径
    fail_notes = {}          # norm_title -> 明细（补试成功即剔除）
    failed_items = []
    for i, it in enumerate(items, 1):
        try:
            st, note = archive_one(it, index_keys, mode)
        except Exception as e:
            st, note = 'fail', f'{type(e).__name__}: {str(e)[:80]}'
        cnt[st] = cnt.get(st, 0) + 1
        if st == 'ok' and mode == 'run':
            ok_paths.append(note)
        mark = {'ok': '✓', 'skipped': '-', 'fail': '×', 'todo': '○'}[st]
        prio_tag = '今' if it.get('prio') == 0 else ('近' if it.get('prio') == 1 else '存')
        log(f'  [{i:>3}/{len(items)}] {mark}[{prio_tag}] [{it["journal"]}] {it["title"][:44]}')
        if st == 'ok' and serial:
            meta = index_keys.get(_norm_title(it['title'])) or {}
            log(f'          → 入库: {_ingest_one(meta)}')
        log(f'          {note[:110]}')
        if st == 'fail':
            failed_items.append(it)
            fail_notes[_norm_title(it['title'])] = (
                f"{it['journal']}|{it['title'][:40]}|{note}")
        # 节流：源站对集中连跑会限流（返回非 PDF），篇间必须歇
        if not args.no_sleep and i < len(items) and st not in ('skipped', 'todo'):
            time.sleep(SLEEP_BETWEEN)

    # ── 当日失败补试 ───────────────────────────────────────────────
    # 当天的新文章若因瞬时限流失败，等到次日就掉出当日窗口（days=1 不再覆盖），
    # 因此在本轮末尾以更长的间隔补试，仍是「一次一篇」节奏。
    #
    # 只补「瞬态失败」（网络/限流/验证码/5xx）：paywalled、no_fulltext、
    # not_pdf_office 这些是**确定性**结论，再试一百次也一样，补试纯属浪费时间
    # （2026-10-20 实测：3 篇 elsevier 让一轮从 60s 拖到 10 分钟以上）。
    TRANSIENT = ('http_429', 'http_5', 'http_403', 'err_', 'blocked', 'timeout')
    if mode == 'run':
        retryable = []
        for it in failed_items:
            note = fail_notes.get(_norm_title(it['title']), '')
            tail = note.rsplit('|', 1)[-1]
            if any(k in tail for k in TRANSIENT) and 'paywalled' not in tail:
                retryable.append(it)
        if len(retryable) < len(failed_items):
            log(f'补试范围：{len(retryable)}/{len(failed_items)} 篇属瞬态失败'
                f'（其余为确定性结论：付费墙 / 无全文 / 仅 Office 稿，不再重试）')
        failed_items = retryable

    if failed_items and args.retry_failed > 0 and mode == 'run':
        for round_no in range(1, args.retry_failed + 1):
            log('-' * 62)
            log(f'补试第 {round_no} 轮：{len(failed_items)} 篇（间隔 {SLEEP_BETWEEN * 3:.0f}s）')
            still, recovered = [], 0
            for it in failed_items:
                time.sleep(SLEEP_BETWEEN * 3)
                try:
                    st, note = archive_one(it, index_keys, mode)
                except Exception as e:
                    st, note = 'fail', f'{type(e).__name__}: {str(e)[:80]}'
                key = _norm_title(it['title'])
                if st == 'ok':
                    recovered += 1
                    ok_paths.append(note)
                    cnt['fail'] -= 1
                    cnt['ok'] += 1
                    fail_notes.pop(key, None)
                    if serial:
                        meta = index_keys.get(key) or {}
                        log(f'  ✓[补] [{it["journal"]}] {it["title"][:40]} → 入库: {_ingest_one(meta)}')
                else:
                    still.append(it)
                    fail_notes[key] = f"{it['journal']}|{it['title'][:40]}|{note}"
                    log(f'  ×[补] [{it["journal"]}] {it["title"][:40]} | {note[:70]}')
            log(f'补试第 {round_no} 轮：恢复 {recovered} 篇，仍失败 {len(still)} 篇')
            failed_items = still
            if not failed_items:
                break
    fail_notes = list(fail_notes.values())

    log('-' * 62)
    log('完成：成功 %(ok)d · 已存在 %(skipped)d · 待抓 %(todo)d · 失败 %(fail)d' % cnt)
    if fail_notes:
        log('失败明细（供次日补漏，失败是常态：源站未上传全文 / 反爬 / 无 DOI 入口）:')
        for n in fail_notes[:15]:
            log('  - ' + n)

    # 记录本轮新下载成功的 PDF（后续入库只处理这批，不吃历史欠账）
    # 只有 run 模式才写：--list / --dry-run 不落盘，不得覆盖真实清单。
    if mode == 'run':
        try:
            LAST_RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
            LAST_RUN_FILE.write_text(json.dumps({
                'date': datetime.now().date().isoformat(),
                'ts': datetime.now().isoformat(timespec='seconds'),
                'paths': ok_paths,
            }, ensure_ascii=False, indent=1), encoding='utf-8')
            log('本轮新下载 %d 篇 → 已记录到 %s' % (len(ok_paths), LAST_RUN_FILE.name))
        except Exception as _e:
            log('[warn] 写入本轮下载清单失败: %s' % _e)

    st = load_state()
    st['days'][datetime.now().date().isoformat()] = {
        'ts': datetime.now().isoformat(), 'count': cnt,
        'failed': fail_notes,
    }
    save_state(st)
    return 0


if __name__ == '__main__':
    sys.exit(main())
