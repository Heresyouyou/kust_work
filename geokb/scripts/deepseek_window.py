# -*- coding: utf-8 -*-
"""DeepSeek 调用时间窗自检（agent 对话侧专用，2026-10-17）

用途：动手前判断「我现在能不能调 DeepSeek API / 能不能做入库」。
     只服务 **agent 对话中我主动发起** 的操作；业务脚本一律不得引用本模块。

判据（用户 2026-10-17 17:24 澄清：是「量 × 时段」二维，不是「能不能用 API」）：

    少量 + 低谷 → ✅ 允许调 API（首选，别浪费窗口干等）
    少量 + 高峰 → ⚠️ 不调 API，改用纯本地手段完成
    大量 + 低谷 → ✅ 允许调 API
    大量 + 高峰 → ❌ 停下并告知用户，等低谷

时间窗（北京时间，边界精确）：
    允许 00:00–08:59 / 12:01–13:59 / 18:01–24:00
    禁止 09:00–11:59 / 14:00–17:59

用法：
    python deepseek_window.py                # 查当前窗口
    python deepseek_window.py --qty large    # 结合工作量判定
    python deepseek_window.py --quiet        # 仅退出码：0=允许, 2=禁止
"""
import argparse
import sys
from datetime import datetime

# (允许区间列表) —— 半开区间，精确到边界值
ALLOW = [((0, 0), (8, 59)), ((12, 1), (13, 59)), ((18, 1), (24, 0))]


def in_allow_window(t=None):
    """当前是否落在低谷窗口。"""
    now = t or datetime.now()
    m = now.hour * 60 + now.minute
    for (h0, m0), (h1, m1) in ALLOW:
        if h0 * 60 + m0 <= m <= h1 * 60 + m1:
            return True
    return False


def judge(qty='small'):
    """qty ∈ {'small', 'large'} → (allowed: bool, advice: str)"""
    now = datetime.now()
    low = in_allow_window(now)
    big = qty == 'large'
    if low and big:
        return True, '大量 + 低谷 → 允许调用 API（低谷正是为大批量准备的）'
    if low:
        return True, '少量 + 低谷 → 允许调用 API，无需为省 API 而降级为纯本地处理'
    if big:
        return False, '大量 + 高峰 → 禁止。先交付纯本地部分，待办列出，等低谷窗口再执行'
    return False, '少量 + 高峰 → 不调 API；改用纯本地手段（手写条目/补台账/非 embed 索引）完成'


def next_window(t=None):
    """下一个允许窗口的起始时间字符串。"""
    now = t or datetime.now()
    for (h0, m0), _ in ALLOW:
        cand = now.replace(hour=h0, minute=m0, second=0, microsecond=0)
        if cand > now:
            return cand.strftime('%Y-%m-%d %H:%M')
    nxt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (nxt.replace(day=min(nxt.day, 28)) + __import__('datetime').timedelta(days=1)
            ).strftime('%Y-%m-%d %H:%M')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--qty', choices=['small', 'large'], default='small')
    ap.add_argument('--quiet', action='store_true', help='只返回退出码')
    a = ap.parse_args()

    allowed, advice = judge(a.qty)
    if a.quiet:
        sys.exit(0 if allowed else 2)

    now = datetime.now()
    low = in_allow_window(now)
    print(f'当前时间   : {now:%Y-%m-%d %H:%M}（北京时间）')
    print(f'时段判定   : {"低谷窗口 ✅" if low else "高峰窗口 ⛔"}')
    print(f'工作量     : {"大量" if a.qty == "large" else "少量"}')
    print(f'可否调 API : {"允许" if allowed else "禁止"}')
    print(f'处置建议   : {advice}')
    if not allowed:
        print(f'下个低谷起点: {next_window()}')
    else:
        print('提醒       : 该调用仅限 agent 对话中的主动操作；业务链路不需查此工具。')
    sys.exit(0 if allowed else 2)


if __name__ == '__main__':
    main()
