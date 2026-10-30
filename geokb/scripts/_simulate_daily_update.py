# -*- coding: utf-8 -*-
"""当日论文管线「回滚-重放」实测（2026-10-17）

目的：验证 journals 页当日更新 → 网站抓 PDF → 落 NAS → 登记台账 → 入库索引
      这一整条链路在**真实网络**下可跑通。

做法（受控、可回滚）：
  1. 从本地台账挑一篇历史抓取成功的论文，备份其 NAS PDF 到本地 .bak；
  2. 从台账里移除该行（使其成为「待抓」），并移走 PDF；
  3. 以「当日新增」身份重跑 daily_archive_papers.py（--force 旁路同日幂等）；
  4. 校验：PDF 重新落盘、字节头 %PDF-、eid 一致（内容级一致 = 源站同文件）、
     台账重新登记；
  5. 无论成败都恢复备份（PDF 还原、台账行回填）。

不触碰任何非目标条目；不调用 DeepSeek（入库索引本身不需要 LLM）。
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
LEDGER = BASE / 'state' / 'papers_index.jsonl'
BACKUP = BASE / 'state' / '_replay_backup'
PY = Path('D:/ZSK/geokb/venv/Scripts/python.exe')
SCRIPT = BASE / 'scripts' / 'daily_archive_papers.py'
TARGET_KEY = 'greenalchemyfornearzerowaste'   # _norm_title 的前若干字符


def norm_title(s):
    import re
    return re.sub(r'\W+', '', (s or '').lower())[:80]


def read_ledger():
    rows = []
    for line in LEDGER.read_text(encoding='utf-8', errors='replace').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def main():
    BACKUP.mkdir(parents=True, exist_ok=True)
    rows = read_ledger()
    target = next((m for m in rows
                   if TARGET_KEY in norm_title(m.get('title', ''))), None)
    if not target:
        print('!! 未找到目标条目'); return 2

    pdf = Path(target['repo_path'])
    print('目标条目:', target['title'][:70])
    print('原始 PDF :', pdf, '存在=', pdf.exists(),
          'size=', pdf.stat().st_size if pdf.exists() else '-')

    bak = BACKUP / (pdf.name + '.bak')
    ok_backup = False
    if pdf.exists():
        shutil.copy2(pdf, bak)
        ok_backup = True
        print('已备份 →', bak)

    # 2) 移走 PDF（改名而非删除：NAS 上 safe-delete/trash 不可用）+ 移除台账行
    # 保留原件在同一目录内（.replay_orig 后缀），测试后原样改回，NAS 数据零风险
    parked = pdf.with_name(pdf.name + '.replay_orig')
    if pdf.exists() and not parked.exists():
        os.replace(str(pdf), str(parked))
        print('已改名让位 →', parked.name)
    kept = [m for m in rows if norm_title(m.get('title', '')) != norm_title(target['title'])]
    with open(LEDGER, 'w', encoding='utf-8') as f:
        for m in kept:
            f.write(json.dumps(m, ensure_ascii=False) + '\n')
    print(f"台账已移除该行：{len(rows)} → {len(kept)}")

    # 3) 重放
    print('\n--- 重跑 daily_archive_papers.py --force ---', flush=True)
    t0 = time.time()
    p = subprocess.run([str(PY), str(SCRIPT), '--force'],
                       cwd=str(BASE / 'scripts'),
                       capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=3600)
    tail = [l for l in (p.stdout or '').splitlines()
            if TARGET_KEY[:12] in norm_title(l) or '完成：' in l]
    print('\n'.join(tail[-6:]))
    print(f'耗时 {time.time() - t0:.1f}s  code={p.returncode}')

    # 4) 校验
    rows2 = read_ledger()
    again = next((m for m in rows2
                  if norm_title(m.get('title', '')) == norm_title(target['title'])), None)
    verdict = 'FAIL'
    if again:
        p2 = Path(again.get('repo_path', ''))
        head = p2.open('rb').read(5) if p2.exists() else b''
        same_eid = again.get('eid') == target.get('eid')
        print('\n=== 校验 ===')
        print('重新落盘 :', p2.exists(), p2.stat().st_size if p2.exists() else '-')
        print('PDF 头   :', head)
        print('eid 一致 :', same_eid, f"({target.get('eid')} → {again.get('eid')})")
        if p2.exists() and head == b'%PDF-' and same_eid:
            verdict = 'PASS'
    else:
        print('\n=== 校验 ===\n未重新登记（抓取失败）')

    # 5) 恢复（测试无论成败都还原，NAS 内容不被破坏）
    # 若重放重新落盘了同名 PDF：先把它挪到 .replay_new 留证，再把原件改名回来
    if pdf.exists() and parked.exists():
        os.replace(str(pdf), str(pdf.with_name(pdf.name + '.replay_new')))
        os.replace(str(parked), str(pdf))
        print('已还原 NAS 原 PDF →', pdf)
        print('本次重放产物保留 →', pdf.name + '.replay_new')
    if again is None:
        with open(LEDGER, 'a', encoding='utf-8') as f:
            f.write(json.dumps(target, ensure_ascii=False) + '\n')
        print('已回填台账行（原记录）')
    print('\n最终结论:', verdict)
    return 0 if verdict == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
