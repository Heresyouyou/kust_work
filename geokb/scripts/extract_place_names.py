# -*- coding: utf-8 -*-
"""从同研究区的中英文材料里提取地名候选，供人工/AI 确认后写入术语表。

★ 原料从哪来
  英文侧：papers 的 PDF 全文（`works.pdf_path`，都在 NAS）—— 这是唯一可信的英文原文；
          `works.title` 存的是中文译名，不能当英文源（「南洋田」误译就是从那儿来的）。
  中文侧：`modules/papers/data/guides/*/` 的中文导读 JSON（标题、findings、qa 里都带地名）。

★ 两段式
  ① 规则抽取（零 token）：按「大写词 + deposit/ore field/massif/belt…」抽英文候选，
     按「汉字 + 矿床/矿区/矿田/岩体/县/市」抽中文候选，统计频次与出处。
  ② AI 配对（可选，低谷窗口跑）：把同一篇论文的英文候选与中文候选交给 DeepSeek，
     让它输出 {en: zh} 对照，标明置信度。它只做**配对**，不自由翻译 —— 避免它编地名。

★ 产物
  state/place_name_candidates.json   待审清单（不自动进术语表）
  确认后的条目由人写进 content/glossary/terms-place-names.md

用法：
  python scripts/extract_place_names.py --dry            # 只规则抽取
  python scripts/extract_place_names.py --dry --limit 20
  python scripts/extract_place_names.py --llm --limit 12 # 调模型配对（花 token）
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

GEOKB = Path('D:/ZSK/geokb')
PAPERS = Path('D:/PostNews/Translate/modules/papers')
PAPERS_DB = PAPERS / 'data' / 'papers.db'
GUIDES = PAPERS / 'data' / 'guides'
OUT = GEOKB / 'state' / 'place_name_candidates.json'

# 英文：地名 + 地貌/构造/矿区中心词。词首大写，允许 1–3 个词、允许连字符。
EN_RE = re.compile(
    r'\b([A-Z][a-zA-Z\u00c0-\u024f\-]{2,}'
    r'(?:\s+[A-Z][a-zA-Z\u00c0-\u024f\-]{2,}){0,2})'
    r'\s+(deposits?|ore\s?fields?|ore\s?districts?|mining\s+districts?|massifs?|'
    r'batholiths?|plutons?|granites?|formations?|groups?|belts?|zones?|'
    r'areas?|regions?|counties|county|cities|city|mines?)\b')

# 中文：只用于**中文原文**（language='zh'）。中文没有词边界，正则必然带噪音，
# 但本地中文原文只有 5 篇，量小到可以人筛。绝不用于 works.title / guides（译文）。
ZH_RE = re.compile(
    r'([\u4e00-\u9fff]{2,5})'
    r'(?=矿床|矿区|矿田|矿集区|岩体|岩基|花岗岩|断裂带|成矿带|背斜|盆地)')

# 明显不是地名的词首（句首大写、泛指、时代、地层）
STOP_EN = {
    'The', 'This', 'These', 'Those', 'In', 'At', 'From', 'For', 'And', 'But',
    'Our', 'Their', 'Its', 'A', 'An', 'As', 'By', 'With', 'On', 'Of', 'To',
    'Two', 'Three', 'Four', 'Five', 'Both', 'Many', 'Most', 'Some', 'All',
    'However', 'Although', 'Because', 'During', 'After', 'Before', 'Since',
    'Figure', 'Table', 'Fig', 'Sample', 'Samples', 'Stage', 'Type', 'Zone',
    'South', 'North', 'East', 'West', 'Central', 'Upper', 'Lower', 'Middle',
    'Early', 'Late', 'Large', 'Small', 'Main', 'Major', 'Minor',
}
# 时代 / 构造期：这些开头的一定不是地名
ERA_PREFIX = (
    'Neoproterozoic', 'Paleoproterozoic', 'Mesoproterozoic', 'Archean', 'Proterozoic',
    'Paleozoic', 'Mesozoic', 'Cenozoic', 'Jurassic', 'Cretaceous', 'Triassic',
    'Permian', 'Carboniferous', 'Devonian', 'Silurian', 'Ordovician', 'Cambrian',
    'Eocene', 'Oligocene', 'Miocene', 'Pliocene', 'Pleistocene', 'Holocene',
    'Indosinian', 'Yanshanian', 'Caledonian', 'Hercynian', 'Himalayan',
    'Precambrian', 'Quaternary', 'Tertiary',
)
STOP_ZH = {'该', '本', '此', '其', '这', '那', '各', '全', '全区', '整个'}
# 矿种组合（W-Mo / Cu-Mo-W）不是地名
ORE_ABBR = re.compile(r'^[A-Z][a-z]?(?:-[A-Z][a-z]?){1,4}$')


def _is_place(name, head):
    if not name or name.split()[0] in STOP_EN:
        return False
    first = name.split()[0]
    if first.endswith('ian') or first.endswith('ozoic') or first.endswith('aic'):
        return False
    if first in ERA_PREFIX:
        return False
    # 「W-Mo deposits」：整个 name 就是矿种缩写组合
    if ORE_ABBR.match(name.replace(' ', '')):
        return False
    # 全是 1-2 字母的缩写（W, Mo, Cu-Mo）
    if all(len(w) <= 2 for w in name.replace('-', ' ').split()):
        return False
    if len(name) < 4:
        return False
    return True



def sha_rows(limit=0):
    c = sqlite3.connect('file:%s?mode=ro' % PAPERS_DB, uri=True)
    c.row_factory = sqlite3.Row
    q = ("SELECT work_id,title,pdf_path,primary_sha256,language FROM works "
         "WHERE pdf_path IS NOT NULL AND pdf_path<>'' ORDER BY created_at DESC")
    rows = c.execute(q).fetchall()
    c.close()
    return rows[:limit] if limit else rows


def pdf_text(path, max_chars=200000):
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            return ''
    try:
        d = pymupdf.open(str(path))
        parts, n = [], 0
        for i in range(d.page_count):
            t = d[i].get_text('text') or ''
            parts.append(t)
            n += len(t)
            if n >= max_chars:
                break
        d.close()
        return '\n'.join(parts)
    except Exception:
        return ''


def guide_text(work_id):
    """导读 JSON 里的中文正文（标题/findings/qa/methods/chain）。"""
    d = GUIDES / work_id
    if not d.is_dir():
        return ''
    buf = []
    for p in sorted(d.glob('*.json')):
        if p.name.endswith('.qc.json'):
            continue
        try:
            j = json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            continue
        buf.append(_walk(j))
    return '\n'.join(buf)[:120000]


def _walk(o):
    if isinstance(o, str):
        return o
    if isinstance(o, dict):
        return '\n'.join(_walk(v) for v in o.values())
    if isinstance(o, list):
        return '\n'.join(_walk(v) for v in o)
    return ''


def harvest(limit=0, verbose=True):
    rows = sha_rows(limit)
    en_cnt, zh_cnt = Counter(), Counter()
    en_src, zh_src = defaultdict(set), defaultdict(set)
    t0 = time.time()
    for i, r in enumerate(rows, 1):
        wid = r['work_id']
        txt = pdf_text(r['pdf_path'])
        lang = (r['language'] or 'en').lower()
        if lang.startswith('zh'):
            # ★ 中文侧只认中文**原文**。works.title 与 guides 都是机器译文 ——
            #   用户 2026-10-29 明确：译文不可信，不得作为地名对照来源
            #   （「南洋田」这个错译就是从译文来的）。中文原文只有 5 篇，
            #   所以这一侧靠人筛，脚本只把出现过的候选列出来。
            for m in ZH_RE.finditer(txt):
                name = m.group(1).strip()
                if name in STOP_ZH or len(name) < 2:
                    continue
                zh_cnt[name] += 1
                zh_src[name].add(wid)
        else:
            for m in EN_RE.finditer(txt):
                name = re.sub(r'\s+', ' ', m.group(1)).strip()
                head = m.group(2).lower()
                if not _is_place(name, head):
                    continue
                key = '%s %s' % (name, head)
                en_cnt[key] += 1
                en_src[key].add(wid)
        if verbose and (i % 10 == 0 or i == len(rows)):
            print('  已扫 %d/%d（英文候选 %d、中文候选 %d）'
                  % (i, len(rows), len(en_cnt), len(zh_cnt)), end='\r')
    if verbose:
        print('\n扫描完成 %.1fs' % (time.time() - t0))
    return {
        'at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'works': len(rows),
        'en': [{'key': k, 'n': n, 'works': sorted(en_src[k])[:6]}
               for k, n in en_cnt.most_common()],
        'zh': [{'key': k, 'n': n, 'works': sorted(zh_src[k])[:6]}
               for k, n in zh_cnt.most_common()],
    }


def _key():
    for p in (GEOKB / '.deepseek_key.papers', Path('D:/PostNews/Translate/.deepseek_key.papers'),
              GEOKB / '.deepseek_key', Path('D:/PostNews/Translate/.deepseek_key')):
        if p.exists():
            return p.read_text(encoding='utf-8').strip()
    return ''


def ai_pair(cand, limit_docs=12, verbose=True):
    """让模型给英文候选**分类**（是不是地名、属于哪类），**不产出中文名**。

    ★ 为什么不再做中英配对（2026-10-29 用户明确）
      译文不可信 —— `Nanyangtian` 被自动翻译写成「南洋田」就是教训。
      本地又没有同研究区的中英双原文（中文原文只有 5 篇，属腾冲地块），
      所以"AI 对照提取"这一步现在只能做一件事：把英文侧候选分干净，
      中文名留给中文原文或人工。宁缺毋滥。
    """
    import urllib.request
    key = _key()
    if not key:
        return {'ok': False, 'error': '找不到 DeepSeek key'}

    items = [x['key'] for x in cand['en']][:300]
    if not items:
        return {'ok': True, 'places': [], 'errors': []}
    out, errors = [], []
    BATCH = 60
    for i in range(0, len(items), BATCH):
        batch = items[i:i + BATCH]
        prompt = (
            '下面是地质论文里用正则抽出的候选短语。请只判断**它是不是地名**，不要翻译。\n'
            '是地名的 → 给出 type：deposit（矿床/矿区）/ orefield（矿田）/ massif（岩体/地块）/'
            ' belt（成矿带）/ region（地区）/ district（矿集区）/ other。\n'
            '不是地名的（地层单位、地质时代、矿种组合、岩石名、普通短语）→ 不要输出。\n'
            '拿不准的也不要输出。\n'
            '只输出 JSON：{"places":[{"key":"Dulong deposit","type":"deposit"}]}\n\n'
            '候选：\n- ' + '\n- '.join(batch))
        body = json.dumps({'model': 'deepseek-flash',
                           'messages': [{'role': 'user', 'content': prompt}],
                           'max_tokens': 1600,
                           'thinking': {'type': 'disabled'}}).encode('utf-8')
        req = urllib.request.Request('https://api.deepseek.com/chat/completions',
                                     data=body, method='POST')
        req.add_header('Content-Type', 'application/json')
        req.add_header('Authorization', 'Bearer ' + key)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            txt = (data.get('choices') or [{}])[0].get('message', {}).get('content', '')
            m = re.search(r'\{[\s\S]*\}', txt)
            got = json.loads(m.group(0)) if m else {}
            got_list = [p for p in (got.get('places') or []) if p.get('key')]
            out.extend(got_list)
            if verbose:
                print('  批 %d：%d → %d 条' % (i // BATCH + 1, len(batch), len(got_list)),
                      end='\r')
        except Exception as e:
            errors.append('batch%d: %s' % (i // BATCH, str(e)[:120]))
    if verbose:
        print('\n分类完成：%d 条地名候选，失败 %d 批' % (len(out), len(errors)))
    return {'ok': True, 'places': out, 'errors': errors}
    if verbose:
        print('\n模型配对得到 %d 对，失败 %d 篇' % (len(pairs), len(errors)))
    return {'ok': True, 'pairs': pairs, 'errors': errors}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--llm', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()

    cand = harvest(limit=a.limit)
    if a.llm:
        res = ai_pair(cand, limit_docs=a.limit or 12)
        cand['ai'] = res
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cand, ensure_ascii=False, indent=1), encoding='utf-8')

    print('\n英文候选 %d 个，中文候选 %d 个 → %s'
          % (len(cand['en']), len(cand['zh']), OUT))
    print('\n频次最高的 12 个英文候选：')
    for x in cand['en'][:12]:
        print('  %-48s ×%d' % (x['key'][:48], x['n']))
    print('\n频次最高的 12 个中文候选：')
    for x in cand['zh'][:12]:
        print('  %-24s ×%d' % (x['key'][:24], x['n']))
    if cand.get('ai', {}).get('places'):
        print('\nAI 判定的地名（前 12）：')
        for p in cand['ai']['places'][:12]:
            print('  %-14s %s' % (p.get('type') or '?', p['key'][:46]))


if __name__ == '__main__':
    main()
