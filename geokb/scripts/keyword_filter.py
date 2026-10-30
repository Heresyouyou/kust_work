# -*- coding: utf-8 -*-
"""
关键词规则筛选：判断期刊/新闻条目是否与团队研究方向相关。
不调用 LLM，纯代码规则，稳定可控。
"""
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PROFILE = json.load(open(BASE / "config" / "team_profile.json", encoding="utf-8"))

# 扩展同义词/变体，提高命中
KEYWORDS = {
    "斑岩铜矿": ["porphyry copper", "porphyry cu", "porphyry molybdenum", "porphyry mo", "斑岩铜", "斑岩钼"],
    "矽卡岩矿床": ["skarn", "矽卡岩", "skarn deposit", "skarn tungsten", "skarn cu", "skarn tin"],
    "钨矿": ["tungsten", "钨矿", "钨", "wolframite", "scheelite", "白钨矿", "黑钨矿"],
    "锡矿": ["tin", "锡矿", "cassiterite", "锡石"],
    "钼矿": ["molybdenum", "钼矿", "钼", "molybdenite", "辉钼矿"],
    "铜矿": ["copper deposit", "铜矿", "黄铜矿", "chalcopyrite"],
    "锂矿": ["lithium", "锂", "锂矿", "lithium brine", "pegmatite lithium"],
    "稀土": ["ree", "rare earth", "稀土", "rare element"],
    "关键矿产": ["critical mineral", "critical metal", "关键矿产", "critical raw material"],
    "成矿流体": ["fluid inclusion", "hydrothermal fluid", "ore-forming fluid", "成矿流体", "流体包裹体", "magmatic-hydrothermal"],
    "成矿作用": ["mineralization", "mineralisation", "成矿作用", "成矿", "metallogenesis"],
    "矿床模型": ["deposit model", "mineral deposit model", "矿床模型", "metallogenic model"],
    "找矿预测": ["mineral exploration", "prospectivity", "找矿", "找矿预测", "resource assessment"],
    "同位素": ["isotope", "同位素", "s isotope", "pb isotope", "hf isotope", "os isotope"],
    "华南": ["south china", "华南", "yangtze", "cathaysia"],
    "三江": ["sanjiang", "三江", "yunnan", "tibet", " Tibetan ", "青藏高原"],
    "显微鉴定": ["microscopy", "microscopic identification", "镜下鉴定", "干涉色", "mineral identification"],
}

THRESHOLD = PROFILE.get("inclusion_threshold", 5)


def _norm(text):
    if not text:
        return ""
    return re.sub(r"[^\u4e00-\u9fa5a-z0-9]", " ", str(text).lower())


def score(text):
    """返回 (总分, 命中关键词列表)。按词边界匹配，避免 tin 命中 platinum。"""
    if not text:
        return 0, []
    norm = _norm(text)
    tokens = set(norm.split())
    total = 0
    hits = []
    for concept, variants in KEYWORDS.items():
        matched = False
        for v in variants:
            key = v.lower()
            # 中文直接子串；英文按空格分词或完整子串（长度>4 的英文不命中短子串）
            if any('\u4e00' <= c <= '\u9fff' for c in key):
                if key in norm:
                    matched = True
                    break
            else:
                # 英文：按词边界（空格分词）或作为独立子串出现
                if key in tokens:
                    matched = True
                    break
                if re.search(r'(?:^|\s)' + re.escape(key) + r'(?:\s|$|[,;.])', norm):
                    matched = True
                    break
        if matched:
            total += 1
            hits.append(concept)
    return total, hits


def is_relevant(title, summary="", source="", threshold=None):
    threshold = threshold or THRESHOLD
    text = f"{title} {summary} {source}"
    s, hits = score(text)
    return s >= threshold, s, hits


if __name__ == "__main__":
    tests = [
        "Porphyry copper mineralization in the Jinhe belt, South China",
        "Sunshuite, FeBi2S4, a new member of the pavonite homologous series",
        "格陵兰Tanbreez重稀土项目采矿计划获批",
    ]
    for t in tests:
        ok, s, hits = is_relevant(t)
        print(f"{ok} [{s}] {hits} | {t}")
