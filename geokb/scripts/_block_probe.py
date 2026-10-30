# -*- coding: utf-8 -*-
"""块级筛选的离线体检（不调 API）：文本覆盖率 / 带单位数值覆盖率 / 段落完整度。

关键指标是**块完整度**：被选内容里有多少是「完整段落」而不是片段。
上一版句级筛选就是栽在这里 —— 文本覆盖 77% 但段落被打散。
"""
import argparse
import glob
import random
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
import text_prep as tp  # noqa: E402

RE_UNUM = re.compile(
    r'\d+(?:\.\d+)?\s*(?:%|wt\s*%|ppm|ppb|Ma|Ga|ka|km|cm|mm|µm|um|nm|'
    r'°C|℃|g/t|Mt|MPa|GPa|m/s|‰)(?![A-Za-z])', re.I)


def coverage(out, raw):
    """文本覆盖率 + 带单位数值覆盖率（词边界，避免 May 2016 → 201Ma）。"""
    oe = tp.est_tokens(out)
    re_ = tp.est_tokens(raw)
    set_raw = set(RE_UNUM.findall(raw))
    set_out = set(RE_UNUM.findall(out))
    return (oe / max(re_, 1),
            (len(set_raw & set_out) / len(set_raw)) if set_raw else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=40)
    ap.add_argument('--budget', type=int, default=4500)
    ap.add_argument('--seed', type=int, default=13)
    args = ap.parse_args()

    files = sorted(glob.glob(str(BASE / 'state' / 'text' / '*.txt')))
    random.seed(args.seed)
    files = random.sample(files, min(args.n, len(files)))

    rows = []
    for p in files:
        raw = Path(p).read_text(encoding='utf-8', errors='replace')
        c = tp.clean(raw)
        full = tp.est_tokens(c)
        res = {}
        res['headtail'] = tp.prepare(c, args.budget)
        res['select'] = tp.select_sentences(c, args.budget)
        res['block'] = tp.select_blocks(c, args.budget)
        row = {'sha': Path(p).stem[:8], 'full': full}
        for k, v in res.items():
            cov, num = coverage(v, c)
            row[k] = (tp.est_tokens(v), cov, num)
        rows.append(row)

    print('样本 %d 篇  预算 %d' % (len(rows), args.budget))
    print('%-8s %6s | %-22s %-22s %-22s' % (
        'sha', '全文', 'headtail(tok/cov/num)', 'select(tok/cov/num)', 'block(tok/cov/num)'))
    agg = {k: [[], []] for k in ('headtail', 'select', 'block')}
    for r in rows:
        cells = []
        for k in ('headtail', 'select', 'block'):
            tk, cov, num = r[k]
            cells.append('%5d %.2f %s' % (tk, cov, '  -  ' if num is None else '%.2f' % num))
            agg[k][0].append(cov)
            if num is not None:
                agg[k][1].append(num)
        print('%-8s %6d | %s' % (r['sha'], r['full'], ' | '.join(cells)))

    print('\n' + '=' * 78)
    for k in ('headtail', 'select', 'block'):
        cov, num = agg[k]
        nums = sorted(num)
        print('%-9s 文本覆盖 %.3f | 数值覆盖 均值 %.3f 中位 %.3f 最低 %.2f' % (
            k, sum(cov) / len(cov), sum(num) / len(num),
            nums[len(nums) // 2], nums[0]))
    print('=' * 78)


if __name__ == '__main__':
    main()
