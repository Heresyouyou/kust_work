# -*- coding: utf-8 -*-
"""校验 prepare2 改造未破坏生产默认行为（2026-10-20）。

三条断言：
  1. 无 GEOKB_PREP 时，prepare2(raw, 10000) 与旧 prepare(raw, 10000) **逐字节相同**
     —— 这是缓存与既有入库一致性的底线。
  2. 两个 LLM 任务（pdf_corpus_ingest / kp_extract）取到的 body 相同。
  3. GEOKB_PREP=block 时确实走块级，且 token 明显下降。
"""
import glob
import hashlib
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
import text_prep as tp  # noqa: E402

files = sorted(glob.glob(str(BASE / 'state' / 'text' / '*.txt')))[:40]
print('样本 %d 篇' % len(files))

# ── 断言 1：逐字节等价 ──
diff = 0
for p in files:
    raw = Path(p).read_text(encoding='utf-8', errors='replace')
    a = tp.prepare(raw, tp.INGEST_BUDGET)
    b = tp.prepare2(raw, tp.INGEST_BUDGET)
    if hashlib.sha1(a.encode()).hexdigest() != hashlib.sha1(b.encode()).hexdigest():
        diff += 1
        print('  ✗ 不一致', Path(p).stem[:8])
print('断言1 默认路径逐字节等价: %s (%d/%d 不一致)' % ('PASS' if diff == 0 else 'FAIL', diff, len(files)))

# ── 断言 3：block 开关（需子进程，PREP_MODE 在 import 时求值）──
code = r'''
import sys, glob, hashlib
from pathlib import Path
BASE = Path(r"%s")
sys.path.insert(0, str(BASE / "scripts"))
import text_prep as tp
fs = sorted(glob.glob(str(BASE / "state" / "text" / "*.txt")))[:40]
tot_b = tot_h = 0
for p in fs:
    raw = Path(p).read_text(encoding="utf-8", errors="replace")
    tot_h += tp.est_tokens(tp.prepare(raw, tp.INGEST_BUDGET))
    tot_b += tp.est_tokens(tp.prepare2(raw, tp.INGEST_BUDGET))
print("%%d %%d %%.3f" %% (tot_h, tot_b, tot_b / tot_h))
''' % str(BASE)
env = dict(os.environ)
env['GEOKB_PREP'] = 'block'
r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=env, cwd=str(BASE))
out = (r.stdout or '').strip().split()
if len(out) == 3:
    h, b, ratio = int(out[0]), int(out[1]), float(out[2])
    print('断言3 block 生效: %s  旧 %d tok → 新 %d tok (%.1f%%)' % (
        'PASS' if b < h * 0.95 else 'FAIL', h, b, ratio * 100))
else:
    print('断言3 FAIL', r.stdout, r.stderr[-400:])

# ── 断言 2：两任务同源（同一函数、同一参数，天然一致；这里校验入口一致）──
import pdf_corpus_ingest as pci  # noqa: E402
import kp_extract as kpx  # noqa: E402
same = (pci.prepare2 is kpx.prepare2 is tp.prepare2)
print('断言2 两任务同源: %s' % ('PASS' if same else 'FAIL'))
print('   预算一致: %s (pci.MAX_PROMPT_TOKENS=%d, kp.INGEST_BUDGET=%d)' % (
    'PASS' if pci.MAX_PROMPT_TOKENS == kpx.INGEST_BUDGET else 'FAIL',
    pci.MAX_PROMPT_TOKENS, kpx.INGEST_BUDGET))
