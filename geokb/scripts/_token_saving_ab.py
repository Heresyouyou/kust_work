# -*- coding: utf-8 -*-
"""入库「省 token」方案的 A/B 对拍（2026-10-20）。

基准侧零成本：直接读 state/llm_raw/<sha>.json 里已存在的旧方案输出，
只对**新版**发起调用，比较：

  1. 字段非空率        —— 少了字段就是掉质量
  2. 关键数值召回      —— 以「原文里确实存在的数值」为金标，看输出覆盖多少
  3. key_content 重叠  —— 英文按词集、中文按字符 bigram
  4. 标签/矿床类型一致 —— 与基准的重合个数
  5. token 用量        —— 输入/输出

用法：
  venv/Scripts/python.exe scripts/_token_saving_ab.py --n 16
  venv/Scripts/python.exe scripts/_token_saving_ab.py --n 16 --budget 3200 --mode select
  venv/Scripts/python.exe scripts/_token_saving_ab.py --n 16 --mode headtail   # 对照组
"""
import argparse
import glob
import json
import os
import random
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / 'scripts'))

import text_prep as tp  # noqa: E402
from deepseek_client import ds_chat, extract_json, last_usage, load_api_key  # noqa: E402

RAW = BASE / 'state' / 'llm_raw'
TXT = BASE / 'state' / 'text'
NEWDIR = BASE / 'logs' / 'ab_new'

# 与生产同源：直接借 pdf_corpus_ingest 的模板，保证只变「输入文本」这一个变量
from pdf_corpus_ingest import (EN_PROMPT_TMPL, USER_PROMPT_TMPL,  # noqa: E402
                               DS_MAX_TOKENS, detect_lang)

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
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else {t}


def _overlap(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a), 1)


def _nonempty_rate(obj, fields):
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


def _nums(s):
    return set(RE_NUM.findall(s or ''))


def _flat(obj, fields):
    parts = []
    for f in fields:
        v = (obj or {}).get(f)
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
        elif v:
            parts.append(str(v))
    return ' '.join(parts)


def pick_samples(n, seed=13):
    """固定样本池：首次按 seed 抽样并存盘，之后一律复用同一批。

    ⚠ 2026-10-20 修正：原实现 `random.sample(files, min(n, len))` 会随 n
    变化而给出完全不同的样本，导致 ab_3200 / ab_4500 / ab_6000 三组数据
    根本不可比（实测同一配置 4500 的召回 0.682 反而低于 3200 的 0.753，
    纯属样本差异）。现在池子一旦生成就锁定，只有文件被清理才重建。
    """
    pool = BASE / 'logs' / 'ab_pool.json'
    files = [p for p in glob.glob(str(RAW / '*.json'))
             if (TXT / (Path(p).stem + '.txt')).exists()]
    by_sha = {Path(p).stem: p for p in files}
    if pool.exists():
        try:
            keep = [s for s in json.loads(pool.read_text(encoding='utf-8'))
                    if s in by_sha]
            if len(keep) >= n:
                return [by_sha[s] for s in keep[:n]]
        except Exception:  # noqa: BLE001
            pass
    random.seed(seed)
    picked = random.sample(files, min(n, len(files)))
    pool.write_text(json.dumps([Path(p).stem for p in picked],
                               ensure_ascii=False), encoding='utf-8')
    return picked


EXTRA_ZH = (
    '\n\n注意：key_content 中必须写出原文给出的关键数值，保留原数值与单位'
    '（如 385±40℃、251±7℃、-7.55、1.58‰、2.07、120 Ma、3.5 g/t、12%），'
    '不要用「中高温」「较高」「一定范围」这类概括词替代具体数字。')
EXTRA_EN = (
    '\n\nNOTE: key_content_en must state the key numeric values given in the '
    'text, keeping the original numbers and units (e.g. 385±40 °C, 251±7 °C, '
    '-7.55, 2.07 permil, 120 Ma, 3.5 g/t, 12%). Do not replace specific '
    'numbers with vague wording such as "moderately high" or "a range of".')


def run_one(sha, mode, budget, api_key, extra=''):
    raw = (TXT / (sha + '.txt')).read_text(encoding='utf-8', errors='replace')
    lang = detect_lang(raw)
    tmpl = EN_PROMPT_TMPL if lang == 'en' else USER_PROMPT_TMPL
    body = tp.prepare2(raw, budget, mode)
    prompt = tmpl.format(text=body)
    if extra:
        prompt += EXTRA_EN if lang == 'en' else EXTRA_ZH
    content, err = ds_chat(
        [{'role': 'system', 'content': tp.SYSTEM_PROMPT},
         {'role': 'user', 'content': prompt}],
        api_key, max_tokens=DS_MAX_TOKENS, thinking=False)
    u = last_usage() or {}
    obj = extract_json(content) if not err else None
    return {'lang': lang, 'obj': obj, 'err': err,
            'in': u.get('prompt_tokens'), 'out': u.get('completion_tokens'),
            'hit': u.get('prompt_cache_hit_tokens'),
            'body_tok': tp.est_tokens(body),
            'full_tok': tp.est_tokens(tp.clean(raw))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=16)
    ap.add_argument('--budget', type=int, default=4500)
    ap.add_argument('--mode', default='block',
                    choices=['block', 'select', 'headtail'])
    ap.add_argument('--out', default='')
    ap.add_argument('--extra', action='store_true',
                    help='追加「必须保留关键数值」的输出约束（只改尾部，不影响前缀缓存）')
    args = ap.parse_args()

    tag = '%s_%d%s' % (args.mode, args.budget, '_num' if args.extra else '')
    nd = BASE / 'logs' / ('ab_new_' + tag)
    nd.mkdir(parents=True, exist_ok=True)

    api_key = load_api_key()
    samples = pick_samples(args.n)
    print(f'样本 {len(samples)} 篇 | 模式 {args.mode} | 预算 {args.budget}', flush=True)

    rows = []
    for i, p in enumerate(samples, 1):
        sha = Path(p).stem
        try:
            base = json.loads(Path(p).read_text(encoding='utf-8'))
        except Exception:
            continue
        try:
            new = run_one(sha, args.mode, args.budget, api_key, args.extra)
        except Exception as e:  # noqa: BLE001
            print(f'  [{i}] {sha[:8]} EXC {type(e).__name__}: {e}', flush=True)
            continue
        if not new['obj']:
            print(f'  [{i}] {sha[:8]} JSON失败 err={new["err"]}', flush=True)
            rows.append({'sha': sha, 'lang': new['lang'], 'json_fail': 1,
                         'in': new['in'], 'out': new['out']})
            continue
        en = new['lang'] == 'en'
        f = ENG_FIELDS if en else ZH_FIELDS
        o, b = new['obj'], base
        (nd / (sha + '.json')).write_text(
            json.dumps(o, ensure_ascii=False, indent=1), encoding='utf-8')
        # 数值金标：原文里出现、且基准输出也认可的数值
        raw_txt = (TXT / (sha + '.txt')).read_text(encoding='utf-8', errors='replace')
        raw_nums = _nums(raw_txt)
        gold = _nums(_flat(b, f)) & raw_nums
        got = _nums(_flat(o, f)) & raw_nums
        rec = {
            'sha': sha, 'lang': new['lang'], 'json_fail': 0,
            'in': new['in'], 'out': new['out'],
            'body_tok': new['body_tok'], 'full_tok': new['full_tok'],
            'base_tok': tp.est_tokens(tp.prepare(raw_txt, tp.INGEST_BUDGET)),
            'ne_base': _nonempty_rate(b, f), 'ne_new': _nonempty_rate(o, f),
            'num_gold': len(gold), 'num_got': len(got),
            'num_recall': (len(got & gold) / len(gold)) if gold else None,
            'ov_key': _overlap(_words(_flat(b, ['key_content_en', 'key_content'])),
                               _words(_flat(o, ['key_content_en', 'key_content']))),
            'ov_tag': _overlap(_words(_flat(b, ['tags_en', 'tags'])),
                               _words(_flat(o, ['tags_en', 'tags']))),
        }
        rows.append(rec)
        print('  [%d] %s %s ne %.2f→%.2f 数值召回 %s  key重叠 %.2f' % (
            i, sha[:8], rec['lang'], rec['ne_base'], rec['ne_new'],
            ('%.2f' % rec['num_recall']) if rec['num_recall'] is not None else 'n/a',
            rec['ov_key']), flush=True)

    ok = [r for r in rows if not r.get('json_fail')]
    def avg(k):
        v = [r[k] for r in ok if r.get(k) is not None]
        return sum(v) / len(v) if v else 0.0

    print('\n' + '=' * 62)
    print('JSON 失败      %d/%d' % (len(rows) - len(ok), len(rows)))
    print('字段非空率     %.3f → %.3f' % (avg('ne_base'), avg('ne_new')))
    print('数值召回(金标) %.3f' % avg('num_recall'))
    print('key_content 重叠 %.3f' % avg('ov_key'))
    print('tags 重叠      %.3f' % avg('ov_tag'))
    print('输入 tok 均    %s   输出 tok 均 %s' % (
        round(sum((r.get('in') or 0) for r in ok) / max(len(ok), 1)),
        round(sum((r.get('out') or 0) for r in ok) / max(len(ok), 1))))
    bt = sum((r.get('base_tok') or 0) for r in ok) / max(len(ok), 1)
    nt = sum((r.get('body_tok') or 0) for r in ok) / max(len(ok), 1)
    print('送入文本 tok  旧版 %.0f → 新版 %.0f  (%+.0f%%)' % (
        bt, nt, (nt / bt - 1) * 100 if bt else 0))
    print('=' * 62)
    print('阈值：非空率≥基准95%%、数值召回≥0.90、key重叠≥0.60')

    outp = args.out or str(BASE / 'logs' / ('ab_%s.json' % tag))
    Path(outp).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
    print('明细 →', outp)


if __name__ == '__main__':
    main()
