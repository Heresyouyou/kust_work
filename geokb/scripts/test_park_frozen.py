# -*- coding: utf-8 -*-
"""park_frozen 隔离测试：造一份假清单 + 假冻结表，验证移出/放回/幂等/floor。

不碰生产清单，全部在临时目录里跑。
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
SCRIPTS = BASE / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

ok_all = True


def check(name, cond, extra=''):
    global ok_all
    print(('  PASS  ' if cond else '  FAIL  ') + name + (('  ' + str(extra)) if extra else ''))
    if not cond:
        ok_all = False


tmp = Path(tempfile.mkdtemp(prefix='park_'))
STATE = tmp / 'state'
STATE.mkdir()

# 造清单：5 条 gsw、3 条 dgyk、2 条正常、1 条坏行
ROWS = []
for i in range(5):
    ROWS.append({'id': 'g%d' % i, 'url': 'https://doi.org/10.1130/G%d' % i,
                 'doi': '10.1130/G%d' % i, 'journal': 'Geology', 'title': 'g %d' % i})
for i in range(3):
    ROWS.append({'id': 'd%d' % i, 'doi': '10.16539/j.dgyk.%d' % i,
                 'url': 'https://dgyk.cbpt.cnki.net/x%d' % i,
                 'journal': '大地构造与成矿学', 'title': 'd %d' % i})
for i in range(2):
    ROWS.append({'id': 'n%d' % i, 'url': 'https://doi.org/10.1007/s00%d' % i,
                 'doi': '10.1007/s00%d' % i, 'journal': 'MD', 'title': 'n %d' % i})
MAN = STATE / 'history_manifest.jsonl'
with open(MAN, 'w', encoding='utf-8') as f:
    for r in ROWS:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')
    f.write('{"broken": 这一行是坏 JSON\n')     # 坏行必须原样保留

# 假冻结表
FRZ = STATE / 'frozen_sources.json'
FRZ.write_text(json.dumps({'version': 1, 'hosts': {
    'pubs.geoscienceworld.org': {'kind': 'blocked', 'reason': 'cf',
                                 'floor': time.time() + 3600},
    'dgyk.cbpt.cnki.net': {'kind': 'broken', 'reason': 'err_index',
                           'floor': time.time() + 3600},
}}, ensure_ascii=False), encoding='utf-8')

# ── 加载模块（真实 source_freeze）+ park_frozen（重定向路径）────────────
spec = importlib.util.spec_from_file_location('sfz_pk', str(SCRIPTS / 'source_freeze.py'))
frz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frz)
frz.FREEZE_FILE = str(FRZ)
frz._cache = {'mtime': 0.0, 'data': {}}
sys.modules['source_freeze'] = frz

spec2 = importlib.util.spec_from_file_location('pf_t', str(SCRIPTS / 'park_frozen.py'))
pf = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(pf)
pf.MANIFEST = MAN
pf.PARKED = STATE / 'history_manifest_frozen.jsonl'
pf.PARKED_META = STATE / 'history_manifest_frozen.meta.json'
pf.BACKUP_DIR = STATE / 'backup'
pf.OP_LOCK = STATE / 'manifest_op.lock'
pf.frz = frz


def count(p):
    if not p.exists():
        return 0, 0
    good, bad = pf.read_rows(p)
    return len(good), len(bad)


print('\n[1] 初始')
g, b = count(MAN)
check('清单 10 条 + 1 坏行', (g, b) == (10, 1), (g, b))

print('\n[2] 移出')
pf.do_park(dry_run=False)
g, b = count(MAN)
gp, bp = count(pf.PARKED)
check('清单剩 2 条', g == 2, g)
check('坏行保留在清单', b == 1, b)
check('备份 8 条（5 gsw + 3 dgyk）', gp == 8, gp)
check('备份里没有坏行', bp == 0, bp)
check('清单只剩 10.1007 的 2 条',
      all('10.1007' in x.get('doi', '') for x in pf.read_rows(MAN)[0]))

print('\n[3] 幂等：再跑一次')
pf.do_park(dry_run=False)
g2, b2 = count(MAN)
gp2, _ = count(pf.PARKED)
check('清单还是 2 条', g2 == 2, g2)
check('备份没膨胀（还是 8 条）', gp2 == 8, gp2)

print('\n[4] floor 保护：未满 48h 拒绝放回')
rc = pf.do_restore('pubs.geoscienceworld.org', force=False, dry_run=False)
check('返回码 3（拒绝）', rc == 3, rc)
gp3, _ = count(pf.PARKED)
check('备份未变（仍 8 条）', gp3 == 8, gp3)

print('\n[5] --force 放回单个站')
rc = pf.do_restore('pubs.geoscienceworld.org', force=True, dry_run=False)
g5, b5 = count(MAN)
gp5, _ = count(pf.PARKED)
check('返回 0', rc == 0, rc)
check('清单 2 → 7 条', g5 == 7, g5)
check('坏行仍在', b5 == 1, b5)
check('备份 8 → 3 条（只剩 dgyk）', gp5 == 3, gp5)

print('\n[6] 放回去重：再放一次同样的站')
rc = pf.do_restore('pubs.geoscienceworld.org', force=True, dry_run=False)
g6, _ = count(MAN)
check('清单不长出重复条目', g6 == 7, g6)

print('\n[7] floor 到期后可正常放回')
hosts = frz.load(force=True)
hosts['dgyk.cbpt.cnki.net']['floor'] = time.time() - 1     # 假装冻满了
frz._save(hosts)
rc = pf.do_restore('all', force=False, dry_run=False)
g7, b7 = count(MAN)
gp7, _ = count(pf.PARKED)
check('返回 0', rc == 0, rc)
check('清单回到 10 条', g7 == 10, g7)
check('坏行仍在', b7 == 1, b7)
check('备份清空', gp7 == 0, gp7)

print('\n[8] 坏行内容一字不差')
txt = MAN.read_text(encoding='utf-8')
check('坏行原样保留', '{"broken": 这一行是坏 JSON' in txt)

print('\n[9] 备份与清单的 id 集合互补（无重复无遗漏）')
ids = {x['id'] for x in pf.read_rows(MAN)[0]}
check('10 个 id 齐全', ids == {r['id'] for r in ROWS}, sorted(ids))

shutil.rmtree(tmp, ignore_errors=True)
print('\n' + ('全部通过' if ok_all else '有失败项，需修'))
sys.exit(0 if ok_all else 1)
