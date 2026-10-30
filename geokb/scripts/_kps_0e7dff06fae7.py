# -*- coding: utf-8 -*-
"""手动重写 0e7dff06fae7 的知识点（零 API）：先按判据挑，再把 evidence 定位回原文行号。"""
import glob
import json
import re

RAWS = glob.glob("state/text/0e7dff06fae7*.txt")
raw = open(RAWS[0], encoding="utf-8", errors="ignore").read()
lines = raw.splitlines()


def ev(probe, span=2):
    """在原文里找 probe，返回 Lx-Ly（找不到就返回空串，不编行号）。

    PDF 抽文常把一句拆成多行，故用「去空白比对」定位，再回推行号。
    """
    key = "".join(probe.split())
    for i, ln in enumerate(lines):
        if key in "".join(ln.split()):
            j = min(i + span, len(lines) - 1)
            return "L%d-L%d" % (i + 1, j + 1)
    # 跨行：拼接 ±3 行再找
    for i in range(len(lines) - 2):
        win = "".join("".join(lines[k].split()) for k in range(i, min(i + 4, len(lines))))
        if key in win:
            return "L%d-L%d" % (i + 1, min(i + 4, len(lines)))
    return ""


kps = [
    {
        "title": "锆石Ce/Eu四指标判别斑岩体成锡或成铜钼",
        "body": ("边家大院含矿石英斑岩的锆石微量元素指标 Ce4+/Ce3+、CeN/CeN*、Ce/Nd、"
                 "EuN/EuN* 均落在“成锡侵入岩”范围内；按 Shu 等（2019）的比值—规模标定，"
                 "对应铜钼成矿潜力 ≤0.3 Mt，即成锡有利、成铜钼不利。该四指标组合可直接"
                 "套用于大兴安岭南段同类含矿斑岩体的矿种判别与潜力排序。"),
        "type": "找矿标志", "reuse": "解释借鉴",
        "why": "换一个含矿斑岩体即可用同一组锆石指标判矿种",
        "probe": "均指示石英斑岩成铜钼矿潜力≤0.3 Mt",
    },
    {
        "title": "还原性贫水岩浆（ΔFMQ-0.15、T(Ti)790℃）",
        "body": ("边家大院石英斑岩锆石记录岩浆 logf(O2) 平均 −14.5，ΔFMQ−0.15；"
                 "T(Zr)=754 ℃，T(Ti)=728~826 ℃（平均 790 ℃）；锆石 10000×(Eu/Eu*)/Y 与 "
                 "(Ce/Nd)/Y 比值指示岩浆贫水。三项参数共同限定其为还原性、高温、贫水岩浆，"
                 "可作为大兴安岭南段晚侏罗世—早白垩世锡相关岩体的对照标定值。"),
        "type": "参数条件", "reuse": "标定参照",
        "why": "同类岩体可直接对比氧逸度与结晶温度",
        "probe": "△FMQ-0.15",
    },
    {
        "title": "还原+贫水→锡富集、铜受抑的机制链",
        "body": ("还原性岩浆中 Sn2+ 比 Sn4+ 更不相容，利于锡在残余熔体中富集；"
                 "低氧逸度使 Cu2+ 与 S2− 提前结合沉淀，抑制铜的后期富集；贫水则延迟岩浆"
                 "达到水饱和出溶，使锡继续留在熔体中。三者叠加解释了边家大院“锡成矿好、"
                 "铜钼成矿差”的分异结局，可迁移到其他 Sn-Cu 共生斑岩系统的矿种偏倚判断。"),
        "type": "成因机制", "reuse": "解释借鉴",
        "why": "解释为何同一岩体只成锡不成铜，可套用到同类矿床",
        "probe": "由于Sn2+在花岗岩岩浆中较Sn4+",
    },
    {
        "title": "εHf(t)与TDM2指示年轻地壳源区",
        "body": ("石英斑岩 εHf(t)=−0.86~5.99（按 t=(136.9±0.8) Ma 计算），平均 1.51，"
                 "TDM2=809~1240 Ma，fLu/Hf 平均 −0.95；指示源区为中元古代—新元古代"
                 "年轻地壳部分熔融，而非古老基底重熔。该组参数可作为大兴安岭南段锡多金属"
                 "岩体源区性质的对比基准。"),
        "type": "定量结果", "reuse": "标定参照",
        "why": "判断一个岩体是新生地壳还是古老基底重熔的对照值",
        "probe": "εHf(t)值为-0.86~5.99",
    },
    {
        "title": "低Zr/Hf、Nb/Ta＋高Rb/Sr识别高分异花岗岩",
        "body": ("石英斑岩为准铝质—弱过铝质（A/CNK 0.93~1.14）、高钾钙碱性；"
                 "ΣREE 236.46×10⁻⁶~323.74×10⁻⁶，LREE/HREE 6.92~9.72，Eu/Eu*=0.10~0.15，"
                 "(La/Yb)N 6.45~11.6；配合较低 Zr/Hf、Nb/Ta 与较高 Rb/Sr，指示岩浆经历"
                 "强烈分离结晶。这套判别组合适用于识别与锡成矿相关的壳源高分异岩体。"),
        "type": "找矿标志", "reuse": "解释借鉴",
        "why": "用主微量组合快速判定岩体是否高分异（成锡前提）",
        "probe": "较低的Zr/Hf和Nb/Ta比值",
    },
    {
        "title": "西矿区斑岩型Sn-Cu-Mo格架与蚀变分带",
        "body": ("边家大院西矿区石英斑岩长 1500 m、宽 270~750 m，隐伏于隐爆角砾岩之下，"
                 "Sn-Cu-Mo 矿化产于岩体上部、边部及内外接触带，呈脉状、细脉状与浸染状；"
                 "蚀变自核部向外为钾化→绢云母化→青磐岩化，与典型斑岩铜钼矿床一致。"
                 "已探明锡矿石量 9170 t（品位 0.33%）、铜 8825 t（0.996%）、钼 589 t（0.107%）。"
                 "该“岩体形态＋蚀变分带＋品位量级”组合可作为区域斑岩型锡矿的类比模板。"),
        "type": "区域实例", "reuse": "类比参照",
        "why": "给区域同类斑岩锡矿提供形态、蚀变与品位的类比模板",
        "probe": "目前累计锡矿石量9170 t",
    },
]

for k in kps:
    k["evidence"] = ev(k.pop("probe"))
    k["tags"] = ["锡多金属", "斑岩型", "大兴安岭南段", "锆石微量元素"]

json.dump({"kps": kps}, open("state/agent_ingest_wip/kps-0e7dff06fae7.json", "w",
                             encoding="utf-8"), ensure_ascii=False, indent=1)
print("写出", len(kps), "条；evidence：", [k["evidence"] for k in kps])
