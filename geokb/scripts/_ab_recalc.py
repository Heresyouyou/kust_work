# -*- coding: utf-8 -*-
"""指标重算（零 API）：读已存的新版输出 + 旧的 llm_raw，重算修正后的指标。

修正两处指标 bug（2026-10-20）：
  1. ov_key / ov_tag 用 `_words()` 只抓 [a-z] 词 → **中文篇目永远算出 0.00**，
     被误读成「质量崩塌」。改为英文用词集、中文用字符 bigram 合并。
  2. 数值比对用原始字符串 → `0.60` 与 `0.6`、`1,200` 与 `1200` 互不相等，
     造成假漏。改为 float 归一化后再比。
"""
import argparse
import glob
import json
import re
import statistics as st
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
RAW = BASE / 'state' / 'llm_raw'
TXT = BASE / 'state' / 'text'
NEW = BASE / 'logs' / 'ab_new'

RE_NUM = re.compile(r'\d+(?:\.\d+)?')
ENG_FIELDS = ['title_en', 'abstract_en', 'key_content_en', 'core_methods_en',
              'study_area_en', 'application_scenarios_en', 'deposit_types_en',
              'commodities', 'tags_en']
ZH_FIELDS = ['title_zh', 'key_content', 'core_methods', 'study_area',
             'application_scenarios', 'deposit_types', 'commodities', 'tags']


def _norm(s):
    return re.sub(r'\s+', ' ', (s or '')).strip().lower()


def _words(s):
    return set(re.findall(r'[a-z][a-z\-]{2,}', _norm(s)))


def _bigrams(s):
    t = re.sub(r'[^\u4e00-\u9fff]', '', s or '')
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else set()


def _units(s):
    """中英混排通用的比对单元：英文词集 ∪ 中文 bigram。"""
    return _words(s) | _bigrams(s)


def _overlap(a, b):
    if not a or not b:
        return None
    return len(a & b) / max(len(a), 1)


def _nums(s):
    out = set()
    for m in RE_NUM.findall(s or ''):
        try:
            out.add('%g' % float(m))
        except ValueError:
            pass
    return out


def _flat(obj, fields):
    parts = []
    for f in fields:
        v = (obj or {}).get(f)
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
        elif v:
            parts.append(str(v))
    return ' '.join(parts)


def _nonempty(obj, fields):
    if not obj:
        return 0.0
    hit = 0
    for f in fields:
        v = obj.get(f)
        if isinstance(v, list):
            hit += 1 if v else 0
        else:
            hit += 1 if (v and str(v).strip() and str(v).lower() != 'null') else 0
    return hit / len(fields)


def recompute(tag):
    rows = []
    nd = NEW if tag in ('', 'ab_new') else (BASE / 'logs' / ('ab_new_' + tag))
    if not nd.exists():
        nd = NEW
    for p in glob.glob(str(nd / '*.json')):
        sha = Path(p).stem
        nf = nd / (sha + '.json')
        bf = RAW / (sha + '.json')
        tf = TXT / (sha + '.txt')
        if not (bf.exists() and tf.exists()):
            continue
        try:
            new = json.loads(nf.read_text(encoding='utf-8'))
            base = json.loads(bf.read_text(encoding='utf-8'))
        except Exception:  # noqa: BLE001
            continue
        raw = tf.read_text(encoding='utf-8', errors='replace')
        lang = 'zh' if len(re.findall(r'[\u4e00-\u9fff]', raw)) > len(raw) * 0.15 else 'en'
        f = ENG_FIELDS if lang == 'en' else ZH_FIELDS
        raw_nums = _nums(raw)
        gold = _nums(_flat(base, f)) & raw_nums
        got = _nums(_flat(new, f)) & raw_nums
        rows.append({
            'sha': sha, 'lang': lang,
            'ne_base': _nonempty(base, f), 'ne_new': _nonempty(new, f),
            'num_gold': len(gold), 'num_got': len(got),
            'num_recall': (len(got & gold) / len(gold)) if gold else None,
            'ov_key': _overlap(_units(_flat(base, ['key_content_en', 'key_content'])),
                               _units(_flat(new, ['key_content_en', 'key_content']))),
            'ov_tag': _overlap(_units(_flat(base, ['tags_en', 'tags'])),
                               _units(_flat(new, ['tags_en', 'tags']))),
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', default='block4500')
    args = ap.parse_args()
    rows = recompute(args.tag)
    if not rows:
        print('无数据')
        return

    def avg(k):
        v = [r[k] for r in rows if r.get(k) is not None]
        return sum(v) / len(v) if v else 0.0

    rec = sorted(r['num_recall'] for r in rows if r['num_recall'] is not None)
    print('样本 %d 篇（en %d / zh %d）' % (
        len(rows), sum(1 for r in rows if r['lang'] == 'en'),
        sum(1 for r in rows if r['lang'] == 'zh')))
    print('字段非空率   %.3f → %.3f' % (avg('ne_base'), avg('ne_new')))
    print('数值召回     均值 %.3f 中位 %.3f 最低 %.2f' % (
        avg('num_recall'), st.median(rec) if rec else 0, rec[0] if rec else 0))
    print('key 重叠     均值 %.3f' % avg('ov_key'))
    print('tags 重叠    均值 %.3f' % avg('ov_tag'))
    print('\n%-9s %-3s %6s %6s %6s %6s' % ('sha', 'lang', 'ne新', '召回', 'keyov', 'gold/got'))
    for r in sorted(rows, key=lambda x: (x['num_recall'] if x['num_recall'] is not None else 9)):
        print('%-9s %-3s %6.2f %6s %6.2f %d/%d' % (
            r['sha'][:8], r['lang'], r['ne_new'],
            '  n/a' if r['num_recall'] is None else '%.2f' % r['num_recall'],
            r['ov_key'] or 0, r['num_gold'], r['num_got']))
    print('阈值：非空≥基准95%%、数值召回≥0.90、key重叠≥0.60')

    out = BASE / 'logs' / ('recalc_%s.json' % args.tag)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
    print('明细 →', out)


if __name__ == '__main__':
    main()
