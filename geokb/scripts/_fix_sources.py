# -*- coding: utf-8 -*-
"""全库引用源公网化修复（2026-10-15）。

四类修复：
  A. papers/kpoints 共 5163 条：sources 只有内部 path → 补公网 url
     （https://sjck.kust.work/api/entry/<id>/pdf，PDF 原文公网可下载）。
  B. mineralogy 12 条：local:// 门户笔记 → https://kust.work/api/notes/assets/handbook/<file>
     （门户 notes 模块公网实测可达）。
  C. glossary 16 条：local:// glossary_verified.json → 四个真实公开来源
     （Earle CC BY / Huston CC BY / USGS B1693 / USGS B2012，均实测可达）。
  D. economic-geology/papers-corpus 1 条：local:// 本地语料 → GeoKB papers 模块公网入口，
     正文补公开访问说明。
  E. feed-521d5b18063d（引用已撤稿论文）：正文补撤稿警示与撤稿通知链接。

安全措施：先整目录备份 content/ → state/backup_content_20261015/；
frontmatter 用 YAML 往返（width 极大防止折叠破坏空格），正文原样保留。
"""
import glob
import os
import re
import shutil
import sys
from collections import Counter

import yaml

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT = os.path.join(BASE, "content")
BACKUP = os.path.join(BASE, "state", "backup_content_20261015")
TODAY = "2026-10-15"

stats = Counter()
errors = []


def split_fm(text):
    """拆出 (frontmatter 字符串, 正文)。没有 frontmatter 返回 (None, 原文)。"""
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def load_entry(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    fm_s, body = split_fm(text)
    if fm_s is None:
        return None, body, text
    try:
        fm = yaml.safe_load(fm_s)
    except Exception as e:
        return None, body, text
    if not isinstance(fm, dict):
        return None, body, text
    return fm, body, text


def save_entry(path, fm, body):
    fm_s = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False,
                          default_flow_style=False, width=100000)
    out = "---\n" + fm_s + "---\n" + body
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)


def backup_content():
    if os.path.exists(BACKUP):
        print(f"备份已存在，跳过: {BACKUP}")
        return
    print(f"备份 content/ -> {BACKUP} ...")
    shutil.copytree(CONTENT, BACKUP)
    n = sum(len(fs) for _, _, fs in os.walk(BACKUP))
    print(f"备份完成：{n} 个文件")


def fix_a_internal_pdfs():
    """papers/kpoints：path → 补公网 url。"""
    n = 0
    for module in ("papers", "kpoints"):
        for path in sorted(glob.glob(os.path.join(CONTENT, module, "*.md"))):
            fm, body, raw = load_entry(path)
            if fm is None:
                errors.append(f"A:{os.path.basename(path)} frontmatter 解析失败")
                continue
            eid = fm.get("id") or os.path.splitext(os.path.basename(path))[0]
            srcs = fm.get("sources") or []
            changed = False
            for s in srcs:
                if isinstance(s, dict) and s.get("path") and not s.get("url"):
                    s["url"] = "https://sjck.kust.work/api/entry/%s/pdf" % eid
                    s["accessed"] = TODAY
                    changed = True
            if changed:
                save_entry(path, fm, body)
                n += 1
    stats["A_internal_pdf_fixed"] = n


def fix_b_mineralogy():
    """mineralogy：local:// 门户笔记 → 公网地址。"""
    n = 0
    for path in sorted(glob.glob(os.path.join(CONTENT, "mineralogy", "*.md"))):
        fm, body, raw = load_entry(path)
        if fm is None:
            errors.append(f"B:{os.path.basename(path)} frontmatter 解析失败")
            continue
        changed = False
        for s in (fm.get("sources") or []):
            if not isinstance(s, dict):
                continue
            u = str(s.get("url") or "")
            if u.startswith("local://PostNews/Translate/modules/notes/assets/"):
                fn = u.rsplit("/", 1)[-1]
                s["url"] = "https://kust.work/api/notes/assets/handbook/%s" % fn
                s["accessed"] = TODAY
                changed = True
        if changed:
            save_entry(path, fm, body)
            n += 1
    stats["B_mineralogy_fixed"] = n


GLOSS_SOURCES = [
    {"name": "Earle, S. Physical Geology, 2nd ed.（BCcampus 开放教材，CC BY 4.0）",
     "url": "https://opentextbc.ca/geology/", "accessed": TODAY},
    {"name": "Huston, D. & Gutzmer, J. (eds.) Isotopes in Economic Geology, "
             "Metallogenesis and Exploration（Springer 开放获取，CC BY 4.0）",
     "url": "https://link.springer.com/content/pdf/10.1007/978-3-031-27897-6.pdf",
     "accessed": TODAY},
    {"name": "USGS Bulletin 1693: Mineral Deposit Models（Cox & Singer 编，美国联邦出版物·公共领域）",
     "url": "https://dggs.alaska.gov/webpubs/usgs/b/text/b1693.pdf", "accessed": TODAY},
    {"name": "USGS Bulletin 2012（美国联邦出版物·公共领域）",
     "url": "https://pubs.usgs.gov/bul/2012/report.pdf", "accessed": TODAY},
]


def fix_c_glossary():
    """glossary：local:// glossary_verified.json → 四个真实公开来源。"""
    n = 0
    for path in sorted(glob.glob(os.path.join(CONTENT, "glossary", "*.md"))):
        fm, body, raw = load_entry(path)
        if fm is None:
            errors.append(f"C:{os.path.basename(path)} frontmatter 解析失败")
            continue
        srcs = fm.get("sources") or []
        if any(isinstance(s, dict) and str(s.get("url") or "").startswith("local://")
               for s in srcs):
            fm["sources"] = [dict(s) for s in GLOSS_SOURCES]
            save_entry(path, fm, body)
            n += 1
    stats["C_glossary_fixed"] = n


def fix_d_papers_corpus():
    """papers-corpus：本地语料声明 → GeoKB papers 模块公网入口 + 正文说明。"""
    path = os.path.join(CONTENT, "economic-geology", "papers-corpus.md")
    if not os.path.exists(path):
        errors.append("D:papers-corpus.md 不存在")
        return
    fm, body, raw = load_entry(path)
    if fm is None:
        errors.append("D:papers-corpus.md frontmatter 解析失败")
        return
    fm["sources"] = [{
        "name": "GeoKB papers 模块（公网可检索，748 篇论文 PDF 原文可下载）",
        "url": "https://sjck.kust.work/api/module/papers",
        "accessed": TODAY,
    }]
    note = ("\n## 公开访问说明（2026-10-15）\n\n"
            "- 本语料库的题录与论文全文已通过 GeoKB 知识库公开："
            "检索入口 `https://sjck.kust.work/api/search?q=<关键词>&format=full`，"
            "PDF 原文下载 `https://sjck.kust.work/api/entry/<id>/pdf`。\n"
            "- 原始 819 篇本地 PDF 工作集（sjiang_porphyry_CuMo_2026）为内部整理语料，"
            "不另行公开；引用请以上述公网条目为准。\n")
    if "公开访问说明" not in body:
        body = body.rstrip("\n") + "\n" + note
    save_entry(path, fm, body)
    stats["D_papers_corpus_fixed"] = 1


RETRACT_WARNING = (
    "> ⚠️ **撤稿警示（2026-10-15 核查）**：本文（American Mineralogist, "
    "DOI 10.2138/am-2025-9723）已被期刊撤稿——原文存在重大错误，"
    "仅以 Early Publication 形式发布、未进入正式期卷。"
    "撤稿通知：<https://doi.org/10.2138/am-2026-e1111017>。"
    "**请勿将本文数据或结论作为证据引用。**\n\n")


def fix_e_retracted():
    """引用已撤稿论文的条目：正文补撤稿警示。"""
    path = os.path.join(CONTENT, "economic-geology", "feed-521d5b18063d.md")
    if not os.path.exists(path):
        errors.append("E:feed-521d5b18063d.md 不存在")
        return
    fm, body, raw = load_entry(path)
    if fm is None:
        errors.append("E:frontmatter 解析失败")
        return
    if "撤稿警示" not in body:
        body = RETRACT_WARNING + body
    save_entry(path, fm, body)
    stats["E_retracted_warned"] = 1


def main():
    backup_content()
    fix_a_internal_pdfs()
    fix_b_mineralogy()
    fix_c_glossary()
    fix_d_papers_corpus()
    fix_e_retracted()
    print("\n===== 修复统计 =====")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")
    if errors:
        print("\n===== 错误 =====")
        for e in errors:
            print(" ", e)
        sys.exit(1)
    print("\n全部完成，无错误。")


if __name__ == "__main__":
    main()
