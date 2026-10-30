#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""GeoKB 入库「防上下文爆炸」摘要器。

把一篇论文的 wip 笔记 + 全文缓存压缩成 ≤ budget 字符的紧凑摘要，替代逐段通读全文。

用法：
  python scripts/ingest_digest.py next                 # 取 FIFO 下一篇（stage=prepped），并写 handoff 指针
  python scripts/ingest_digest.py <sha12>              # 指定论文
  python scripts/ingest_digest.py <sha12> --slices 552-600,679-700   # 需要结果段时按行切片
  python scripts/ingest_digest.py <sha12> --budget 3500
  python scripts/ingest_digest.py --list                # 只列 prepped 队列，不输出正文
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIP = os.path.join(ROOT, 'state', 'agent_ingest_wip')
TEXT = os.path.join(ROOT, 'state', 'text')
COLD_TEXT = os.path.join(ROOT, 'state', '_cold', 'text')
STATE = os.path.join(ROOT, 'state', 'agent_ingest_state.jsonl')
HANDOFF = os.path.join(ROOT, 'state', 'ingest_handoff.md')

JUNK = re.compile(r'^[\s\dIVXLCⅠ-Ⅹ]{0,8}$')


def latest_stages():
    """sha12 -> 最新一条 state 记录（按 ts 取最大）"""
    best = {}
    if not os.path.exists(STATE):
        return best
    with open(STATE, encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            sha = r.get('sha12')
            if not sha:
                continue
            prev = best.get(sha)
            if prev is None or str(r.get('ts', '')) >= str(prev.get('ts', '')):
                best[sha] = r
    return best


def prepped_fifo():
    """待办 = wip 笔记（.md）中尚无 content/papers/paper-<sha>.md 的论文，按笔记 mtime 先进先出。
    prep 不写 state.jsonl，故不能用 stage 判断；以「笔记存在 + 条目未生成」为唯一判据。"""
    rows = []
    for p in glob.glob(os.path.join(WIP, '*.md')):
        sha = os.path.basename(p)[:-3]
        if not re.match(r'^[0-9a-f]{12}$', sha):
            continue
        if os.path.exists(os.path.join(ROOT, 'content', 'papers', 'paper-%s.md' % sha)):
            continue
        rows.append((os.path.getmtime(p), sha))
    rows.sort()
    return [(sha, {}) for _, sha in rows]


def load_note(sha):
    p = os.path.join(WIP, sha + '.md')
    if not os.path.exists(p):
        return None
    return open(p, encoding='utf-8', errors='ignore').read()


def note_meta(note):
    meta = {}
    for key in ('期刊', '年份', '台账标题', '分桶', '文本字符数', '路径'):
        m = re.search(r'\|\s*%s\s*\|\s*(.+?)\s*\|' % re.escape(key), note)
        if m:
            meta[key] = m.group(1).strip().strip('`')
    return meta


def note_anchors(note):
    out = []
    for m in re.finditer(r'^###\s*L(\d+)\s*·\s*(.+)$', note, re.M):
        out.append((int(m.group(1)), m.group(2).strip()))
    return out


def note_numbers(note, limit):
    out = []
    for m in re.finditer(r'^-\s*L(\d+):\s*(.+)$', note, re.M):
        out.append((int(m.group(1)), m.group(2).strip()))
    return out[:limit]


def load_text(sha):
    pats = (sha + '*.txt', '_' + sha + '*.txt')
    for base in (TEXT, COLD_TEXT):  # 热区优先，已出栈的篇在冷区
        for pat in pats:
            cands = [p for p in glob.glob(os.path.join(base, pat))
                     if not p.endswith('.readable.txt')]
            if cands:
                p = sorted(cands)[0]
                lines = open(p, encoding='utf-8', errors='ignore').read().split('\n')
                return p, lines
    return None, []


def clean(block):
    parts = []
    for l in block:
        s = l.strip()
        if not s or JUNK.match(s):
            continue
        parts.append(s)
    txt = ''.join(parts)
    return re.sub(r'\s{2,}', ' ', txt)


def find_block(lines, starters, stopwords, maxlines):
    """返回 (start_line_no, end_line_no, text) 1-based，找不到返回 None"""
    for i, l in enumerate(lines):
        s = l.strip()
        if not s:
            continue
        if any(re.match(p, s) for p in starters) and len(s) <= 30:
            end = min(len(lines), i + maxlines)
            for j in range(i + 1, end):
                if any(w in lines[j] for w in stopwords) and len(lines[j].strip()) <= 30:
                    end = j
                    break
            return i + 1, end, clean(lines[i:end])
    return None


def spaces_in_numbers(text):
    """检测「0.962 3」「2 000」这类原文内部带空格的数字，写 payload 时必须照抄"""
    pat = re.compile(r'(?:\d+\.\d+\s\d+|\d+\s\d{3}\b)')
    seen, out = set(), []
    for m in pat.finditer(text):
        s = m.group(0)
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= 6:
            break
    return out


def build_digest(sha, budget, num_limit):
    note = load_note(sha)
    if note is None:
        return None
    meta = note_meta(note)
    anchors = note_anchors(note)
    nums = note_numbers(note, num_limit)
    path, lines = load_text(sha)

    out = []
    out.append('## %s | %s %s | 全文 %s 字' % (
        sha, meta.get('期刊', '?'), meta.get('年份', '?'), meta.get('文本字符数', '?')))
    out.append('标题：%s' % meta.get('台账标题', '?'))
    if lines:
        body = [l for l in lines if l.strip()]
        med = sorted(len(l.strip()) for l in body)[len(body) // 2] if body else 0
        out.append('原文缓存：%s（%d 行，正文行中位长度 %d %s）' % (
            os.path.basename(path), len(lines), med, '→ 逐字断行，读时需拼接' if med <= 6 else ''))
    else:
        out.append('!! 原文缓存缺失（state/text/%s.txt），需先重建' % sha)

    if anchors:
        out.append('章节锚点：' + '；'.join('L%d %s' % (n, t) for n, t in anchors[:6]))

    if lines:
        ab = find_block(
            lines,
            [r'^内容提要', r'^摘\s*要', r'^摘$', r'^要[：:]', r'^提\s*要', r'^摘要[：:]',
             r'^ABSTRAC\s*T?$', r'^Abstract[：:]?$'],
            ['关键词', '关键字', 'Key words', 'Keywords', 'KEYWORDS', 'INTRODUCTION'], 60)
        if ab is None:
            # 兜底：取首页正文首段（跳过刊头/作者/基金等噪声后的前 25 行）
            for i, l in enumerate(lines[:80]):
                if len(l.strip()) >= 30 and ('。' in l or '，' in l):
                    ab = (i + 1, min(len(lines), i + 25), clean(lines[i:i + 25]))
                    break
        if ab:
            txt = ab[2] if budget <= 0 else ab[2][:int(budget * 0.34)]
            out.append('摘要（L%d-L%d）：%s' % (ab[0], ab[1], txt))
        cc = find_block(lines, [r'^结\s*论', r'^\d+\s*结\s*论', r'^[6-9]\s*结\s*论', r'^五[、.]\s*结论',
                                r'^CONCLUSIONS?$', r'^Conclusions?[：:]?$'],
                        ['References', 'REFERENCES', '参考文献', '致谢', '致　谢', 'Acknowledg'], 45)
        if cc is None:
            # 兜底：参考文献之前的最后 20 行（方法类论文常无独立结论）
            ref = None
            for i, l in enumerate(lines):
                if re.match(r'^\s*(References|参考文献)\s*$', l):
                    ref = i
                    break
            if ref and ref > 25:
                cc = (ref - 19, ref, clean(lines[ref - 20:ref]))
        if cc:
            txt = cc[2] if budget <= 0 else cc[2][:int(budget * 0.30)]
            out.append('结论（L%d-L%d）：%s' % (cc[0], cc[1], txt))

    if nums:
        out.append('数值候选（照抄原文，勿改写）：' + '；'.join('L%d %s' % (n, t[:46]) for n, t in nums))

    if lines:
        joined = clean(lines)
        sp = spaces_in_numbers(joined)
        if sp:
            out.append('⚠ 原文数字含内部空格：%s —— payload 必须原样照抄（如 0.962 3 不能写 0.9623）' % '、'.join(sp))

    txt = '\n'.join(out)
    if budget > 0 and len(txt) > budget:
        txt = txt[:budget] + '\n…[已截断，需要更多内容用 --slices a-b / --full]'
    return txt, len(txt), len(clean(lines)) if lines else 0


def print_slices(sha, spec):
    path, lines = load_text(sha)
    if not lines:
        print('!! 无原文缓存')
        return
    for part in spec.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            a, b = part.split('-', 1)
            a, b = int(a), int(b)
        else:
            a = b = int(part)
        a = max(1, a)
        b = min(len(lines), b)
        print('--- L%d-L%d ---' % (a, b))
        print(clean(lines[a - 1:b]))


def write_handoff(sha, meta, remaining):
    with open(HANDOFF, 'w', encoding='utf-8') as f:
        f.write('# 入库断点指针\n\n')
        f.write('- 更新时间：%s\n' % datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        f.write('- 当前篇：`%s` — %s\n' % (sha, meta.get('台账标题', '?')))
        f.write('- 处理方式：`python scripts/ingest_digest.py %s` → 写 payload → write → 每 6 篇 commit+build_facet\n' % sha)
        f.write('- 队列中剩余 prepped：%d 篇%s\n' % (
            len(remaining), ('（' + '、'.join(remaining[:6]) + '）') if remaining else ''))
        f.write('\n> 新会话恢复只需读本文件 + 本篇 digest，禁止复读已 committed 论文正文。\n')


REF_RE = re.compile(
    r'^\s*(参\s*考\s*文\s*献|References?|REFERENCES?|Bibliography|致\s*谢|'
    r'Acknowledg(e)?ments?|AUTHOR\s+CONTRIBUTIONS)\s*$', re.I)


def body_cut(lines):
    """返回正文结束行号（0-based），其后为参考文献/致谢；找不到返回 len(lines)。

    只在全文 35% 之后才认标题，避免把小结里出现的「参考文献」字样当分界。
    """
    n = len(lines)
    for i in range(int(n * 0.35), n):
        s = lines[i].strip()
        if len(s) <= 20 and REF_RE.match(s):
            return i
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('sha', nargs='?', default=None)
    ap.add_argument('--slices', default=None)
    ap.add_argument('--budget', type=int, default=0,
                    help='0（默认）=不截断，digest 只做导航不做压缩；>0 才截断')
    ap.add_argument('--numbers', type=int, default=10)
    ap.add_argument('--full', action='store_true', help='输出拼接后的全文（子代理一次性通读用）')
    ap.add_argument('--body', action='store_true',
                    help='输出正文（自动截到参考文献/致谢前），带 120 行锚点，省 20-35%% 输入；行号与 --full 一致')
    ap.add_argument('--list', action='store_true')
    a = ap.parse_args()

    if a.list:
        rows = prepped_fifo()
        print('prepped 队列：%d 篇' % len(rows))
        for sha, r in rows[:12]:
            note = load_note(sha) or ''
            print('  %s | %s | %s' % (sha, note_meta(note).get('期刊', '?'), note_meta(note).get('台账标题', '?')[:40]))
        return

    sha = a.sha
    remaining = []
    if sha in (None, 'next'):
        rows = prepped_fifo()
        if not rows:
            print('!! 队列无待办论文（wip 笔记均已生成条目），先 scan + prep')
            sys.exit(1)
        sha = rows[0][0]
        remaining = [s for s, _ in rows[1:]]
    else:
        remaining = [s for s, _ in prepped_fifo() if s != sha]

    if a.slices:
        print_slices(sha, a.slices)
        return

    if a.body:
        path, lines = load_text(sha)
        if not lines:
            print('!! 无原文缓存: %s' % sha)
            sys.exit(1)
        cut = body_cut(lines)
        print('=== %s 正文 %d / %d 行（省略其后 %d 行参考文献/致谢；行号与 --full 一致）==='
              % (sha, cut, len(lines), len(lines) - cut))
        for start in range(0, cut, 120):
            end = min(cut, start + 120)
            print('--- L%d-L%d ---' % (start + 1, end))
            print(clean(lines[start:end]))
        if cut < len(lines):
            print('--- 正文止于 L%d，其后为参考文献/致谢（写 kp 无用，已省略）。'
                  '需精确锚定某句用 --slices 起-止 ---' % cut)
        note = load_note(sha) or ''
        write_handoff(sha, note_meta(note), remaining)
        return

    if a.full:
        path, lines = load_text(sha)
        if not lines:
            print('!! 无原文缓存: %s' % sha)
            sys.exit(1)
        body = clean(lines)
        print('=== %s 全文 %d 行 / %d 字符（%s）===' % (
            sha, len(lines), len(body), os.path.basename(path)))
        print(body)
        note = load_note(sha) or ''
        write_handoff(sha, note_meta(note), remaining)
        return

    res = build_digest(sha, a.budget, a.numbers)
    if res is None:
        print('!! 缺少 wip 笔记 state/agent_ingest_wip/%s.md' % sha)
        sys.exit(1)
    txt, n, total = res
    print(txt)
    note = load_note(sha) or ''
    write_handoff(sha, note_meta(note), remaining)
    pct = (n * 100.0 / total) if total else 0
    print('\n---\n[digest %d 字符 / 原文 %d 字符 = %.1f%% ｜ handoff 已更新 %s]' % (
        n, total, pct, os.path.relpath(HANDOFF, ROOT)))


if __name__ == '__main__':
    main()
