# -*- coding: utf-8 -*-
"""20 年清单生成后的核验（只读，不改任何文件）。

查五件事：
1. 五桶条数与守恒（原始 = 候选 + 各环节剔除 + 重复，且五桶之和 = 新增）
2. auto 桶是否真的都能派发（host 有 handler、不在冻结表、不是 SD）
3. auto 桶是否真的都是新增（抽查是否已在生产清单 / 台账里）
4. 10 年暂存区（5736 条）是否已被 20 年清单完全覆盖（决定它能否退休）
5. 各桶按源站 / 年份分布
"""
import json
import sys
from collections import Counter
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
STATE = BASE / 'state'
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, 'D:/PostNews')

import history_manifest as hm                      # noqa: E402
import source_freeze as sf                         # noqa: E402
import sd_policy                                   # noqa: E402

BUCKETS = ['auto', 'sd', 'blocked', 'quota', 'unsupported']


def jl(p):
    if not Path(p).exists():
        return []
    out = []
    for line in Path(p).read_text(encoding='utf-8', errors='ignore').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def keys(rows):
    t = {hm.norm_title(r.get('title') or '') for r in rows if r.get('title')}
    d = {hm.norm_doi(r.get('doi') or '') for r in rows if r.get('doi')}
    t.discard('')
    d.discard('')
    return t, d


data = {b: jl(STATE / ('history_manifest_20y_%s.jsonl' % b)) for b in BUCKETS}
stats_p = STATE / 'history_manifest_20y_stats.json'
st = json.loads(stats_p.read_text(encoding='utf-8')) if stats_p.exists() else {}

print('== 1. 五桶与守恒 ==')
for b in BUCKETS:
    print('  %-12s %6d' % (b, len(data[b])))
tot = sum(len(v) for v in data.values())
fun = st.get('funnel', {})
print('  合计         %6d' % tot)
print('  漏斗：%s' % fun)
if fun:
    expect = fun.get('raw', 0) - fun.get('out_window', 0) - fun.get('junk_title', 0) \
        - fun.get('nonarticle', 0) - fun.get('irrelevant', 0) - fun.get('below_tier', 0) \
        - fun.get('dup_kb', 0) - fun.get('dup_meta', 0) - fun.get('dup_doi', 0) \
        - fun.get('dup_url', 0)
    print('  守恒校验：原始推算 %d vs 实际 %d  →  %s'
          % (expect, tot, 'OK' if expect == tot else '不一致（差 %d）' % (tot - expect)))

print('\n== 2. auto 桶可派发性 ==')
frozen = sf.load()
import importlib.util
spec = importlib.util.spec_from_file_location(
    '_jpd', 'D:/PostNews/.workbuddy/journal_pdf_downloader.py')
dl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dl)

bad_frozen, bad_sd, bad_handler, bad_host = [], [], [], []
for r in data['auto']:
    host = r.get('host') or sf.host_of(r.get('url', ''), r.get('doi', ''))
    ok, why = sf.is_frozen(host, frozen)
    if ok:
        bad_frozen.append((host, why))
    if r.get('sd') or sd_policy.is_sd(r.get('doi', ''), r.get('url', ''), r.get('journal', '')):
        bad_sd.append(r.get('title', '')[:40])
    src = dl.source_of(dl.normalize_url(r.get('url') or ''))
    if src == 'unknown' or src not in dl.HANDLERS:
        bad_handler.append((src, r.get('title', '')[:40]))
print('  冻结站残留 %d ｜ SD 残留 %d ｜ 无 handler %d'
      % (len(bad_frozen), len(bad_sd), len(bad_handler)))
for x in (bad_frozen[:3] + bad_handler[:3]):
    print('    !', x)

print('\n== 3. auto 桶是否真新增 ==')
man = jl(STATE / 'history_manifest.jsonl')
mt, md = keys(man)
dup_t = sum(1 for r in data['auto'] if hm.norm_title(r.get('title') or '') in mt)
dup_d = sum(1 for r in data['auto'] if hm.norm_doi(r.get('doi') or '') in md)
print('  与生产清单同标题 %d ｜ 同 DOI %d' % (dup_t, dup_d))

print('\n== 4. 10 年暂存区覆盖度 ==')
add10 = jl(STATE / 'history_manifest_add10y.jsonl')
allrows = [r for b in BUCKETS for r in data[b]]
at, ad = keys(allrows)
cov_t = sum(1 for r in add10 if hm.norm_title(r.get('title') or '') in at)
cov_d = sum(1 for r in add10 if hm.norm_doi(r.get('doi') or '') in ad)
cov = sum(1 for r in add10
          if hm.norm_title(r.get('title') or '') in at
          or hm.norm_doi(r.get('doi') or '') in ad)
print('  10 年暂存区 %d 条 ｜ 已被覆盖 %d（%.1f%%）｜ 未覆盖 %d'
      % (len(add10), cov, 100.0 * cov / max(len(add10), 1), len(add10) - cov))
if add10 and cov < len(add10):
    unc = [r for r in add10 if hm.norm_title(r.get('title') or '') not in at
           and hm.norm_doi(r.get('doi') or '') not in ad]
    print('  未覆盖样本：')
    for r in unc[:5]:
        print('    - [%s] %s' % ((r.get('journal') or '?')[:28], (r.get('title') or '')[:56]))

print('\n== 5. 分布 ==')
for b in BUCKETS:
    if not data[b]:
        continue
    print('  [%s] 按源站：%s' % (b, dict(Counter(
        r.get('host', '?') for r in data[b]).most_common(6))))
    yrs = Counter((r.get('year') or '?') for r in data[b])
    print('        年份 %s ~ %s ｜ 顶层期刊 %s'
          % (min(yrs), max(yrs), dict(Counter(
              r.get('journal', '?') for r in data[b]).most_common(4))))
