# -*- coding: utf-8 -*-
"""近 20 年清单总览：把「已入队 + 五桶归档」拼成一张可筛选的表。

数据来源：
  1) state/history_manifest.jsonl 中 batch == 'add20y' 的条目（已并入生产队列）
  2) state/history_manifest_20y_{auto,sd,blocked,quota,unsupported}[_cn].jsonl
     —— 与 1) 按 id 去重，避免同一条重复出现

输出：state/清单20年_总览.html（自包含，无外链）
"""
import json
import os
from datetime import datetime
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
STATE = BASE / 'state'
OUT = STATE / '清单20年_总览.html'

BUCKETS = [
    ('queued', '已入队', '#2e7d4f', '已在爬取队列，逐篇下载中'),
    ('sd', 'SD 暂停', '#b45309', 'ScienceDirect 全线暂停，不进队列'),
    ('blocked', '冻结站', '#b91c1c', '源站人机挑战/熔断中，冷却后复核'),
    ('quota', '配额', '#7c3aed', 'OUP 25 篇/天，自动排队消化'),
    ('unsupported', '无 handler', '#6b7280', 'DOI 失效或解析不到出版社'),
]
BNAME = {k: n for k, n, _, _ in BUCKETS}
BCOLOR = {k: c for k, _, c, _ in BUCKETS}


def rows_from_manifest():
    out = []
    p = STATE / 'history_manifest.jsonl'
    for ln in open(p, encoding='utf-8'):
        s = ln.strip()
        if not s:
            continue
        try:
            d = json.loads(s)
        except Exception:
            continue
        if d.get('batch') != 'add20y':
            continue
        out.append({
            'id': d['id'], 'title': d.get('title') or '', 'journal': d.get('journal') or '',
            'year': d.get('year') or (d.get('date') or '')[:4], 'score': d.get('score') or 0,
            'tier': d.get('tier') or '', 'host': d.get('host') or '',
            'url': d.get('url') or '', 'b': 'queued',
        })
    return out


def rows_from_stage():
    """⚠️ `_cn` 暂存区是「全期刊 + 中文刊」的超集，与无后缀那套 **id 完全相同**，
    必须在这里就地按 id 去重，否则每个桶都会被算两遍（实测 sd 15,751 变 31,502）。"""
    out, seen = [], set()
    for key in ('auto', 'sd', 'blocked', 'quota', 'unsupported'):
        for suf in ('', '_cn'):
            p = STATE / ('history_manifest_20y_%s%s.jsonl' % (key, suf))
            if not p.exists():
                continue
            for ln in open(p, encoding='utf-8'):
                s = ln.strip()
                if not s:
                    continue
                try:
                    d = json.loads(s)
                except Exception:
                    continue
                rid = d.get('id')
                if not rid or rid in seen:
                    continue
                seen.add(rid)
                out.append({
                    'id': rid, 'title': d.get('title') or '',
                    'journal': d.get('journal') or '',
                    'year': d.get('year') or (d.get('date') or '')[:4],
                    'score': d.get('score') or 0, 'tier': d.get('tier') or '',
                    'host': d.get('host') or '', 'url': d.get('url') or '',
                    'b': 'queued' if key == 'auto' else key,
                })
    return out


def main():
    man = rows_from_manifest()
    seen = {r['id'] for r in man}
    extra = [r for r in rows_from_stage() if r['id'] not in seen]
    rows = man + extra

    # 同一 DOI 在 sd/blocked 两个桶里只会出现一次（bucket 唯一），无需再按 DOI 去重
    from collections import Counter
    cnt = Counter(r['b'] for r in rows)
    jset = sorted({r['journal'] for r in rows})
    ymin = min((int(r['year']) for r in rows if (r['year'] or '').isdigit()), default=2006)
    ymax = max((int(r['year']) for r in rows if (r['year'] or '').isdigit()), default=2026)

    payload = {'rows': rows, 'buckets': [k for k, *_ in BUCKETS]}

    cards = ''.join(
        '<div class="card" data-b="%s"><div class="n" style="color:%s">%s</div>'
        '<div class="l">%s</div><div class="d">%s</div></div>'
        % (k, BCOLOR[k], format(cnt.get(k, 0), ','), n, desc)
        for k, n, _, desc in BUCKETS)

    opts = ''.join('<option>%s</option>' % j.replace('"', '&quot;') for j in jset)

    html = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>目标期刊近 20 年 · 清单总览（{total} 条）</title>
<style>
:root{{--bg:#fbfbf9;--fg:#1f2328;--mut:#6b7280;--line:#e5e7eb;--acc:#2e7d4f;--card:#fff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#15171a;--fg:#e6e8eb;--mut:#9aa3ad;--line:#2b2f34;--acc:#4a9e6a;--card:#1c1f23}}}}
*{{box-sizing:border-box}}body{{margin:0;padding:26px 30px 60px;background:var(--bg);color:var(--fg);
font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
h1{{font-size:21px;margin:0 0 4px;letter-spacing:-.01em}}
.sub{{color:var(--mut);font-size:13px;margin-bottom:18px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:12px;margin-bottom:20px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;cursor:pointer;
transition:border-color .15s,box-shadow .15s}}
.card:hover{{border-color:var(--acc)}}
.card.on{{border-color:var(--acc);box-shadow:0 0 0 1px var(--acc) inset}}
.card .n{{font-size:24px;font-weight:700;line-height:1.15;font-variant-numeric:tabular-nums}}
.card .l{{font-size:13px;font-weight:600;margin-top:2px}}
.card .d{{font-size:11.5px;color:var(--mut);margin-top:4px;line-height:1.45}}
.bar{{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:12px}}
input,select{{padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);
color:var(--fg);font-size:13px}}
input{{min-width:300px}}
#cnt{{color:var(--acc);font-weight:600;font-size:13px;margin-left:auto}}
table{{border-collapse:collapse;width:100%;background:var(--card);border-radius:10px;overflow:hidden;
table-layout:fixed}}
th,td{{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{position:sticky;top:0;background:var(--card);font-size:12px;color:var(--mut);font-weight:600;z-index:2}}
th:nth-child(1){{width:auto}}th:nth-child(2){{width:210px}}th:nth-child(3){{width:52px}}
th:nth-child(4){{width:50px}}th:nth-child(5){{width:74px}}th:nth-child(6){{width:132px}}
td.j{{font-size:12px;color:var(--mut)}}td.y{{font-size:12px;color:var(--mut);text-align:center}}
td.sc{{font-weight:700;color:var(--acc);text-align:center}}
td.t a{{color:var(--fg);text-decoration:none}}td.t a:hover{{color:var(--acc);text-decoration:underline}}
td.b{{font-size:12px;font-weight:600}}
td.h{{font-size:11.5px;color:var(--mut);word-break:break-all}}
.pg{{display:flex;gap:8px;justify-content:center;align-items:center;margin-top:16px;font-size:13px;color:var(--mut)}}
.pg button{{padding:6px 14px;border:1px solid var(--line);border-radius:8px;background:var(--card);
color:var(--fg);cursor:pointer;font-size:13px}}
.pg button:disabled{{opacity:.4;cursor:default}}
</style></head><body>
<h1>目标期刊近 20 年 · 清单总览</h1>
<div class="sub">生成于 {gen} ｜ 共 <b>{total}</b> 条 ｜ 队列在跑 <b>{queued}</b> 条 ｜
其余按「能不能真下到」归档，一条不丢</div>
<div class="cards" id="cards">{cards}</div>
<div class="bar">
  <input id="q" placeholder="搜标题 / 期刊 / DOI（空格分词，全部命中）">
  <select id="fy"><option value="">全部年份</option>{yopts}</select>
  <select id="fj"><option value="">全部期刊</option>{jopts}</select>
  <select id="ft"><option value="">全部层级</option><option>core</option><option>ext</option><option>geo</option></select>
  <span id="cnt"></span>
</div>
<table><thead><tr><th>标题</th><th>期刊</th><th>年</th><th>分</th><th>层级</th><th>状态 / 站点</th></tr></thead>
<tbody id="tb"></tbody></table>
<div class="pg"><button id="pv">上一页</button><span id="pi"></span><button id="nx">下一页</button></div>
<script>
const DATA = {data};
const BNAME = {bname}, BCOLOR = {bcolor};
let cur = '', page = 0, PER = 200, view = [];
const $ = s => document.querySelector(s);
function buildYears(){{
  const s = new Set();
  DATA.rows.forEach(r => {{ if (r.year) s.add(r.year); }});
  return [...s].sort().reverse();
}}
const ysel = $('#fy');
buildYears().forEach(y => {{ const o = document.createElement('option'); o.textContent = y; ysel.appendChild(o); }});
function esc(s){{ return (s||'').replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}})[c]); }}
function apply(){{
  const terms = $('#q').value.toLowerCase().trim().split(/\\s+/).filter(Boolean);
  const fy = $('#fy').value, fj = $('#fj').value, ft = $('#ft').value;
  view = DATA.rows.filter(r => {{
    if (cur && r.b !== cur) return false;
    if (fy && r.year !== fy) return false;
    if (fj && r.journal !== fj) return false;
    if (ft && r.tier !== ft) return false;
    if (!terms.length) return true;
    const hay = (r.title + ' ' + r.journal + ' ' + (r.url||'')).toLowerCase();
    return terms.every(t => hay.includes(t));
  }});
  page = 0; render();
}}
function render(){{
  const tot = view.length, pages = Math.max(1, Math.ceil(tot / PER));
  page = Math.min(page, pages - 1);
  const slice = view.slice(page * PER, page * PER + PER);
  $('#cnt').textContent = tot.toLocaleString() + ' 条命中';
  $('#pi').textContent = (page + 1) + ' / ' + pages;
  $('#pv').disabled = page === 0; $('#nx').disabled = page >= pages - 1;
  const rows = slice.map(r => {{
    const u = r.url || '#';
    const t = '<a href="' + esc(u) + '" target="_blank" rel="noopener">' + esc(r.title) + '</a>';
    return '<tr><td class="t">' + t + '</td><td class="j">' + esc(r.journal) + '</td>' +
      '<td class="y">' + esc(r.year) + '</td><td class="sc">' + r.score + '</td>' +
      '<td class="y">' + esc(r.tier) + '</td>' +
      '<td class="b" style="color:' + BCOLOR[r.b] + '">' + BNAME[r.b] +
      '<div class="h">' + esc(r.host) + '</div></td></tr>';
  }}).join('');
  $('#tb').innerHTML = rows;
}}
document.querySelectorAll('.card').forEach(c => c.addEventListener('click', () => {{
  const b = c.dataset.b;
  cur = (cur === b) ? '' : b;
  document.querySelectorAll('.card').forEach(x => x.classList.toggle('on', x.dataset.b === cur));
  apply();
}}));
['#q','#fy','#fj','#ft'].forEach(s => $(s).addEventListener('input', apply));
$('#pv').onclick = () => {{ if (page > 0) {{ page--; render(); window.scrollTo(0,0); }} }};
$('#nx').onclick = () => {{ page++; render(); window.scrollTo(0,0); }};
$('#q').addEventListener('keydown', e => {{ if (e.key === 'Enter') apply(); }});
apply();
</script></body></html>""".format(
        gen=datetime.now().strftime('%Y-%m-%d %H:%M'),
        total=format(len(rows), ','),
        queued=format(cnt.get('queued', 0), ','),
        cards=cards,
        yopts='',
        jopts=opts,
        data=json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
        bname=json.dumps(BNAME, ensure_ascii=False),
        bcolor=json.dumps(BCOLOR, ensure_ascii=False),
    )

    tmp = OUT.with_suffix('.tmp')
    tmp.write_text(html, encoding='utf-8')
    os.replace(tmp, OUT)
    print('已写 %s  %.1f MB' % (OUT, OUT.stat().st_size / 1048576))
    print('构成：%s ｜ 合计 %d' % (dict(cnt), len(rows)))


if __name__ == '__main__':
    main()
