# -*- coding: utf-8 -*-
"""离线自检：冻结表读写 + _pick 是否真的跳过被冻源站。不碰网络、不写清单。"""
import importlib.util
import os
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


# ── 1. source_freeze 读写往返（用临时文件，不动生产表）────────────────
print('\n[1] source_freeze 读写')
spec = importlib.util.spec_from_file_location('sfz_t', str(SCRIPTS / 'source_freeze.py'))
frz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frz)

tmpdir = tempfile.mkdtemp(prefix='sfz_')
frz.FREEZE_FILE = os.path.join(tmpdir, 'frozen.json')
frz._cache = {'mtime': 0.0, 'data': {}}

check('初始为空', frz.load(force=True) == {})
check('未冻结时 is_frozen=False', frz.is_frozen('a.com') == (False, ''))
frz.freeze('A.com', kind='blocked', reason='cloudflare', note='测试', stats={'fail': 5})
check('freeze 后 host 小写归一', 'a.com' in frz.load(force=True))
f, why = frz.is_frozen('a.com')
check('is_frozen 命中', f is True and 'cloudflare' in why, why)
# until 语义只在**没有 floor** 时生效（min_hours=0 才不写 floor）
frz.freeze('b.com', kind='broken', reason='x', hours=0.0001, min_hours=0)
check('无 floor 时 until 之前仍冻着', frz.is_frozen('b.com')[0] is True)
time.sleep(0.5)
check('无 floor 时 until 到点自动失效', frz.is_frozen('b.com')[0] is False)
check('table_for_dispatch 只含有效项',
      sorted(frz.table_for_dispatch().keys()) == ['a.com'],
      frz.table_for_dispatch())

# ── 48h 最短冻结期（floor）────────────────────────────────────────
print('\n[1b] 最短冻结期 floor')
rec = frz.load(force=True)['a.com']
check('freeze 默认写入 floor≈48h',
      47.9 * 3600 < (rec['floor'] - time.time()) < 48.1 * 3600,
      round((rec['floor'] - time.time()) / 3600, 2))
try:
    frz.thaw('a.com')
    check('floor 未满时 thaw 必须拒绝', False, '竟然成功了')
except RuntimeError as e:
    check('floor 未满时 thaw 必须拒绝', '拒绝解冻' in str(e), str(e)[:50])
check('拒绝后记录仍在', 'a.com' in frz.load(force=True))
# 重复冻结不许把 floor 重置得更晚/更早（取 max，只延长不缩短）
old_floor = frz.load(force=True)['a.com']['floor']
frz.freeze('a.com', kind='blocked', reason='cloudflare', min_hours=1)
new_floor = frz.load(force=True)['a.com']['floor']
check('重复冻结不缩短 floor', new_floor >= old_floor - 1, (old_floor, new_floor))
# 把 floor 拨到过去，模拟冻满
h = frz.load(force=True)
h['a.com']['floor'] = time.time() - 1
frz._save(h)
gone = frz.thaw('a.com')
check('floor 到期后可解冻', gone == ['a.com'], gone)
# b.com 的 until 已过 → load 时被剔除，表里应已空
check('表已空（b.com 到期自动剔除）', frz.load(force=True) == {}, frz.load(force=True))
frz.freeze('x.com', kind='dead', reason='r', min_hours=0)
check('无 floor 时可 thaw all', frz.thaw('all') == ['x.com'] and frz.load(force=True) == {})
frz.freeze('y.com', kind='blocked', reason='r')
try:
    frz.thaw('all')
    check('thaw all 也要受 floor 约束', False, '竟然成功了')
except RuntimeError:
    check('thaw all 也要受 floor 约束', True)
check('--force 可强解', frz.thaw('all', force=True) == ['y.com'])

# 坏文件不应抛异常
with open(frz.FREEZE_FILE, 'w', encoding='utf-8') as fh:
    fh.write('{ 不是 json')
frz._cache = {'mtime': 0.0, 'data': {}}
try:
    bad = frz.load(force=True)
    check('坏文件按未冻结处理（不抛异常）', bad == {})
except Exception as e:
    check('坏文件按未冻结处理（不抛异常）', False, e)

# ── 2. host_of 归一 ───────────────────────────────────────────────
print('\n[2] host_of 归一（GSW 三刊必须并到一个桶）')
cases = [
    ('https://doi.org/10.1130/G12345.1', '', 'pubs.geoscienceworld.org'),
    ('https://doi.org/10.2138/am-2020-1234', '', 'pubs.geoscienceworld.org'),
    ('https://doi.org/10.5382/econgeo.2020.1', '', 'pubs.geoscienceworld.org'),
    ('https://doi.org/10.1093/petrology/egaa001', '', 'academic.oup.com'),
    ('https://www.kcdz.ac.cn/x', '', 'www.kcdz.ac.cn'),
    ('', '10.16539/j.dgyk.2020.01.001', 'dgyk.cbpt.cnki.net'),
]
for url, doi, want in cases:
    got = frz.host_of(url, doi)
    check('%s → %s' % ((url or doi)[:44], want), got == want, got)

# ── 3. _pick 跳过冻结源站 ─────────────────────────────────────────
print('\n[3] _pick 跳过冻结源站')
spec2 = importlib.util.spec_from_file_location('hql_t', str(SCRIPTS / 'history_queue_loop.py'))
hql = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(hql)
spec3 = importlib.util.spec_from_file_location('abs_t', str(SCRIPTS / 'antibot_session.py'))
ab = importlib.util.module_from_spec(spec3)
spec3.loader.exec_module(ab)


class StubQ:
    """最小替身：所有条目都 ready，SD 一律 False。"""
    def select_ready(self, items):
        return list(items), 0, 0

    def is_sd(self, doi, url, journal):
        return False


items = [
    {'id': '1', 'url': 'https://doi.org/10.1130/G1', 'doi': '10.1130/G1',
     'journal': 'Geology', 'title': 'blocked one'},
    {'id': '2', 'url': 'https://doi.org/10.1007/s00126-020-0001', 'doi': '',
     'journal': 'Mineralium Deposita', 'title': 'springer one'},
]
q = StubQ()
frozen = {'pubs.geoscienceworld.org': 'blocked'}
it, host, why, wait = hql._pick(q, ab, items, set(), {}, 1.5, False, frozen)
check('被冻的 gsw 不被选中', it is not None and it['id'] == '2', (it or {}).get('id'))
check('选中的是未被冻的源站', host == 'link.springer.com', host)

# 全部冻结 → why 应为 frozen
frozen2 = {'pubs.geoscienceworld.org': 'blocked', 'link.springer.com': 'x'}
it2, _h2, why2, _w2 = hql._pick(q, ab, items, set(), {}, 1.5, False, frozen2)
check('全冻结时 why=frozen（不是 starve）', it2 is None and why2 == 'frozen', why2)

# 没冻结 → 正常挑第一篇
it3, host3, why3, _ = hql._pick(q, ab, items, set(), {}, 0.001, False, {})
check('不冻结时能正常挑', it3 is not None and host3 == 'pubs.geoscienceworld.org', host3)

# 冻结站不应把自己的静默期算进 wait
frozen3 = {'pubs.geoscienceworld.org': 'blocked'}
it4, _h4, why4, wait4 = hql._pick(q, ab, items[:1], set(), {}, 1.5, False, frozen3)
check('只剩冻站时即时返回，不长等', it4 is None and why4 == 'frozen' and wait4 == 0.0,
      (why4, wait4))

print('\n' + ('全部通过' if ok_all else '有失败项，需修'))
sys.exit(0 if ok_all else 1)
