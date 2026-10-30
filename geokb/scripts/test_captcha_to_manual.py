# -*- coding: utf-8 -*-
"""回归：人机挑战 2 次即转人工补缺。

覆盖三处契约：
  1. classify_failure：blocked_captcha 仍属 retryable（不是硬失败），
     真正决定转人工的是 CAPTCHA_MAX 计数，两套机制不要混。
  2. revive_parked：park_kind='captcha' 不自动复活 —— 已转人工的条目
     自动复活只会再去撞同一堵墙。
  3. manual_todo._bucket_of：blocked_captcha 必须落到 blocked（风控拦截）桶，
     否则人工在页面上找不到它。
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import history_queue as hq
import manual_todo as mt


def t_const():
    assert hq.CAPTCHA_MAX == 2, hq.CAPTCHA_MAX
    print('  CAPTCHA_MAX = %d' % hq.CAPTCHA_MAX)


def t_classify():
    assert hq.classify_failure('blocked_captcha') == 'retryable'
    assert hq.classify_failure('blocked_captcha: cf challenge') == 'retryable'
    # 确认别把 captcha 误归硬失败（那会走 3 次 permanent 路径，语义不对）
    assert hq.classify_failure('paywalled: 订阅墙') == 'permanent'
    print('  classify_failure：captcha 属 retryable，paywalled 属 permanent')


def t_revive():
    rows = [
        {'id': 'captcha', 'status': 'parked', 'park_kind': 'captcha',
         'parked_at': '2020-01-01T00:00:00', 'attempts': 0, 'soft_fails': 0},
        {'id': 'retryable', 'status': 'parked', 'park_kind': 'retryable',
         'parked_at': '2020-01-01T00:00:00', 'attempts': 0, 'soft_fails': 0},
        {'id': 'permanent', 'status': 'parked', 'park_kind': 'permanent',
         'parked_at': '2020-01-01T00:00:00', 'attempts': 0, 'soft_fails': 0},
        {'id': 'fresh', 'status': 'parked', 'park_kind': 'retryable',
         'parked_at': '2099-01-01T00:00:00', 'attempts': 0, 'soft_fails': 0},
    ]
    n = hq.revive_parked(rows)
    st = {x['id']: x['status'] for x in rows}
    assert n == 1, '应只复活 1 条，实际 %d' % n
    assert st['captcha'] == 'parked', st
    assert st['retryable'] == 'pending', st
    assert st['permanent'] == 'parked', st
    assert st['fresh'] == 'parked', '未到冷却期不该复活'
    print('  revive_parked：只复活到期 retryable，跳过 captcha/permanent')


def t_bucket():
    assert mt._bucket_of('blocked_captcha: cf challenge', '', False) == 'blocked'
    assert mt._bucket_of('blocked_captcha', '', False) == 'blocked'
    assert mt._bucket_of('paywalled: 订阅墙', '', False) == 'wall'
    assert mt._bucket_of('no_fulltext: x', '', False) == 'dead'
    # 冻结表优先级高于错误码（captcha 条目在已冻结站里仍应显示冻结的 kind）
    assert mt._bucket_of('blocked_captcha', 'broken', False) == 'broken'
    print('  _bucket_of：captcha→blocked，冻结表优先')


def t_reason_cn():
    assert '人机' in mt._reason_cn('blocked_captcha')
    # 冻结表里的中文原因要原样透出，不能被当成「未知错误码」清空
    assert mt._reason_cn('Cloudflare 人机挑战') == 'Cloudflare 人机挑战'
    print('  _reason_cn：中文原因透传')


def t_cooldown():
    # 第 1 次 captcha 用 30 分钟短退避（cooldown_sec(1)），不是 2 小时
    assert hq.cooldown_sec(1) == 1800
    print('  首次 captcha 退避 = %d 分钟' % (hq.cooldown_sec(1) // 60))


def t_quota():
    # 配额冷却必须落在次日 00:05 附近，不能是 12h 固定退避
    cd = hq.secs_until_tomorrow(300)
    assert 60 <= cd <= 24 * 3600 + 300, cd
    print('  quota 冷却对齐次日 00:05：%.1f 小时' % (cd / 3600.0))


def t_apply_failure():
    """apply_failure 是失败记账的唯一实现，两台（调度器/单轮）共同调用。

    2026-10-27 踩坑：这段规则原本写了两份，改一处另一处不生效 ——
    实测「人机挑战 2 次转人工」「配额对齐次日」在生产里都是死代码。
    """
    def fresh(**kw):
        d = {'id': 't', 'title': 't', 'url': 'u', 'journal': 'J',
             'date': '2026', 'status': 'pending'}
        d.update(kw)
        return d

    # ① 人机挑战第 1 次：30 分钟短退避，不转人工
    it = fresh()
    v = hq.apply_failure(it, 'blocked_captcha', 'CF challenge')
    assert v['cd'] == 1800 and not v['parked'], v
    assert it['last_error'] == 'blocked_captcha: CF challenge', it['last_error']

    # ② 第 2 次：转人工（park_kind=captcha，清空 next_try_at）
    v = hq.apply_failure(it, 'blocked_captcha', 'CF challenge')
    assert v['newly_parked'] and it['park_kind'] == 'captcha', it
    assert it['next_try_at'] == '', it['next_try_at']

    # ③ 配额用尽：对齐次日，不是 12h 曲线
    it2 = fresh(soft_fails=4)
    v = hq.apply_failure(it2, 'quota_exhausted', 'daily quota exhausted (25/25)')
    assert v['cd'] < 24 * 3600, v
    assert abs(v['cd'] - hq.secs_until_tomorrow(300)) < 120, v

    # ④ 订阅墙：硬失败 3 次停放 permanent
    it3 = fresh()
    for _ in range(3):
        v = hq.apply_failure(it3, 'paywalled', 'no access')
    assert v['parked'] and it3['park_kind'] == 'permanent', it3

    # ⑤ 普通网络错误仍走退避曲线
    it4 = fresh()
    v = hq.apply_failure(it4, 'timeout', 'read timeout')
    assert v['cd'] == hq.cooldown_sec(1), v
    print('  apply_failure 五种分支（captcha×2 / quota / permanent / retryable）全部正确')


if __name__ == '__main__':
    for fn in (t_const, t_classify, t_revive, t_bucket, t_reason_cn,
               t_cooldown, t_quota, t_apply_failure):
        fn()
    print('全部通过')
