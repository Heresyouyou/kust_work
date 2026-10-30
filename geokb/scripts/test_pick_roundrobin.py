# -*- coding: utf-8 -*-
"""离线回归：同源站多刊轮转 + 熔断不计硬失败。

只调 _pick / _settle 的纯逻辑，不碰网络、不碰生产清单。
"""
import importlib.util
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('hqloop', str(BASE / 'history_queue_loop.py'))
L = importlib.util.module_from_spec(spec)
sys.modules['hqloop'] = L
spec.loader.exec_module(L)

# 测试绝不能写生产日志（第一次跑就把假记录写进了 logs/history_queue_loop.log）
L.log = lambda *a, **k: None


class FakeQ:
    def __init__(self, items):
        self._items = items

    def select_ready(self, items):
        return list(items), [], []

    def is_sd(self, *a, **k):
        return False

    MAX_ATTEMPTS = 3
    MAX_SOFT_FAILS = 12
    CAPTCHA_MAX = 2
    REVIVE_HOURS = 12

    def append_done(self, rec):
        pass

    def save_manifest(self, items, backup=True):
        pass

    def apply_failure(self, item, err, note):
        self.last_kind = 'permanent' if err in ('paywalled', 'http_404') else 'retryable'
        item['last_error'] = '%s: %s' % (err, note)
        return {'kind': self.last_kind, 'attempts': 1, 'soft': 1, 'captcha': 0,
                'cd': 0, 'newly_parked': False, 'parked': False}


class FakeAB:
    def pace_host(self, url, doi=''):
        return 'link.springer.com' if '10.1007' in (url + doi) else 'other.org'

    def gap_for(self, host):
        return 0.0

    def pace_wait(self, url, doi, scale):
        return 0.0


def mk(i, journal):
    return {'id': 'id%d' % i, 'journal': journal, 'title': 'T%d' % i,
            'url': 'https://doi.org/10.1007/x%d' % i, 'date': '2020',
            'score': 1, 'status': 'pending', 'attempts': 0}


items = ([mk(i, 'JGSA') for i in range(4)] +
         [mk(100 + i, 'Mineralium Deposita') for i in range(3)] +
         [mk(200 + i, 'Contributions to Mineralogy and Petrology') for i in range(3)])
q, ab = FakeQ(items), FakeAB()

# ① 轮转：连着挑 6 次，三个刊应各占 2 次（严格循环，不是两人转）
picked = []
for _ in range(6):
    it, host, why, wait = L._pick(q, ab, items, set(), {}, 1.0, False, {})
    picked.append(it['journal'][:6])
    L._HOST_LAST_JOURNAL[host] = it['journal']
    items = [x for x in items if x['id'] != it['id']]
print('① 轮转序列:', picked)
from collections import Counter
c = Counter(picked)
assert len(c) == 3, '三个刊没有都轮到：%s' % c
assert set(c.values()) == {2}, '轮转不均匀：%s' % c

# ② 只剩一个刊时不能死循环，仍要正常返回
items = [mk(i, 'JGSA') for i in range(3)]
it, host, why, wait = L._pick(q, ab, items, set(), {}, 1.0, False, {})
print('② 单刊仍可挑出:', it['journal'], host)
assert it is not None

# ③ 熔断：永久失败不计入 _HOST_FAILS / 不触发暂停
L._HOST_FAILS.clear(); L._HOST_PAUSE.clear()
L.HOST_FAIL_LIMIT, L.HOST_PAUSE = 3, 1800
calls = []


class Fut:
    def __init__(self, r):
        self._r = r

    def result(self):
        return self._r


for i in range(6):
    it = mk(i, 'JGSA')
    L._settle(q, [it], it, 'link.springer.com', Fut(('fail', 'paywalled: x', 'paywalled')),
              {}, 1.0)
print('③ 6 次 paywalled 后：暂停=', bool(L._HOST_PAUSE), '连续失败=', L._HOST_FAILS)
assert not L._HOST_PAUSE, '硬失败不该触发源站熔断'
assert L._HOST_FAILS.get('link.springer.com', 0) == 0

# ④ 可重试失败仍要熔断（不能把熔断整个废掉）
L._HOST_FAILS.clear(); L._HOST_PAUSE.clear()
for i in range(4):
    it = mk(i, 'JGSA')
    L._settle(q, [it], it, 'link.springer.com', Fut(('fail', 'err_network: x', 'err_network')),
              {}, 1.0)
print('④ 4 次 err_network 后：暂停=', bool(L._HOST_PAUSE))
assert L._HOST_PAUSE.get('link.springer.com', 0) > time.time(), '软失败必须仍能熔断'

print('\n全部通过')
