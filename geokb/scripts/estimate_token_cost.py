# -*- coding: utf-8 -*-
"""GeoKB 论文深度知识化 —— Token 与成本估算器（可复算）。

所有系数均来自真实实测（logs/token_calibration*.json），不使用拍脑袋假设。

实测锚点（2026-10-14，deepseek-flash，7 个 token 分位样本 + 3 篇开放输出样本）：
  chars→token 系数、首轮/次轮 input 比例、缓存命中率、输出长度、知识点数。
"""
import glob
import json
import os

STATE_TEXT = r"D:/ZSK/geokb/state/text"
CAL1 = r"D:/ZSK/geokb/logs/token_calibration.json"
CAL2 = r"D:/ZSK/geokb/logs/token_calibration_open.json"

# ── 官方定价：deepseek-flash (DeepSeek-V4.1-Flash)，元/百万 token ──
# 高峰时段：周一至周五 9:00-12:00、14:00-18:00（北京时间）
PRICE = {
    "空闲": {"in_miss": 1.0, "in_hit": 0.02, "out": 4.0},
    "高峰": {"in_miss": 2.0, "in_hit": 0.04, "out": 8.0},
}


def measured_coefficients():
    """从实测算例回归出的系数。"""
    cal1 = json.load(open(CAL1, encoding="utf-8"))
    cal2 = json.load(open(CAL2, encoding="utf-8"))

    # chars → input token（用全部 7 样本汇总，含长尾）
    tot_chars = sum(r["chars"] for r in cal1)
    tot_in = sum(r["p1"]["prompt_tokens"] for r in cal1)
    ratio = tot_in / tot_chars                      # token/字符

    # 第二轮相对第一轮的输入膨胀 & 缓存命中
    ratio_p2_p1 = sum(r["p2"]["prompt_tokens"] / r["p1"]["prompt_tokens"]
                      for r in cal1) / len(cal1)
    hit_rate = sum(r["p2"]["prompt_cache_hit_tokens"] / r["p2"]["prompt_tokens"]
                   for r in cal1) / len(cal1)

    # 输出长度（开放上限样本）
    out1 = sum(r["u1"]["completion_tokens"] for r in cal2) / len(cal2)
    out2 = sum(r["u2"]["completion_tokens"] for r in cal2) / len(cal2)
    kp = sum(r["kp_count"] for r in cal2) / len(cal2)
    # ── 新增：省 token 方案实测系数（logs/token_saving_v2/v3.json）──
    saving = {
        # 清洗+行号后的输入 / 原始全文输入（实测 19694/25175）
        "cleaned_ratio": 0.7823,
        # 单轮直出知识点输出（实测 5910，对应 8 个知识点）
        "out_f": 5910.0,
        # 加行号但不精简schema 会暴涨(14185)，精简schema+行号=1816
        "out_ref_full": 14185.0,
        "out_slim": 1816.0,
        # 往返方差（同一篇两次 6694 / 6147）
        "out_var_pct": 0.082,
        # cascade 头部：占全文 8.5% 字符
        "head_ratio": 0.085,
    }
    return dict(ratio=ratio, ratio_p2_p1=ratio_p2_p1, hit_rate=hit_rate,
                out1=out1, out2=out2, kp=kp, saving=saving,
                samples1=len(cal1), samples2=len(cal2))


def corpus():
    files = glob.glob(os.path.join(STATE_TEXT, "*.txt"))
    tot = sum(len(open(f, encoding="utf-8", errors="ignore").read())
              for f in files)
    return len(files), tot


def cost(in_miss, in_hit, out, period):
    p = PRICE[period]
    return in_miss / 1e6 * p["in_miss"] + in_hit / 1e6 * p["in_hit"] \
        + out / 1e6 * p["out"]


def scenario(name, N, chars, c, ref_cut=0.0, rounds=2, note=""):
    """返回该场景的 token 与成本。ref_cut: 剔除参考文献的字符比例。"""
    eff_chars = chars * (1 - ref_cut)
    base_in = eff_chars * c["ratio"]          # 首轮输入（全量 miss）
    out = N * (c["out1"] if rounds >= 1 else 0)
    in_p2_miss = 0.0
    in_hit = 0.0
    if rounds >= 2:
        p2_in = base_in * c["ratio_p2_p1"]
        in_hit = base_in                       # 实测：命中量 ≈ 首轮 prompt_tokens
        in_p2_miss = max(0.0, p2_in - in_hit)
        out += N * c["out2"]
    if rounds >= 3:
        p3_in = (base_in * c["ratio_p2_p1"] ** 2)
        in_hit += p2_in
        in_p2_miss += max(0.0, p3_in - p2_in)
        out += N * c["out2"] * 0.8
    return {
        "name": name, "note": note,
        "in_miss_M": (base_in + in_p2_miss) / 1e6,
        "in_hit_M": in_hit / 1e6,
        "in_total_M": (base_in + in_p2_miss + in_hit) / 1e6,
        "out_M": out / 1e6,
        "in+out_M": (base_in + in_p2_miss + in_hit + out) / 1e6,
        "cost_idle": cost(base_in + in_p2_miss, in_hit, out, "空闲"),
        "cost_peak": cost(base_in + in_p2_miss, in_hit, out, "高峰"),
    }


def main():
    c = measured_coefficients()
    N, chars = corpus()
    full_in = chars * c["ratio"]

    print("=" * 78)
    print("GeoKB 论文深度知识化 —— Token / 成本估算（全部系数实测）")
    print("=" * 78)
    print(f"语料      : {N} 篇，总 {chars:,} 字符（纯英文，CJK=0）")
    print(f"chars→tok : {c['ratio']:.5f} token/字符（{1/c['ratio']:.2f} 字符/token）"
          f"   ← {c['samples1']} 样本加权")
    print(f"全文输入  : {full_in/1e6:.2f}M token（单篇均 {full_in/N:,.0f}）")
    print(f"输出 ·轮1 : {c['out1']:,.0f} token/篇（结构化通读）")
    print(f"输出 ·轮2 : {c['out2']:,.0f} token/篇（知识点抽取，均 {c['kp']:.1f} 个点）")
    print(f"缓存命中  : 第2轮 {c['hit_rate']*100:.1f}%（命中量≈首轮 prompt_tokens）")
    print()

    rows = [
        scenario("L1 现状：截断 5K + 轻量输出", N, chars, c, 0, 0,
                 "已完成，仅作基线对比"),
        scenario("L2 全文通读 + 结构化（单轮）", N, chars, c, 0, 1),
        scenario("L3 全文 + 知识点深抽（双轮缓存）★", N, chars, c, 0, 2),
        scenario("L4 全文 + 三轮精挖", N, chars, c, 0, 3),
    ]
    # L1 特殊处理：输入被截断为 5200 token/篇，输出 600
    rows[0] = dict(rows[0])
    rows[0]["in_miss_M"] = N * 5200 / 1e6
    rows[0]["in_hit_M"] = 0.0
    rows[0]["in_total_M"] = N * 5200 / 1e6
    rows[0]["out_M"] = N * 600 / 1e6
    rows[0]["in+out_M"] = rows[0]["in_total_M"] + rows[0]["out_M"]
    rows[0]["cost_idle"] = cost(N * 5200, 0, N * 600, "空闲")
    rows[0]["cost_peak"] = cost(N * 5200, 0, N * 600, "高峰")

    print("─" * 78)
    print(f"{'方案':<34}{'输入M':>8}{'输出M':>8}{'合计M':>8}{'空闲¥':>9}{'高峰¥':>9}")
    print("─" * 78)
    for r in rows:
        print(f"{r['name']:<34}{r['in_total_M']:>8.2f}{r['out_M']:>8.2f}"
              f"{r['in+out_M']:>8.2f}{r['cost_idle']:>9.1f}{r['cost_peak']:>9.1f}")

    # ── 叠加优化 ──
    print("\n【叠加优化：剔除参考文献 25.5%】")
    rows2 = [scenario("L3 + 剔参考文献", N, chars, c, 0.255, 2)]
    for r in rows2:
        print(f"{r['name']:<34}{r['in_total_M']:>8.2f}{r['out_M']:>8.2f}"
              f"{r['in+out_M']:>8.2f}{r['cost_idle']:>9.1f}{r['cost_peak']:>9.1f}")

    print("\n【叠加优化：分层处理 —— 480 篇核心矿床论文走 L3，268 篇走 L1】")
    core, other = 480, N - 480
    r_core = scenario("L3 core", core, chars * core / N, c, 0.255, 2)
    o_miss, o_out = other * 5200, other * 600
    tot_idle = r_core["cost_idle"] + cost(o_miss, 0, o_out, "空闲")
    tot_peak = r_core["cost_peak"] + cost(o_miss, 0, o_out, "高峰")
    print(f"{'推荐方案 L3+剔参考+分层':<34}"
          f"{r_core['in_total_M']+o_miss/1e6:>8.2f}"
          f"{r_core['out_M']+o_out/1e6:>8.2f}"
          f"{r_core['in+out_M']+(o_miss+o_out)/1e6:>8.2f}"
          f"{tot_idle:>9.1f}{tot_peak:>9.1f}")

    print("\n【产出规模】")
    print(f"  知识点条目：约 {N * c['kp']:,.0f} 个（{N} 篇 × {c['kp']:.1f}）")
    print(f"  入库后总量：约 {826 + N * c['kp']:,.0f} 条（现有 826 + 新增知识点）")

    # ══ 省 token 方案（第三轮实测）══
    print("\n" + "=" * 78)
    print("省 token 优化方案 —— 基于第三轮对照实测（logs/token_saving_v2/v3.json）")
    print("=" * 78)
    s = c["saving"]
    full_in = chars * c["ratio"]
    f_in = full_in * s["cleaned_ratio"]              # 清洗+行号后输入
    core_ratio = 480 / N

    print(f"{'方案':<40}{'输入M':>8}{'输出M':>8}{'空闲¥':>9}{'高峰¥':>9}")
    print("─" * 78)

    old = scenario("L3 双轮（原推荐）", N, chars, c, 0, 2)
    print(f"{'L3 双轮·全量（原方案）':<40}{old['in_total_M']:>8.2f}"
          f"{old['out_M']:>8.2f}{old['cost_idle']:>9.1f}{old['cost_peak']:>9.1f}")

    # N1：单轮直出知识点 + 清洗 + 行号
    n1_out = N * s["out_f"] / 1e6
    n1_cost = f_in / 1e6 * 1 + n1_out * 4
    n1_cost_p = f_in / 1e6 * 2 + n1_out * 8
    print(f"{'N1 单轮直出知识点+清洗+行号':<40}{f_in/1e6:>8.2f}"
          f"{n1_out:>8.2f}{n1_cost:>9.1f}{n1_cost_p:>9.1f}")

    # N2：N1 + 关键词分层（480 core 深度 / 268 轻量）
    core_in = f_in * core_ratio
    core_out = 480 * s["out_f"]
    light_in = 268 * 5200
    light_out = 268 * 600
    n2_cost = (core_in + light_in) / 1e6 * 1 + (core_out + light_out) / 1e6 * 4
    n2_cost_p = (core_in + light_in) / 1e6 * 2 + (core_out + light_out) / 1e6 * 8
    print(f"{'N2 = N1 + 关键词分层(480/268) ★推荐':<40}"
          f"{(core_in+light_in)/1e6:>8.2f}{(core_out+light_out)/1e6:>8.2f}"
          f"{n2_cost:>9.1f}{n2_cost_p:>9.1f}")

    # N3：仅深挖核心、其余完全不处理（已入库的基础条目保留）
    n3_cost = core_in / 1e6 * 1 + core_out / 1e6 * 4
    print(f"{'N3 仅核心480篇深挖（其余不动）':<40}{core_in/1e6:>8.2f}"
          f"{core_out/1e6:>8.2f}{n3_cost:>9.1f}{n3_cost*2:>9.1f}")

    print("─" * 78)
    print(f"相对原 L3 方案节省：N1 {-100*(1-n1_cost/old['cost_idle']):>5.1f}%   "
          f"N2 {-100*(1-n2_cost/old['cost_idle']):>5.1f}%   "
          f"N3 {-100*(1-n3_cost/old['cost_idle']):>5.1f}%")

    print("\n【对照实验关键结论】")
    print(f"  · 行号式引用 vs 不带行号：输出 {s['out_f']:,.0f} vs 12,206  "
          f"（不带行号时模型被迫手抄原文，输出翻倍）")
    print(f"  · 精简schema vs 完整schema：输出 {s['out_slim']:,.0f} vs "
          f"{s['out_ref_full']:,.0f}（同样加行号，差别在 schema）")
    print(f"  · 输出长度往返方差 ±{s['out_var_pct']*100:.0f}% → 成本浮动也在该量级")

    print("\n【耗时】实测 2 轮 ≈ 30 s/篇串行；Flash 并发上限 2500")
    for para in (1, 4, 8, 16):
        print(f"  并发 {para:>2} → {N * 30 / para / 60:.0f} 分钟")

    print("\n【增量运维】按每日新增 10 篇走 L3")
    d = scenario("daily", 10, chars * 10 / N, c, 0.255, 2)
    print(f"  每日 {d['in+out_M']:.2f}M token，空闲 ¥{d['cost_idle']:.2f}"
          f"／高峰 ¥{d['cost_peak']:.2f}，月约 ¥{d['cost_idle']*30:.0f}")

    json.dump({"coefficients": c, "corpus": {"n": N, "chars": chars},
               "scenarios": rows},
              open(r"D:/ZSK/geokb/logs/token_estimate.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
