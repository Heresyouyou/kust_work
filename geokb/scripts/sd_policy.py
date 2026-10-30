# -*- coding: utf-8 -*-
"""ScienceDirect（Elsevier）抓取策略：**全局暂停开关 + 清单后排**。

为什么单独成模块
----------------
2026-10-20 实测（以 S0169136826004932 为例）：SD 的 `/pdf`、`/pdfft` 端点返回
403，响应体是 `::CLOUDFLARE_ERROR_1000S_BOX::`；机构登录态（EUID）、TLS 指纹
（chrome/edge101/safari）、UA、Referer、人工过 Turnstile **全部无效**，仍 302 回
文章页。继续重试只会加深出口 IP 的标记（当天评分曲线 43s→112s→200s→直接 403）。
故收敛为：**SD 全线暂停，清单里也沉到最末尾**。

一处开关，三处生效
------------------
  · `history_queue.py`            —— 暂停期整批跳过；恢复后 SD 仍排最后
  · `daily_archive_papers.py`     —— 当日新论文不抓 SD（不吃掉 limit 配额）
  · `journal_pdf_downloader.fetch_one` —— 兜底闸门：只保留 OA 渠道，不碰 SD 端点

开关文件：`state/sd_pause.json`
    {"paused": true, "since": "...", "reason": "..."}
环境变量 `GEOKB_SD_PAUSE=1/0` 优先级最高（临时覆盖，不用改文件）。

CLI
----
    python scripts/sd_policy.py --status
    python scripts/sd_policy.py --pause --reason "…"
    python scripts/sd_policy.py --resume
    python scripts/sd_policy.py --reorder            # 只报告，不动文件
    python scripts/sd_policy.py --reorder --apply    # 备份后重排 manifest
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
STATE = BASE / 'state'
PAUSE_FILE = STATE / 'sd_pause.json'
MANIFEST = STATE / 'history_manifest.jsonl'
STATS = STATE / 'history_manifest_stats.json'

# ── SD 身份判据（三者任一命中即视为 SD）────────────────────────────────
# ① DOI 前缀 10.1016 覆盖 Elsevier 全部期刊（Ore Geology Reviews / Lithos /
#    Earth and Planetary Science Letters / Journal of Asian Earth Sciences …）
# ② 主机名覆盖 sciencedirect / linkinghub / els-cdn 等前端与 CDN
# ③ 刊名兜底（有些条目只给刊名，没有 DOI/URL）
SD_DOI_PREFIXES = ('10.1016/',)
SD_HOSTS = ('sciencedirect.com', 'linkinghub.elsevier.com',
            'els-cdn.com', 'elsevier.com', 'doi.org/10.1016/')
SD_JOURNALS = frozenset({
    'ore geology reviews',
    'lithos',
    'earth and planetary science letters',
})


def is_sd(doi: str = '', url: str = '', journal: str = '') -> bool:
    """判断一条记录是否属于 ScienceDirect / Elsevier。"""
    d = (doi or '').strip().lower()
    d = d.replace('https://doi.org/', '').replace('http://doi.org/', '')
    d = d.replace('doi:', '').lstrip('/')
    for p in SD_DOI_PREFIXES:
        if d.startswith(p):
            return True
    u = (url or '').strip().lower()
    for h in SD_HOSTS:
        if h in u:
            return True
    j = (journal or '').strip().lower()
    return j in SD_JOURNALS


# ── 暂停开关 ────────────────────────────────────────────────────────────
_cache = {'mtime': None, 'state': None}


def pause_state() -> dict:
    """读开关文件（按 mtime 缓存）。文件不存在视为「未暂停」。"""
    env = os.environ.get('GEOKB_SD_PAUSE', '').strip().lower()
    try:
        mt = PAUSE_FILE.stat().st_mtime if PAUSE_FILE.exists() else None
    except Exception:
        mt = None
    if _cache['state'] is None or _cache['mtime'] != mt:
        st = {'paused': False, 'since': '', 'reason': '', 'src': 'default'}
        if mt is not None:
            try:
                raw = json.loads(PAUSE_FILE.read_text(encoding='utf-8'))
                st.update({k: raw.get(k, st[k]) for k in ('paused', 'since', 'reason')})
                st['paused'] = bool(raw.get('paused'))
                st['src'] = 'file'
            except Exception as e:  # noqa: BLE001
                st['reason'] = '开关文件解析失败: %s' % e
        _cache.update({'mtime': mt, 'state': st})
    st = dict(_cache['state'])
    if env in ('1', 'true', 'yes', 'on'):
        st.update({'paused': True, 'src': 'env'})
    elif env in ('0', 'false', 'no', 'off'):
        st.update({'paused': False, 'src': 'env'})
    return st


def paused() -> bool:
    """SD 抓取是否处于暂停状态。"""
    return bool(pause_state().get('paused'))


def set_paused(flag: bool, reason: str = '', who: str = 'cli') -> dict:
    """写开关。保留历史 since（恢复时清空）。"""
    old = pause_state()
    payload = {
        'paused': bool(flag),
        'since': datetime.now().isoformat(timespec='seconds') if flag else '',
        'reason': (reason or ('手动暂停' if flag else '手动恢复'))[:500],
        'by': who,
        'resumed_at': datetime.now().isoformat(timespec='seconds') if not flag else '',
        'prev_since': old.get('since', '') if not flag else '',
    }
    STATE.mkdir(parents=True, exist_ok=True)
    PAUSE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                          encoding='utf-8')
    _cache.update({'mtime': None, 'state': None})
    return payload


# ── 清单重排（SD 沉底）──────────────────────────────────────────────────
def reorder_items(items):
    """稳定分区：非 SD 保持原相对顺序在前，SD 原相对顺序在后。

    返回 (new_items, n_sd)。同时给每条打上 `sd` 布尔字段，供队列快速判定。
    """
    head, tail, n_sd = [], [], 0
    for x in items:
        sd = bool(x.get('sd')) if 'sd' in x else is_sd(
            x.get('doi', ''), x.get('url', ''), x.get('journal', ''))
        x['sd'] = sd
        if sd:
            tail.append(x)
            n_sd += 1
        else:
            head.append(x)
    return head + tail, n_sd


def reorder_manifest(apply: bool = False):
    """重排 state/history_manifest.jsonl（SD → 末尾），刷新 HTML/统计。"""
    if not MANIFEST.exists():
        return {'ok': False, 'msg': '清单不存在: %s' % MANIFEST}
    items = []
    with open(MANIFEST, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                continue
    if not items:
        return {'ok': False, 'msg': '清单为空'}

    before_first10 = [x['journal'] for x in items[:10]]
    new_items, n_sd = reorder_items(items)
    after_first10 = [x['journal'] for x in new_items[:10]]
    res = {
        'ok': True, 'total': len(new_items), 'sd': n_sd,
        'non_sd': len(new_items) - n_sd,
        'first10_before': before_first10, 'first10_after': after_first10,
        'applied': False, 'backup': '',
    }
    if not apply:
        return res

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    bak = MANIFEST.with_name(MANIFEST.name + '.bak-%s' % stamp)
    shutil.copy2(MANIFEST, bak)
    tmp = MANIFEST.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        for x in new_items:
            f.write(json.dumps(x, ensure_ascii=False) + '\n')
    os.replace(tmp, MANIFEST)
    res.update({'applied': True, 'backup': str(bak)})

    # 刷新统计与人读清单（失败不影响主结果）
    try:
        stats = json.loads(STATS.read_text(encoding='utf-8')) if STATS.exists() else {}
        stats['todo'] = len(new_items)
        stats['sd_pending'] = n_sd
        by_j = {}
        for x in new_items:
            by_j[x['journal']] = by_j.get(x['journal'], 0) + 1
        stats['by_journal'] = by_j
        stats['reordered_at'] = datetime.now().isoformat(timespec='seconds')
        STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding='utf-8')
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'history_manifest', str(BASE / 'scripts' / 'history_manifest.py'))
        hm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hm)
        hm.write_html(new_items, stats)
        res['html'] = 'ok'
    except Exception as e:  # noqa: BLE001
        res['html'] = '%s: %s' % (type(e).__name__, str(e)[:120])
    return res


# ── CLI ────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description='ScienceDirect 抓取策略开关')
    ap.add_argument('--status', action='store_true', help='查看当前状态')
    ap.add_argument('--pause', action='store_true', help='暂停全部 SD 抓取')
    ap.add_argument('--resume', action='store_true', help='恢复 SD 抓取')
    ap.add_argument('--reason', default='', help='暂停原因（写进开关文件）')
    ap.add_argument('--reorder', action='store_true', help='重排清单：SD 沉到最后')
    ap.add_argument('--apply', action='store_true', help='配合 --reorder 真正写盘')
    args = ap.parse_args()

    if args.pause or args.resume:
        p = set_paused(bool(args.pause), args.reason)
        print(('已暂停' if p['paused'] else '已恢复') + '｜' + p['reason'])
        return 0

    if args.reorder:
        r = reorder_manifest(apply=args.apply)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0 if r.get('ok') else 1

    st = pause_state()
    n_sd = n_all = 0
    if MANIFEST.exists():
        with open(MANIFEST, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                n_all += 1
                if is_sd(d.get('doi', ''), d.get('url', ''), d.get('journal', '')):
                    n_sd += 1
    print('SD 抓取：%s' % ('已暂停 ⏸' if st['paused'] else '运行中 ▶'))
    print('  来源  : %s' % st.get('src'))
    print('  自    : %s' % (st.get('since') or '-'))
    print('  原因  : %s' % (st.get('reason') or '-'))
    print('  清单  : 共 %d 条，其中 SD %d 条（%.1f%%）'
          % (n_all, n_sd, (100.0 * n_sd / n_all) if n_all else 0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
