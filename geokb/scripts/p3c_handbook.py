# -*- coding: utf-8 -*-
"""Phase 3c：12 矿种系统鉴定手册 HTML -> mineralogy Markdown 条目。
转换规则：h1/h2/h3 -> # 级标题；p/li/strong/br 转文本；table -> Markdown 表；svg -> 图占位。"""
import html as htmllib
import os
import re

SRC = r"D:/PostNews/Translate/modules/notes/assets/handbook"
OUT = r"D:/ZSK/geokb/content/mineralogy"
TODAY = "2026-10-13"

MINERALS = {
    "quartz": ("石英", "Quartz", ["硅酸盐", "架状硅酸盐", "一轴晶", "镜下鉴定"]),
    "plagioclase": ("斜长石", "Plagioclase", ["硅酸盐", "长石", "二轴晶", "镜下鉴定"]),
    "orthoclase": ("正长石", "Orthoclase", ["硅酸盐", "长石", "二轴晶", "镜下鉴定"]),
    "olivine": ("橄榄石", "Olivine", ["硅酸盐", "岛状硅酸盐", "二轴晶", "镜下鉴定"]),
    "augite": ("普通辉石", "Augite", ["硅酸盐", "链状硅酸盐", "辉石", "镜下鉴定"]),
    "hornblende": ("普通角闪石", "Hornblende", ["硅酸盐", "链状硅酸盐", "角闪石", "镜下鉴定"]),
    "biotite": ("黑云母", "Biotite", ["硅酸盐", "层状硅酸盐", "云母", "镜下鉴定"]),
    "muscovite": ("白云母", "Muscovite", ["硅酸盐", "层状硅酸盐", "云母", "镜下鉴定"]),
    "calcite": ("方解石", "Calcite", ["碳酸盐", "一轴晶", "镜下鉴定"]),
    "garnet": ("石榴子石", "Garnet", ["硅酸盐", "岛状硅酸盐", "等轴晶系", "镜下鉴定"]),
    "fluorite": ("萤石", "Fluorite", ["卤化物", "等轴晶系", "镜下鉴定"]),
    "apatite": ("磷灰石", "Apatite", ["磷酸盐", "一轴晶", "镜下鉴定"]),
}

def strip_tags(s):
    s = re.sub(r"<br\s*/?>", " ", s)
    s = re.sub(r"</(p|div|li|tr|h\d)>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    return htmllib.unescape(s)

def conv_table(m):
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(0), re.S | re.I)
    out = []
    for i, row in enumerate(rows):
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S | re.I)
        cells = [strip_tags(c).replace("|", "\\|").replace("\n", " ").strip() for c in cells]
        if not cells:
            continue
        out.append("| " + " | ".join(cells) + " |")
        if i == 0:
            out.append("|" + "---|" * len(cells))
    return "\n" + "\n".join(out) + "\n"

def html_to_md(body):
    body = re.sub(r"<script.*?</script>", "", body, flags=re.S | re.I)
    body = re.sub(r"<style.*?</style>", "", body, flags=re.S | re.I)
    # svg 图：保留图题占位
    def fig_repl(m):
        t = re.search(r'aria-label="([^"]*)"', m.group(0))
        return f"\n> （示意/显微图：{t.group(1) if t else '略'}——见源 HTML）\n"
    body = re.sub(r"<svg.*?</svg>", fig_repl, body, flags=re.S | re.I)
    body = re.sub(r"<table.*?</table>", conv_table, body, flags=re.S | re.I)
    body = re.sub(r"<h1[^>]*>(.*?)</h1>", lambda m: f"\n# {strip_tags(m.group(1)).strip()}\n", body, flags=re.S | re.I)
    body = re.sub(r"<h2[^>]*>(.*?)</h2>", lambda m: f"\n## {strip_tags(m.group(1)).strip()}\n", body, flags=re.S | re.I)
    body = re.sub(r"<h3[^>]*>(.*?)</h3>", lambda m: f"\n### {strip_tags(m.group(1)).strip()}\n", body, flags=re.S | re.I)
    body = re.sub(r"<li[^>]*>(.*?)</li>", lambda m: f"\n- {strip_tags(m.group(1)).strip()}", body, flags=re.S | re.I)
    body = strip_tags(body)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()

FM = """---
id: min-{slug}-001
title: {zh}（{en}）镜下系统鉴定手册
module: mineralogy
tags: {tags}
summary: {zh}（{en}）的偏光显微镜下系统鉴定实操手册：按定向切面组织，含光性测定路线、干涉色、消光、轴性与光性符号判定。
confidence: B
sources:
  - name: 用户整理·岩矿镜下鉴定手册（web 门户笔记模块）
    url: local://PostNews/Translate/modules/notes/assets/handbook/{slug}-systematic-id.html
    accessed: {today}
updated: {today}
status: active
---
"""

for slug, (zh, en, tags) in MINERALS.items():
    path = os.path.join(SRC, f"{slug}-systematic-id.html")
    raw = open(path, encoding="utf-8").read()
    body = re.search(r"<body[^>]*>(.*)</body>", raw, re.S | re.I).group(1)
    md = html_to_md(body)
    tagstr = "[" + ", ".join(tags) + "]"
    with open(os.path.join(OUT, f"{slug}-microscopy-id.md"), "w", encoding="utf-8") as f:
        f.write(FM.format(slug=slug, zh=zh, en=en, tags=tagstr, today=TODAY))
        f.write("\n" + md + "\n")
    print(f"{slug}: {len(md)} chars")
print("done")
