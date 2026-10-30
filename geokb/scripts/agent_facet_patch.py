# -*- coding: utf-8 -*-
"""
P2 批量补受控词（Agent 亲自入库 · 离线）

背景：P2 条目有条目、有知识点、有索引，但 `facets` 为空。原因是其 tags 多为
英文自由词，与 config/taxonomy.yaml 的中文受控词表无法命中，`build_facet_index.py`
跑完返回「frontmatter 更新 0」。

本工具由 Agent 设计映射规则（中英文关键词 → taxonomy 规范词），批量把规范词
prepend 到条目 tags，再交给 build_facet_index.py 归一回写 facets。
**全程零 LLM 调用**——规则即 Agent 的判断。

用法：
    python agent_facet_patch.py --bucket P2 --dry-run      # 输出候选，不落盘
    python agent_facet_patch.py --bucket P2 --apply        # 落盘（改 tags）
    python agent_facet_patch.py --sha <sha12> --dry-run    # 单篇

输出格式（dry-run）：sha12 | 期刊 | 标题(截断) | 新增规范词
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, OrderedDict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))

from pathlib import Path
_NO_WIN = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW：子孙全继承隐藏控制台
PAPERS_DIR = Path(BASE) / "content" / "papers"   # 与 agent_ingest.py 保持一致
QUEUE = os.path.join(BASE, "state", "agent_ingest_queue.jsonl")
MAX_TAGS = 12

# ──────────────────────────────────────────────────────────────────────
# Agent 设计的「中英文关键词 → taxonomy 规范词」映射
# 每条规则：(规范词, 正则, 命中所需最小上下文？)
# 说明：正则对 title + tags + key_content 的小写文本匹配；中文词直接子串匹配。
# ──────────────────────────────────────────────────────────────────────
RULES = [
    # ---- deposit_type ----
    ("斑岩铜矿", r"porphyry copper|porphyry cu|porphyry-cu|斑岩铜|porphyry cu-au|porphyry cu-mo"),
    ("斑岩钼矿", r"porphyry molybdenum|porphyry mo\b"),
    ("矽卡岩", r"\bskarn\b|矽卡岩"),
    ("浅成低温热液", r"epithermal|浅成低温"),
    ("造山型金矿", r"orogenic gold|造山型金"),
    ("VMS", r"\bvms\b|volcanogenic massive sulfide|volcanic-hosted massive"),
    ("SEDEX", r"\bsedex\b|sedimentary exhalative"),
    ("IOCG", r"\biocg\b|iron oxide copper"),
    ("MVT", r"\bmvt\b|mississippi valley"),
    ("伟晶岩", r"pegmatite|伟晶岩"),
    ("岩浆镍铜矿", r"magmatic ni-cu|nickel-copper sulfide|岩浆镍铜"),
    ("热液脉型", r"hydrothermal vein|quartz vein(?:-type|\s+type)|热液脉|vein-type deposit"),
    ("沉积型矿床", r"sediment-hosted|sedimentary rock-hosted|stratiform|沉积岩容矿|层状铜"),
    ("红土镍矿", r"laterite|lateritic|红土"),
    ("砂岩铀矿", r"sandstone uranium|uranium deposit|roll-front|\buranium\b"),
    ("油气藏", r"\boil and gas\b|hydrocarbon reservoir|shale gas|油气|source rock"),
    ("煤矿", r"\bcoal\b|coal-bearing"),

    # ---- process ----
    ("岩浆-热液演化", r"magmatic-hydrothermal|岩浆-热液|magmatic to hydrothermal"),
    ("流体包裹体", r"fluid inclusion|包裹体"),
    ("蚀变分带", r"alteration|altered|蚀变"),
    ("成矿物质来源", r"metal source|source of (?:the )?(?:metal|ore)|provenance|ore-forming material|物质来源|ore source"),
    ("成矿时代", r"mineralization age|ore-forming age|geochronolog|成矿时代|成矿年代"),
    ("构造控制", r"\bfault\b|\bshear zone\b|structural control|tectonic control|构造控矿|构造控制|deformation"),
    ("岩浆起源", r"petrogenesis|magma genesis|岩浆成因|岩石成因|magma source|magmatic evolution"),
    ("变质作用", r"metamorph|变质"),
    ("表生作用", r"supergene|weathering|oxidation zone|表生|风化"),
    ("沉积成岩作用", r"diagenesis|diagenetic|成岩"),
    ("地幔过程", r"\bmantle\b|地幔|lithospheric|crust-mantle"),
    ("矿床保存", r"preservation|exhumation|preserved|保存|剥露"),

    # ---- method ----
    ("锆石U-Pb", r"u-pb|zircon.*?dating|zircon geochronolog|shrimp|cameka|锆石.{0,6}定年"),
    ("Re-Os", r"re-os|rhenium-osmium|辉钼矿.{0,4}re-os"),
    ("Ar-Ar", r"ar-ar|40ar/39ar|40ar-39ar|氩氩"),
    ("Lu-Hf", r"lu-hf|hf isotope|锆石hf"),
    ("Sr-Nd同位素", r"sr-nd|sm-nd|nd isotope|sr isotope|锶钕|sm-nd"),
    ("S同位素", r"sulfur isotope|sulphur isotope|δ34s|δ ?34s|硫同位素"),
    ("Pb同位素", r"lead isotope|pb isotope|铅同位素"),
    ("Li同位素", r"lithium isotope|li isotope|锂同位素"),
    ("微量元素", r"trace element|微量元素|in-situ trace"),
    ("稀土元素", r"rare earth|\bree\b|rare-earth element|稀土"),
    ("LA-ICP-MS", r"la-icp-ms|laser ablation|激光剥蚀"),
    ("电子探针", r"\bepma\b|electron microprobe|electron probe|电子探针"),
    ("地球物理勘探", r"geophysical|magnetotelluric|gravity (?:survey|anomaly)|magnetic survey|seismic|物探|激发极化"),
    ("地球化学勘探", r"geochemical exploration|stream sediment|soil geochem|化探|水系沉积物"),
    ("遥感", r"remote sensing|hyperspectral|satellite imagery|遥感|高光谱"),
    ("机器学习", r"machine learning|deep learning|random forest|neural network|artificial intelligence|机器学习|深度学习"),
    ("数值模拟", r"numerical (?:simulation|model)|thermodynamic model|geodynamic model|numerical model|数值模拟|模拟|modeling"),
    ("实验岩石学", r"experimental petrolog|high-pressure experiment|high temperature.*experiment|高温高压实验|实验岩石学"),

    # ---- region（仅在明确出现地名时命中，避免误标）----
    ("青藏高原", r"tibet|tibetan plateau|qinghai-tibet|青藏高原"),
    ("冈底斯", r"gangdese|冈底斯"),
    ("喜马拉雅", r"himalaya|喜马拉雅"),
    ("秦岭", r"qinling|秦岭"),
    ("中亚造山带", r"central asian orogenic|altai|中亚造山"),
    ("天山", r"tianshan|tian shan|天山"),
    ("长江中下游", r"middle-lower yangtze|yangtze river|长江中下游"),
    ("华北克拉通", r"north china craton|华北克拉通|华北地块"),
    ("扬子克拉通", r"yangtze craton|扬子克拉通|扬子地块"),
    ("柴达木", r"qaidam|柴达木"),
    ("哀牢山", r"ailaoshan|哀牢山"),
    ("安第斯", r"\bandes\b|andean|chile|peru|argentina|bolivia|ecuador|安第斯"),
    ("特提斯", r"tethys|tethyan|特提斯"),
    ("环太平洋", r"circum-pacific|pacific rim|环太平洋|philippines|indonesia|japan|new guinea"),
    ("东非裂谷", r"east african rift|东非裂谷"),
    ("澳大利亚", r"australia|australian|澳大利亚"),
    ("北美", r"north america|united states|\busa\b|canada|nevada|arizona|alaska|北美"),
    ("欧洲", r"europe|european|spain|portugal|alps|carpathian|欧洲"),
]

# commodity：**只用全名匹配**。
# ⚠️ 元素符号（Cu/Au/Pb/U…）不再由此脚本判定——小写化后 `\bu\b`、`\bco\b` 之类会
#    大面积误命中（实测 173 篇里几乎每篇都被标上 Pb、U）。
#    符号匹配交给 build_facet_index.py 内置的词边界逻辑（_commodity_hit，已按同位素/
#    年代学记号排除），它对 Cu/Au/Ag 等主力矿种表现可靠。
COMMODITY = [
    ("Cu", r"copper"),
    ("Mo", r"molybdenum"),
    ("Au", r"\bgold\b"),
    ("Ag", r"\bsilver\b"),
    ("Zn", r"\bzinc\b"),
    ("Pb", r"\blead\b"),
    ("Fe", r"\biron\b"),
    ("W", r"tungsten|scheelite|wolframite"),
    ("Sn", r"\btin\b|cassiterite"),
    ("Li", r"lithium|spodumene|lepidolite"),
    ("REE", r"rare earth|\bree\b"),
    ("Nb", r"niobium|pyrochlore"),
    ("Ta", r"tantalum"),
    ("U", r"uranium"),
    ("Co", r"cobalt"),
    ("Ni", r"\bnickel\b"),
    ("Sb", r"antimony|stibnite"),
    ("He", r"\bhelium\b"),
    ("油气", r"\bhydrocarbon\b|oil and gas|shale gas"),
]

# 元素符号匹配时的例外：这些词里的 "Co/Ni/Sn/..." 是误命中
ELEMENT_GUARD = re.compile(
    r"\bco(?:ncentration|ntent|mposition|mplex|ntinent|rrelation|efficient|ndition|"
    r"mparison|nclusion|ntrol|mponent|uple)\w*", re.I)


def commodity_hit(sym, hay_raw):
    """元素符号：词边界 + 排除同位素/年代学记号（U-Pb、Lu-Hf 等）。"""
    for m in re.finditer(r"(?<![A-Za-z0-9])" + sym + r"(?![A-Za-z0-9])", hay_raw):
        prev = hay_raw[m.start() - 1] if m.start() > 0 else ""
        nxt = hay_raw[m.end()] if m.end() < len(hay_raw) else ""
        if prev in "-/–—":
            continue
        if nxt in "-/–—" and m.end() + 1 < len(hay_raw) and hay_raw[m.end() + 1].isalpha():
            continue
        return True
    return False


def load_queue(bucket=None, sha=None):
    rows = [json.loads(l) for l in open(QUEUE, encoding="utf-8") if l.strip()]
    if sha:
        return [r for r in rows if r["sha12"] == sha]
    if bucket:
        return [r for r in rows if r.get("bucket") == bucket]
    return rows


def read_entry(sha12):
    p = PAPERS_DIR / ("paper-%s.md" % sha12)
    if not p.exists():
        return None, None
    t = p.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n(.*)", t, re.S)
    if not m:
        return None, None
    import yaml
    fm = yaml.safe_load(m.group(1)) or {}
    body = m.group(2)
    kc = ""
    mm = re.search(r"## Key Content\n\n(.*?)(?=\n\n## |\Z)", body, re.S)
    if mm:
        kc = mm.group(1)
    else:
        mm = re.search(r"## 关键内容\n\n(.*?)(?=\n\n## |\Z)", body, re.S)
        kc = mm.group(1) if mm else ""
    return fm, kc


def suggest(fm, kc):
    """返回建议新增的规范词（按 taxonomy 顺序：deposit/process/method/commodity/region）。"""
    hay_raw = " ".join([
        str(fm.get("title", "")),
        " ".join(fm.get("tags", []) or []),
        str(fm.get("summary", ""))[:600],
        kc[:1500],
    ])
    hay = hay_raw.lower()
    out = []
    for canon, pat in RULES:
        if re.search(pat, hay, re.I):
            out.append(canon)
    # commodity：只按全名命中（符号交给 build_facet_index.py 处理，见 COMMODITY 注释）
    for sym, pat in COMMODITY:
        if re.search(pat, hay, re.I):
            out.append(sym)
    # 去重保序
    seen = set()
    res = []
    for x in out:
        if x not in seen:
            seen.add(x)
            res.append(x)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", default="P2")
    ap.add_argument("--sha")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only-empty", action="store_true",
                    help="只输出「建议词为空」的条目（需 Agent 人工判定）")
    ap.add_argument("--commit", action="store_true",
                    help="apply 后自动跑一次分面回写 + 增量索引 + 置 committed")
    ap.add_argument("--manual",
                    help="Agent 人工映射 JSON：{sha12: [规范词...]}。用于规则覆盖不到的条目")
    args = ap.parse_args()

    rows = load_queue(bucket=args.bucket, sha=args.sha)
    if args.limit:
        rows = rows[:args.limit]

    # 人工映射优先：不再跑规则，直接用 Agent 判定的词
    if args.manual:
        man = json.load(open(args.manual, encoding="utf-8"))
        patch = OrderedDict()
        for r in rows:
            k = r["sha12"]
            if k in man and man[k]:
                patch[k] = man[k]
        print("人工映射命中 %d/%d 篇" % (len(patch), len(rows)))
        if not args.apply:
            for k, v in patch.items():
                print("  %s | %s" % (k, "、".join(v)))
            return 0
        return _apply(patch, [], args)

    stat = Counter()
    patch = OrderedDict()
    empty = []
    for r in rows:
        fm, kc = read_entry(r["sha12"])
        if fm is None:
            stat["no_entry"] += 1
            continue
        sugg = suggest(fm, kc)
        cur = fm.get("tags") or []
        add = [x for x in sugg if x not in cur]
        if not add:
            stat["no_suggest"] += 1
            empty.append(r)
            continue
        patch[r["sha12"]] = add
        stat["ok"] += 1

    if not args.apply:
        print("=" * 78)
        print("P2 补受控词候选（dry-run）")
        print("=" * 78)
        for r in rows:
            if r["sha12"] not in patch:
                continue
            if args.only_empty:
                continue
            t = (r.get("title") or "")[:62]
            print("%s | %-14s | %-62s | %s" % (
                r["sha12"], (r.get("journal") or "")[:14], t,
                "、".join(patch[r["sha12"]])))
        if args.only_empty:
            print("--- 无自动候选（需 Agent 人工判定）---")
            for r in empty:
                print("%s | %-14s | %s" % (
                    r["sha12"], (r.get("journal") or "")[:14], (r.get("title") or "")[:70]))
        print()
        print("统计:", dict(stat), "| 有候选:", len(patch), "| 无候选:", len(empty))
        return 0

    return _apply(patch, empty, args)


def _apply(patch, empty, args):
    """把规范词 prepend 到条目 tags；可选跑分面回写 + 增量索引。"""
    import yaml
    n = 0
    for sha12, add in patch.items():
        p = PAPERS_DIR / ("paper-%s.md" % sha12)
        if not p.exists():
            print("  跳过（无条目）", sha12)
            continue
        t = p.read_text(encoding="utf-8")
        m = re.match(r"---\n(.*?)\n---\n(.*)", t, re.S)
        fm = yaml.safe_load(m.group(1)) or {}
        body = m.group(2)
        cur = list(fm.get("tags") or [])
        new = []
        for x in add + cur:
            if x not in new:
                new.append(x)
        fm["tags"] = new[:MAX_TAGS]
        front = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)
        p.write_text("---\n%s---\n%s" % (front, body), encoding="utf-8")
        n += 1
    print("已更新 %d 篇条目 tags（规范词置顶，总量 ≤%d）" % (n, MAX_TAGS))
    if empty:
        print("无候选需人工判定: %d" % len(empty))
        for r in empty[:40]:
            print("  MANUAL %s | %s" % (r["sha12"], (r.get("title") or "")[:60]))

    if args.commit:
        import subprocess
        # ① 分面回写：**整个批次只跑一次**（每篇跑一次会重复上千条目全量计算）
        print("分面回写中...", flush=True)
        subprocess.run([sys.executable, os.path.join(BASE, "scripts", "build_facet_index.py")],
                       cwd=BASE, timeout=1800, creationflags=_NO_WIN)
        # ② 增量索引（复用 agent_ingest._index）
        sys.path.insert(0, os.path.join(BASE, "scripts"))
        import agent_ingest as ai
        ok_t = 0
        for i, sha12 in enumerate(patch.keys(), 1):
            eid = "paper-%s" % sha12
            kp_ids = sorted(p.stem for p in ai.KPOINTS_DIR.glob("kp-%s-*.md" % sha12))
            got = ai._index([eid] + kp_ids)
            if got == len(kp_ids) + 1:
                ai.set_state(sha12, "committed", notes="P2 facet patch")
                ok_t += 1
            if i % 20 == 0:
                print("  索引 %d/%d (ok %d)" % (i, len(patch), ok_t), flush=True)
        print("索引完成: %d/%d" % (ok_t, len(patch)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
