# -*- coding: utf-8 -*-
"""Phase 3a：创建 content/ 模块树与 _module.yaml。"""
import os

BASE = r"D:/ZSK/geokb/content"
MODULES = [
    ("mineralogy",       "矿物学",            1, "晶体化学、矿物分类、鉴定特征（含镜下系统鉴定手册）"),
    ("petrology",        "岩石学",            2, "火成岩、沉积岩、变质岩的分类、成因与相"),
    ("structural",       "构造地质学",        3, "褶皱、断裂、节理、应变与构造解析"),
    ("geochemistry",     "地球化学",          4, "元素地球化学、同位素、分析方法与数据解释"),
    ("economic-geology", "矿床学·经济地质",   5, "矿床成因类型、典型矿床、勘查与矿床模型"),
    ("geophysics",       "地球物理",          6, "重力、磁法、电法、地震方法原理与解释"),
    ("geochronology",    "地质年代学",        7, "定年方法、国际年代地层表、地质时间"),
    ("field-methods",    "野外地质方法",      8, "填图、采样、编录、野外安全"),
    ("glossary",         "术语词典",          9, "中英文对照术语与同义词映射（跨模块共享）"),
    ("data-sheets",      "数据速查",         10, "物理常数、矿物物性表、矿种产储量速查"),
]

for slug, zh, order, desc in MODULES:
    d = os.path.join(BASE, slug)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "_module.yaml"), "w", encoding="utf-8") as f:
        f.write(f"slug: {slug}\nname: {zh}\norder: {order}\ndescription: {desc}\n")
    print("created", d)
print("done")
