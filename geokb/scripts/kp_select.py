# -*- coding: utf-8 -*-
"""知识点候选的「发现—校验—打分—去重—覆盖」引擎（API 管线与 Agent 管线共用）。

为什么要有这个模块（2026-10-23 重写）
--------------------------------------
旧逻辑把「选几条」完全交给模型：`kp_extract.PROMPT` 写「最多 6 个，宁缺毋滥」。
实测 1543 篇里 **1469 篇恰好 6 条（95.2%）** —— 模型把「上限」当「目标」，
条数被名额驱动而非信息密度驱动，这正是「凑数」的根因。

另一个方向的问题同样存在：模型只被要求「最多 6 条」，没有任何机制保证
**摘要/结论里的关键定量结论都被覆盖**，于是高信息密度论文会漏点。

本模块把取舍从模型手里拿回来：
    模型只负责**穷尽式产出候选**（claim + 数值 + 原文片段 + 行号 + 类型），
    本模块负责**逐条可判定的取舍**：锚定校验 → 信息量打分 → 去重 → 覆盖补漏。

五条判据（全部可在代码里复核，不依赖模型自评）
--------------------------------------------
1. 锚定（anchor）  claim 中每个带单位数值必须能在原文找到；quote 必须是原文子串。
                   → 直接杀死「编数」「编结论」型幻觉。
2. 类型（type）    定量结果 / 找矿标志 / 成因机制 / 参数条件 / 方法技术 /
                   时空格架 / 区域实例，各自基础价值不同（可检索复用性）。
3. 信息量（info）  带单位数值密度 + 命名实体密度 + 机制/方法连接词 + 篇幅形状。
4. 冗余（redundancy）  与已选条目的 3-gram Jaccard + 数值集合重叠，超阈值即合并/丢弃。
5. 覆盖（coverage）    摘要与结论中「带数值的结论句」构成必须覆盖的槽位；
                       选完回查，未覆盖的槽位进 gaps，可由调用方补抽一轮。

结论：条数 k **由通过阈值的候选数决定**，没有下限。k=0 是合法结果
（征稿简则、书评、纯区域地质描述类论文）。
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TAXONOMY = BASE / "config" / "taxonomy.yaml"

# 版本号写进知识点 frontmatter（kp_version）与 state/kp_done.jsonl，
# 用于「算法升级后只重跑旧版本」的增量重算。
KP_VERSION = "2.0"

# ════════════════════════════════════════════════════════════
# 收录判据（用户口径 2026-10-23）
#
#   核心问句：**这条知识点能给团队未来的研究带来多少价值？
#             今后做同类研究时，还能不能再用到它？**
#
#   典型可复用形态：
#     · 相近研究区的地球动力环境 / 大地构造背景  → 换区可类比
#     · 同一矿种、同一矿床类型的研究            → 直接对标
#     · 创新性的方法 / 技术 / 流程               → 可迁移
#     · 对地质现象的解释方法与判据               → 可借鉴
#
#   因此「数值多少」不是价值核心：一条只描述某样品斜长石含量的记录，
#   数字再多也换不了场景；而一句「高 Sr/Y + 负 εHf 指示加厚下地壳」，
#   零数值却能在几十个矿区复用。打分轴据此重排。
# ════════════════════════════════════════════════════════════

# 复用模式：这条知识「怎么被再次使用」，权重即其再利用价值
REUSE_MODES = {
    "解释借鉴": 1.00,   # 成因机制、判别判据、解释框架——换区换矿都能借
    "方法迁移": 0.96,   # 新方法/新技术/新流程/参数设置——可直接照做
    "类比参照": 0.90,   # 构造背景、成矿格架、矿床类型特征——供同类地区类比
    "标定参照": 0.82,   # 年龄/品位/温压/同位素参照值——供横向对比
    "本区事实": 0.40,   # 仅对本区有意义（样品描述、局部流水账）
}
REUSE_ORDER = list(REUSE_MODES)
DEFAULT_REUSE = "本区事实"

# 地质类型（保留作标签与分面参考，只占很小的权重）
KP_TYPES = {
    "定量结果": 1.00, "找矿标志": 0.95, "成因机制": 0.88, "参数条件": 0.84,
    "方法技术": 0.72, "时空格架": 0.68, "区域实例": 0.62,
}
TYPE_ORDER = list(KP_TYPES)
DEFAULT_TYPE = "成因机制"

# ─── 决策权重（用户口径 2026-10-23）────────────────────────────
# 「有没有价值」本质主观，不能全按分数裁：规则分 60%（可查证的部分），
# 模型判断 40%（读完全文后的整体判断），另给模型 must/drop 的显式裁量权。
W_RULE = 0.60
W_AI = 0.40
MUST_MAX = 2              # 模型可强行保留的条数上限（再多就等于取消闸门）

# ─── 阈值（--audit-existing 用真实库校准，见 docs/知识点拆分逻辑_20261023.md）──
MIN_SCORE = 0.58          # 融合分下限（有 AI 主观分时用这条）
MIN_SCORE_RULE = 0.40     # 纯规则分下限（老 payload / 无 AI 分时退回这条）
MIN_SCORE_AGENT = 0.32    # Agent 亲自撰写的条目：只告警不拦（人已过一遍）
REL_FLOOR = 0.60          # 相对下限：低于本篇最高分×0.60 的视为填充
DUP_SIM = 0.62            # 与已选条目的相似度上限，超过视为重复
MAX_K = 8                 # 单篇上限（无下限；0 条合法）

# ─── 数值识别 ────────────────────────────────────────────────
_UNIT = (r"(?:%|wt\.?\s*%|ppm|ppb|ppt|‰|permil|Ma|Ga|ka|a|°C|℃|K|km2|km|cm|mm|µm|um|nm|"
         r"g/t|g/cm3|mg/L|Mt|Gt|MPa|GPa|bar|mol|kmol|m3|m2|m|"
         r"s-1|s−1|s⁻¹|a-1|Ma-1|yr-1)")
RE_NUM_UNIT = re.compile(
    # ⚠ 前置断言只能用 [A-Za-z0-9_.]，**不能用 \w**：Python 的 \w 默认含汉字，
    #   于是「年龄95.4 Ma」这种紧贴中文的写法会被判成「前面还有字符」而漏掉
    #   （实测导致 2/14 条真实数值被误判为编造，覆盖率也凭空掉一半）。
    # 数值与单位之间允许一个右括号：「(95.4±1.2) Ma」是最常见的英文/中文括注写法，
    # 旧正则要求单位紧跟数字，导致这类年龄值全部漏检（覆盖率凭空少一档）。
    r"(?<![A-Za-z0-9_.])(\d+(?:\.\d+)?(?:\s*(?:±|~|–|—|-|to|至)\s*\d+(?:\.\d+)?)?)\s*[)）\]]?\s*(" + _UNIT + r")(?![A-Za-z])",
    re.I)
# 裸数值（用于「原文里到底有没有这个数」的宽松核对）
RE_ANY_NUM = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:\.\d+)?")
# 同位素/比值/异常等「准数值」写法：10Be、δ18O、εNd(t)、87Sr/86Sr、×10-6
RE_QUASI_NUM = re.compile(
    r"(?:\d{1,2}\s?(?:Be|Al|C|N|O|S|Ar|Sr|Nd|Hf|Pb|U|Th|He|Ne|Kr|Xe)\b|"
    r"δ\s?\d{1,2}\s?[A-Za-z]{1,2}|ε\s?[A-Za-z]{2}|"
    r"\d{1,3}\s?[A-Za-z]{1,2}\s?/\s?\d{1,3}\s?[A-Za-z]{1,2}|×\s?10\s?[-−]\s?\d+|"
    r"\d{1,3}\s?°\s?\d{1,2}\s?['′])")

RE_GENERIC = re.compile(
    r"(本文|本研究|该文|该研究|作者|文章)\s*(研究|提出|分析|讨论|介绍|报道|认为|指出)|"
    r"(具有|有着)(重要|较大|一定|显著)的?(理论|现实|指导|参考)?(意义|价值)|"
    r"(提供|可供)(了)?(新的)?(思路|参考|依据|借鉴)|"
    r"随着.{0,12}(发展|深入|推进)|(有待|尚需)(进一步)?(研究|验证|探讨)|"
    r"^(综述|评述|展望)|"
    r"(this|the present)\s+(paper|study|article|work)\s+(presents?|reports?|investigat|discuss|introduc)|"
    r"(provides?|offers?)\s+(a\s+)?(new\s+)?(reference|insight|basis)|"
    r"further\s+(work|research|study)\s+is\s+(needed|required)|"
    r"with\s+the\s+development\s+of", re.I)

# 机制/推断连接词：一条知识点若没有任何「因果或推断」信号，基本只是名词堆叠。
RE_CAUSAL = re.compile(
    r"导致|致使|引起|造成|由于|归因于|来源于|源自|受.{0,8}(?:控制|制约|限制)|"
    r"指示|表明|说明|揭示|暗示|反映|意味着|"
    r"有利于|不利于|促进|抑制|驱动|触发|"
    r"机制|机理|路径|过程为|模式为|"
    r"results?\s+(?:from|in)|caused\s+by|due\s+to|attributed\s+to|"
    r"controlled\s+by|driven\s+by|indicat\w+|suggest\w*|impl(?:y|ies)|reflect\w*|"
    r"consistent\s+with|favou?rs?\b|inhibit\w*|promot\w*|mechanism", re.I)

# 定位/归属谓词：价值低于因果推断，但比「名词堆叠」强（区域实例类主体的骨架）。
RE_LOC = re.compile(
    r"赋存于|产于|位于|分布于|集中于|形成于|发育于|侵位于|展布|"
    r"hosted\s+(?:in|by)|located\s+(?:in|at)|occurs?\s+in|is\s+situated|"
    r"emplaced\s+(?:in|into)|distributed\s+(?:in|across)", re.I)

# 可迁移性措辞：明说了「可推广/可判别/可照做」，是复用价值最直接的文本证据。
#   典型：「可作为…判据」「适用于…」「通常…」「判别图解」「端元」「阈值」
#        "can be used as", "diagnostic", "criterion", "generally", "applicable"
RE_TRANSFER = re.compile(
    r"可用于|适用于|可作为|可用以|通常|一般而?言|普遍|规律|判据|判别|阈值|端元|"
    r"图解|判别图|指标体系|评价标准|模式|框架|流程|参数设置|"
    r"can\s+be\s+used\s+(?:as|to|for)|is\s+applicable|are\s+applicable|"
    r"diagnostic|criteri(?:on|a)|threshold|end-?member|discriminat\w+|"
    r"generally|commonly|in\s+general|rule\s+of\s+thumb|workflow|protocol", re.I)

RE_NOISE = re.compile(
    r"(^\s*(图|表|Fig|Table)\s*\d|如图|见表|as\s+shown\s+in\s+(Fig|Table)|"
    r"(征稿|简则|启事|书评|序言|编者按|致谢|投稿须知))", re.I)

# 中英文命名实体后缀（矿床/岩体/构造单元/地层）
RE_PROPER_CN = re.compile(
    r"[\u4e00-\u9fa5]{2,10}(?:矿床|矿田|矿区|矿集区|岩体|岩株|岩基|岩脉|岩带|"
    r"群|组|系|带|盆地|隆起|凹陷|地体|地块|克拉通|造山带|断裂|剪切带|杂岩体|穹隆|凸起)")
RE_PROPER_EN = re.compile(
    r"\b(?:[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,3})\s+"
    r"(?:Deposit|Massif|Batholith|Pluton|Complex|Formation|Group|Basin|Uplift|"
    r"Belt|Terrane|Craton|Orogen|Fault|Shear\s+Zone|District)\b")

RE_LN = re.compile(r"\bL\d+\s*[:\-]?\s*")


# ════════════════════════════════════════════════════════════
# 文本归一化与锚定校验
# ════════════════════════════════════════════════════════════
def norm(s):
    """NFKC + 小写 + 去空白 + 统一各类连字符/引号（用于子串比对）。"""
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = s.replace("\u2014", "-").replace("\u2013", "-").replace("\u2212", "-")
    s = s.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    return re.sub(r"\s+", "", s).lower()


def strip_ln(s):
    """去掉行号前缀（L123:），行号只在 prepare2 的带号文本里存在。"""
    return RE_LN.sub("", str(s or ""))


def _tighten(s):
    """把数字内部被 PDF 排版拆开的空白收掉：「0. 03 %」→「0.03%」，「1 405」→「1405」。

    不少期刊 PDF 抽出来的表格式文本是「0. 03」这种断法，不做这步会把真实数值
    判成「原文没有」，进而误杀整条知识点（实测样本里 2/14 条如此）。
    """
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = re.sub(r"(?<=\d)\s*[.．]\s*(?=\d)", ".", s)      # 0. 03 → 0.03
    s = re.sub(r"(?<=\d)\s+(?=\d)", "", s)                # 1 405 → 1405
    s = re.sub(r"(?<=\d)\s+(?=[%‰])", "", s)              # 0.62 ‰ → 0.62‰
    return s


def values(s):
    """抽取文本中的「值」集合（不含单位）。

    只取带单位的数值，避免把「3 类」「第 2 章」这类序数当事实值。
    范围写法 143~130 Ma 拆成 {143, 130}，两边任一侧出现即视为命中。
    """
    out = set()
    t = _tighten(strip_ln(s))
    for m in RE_NUM_UNIT.finditer(t):
        for v in re.findall(r"\d+(?:\.\d+)?", m.group(1)):
            out.add(_canon_num(v))
    for m in RE_QUASI_NUM.finditer(t):
        out.add(norm(m.group(0)))
    return out


def _canon_num(v):
    """数值规范化：143.0 → 143；.5 → 0.5。"""
    v = v.strip()
    try:
        f = float(v)
    except ValueError:
        return v
    if f == int(f):
        return str(int(f))
    return ("%.6f" % f).rstrip("0").rstrip(".")


def _val_match(v, pool):
    """值 v 是否在 pool 中（允许 143 / 143.0 互认，允许 0.5 / .5 互认）。"""
    if v in pool:
        return True
    try:
        f = float(v)
    except ValueError:
        return False
    for p in pool:
        try:
            if abs(float(p) - f) < 1e-9:
                return True
        except ValueError:
            continue
    return False


def decimals(s):
    """文本里的**裸小数**集合（无单位）。

    为什么需要它：`values()` 只取带单位的数，而成分表/温压表把单位写在表头，
    单元格里是 44.65、13.15、9.73 这样的裸小数 —— 只查带单位的池子，整张表
    的数据都会被判成「原文里没有」（实测白云鄂博的表1/表3/表5 全部误杀）。

    只取带小数点的数：整数裸数字大多是图号/表号/序号（图7、表3、第8勘探线），
    拿它们做校验只会制造误报；而关键定量数据（成分、比值、温压）几乎都带小数。
    """
    out = set()
    # ⚠ 不能用 _tighten：它为了还原「1 405」会把相邻数字间的空格全部删掉，
    # 表格里 `12.20 24.00 5.50` 会被粘成 `12.2024.005.50`，小数全部匹配不上
    # （实测 decimals 只剩 31 个，白云鄂博表1/3/5 一个都没抓到）。
    # 这里只做「0. 03 → 0.03」的断点修复，保留数字间的分隔。
    t = unicodedata.normalize("NFKC", strip_ln(str(s or "")))
    t = re.sub(r"(?<=\d)\s*[.．]\s*(?=\d)", ".", t)
    for m in re.finditer(r"(?<![\d.])(\d+\.\d+)(?![\d.])", t):
        out.add(_canon_num(m.group(1)))
    return out


def verify_numbers(text, source):
    """文本里的数值是否都能在 source 找到 → (ok, missing[])。

    这是「编数」的硬闸门。source 应传**论文原文**（未截断），
    因为模型看到的 body 可能只是节选，但数值必须真实存在。

    比对池 = 带单位数值 ∪ 裸小数（表格数据）。两个池子缺一不可：
    只有带单位池时，凡单位在表头的成分数据一律误判为编造。
    """
    src = values(source) | decimals(source)
    if not src:
        return True, []
    cand = values(text) | decimals(text)
    miss = [v for v in cand if not _val_match(v, src)]
    if miss:
        # 裸串兜底：PDF 常把表格序号与首个数值粘在一起（`磷灰石17.6034.71`），
        # token 级匹配切不开，但数值本身确实在原文。这一步只做「字符串是否出现」，
        # 边界宽松（`7.6` 可能在 `17.60` 里命中），但硬闸门的目的只是拦「编数」，
        # 宁放过边界、不误杀真数据 —— 误杀会让人回头改 payload 去掉真实数值。
        flat = re.sub(r"\s+", "", unicodedata.normalize("NFKC", strip_ln(str(source or ""))))
        miss = [v for v in miss if v not in flat]
    return (not miss), sorted(miss)


def verify_quote(quote, source_norm, source_loose=None):
    """quote 是否逐字出现在原文（容忍空白/折行/行号差异）→ (ok, ratio)。

    source_norm  = norm(原文)        —— 已去空白、小写
    source_loose = 去全部标点后的原文 —— 可选，用于二级宽松比对
    """
    q = norm(strip_ln(quote))
    if len(q) < 12:
        return False, 0.0
    if q in source_norm:
        return True, 1.0
    # 折行/连字符断词会让整段不连续：退化为「前缀片段命中率」
    for win in (60, 40, 25, 16):
        if len(q) >= win and q[:win] in source_norm:
            return True, 0.75
    # 二级宽松：去掉全部标点再比（PDF 里常把「，」抽成「,」或丢失）
    if source_loose:
        ql = re.sub(r"[\W_]+", "", q)
        if len(ql) >= 16 and ql in source_loose:
            return True, 0.7
        for win in (40, 25):
            if len(ql) >= win and ql[:win] in source_loose:
                return True, 0.6
    # 最后退路：按 12 字切片统计命中率
    chunks = [q[i:i + 12] for i in range(0, max(len(q) - 11, 1), 12)]
    if not chunks:
        return False, 0.0
    hit = sum(1 for c in chunks if c in source_norm)
    r = hit / len(chunks)
    return (r >= 0.6), round(r, 2)


# ════════════════════════════════════════════════════════════
# 实体 / 类型 / 相似度
# ════════════════════════════════════════════════════════════
_LEX = None


def lexicon():
    """受控词表拍平为 {facet: set(词)}（canonical + synonyms）。"""
    global _LEX
    if _LEX is not None:
        return _LEX
    lex = {}
    try:
        import yaml
        data = yaml.safe_load(TAXONOMY.read_text(encoding="utf-8")) or {}
        for facet, body in (data.get("facets") or {}).items():
            words = set()
            for canon, syns in (body or {}).get("canonical") or []:
                for w in [canon] + list(syns or []):
                    w = str(w).strip()
                    # 单字词（煤/铀/铁…）在长文里误命中率太高，只留 2 字以上
                    if len(w) >= 2:
                        words.add(w.lower())
            lex[facet] = words
    except Exception:  # noqa: BLE001
        lex = {}
    _LEX = lex
    return lex


def entity_hits(text):
    """命名实体命中数（受控词表 + 专名后缀 + 同位素符号）。"""
    t = str(text or "")
    low = t.lower()
    hits = set()
    for facet, words in lexicon().items():
        for w in words:
            if w in low and w not in ("铁", "金"):
                hits.add(w)
    for m in RE_PROPER_CN.finditer(t):
        hits.add(m.group(0))
    for m in RE_PROPER_EN.finditer(t):
        hits.add(m.group(0))
    for m in RE_QUASI_NUM.finditer(unicodedata.normalize("NFKC", t)):
        hits.add(m.group(0))
    return hits


# ─── 团队研究画像（config/team_focus.yaml）────────────────────
_FOCUS = None
_FOCUS_MULT = {"commodities": 1.0, "deposit_types": 1.0, "minerals": 0.9,
               "tectonic": 0.8, "methods": 0.8, "themes": 0.7}


def team_focus():
    """→ [(section, canonical, [别名...])]，读取失败时返回空表（不影响主流程）。"""
    global _FOCUS
    if _FOCUS is not None:
        return _FOCUS
    out = []
    try:
        import yaml
        p = BASE / "config" / "team_focus.yaml"
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for section in ("commodities", "deposit_types", "minerals", "tectonic",
                        "methods", "themes"):
            for item in (data.get(section) or []):
                if not isinstance(item, (list, tuple)) or not item:
                    continue
                canon = str(item[0])
                syns = [str(x) for x in (item[1] if len(item) > 1 else [])]
                out.append((section, canon, [canon] + syns))
    except Exception:  # noqa: BLE001
        out = []
    _FOCUS = out
    return out


def team_fit(text):
    """团队相关性 ∈ [0,1] + 命中项。

    口径（用户 2026-10-23）：同一矿种、同一矿床类型可**直接对标**；
    相近研究区的构造/地球动力背景可**类比**；方法可**迁移**；解释范式可**借鉴**。
    加权求和后按 3 项饱和：命中三项及以上即视为与团队高度相关。
    """
    t = str(text or "").lower()
    if not t:
        return 0.0, []
    hits, score = [], 0.0
    for section, canon, syns in team_focus():
        if any(str(s).lower() in t for s in syns if len(str(s)) >= 2):
            mult = _FOCUS_MULT.get(section, 0.7)
            score += mult
            hits.append("%s:%s" % (section[:4], canon))
    return round(min(score, 3.0) / 3.0, 3), hits[:6]


TYPE_RULES = [
    # 「异常」单独出现多为地球化学异常描述，不足以判定为找矿标志，故要求与找矿/勘查/靶区同现
    ("找矿标志", re.compile(
        r"(找矿|勘查|靶区|指示(?!剂)|找矿标志|判别标志|评价指标|"
        r"indicator|pathfinder|exploration|prospectivity|targeting|vector)", re.I)),
    ("定量结果", re.compile(
        r"(年龄|年代|定年|品位|含量|丰度|比值|温度|压力|盐度|δ|ε|"
        r"age|grade|content|abundance|ratio|dating|dated|temperature)", re.I)),
    ("参数条件", re.compile(
        r"(盐度|均一温度|压力|深度|pH|fO2|氧逸度|活度|条件|"
        r"salinity|homogeni[sz]ation|pressure|depth|oxygen\s+fugacity|condition)", re.I)),
    ("方法技术", re.compile(
        r"(方法|技术|采用|运用|测定|测试|分析|模拟|反演|算法|模型|流程|"
        r"method|technique|analys|measure|simulat|inversion|algorithm|workflow|model)", re.I)),
    ("时空格架", re.compile(
        r"(期|阶段|时代|构造|演化|空间|分布|格架|序列|"
        r"stage|epoch|episode|tectonic|evolution|spatial|framework|sequence)", re.I)),
    ("区域实例", re.compile(
        r"(矿床|矿区|矿田|岩体|地区|区域|杂岩|"
        r"deposit|district|ore\s?field|massif|complex|area|region)", re.I)),
]


def infer_type(text, title=""):
    """按关键词推断知识点类型（模型缺 type 时兜底；审计历史条目也用它）。"""
    s = "%s %s" % (title or "", text or "")
    for name, rx in TYPE_RULES:
        if rx.search(s):
            return name
    return DEFAULT_TYPE


# 复用模式推断（模型未给 reuse 时兜底）。顺序即优先级：
# 先看「有没有解释/判据」，再看「有没有方法创新」，再看「有没有参照值」，
# 最后才落到「能不能类比」与「只对本区有意义」。
RE_METHOD_NOVEL = re.compile(
    r"(提出|建立|构建|改进|优化|首次|新方法|新流程|本文采用|采用.{0,12}方法|"
    r"we\s+(?:propose|present|develop|introduce)|novel|newly\s+developed|"
    r"improved|optimized|coupled\s+approach)", re.I)

REUSE_RULES = [
    ("方法迁移", re.compile(
        r"(方法|技术|流程|算法|模型|程序|参数设置|分析流程|测试方案|"
        r"method|technique|workflow|protocol|algorithm|pipeline)", re.I)),
    ("解释借鉴", re.compile(
        r"(机制|机理|判据|判别|指示|解释|归因|由于|导致|受.{0,8}控制|模式|"
        r"约束|制约|means|indicates|suggests|interpret|mechanism|criterion|"
        r"attribut|constrain)", re.I)),
    ("标定参照", re.compile(
        r"(年龄|年代|品位|含量|温度|压力|盐度|同位素|比值|丰度|"
        r"age|grade|content|temperature|pressure|salinity|isotop|ratio)", re.I)),
    ("类比参照", re.compile(
        r"(构造|背景|环境|演化|格架|带|盆地|克拉通|造山|岩浆|蚀变|矿物组合|"
        r"tectonic|setting|environment|evolution|framework|belt|basin|craton|"
        r"magmat|alteration|assemblage)", re.I)),
]


def infer_reuse(text, title=""):
    """推断复用模式；都没有命中则视为「本区事实」。"""
    s = "%s %s" % (title or "", text or "")
    for name, rx in REUSE_RULES:
        if not rx.search(s):
            continue
        if name == "方法迁移" and not RE_METHOD_NOVEL.search(s):
            # 只是「用了某方法」不算可迁移方法，降级看下一档
            continue
        return name
    return DEFAULT_REUSE


def grams(s):
    """去标点后的字符 3-gram 集合。"""
    t = re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFKC", str(s or "")).lower())
    if len(t) < 3:
        return {t} if t else set()
    return {t[i:i + 3] for i in range(len(t) - 2)}


def similarity(a, b):
    """两条知识点正文的相似度 ∈ [0,1]：0.6×3gram Jaccard + 0.4×数值重叠。"""
    ga, gb = grams(a), grams(b)
    j = (len(ga & gb) / len(ga | gb)) if (ga and gb) else 0.0
    va, vb = values(a), values(b)
    # 数值集合完全相同（同一组数据）也说明是同一条事实的不同说法
    ov = (len(va & vb) / len(va | vb)) if (va and vb) else 0.0
    return round(0.6 * j + 0.4 * ov, 3)


# ════════════════════════════════════════════════════════════
# 评分
# ════════════════════════════════════════════════════════════
def _len_shape(body, lang="zh"):
    n = len(body or "")
    lo, hi = (100, 380) if lang != "en" else (150, 700)
    if lo <= n <= hi:
        return 1.0
    if 0.6 * lo <= n < lo or hi < n <= 1.6 * hi:
        return 0.6
    if n < 0.6 * lo:
        return 0.15
    return 0.3


def score_candidate(c, lang="zh"):
    """给单条候选打分 → dict(score, parts, flags, type, reuse, reuse_hits)。

    权重口径 = 「换场景还能不能用」：
      0.34 复用模式   解释借鉴 > 方法迁移 > 类比参照 > 标定参照 >> 本区事实
      0.22 可迁移信号 明说「可作判据/适用/通常/判别图」等 → 强信号
      0.18 团队相关   命中 config/team_focus.yaml（同矿种/同类型/同区/同方法）
      0.12 参照值     带单位数值密度（供横向对比，不是越多越好，3 个饱和）
      0.10 命名实体   地名/矿床/岩体/矿物/同位素 —— 决定「可检索性」
      0.04 篇幅形状
    """
    title = str(c.get("title") or "")
    body = str(c.get("body") or "")
    text = title + " " + body
    typ = c.get("type") if c.get("type") in KP_TYPES else infer_type(text, title)
    reuse = c.get("reuse") if c.get("reuse") in REUSE_MODES else infer_reuse(text, title)
    # 防「标签通胀」：模型自报的高价值类型必须有文本证据支撑，否则降级回推断值。
    # 否则只要全标「解释借鉴」，阈值就形同虚设。
    if reuse == "解释借鉴" and not (RE_CAUSAL.search(text) or RE_TRANSFER.search(text)):
        reuse = infer_reuse(text, title)
    if reuse == "方法迁移" and not (RE_METHOD_NOVEL.search(text)
                                    or re.search(r"方法|技术|流程|算法|method|workflow|technique",
                                                 text, re.I)):
        reuse = infer_reuse(text, title)
    # 反向纠正：模型常把「某个年龄/品位/温压值」笼统标成「本区事实」，
    # 但带单位的定量值本身就是可横向对比的参照，应归「标定参照」。
    if reuse == "本区事实" and values(text) and re.search(
            r"年龄|年代|品位|含量|温度|压力|盐度|同位素|比值|"
            r"age|grade|content|temperature|pressure|isotop", text, re.I):
        reuse = "标定参照"

    v = values(text)
    q = min(len(v), 3) / 3.0
    ents = entity_hits(text)
    e = min(len(ents), 3) / 3.0
    fit, fit_hits = team_fit(text)
    # 可迁移性：明说可复用 → 满；只有因果/机制 → 0.6；只有定位 → 0.25
    tr = 1.0 if RE_TRANSFER.search(text) else 0.0
    if tr == 0.0:
        tr = 0.6 if RE_CAUSAL.search(text) else (0.25 if RE_LOC.search(text) else 0.0)
    l = _len_shape(body, lang)

    flags = []
    pen = 0.0
    # 套话惩罚要看**位置**：句首「本文研究了…」是元叙述，整条作废；
    # 句尾「…可提供参考」只是收尾习惯，轻微扣分即可（实测 8.6% 的条目
    # 仅因句尾套话被判 0 分，属误杀）。
    g = RE_GENERIC.search(body)
    if g and g.start() <= 12:
        pen += 0.30
        flags.append("generic")
    elif g:
        pen += 0.08
        flags.append("generic_tail")
    if g and len(RE_GENERIC.findall(body)) >= 2:
        pen += 0.10
        flags.append("generic")
    if RE_NOISE.search(body) or RE_NOISE.search(title):
        pen += 0.40
        flags.append("noise")
    if len(body) < 40:
        pen += 0.25
        flags.append("too_short")
    if len(title) > 40:
        pen += 0.10
        flags.append("title_long")
    # 全是数值没有结论动词 → 是表格片段而非知识点
    if q > 0 and len(RE_CAUSAL.findall(text)) == 0 and len(body) < 70:
        pen += 0.10
        flags.append("number_dump")
    # 「本区事实」且无团队相关性且无可迁移信号 → 换不了场景，扣分
    if reuse == "本区事实" and fit == 0 and tr < 0.5:
        pen += 0.12
        flags.append("non_transferable")

    parts = {
        "reuse": REUSE_MODES.get(reuse, 0.4),
        "transfer": tr,
        "team": fit,
        "quant": q,
        "entity": e,
        "length": l,
        "type": KP_TYPES.get(typ, 0.6),
        "coverage": 1.0 if c.get("_covers") else 0.0,
    }
    sc = (0.34 * parts["reuse"] + 0.22 * parts["transfer"] + 0.18 * parts["team"]
          + 0.12 * parts["quant"] + 0.10 * parts["entity"] + 0.04 * parts["length"])
    sc = max(0.0, sc - pen)
    return {"score": round(sc, 3), "parts": parts, "flags": flags, "type": typ,
            "reuse": reuse, "reuse_hits": fit_hits, "penalty": round(pen, 3)}


# ════════════════════════════════════════════════════════════
# 覆盖槽位（摘要 + 结论里「带数值的结论句」）
# ════════════════════════════════════════════════════════════
_SENT_SPLIT = re.compile(r"(?<=[。；！？;!?])\s*|(?<=\.)\s+(?=[A-Z])")
# 方法/仪器罗列句：带一堆数字（5%、20 mL、193 nm…）但没有任何结论，
# 若当作「必须覆盖的槽位」，覆盖率会被这些噪声永久拖低（实测摘要里的
# 测试条件句就让一篇论文的覆盖率从 1.0 掉到 0.5）。
RE_METHOD_SENT = re.compile(
    r"(分析|测试|实验|仪器|流程|前处理|制样|实验室|标样|监控|"
    r"analys\w+|measure\w*|instrument|procedure|protocol|laborator\w+|standard\s+reference)",
    re.I)
RE_CONCL_SENT = re.compile(
    r"(表明|显示|指示|说明|揭示|认为|获得|得到|限定|约束|"
    r"indicat\w+|suggest\w*|show\w*|reveal\w*|yield\w*|constrain\w*|implies?|"
    r"consistent\s+with)", re.I)


def claim_slots(source, max_slots=10):
    """把摘要/结论拆成「必须覆盖」的槽位：带单位数值的结论句。

    这是「漏点」的检测基线：论文最重要的定量结论几乎都在摘要与结论里，
    若一条都没被知识点覆盖，说明拆分漏了主结论。
    """
    try:
        import text_prep
        segs = text_prep._segments(source)          # noqa: SLF001  (同仓库内部复用)
    except Exception:  # noqa: BLE001
        segs = {}
    chunks = []
    for key in ("abstract", "concl"):
        r = segs.get(key)
        if not r:
            continue
        chunks.append(source[r[0]:r[1]])
    if not chunks:
        chunks = [source[:4000], source[-4000:]]
    slots = []
    for ch in chunks:
        # 先把 PDF 折行/逐词断行拼回句子，否则槽位文本会是
        # 「The\n \ntwo-stage\n \nzircon…」这种碎片（实测英文摘要常见）
        ch = re.sub(r"[ \t]*\n[ \t]*", " ", ch)
        ch = re.sub(r"\s{2,}", " ", ch)
        for s in _SENT_SPLIT.split(ch):
            s = s.strip()
            if len(s) < 24:
                continue
            v = values(s)
            if not v:
                continue
            # 纯方法罗列句不算「必须覆盖的结论」：它有数字没结论，
            # 拿它当槽位只会让覆盖率虚低、触发无谓的补抽。
            if RE_METHOD_SENT.search(s) and not RE_CONCL_SENT.search(s):
                continue
            slots.append({"text": s[:300], "values": v})
    # 数值多的句子优先，去重后截断
    slots.sort(key=lambda x: -len(x["values"]))
    out, seen = [], set()
    for s in slots:
        key = frozenset(s["values"])
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= max_slots:
            break
    return out


def mark_coverage(cands, slots):
    """给候选打 _covers 标记，并回填每个槽位被谁覆盖。

    ⚠ 一条候选可以同时覆盖多个槽位（同一批 Hf 数据既给模式年龄又给 εHf 结论）。
    早期版本命中第一个槽位就 break，导致「一条顶两条」的情况被算成漏覆盖，
    覆盖率虚低、白触发补抽。
    """
    covered = set()
    for s in slots:
        s["covered_by"] = []
    for c in cands:
        intro = str(c.get("title") or "") + " " + str(c.get("body") or "")
        cv = values(intro)
        hit_any = False
        for i, s in enumerate(slots):
            if i in covered:
                continue
            inter = {v for v in cv if _val_match(v, s["values"])}
            if inter and len(inter) / max(len(s["values"]), 1) >= 0.5:
                hit_any = True
                s["covered_by"].append(c.get("title") or c.get("claim") or "")
                covered.add(i)
        if not hit_any:
            # 文本相似度兜底：数值写法不同但说的是同一结论
            for i, s in enumerate(slots):
                if i in covered:
                    continue
                if similarity(intro, s["text"]) >= 0.35:
                    hit_any = True
                    s["covered_by"].append(c.get("title") or c.get("claim") or "")
                    covered.add(i)
        c["_covers"] = bool(hit_any)
    return slots, covered


def coverage_report(kps, slots):
    """覆盖率报告 → {rate, covered, gaps[]}。"""
    hit = [s for s in slots if s.get("covered_by")]
    gaps = [{"text": s["text"][:180], "values": sorted(s["values"])}
            for s in slots if not s.get("covered_by")]
    return {
        "slots": len(slots),
        "covered": len(hit),
        "rate": round(len(hit) / len(slots), 2) if slots else 1.0,
        "gaps": gaps,
    }


# ════════════════════════════════════════════════════════════
# 选择主流程
# ════════════════════════════════════════════════════════════
def _dup_reason(a, b, dup_sim=DUP_SIM):
    """判断 a 与 b 是否重复 → (reason, b) 或 None。

    两级判据：
      ① 正文相似度（3-gram + 数值重叠）够高 → 同一事实的两种写法
      ② 「同一组数据被拆成多条」：数值集合重叠 ≥50% 且标题主题相近
         （实测：同一批锆石 Hf 数据被拆成「Lu-Hf 同位素特征」「Hf 同位素特征」
          「U-Pb 定年结果」「U-Pb 年龄」四条，正文各不相同但数据同源，
          只靠正文相似度抓不到，会让一条事实碎成四片）
    """
    body_sim = similarity(a.get("body", ""), b.get("body", ""))
    if body_sim >= dup_sim:
        return ("duplicate:%.2f" % body_sim, b)
    ga, gb = grams(a.get("title", "")), grams(b.get("title", ""))
    t_j = (len(ga & gb) / len(ga | gb)) if (ga and gb) else 0.0
    if t_j >= 0.62:
        return ("same_topic:%.2f" % t_j, b)
    va, vb = values(a.get("body", "")), values(b.get("body", ""))
    num_j = (len(va & vb) / len(va | vb)) if (va and vb) else 0.0
    if num_j >= 0.5 and t_j >= 0.28:
        return ("same_data:%.2f" % num_j, b)
    return None


def select(cands, source, lang="zh", max_k=MAX_K, min_score=None,
           dup_sim=DUP_SIM, verify_anchor=True):
    """候选 → 最终知识点。返回 (kept, dropped, report)。

    决策口径（2026-10-23 用户定调）：
        价值判断天然主观，**不能全按分数裁**。规则分占 60%，模型自己的
        判断（value 0–10 + verdict）占 40%；模型还可以显式 `must`（最多 2 条）
        强行保留、或 `drop` 显式否决——那是它的自由裁量空间。

        规则分只负责「能不能查证」（数值在不在原文、是不是重复、是不是噪声、
        与团队研究方向的相关度），不管「想不想要」。

    kept    按 evidence 行号升序（同篇内保持原文顺序，便于人工核对）
    dropped 每条带 reason，便于审计「为什么这条没入库」
    """
    src_norm = norm(source)
    src_loose = re.sub(r"[\W_]+", "", src_norm)
    slots = claim_slots(source)
    mark_coverage(cands, slots)

    judged = []
    for c in cands:
        c = dict(c)
        body = str(c.get("body") or "").strip()
        title = str(c.get("title") or "").strip()
        if not body or not title:
            c["_reason"] = "empty"
            judged.append(c)
            continue
        sc = score_candidate(c, lang)
        c.update(sc)
        rule = c["score"]

        if verify_anchor:
            ok_n, miss = verify_numbers(body + " " + title, source)
            if not ok_n:
                c["_reason"] = "unverified_number:" + ",".join(miss[:4])
                judged.append(c)
                continue
            qt = c.get("quote") or ""
            if qt:
                ok_q, ratio = verify_quote(qt, src_norm, src_loose)
                c["quote_ratio"] = ratio
                if not ok_q:
                    # 引文对不上**不直接判死**：数值已核对过，真正的幻觉已被挡住；
                    # PDF 抽字（异体字/连字/表格）常见，硬判会误杀好条目（实测
                    # 「A型花岗岩判别」0.73 分被 0.00 引文比误杀）。改为扣分+留痕。
                    rule = round(max(0.0, rule - 0.08), 3)
                    c["flags"] = list(c.get("flags") or []) + ["quote_weak"]

        if "noise" in c.get("flags", []):
            c["_reason"] = "noise"
            judged.append(c)
            continue

        # ── 60/40 融合：规则分 × 0.6 + 模型主观分 × 0.4 ──────────────
        ai = _ai_value(c)
        c["score_rule"] = rule
        c["ai_value"] = ai
        if ai is None:
            c["score"] = rule               # 没有主观分（老 payload）→ 退回纯规则
        else:
            c["score"] = round(W_RULE * rule + W_AI * (ai / 10.0), 3)
        c["verdict"] = _verdict(c)
        c["_reason"] = ""
        judged.append(c)

    # 模型显式否决：直接出局（这是它的裁量权，规则分再高也尊重）
    pool = []
    for c in judged:
        if c.get("_reason"):
            continue
        if c.get("verdict") == "drop" and c.get("ai_value") is not None:
            c["_reason"] = "ai_veto"
            continue
        pool.append(c)

    # 排序：must 优先 → 融合分高 → 覆盖槽位 → 行号
    pool.sort(key=lambda x: (0 if x.get("verdict") == "must" else 1,
                             -x["score"], 0 if x.get("_covers") else 1,
                             _ev_start(x)))

    kept, dropped = [], [c for c in judged if c.get("_reason")]
    best = max([c["score"] for c in pool], default=0.0)
    rel_floor = round(best * REL_FLOOR, 3)
    must_left = MUST_MAX
    for c in pool:
        if len(kept) >= max_k:
            c["_reason"] = "over_limit"
            dropped.append(c)
            continue
        is_must = c.get("verdict") == "must" and must_left > 0
        if is_must:
            must_left -= 1
        floor_abs = (min_score if min_score is not None
                     else (MIN_SCORE if c.get("ai_value") is not None else MIN_SCORE_RULE))
        if not is_must:
            # 覆盖主结论的条目放宽绝对下限：它是「防遗漏」的兜底
            if c.get("_covers"):
                floor_abs = round(floor_abs * 0.8, 3)
            if c["score"] < floor_abs:
                c["_reason"] = "below_threshold:%.2f" % c["score"]
                dropped.append(c)
                continue
        if not is_must and c["score"] < rel_floor:
            # 两类豁免：
            #   ① 覆盖摘要/结论主结论的条目 —— 防遗漏的兜底，表述朴素也不该丢
            #   ② 解释借鉴 / 方法迁移 —— 常常没有数值、分数天然偏低，但正是
            #      最可复用的两类（换区能借的解释、能照做的方法）；
            #      相对下限只该砍同类填充项，不该把另一类价值一并砍掉
            exempt = c.get("_covers") or (
                c.get("reuse") in ("解释借鉴", "方法迁移")
                and c["score"] >= floor_abs)
            if not exempt:
                c["_reason"] = "below_relative:%.2f<%.2f" % (c["score"], rel_floor)
                dropped.append(c)
                continue
        dup = None
        for k in kept:
            dup = _dup_reason(c, k, dup_sim)
            if dup:
                break
        if dup:
            c["_reason"] = dup[0]
            c["_dup_of"] = dup[1].get("title", "")
            dropped.append(c)
            continue
        # 「本区事实」按定义不可迁移，每篇最多留 1 条（分最高的那条）。
        # 否则一篇区域地质论文能靠样品描述刷满名额，正是凑数的典型。
        if (not is_must and c.get("reuse") == "本区事实"
                and sum(1 for k in kept if k.get("reuse") == "本区事实") >= 1):
            c["_reason"] = "local_fact_cap"
            dropped.append(c)
            continue
        kept.append(c)

    kept.sort(key=_ev_start)
    # 覆盖率必须按**最终留下**的条目重算：mark_coverage 之前跑的是全体候选，
    # 被淘汰的候选仍挂在 covered_by 上，会虚报覆盖、进而让「补抽」失灵。
    mark_coverage(kept, slots)
    rep = {
        "version": KP_VERSION,
        "candidates": len(cands),
        "kept": len(kept),
        "dropped": len(dropped),
        "drop_reasons": _count_reasons(dropped),
        "score_min": min([c["score"] for c in kept], default=0),
        "score_mean": round(sum(c["score"] for c in kept) / len(kept), 3) if kept else 0,
        "rule_mean": round(sum(c.get("score_rule", 0) for c in kept) / len(kept), 3) if kept else 0,
        "ai_mean": round(sum((c.get("ai_value") or 0) for c in kept) / len(kept), 1) if kept else 0,
        "must": sum(1 for c in kept if c.get("verdict") == "must"),
        "veto": sum(1 for c in dropped if c.get("_reason") == "ai_veto"),
        "coverage": coverage_report(kept, slots),
        "types": _count_types(kept),
    }
    return kept, dropped, rep


def _ai_value(c):
    """解析模型给的主观价值分（0–10）。缺省返回 None（退回纯规则分）。"""
    v = c.get("value", c.get("ai_value"))
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    if f > 10:                    # 容忍 0–100 制
        f = f / 10.0
    return max(0.0, min(10.0, f))


def _verdict(c):
    v = str(c.get("verdict") or c.get("keep") or "").strip().lower()
    if v in ("must", "keep_force", "保留", "必收"):
        return "must"
    if v in ("drop", "reject", "剔除", "不收", "no"):
        return "drop"
    return "keep"


def _count_reasons(dropped):
    from collections import Counter
    out = Counter()
    for c in dropped:
        r = (c.get("_reason") or "unknown").split(":")[0]
        out[r] += 1
    return dict(out)


def _count_types(kps):
    from collections import Counter
    return dict(Counter(c.get("type") or DEFAULT_TYPE for c in kps))


def _ev_start(c):
    m = re.search(r"L?(\d+)", str(c.get("evidence") or ""))
    return int(m.group(1)) if m else 10 ** 6


# ════════════════════════════════════════════════════════════
# Agent 路径：单条校验（硬错拦截 / 软警提示）
# ════════════════════════════════════════════════════════════
def check_kp(kp, source, peers=(), lang="zh"):
    """校验 Agent 手写的单条知识点 → {"errors":[], "warnings":[], "score":…}。

    硬错（必须改）只留三类，都是为了「不许编、不许重、不许空」：
      · 数值在原文找不到
      · 与同篇其它知识点重复
      · 正文过短/过长、证据行号格式错
    其余（分数偏低、类型缺失）只告警，不拦。
    """
    title = str(kp.get("title") or "").strip()
    body = str(kp.get("body") or "").strip()
    ev = str(kp.get("evidence") or "").strip()
    errs, warns = [], []

    if not title:
        errs.append("标题为空")
    if len(body) < 40:
        errs.append("正文过短（%d 字，最少 40）" % len(body))
    if len(body) > 800:
        errs.append("正文过长（%d 字，最多 800）" % len(body))
    if title and len(title) > 40:
        warns.append("标题 %d 字，建议 ≤30 字" % len(title))
    if ev:
        # 规范允许「L375-L387,L515-L532」这类多段定位（多条用英文逗号），
        # 因此逐段校验，而不是只认单段——否则会把合规写法误判成格式问题。
        segs = [s.strip() for s in re.split(r"[,，、;；]", ev) if s.strip()]
        rng = re.compile(r"^(L?)(\d+)\s*[-–~至]\s*(L?)(\d+)$")
        parsed = [(s, rng.match(s)) for s in segs]
        bad = [s for s, m in parsed if not m]
        bare = [s for s, m in parsed if m and not m.group(1) and not m.group(3)]
        if bare and not bad:
            # 「120-135」少了 L 前缀，是明确的格式笔误
            errs.append("证据行号缺 L 前缀，应为 L120-L135（当前 %r）" % bare[0])
        if bad:
            # 非行号定位（「内容提要（7.2 花岗岩类型、表 3）」「图 9」）是 Agent 模式的
            # 既有约定，只提示、不拦——它不是错，只是不可机读
            warns.append("证据定位非行号格式（%s），检索可读性略差" % ev[:24])
        for s, m in parsed:
            if m and int(m.group(4)) - int(m.group(2)) > 60:
                warns.append("证据跨度 %d 行，建议 ≤20 行" % (int(m.group(4)) - int(m.group(2))))

    ok, miss = verify_numbers(body + " " + title, source)
    if not ok:
        errs.append("数值在原文未找到: " + ", ".join(miss[:5]))

    for p in peers:
        sim = similarity(body, str(p.get("body") or ""))
        if sim >= DUP_SIM:
            errs.append("与「%s」重复（相似度 %.2f），请合并" %
                        (str(p.get("title") or "")[:20], sim))

    sc = score_candidate({"title": title, "body": body,
                          "type": kp.get("type") or kp.get("kp_type"),
                          "reuse": kp.get("reuse") or kp.get("kp_reuse")}, lang)
    if sc["score"] < MIN_SCORE_AGENT:
        warns.append("信息量偏低（%.2f < %.2f）：%s" %
                     (sc["score"], MIN_SCORE_AGENT, ",".join(sc["flags"]) or "无数值/无机制"))
    if "generic" in sc["flags"]:
        warns.append("疑似套话表述，建议改写为可检索的事实")
    return {"errors": errs, "warnings": warns, "score": sc["score"],
            "type": sc["type"], "reuse": sc["reuse"]}


# ════════════════════════════════════════════════════════════
# 合并（重复候选 → 一条）
# ════════════════════════════════════════════════════════════
def merge_pair(a, b):
    """把 b 并入 a：正文接续、数值/标签并集、证据取并区间。"""
    out = dict(a)
    ba, bb = str(a.get("body") or ""), str(b.get("body") or "")
    out["body"] = ba if bb in ba else (ba + "；" + bb if bb not in ba else ba)
    out["title"] = a.get("title") or b.get("title")
    out["tags"] = sorted(set(list(a.get("tags") or []) + list(b.get("tags") or [])))
    ea = str(a.get("evidence") or "")
    eb = str(b.get("evidence") or "")
    na = re.findall(r"\d+", ea)
    nb = re.findall(r"\d+", eb)
    if na and nb:
        out["evidence"] = "L%s-L%s" % (min(na + nb, key=int), max(na + nb, key=int))
    return out


# ════════════════════════════════════════════════════════════
# CLI：审计历史库 / 查看单篇覆盖
# ════════════════════════════════════════════════════════════
def _fm_val(raw, key):
    """取 frontmatter 单行值。写入端统一用 json.dumps 转义，故先按 JSON 解码。

    历史坑：Agent 路径的标题里带引号时写成 `title: "\\"…\\""`，
    用 `^title:\\s*"?([^"\\n]+)"?` 只能抓到第一个反斜杠（实测标题全变 `\\`）。
    """
    m = re.search(r"(?m)^%s:\s*(.+)$" % key, raw)
    if not m:
        return ""
    v = m.group(1).strip()
    if v.startswith('"'):
        try:
            v = json.loads(v)
        except Exception:  # noqa: BLE001
            v = v.strip('"')
    return str(v).strip().strip('"').strip()


def _read_kp(path):
    raw = Path(path).read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"## 知识点\s*\n+([\s\S]*?)\n+\*\*证据", raw)
    body = (m.group(1) if m else "").strip().strip('"').strip()
    ev = re.search(r"\*\*证据行号\*\*：\s*([^\n]+)", raw)
    return {"title": _fm_val(raw, "title"), "body": body,
            "evidence": (ev.group(1) if ev else "").strip()}


def audit_existing(limit=0, show=12):
    """给全库已有知识点打分，输出分布 + 低分样例 + 同篇重复。"""
    import glob
    from collections import Counter, defaultdict
    files = sorted(glob.glob(str(BASE / "content" / "kpoints" / "kp-*.md")))
    if limit:
        files = files[:limit]
    bysha = defaultdict(list)
    for f in files:
        m = re.match(r"kp-([0-9a-f]{12})-(\d+)\.md", Path(f).name)
        if m:
            bysha[m.group(1)].append(f)

    scores, low, dups, types = [], [], [], Counter()
    reuse_cnt = Counter()
    per_paper = Counter()
    survive = Counter()
    for sha, fs in bysha.items():
        kps = [_read_kp(f) for f in fs]
        per_paper[len(kps)] += 1
        paper_scores = []
        for i, k in enumerate(kps):
            sc = score_candidate(k)
            scores.append(sc["score"])
            paper_scores.append(sc["score"])
            types[sc["type"]] += 1
            reuse_cnt[sc["reuse"]] += 1
            if sc["score"] < MIN_SCORE_RULE:
                low.append((round(sc["score"], 3), Path(fs[i]).name,
                            k["title"][:36], k["body"][:90]))
            for j in range(i + 1, len(kps)):
                sim = similarity(k["body"], kps[j]["body"])
                if sim >= DUP_SIM:
                    dups.append((sim, Path(fs[i]).name, Path(fs[j]).name))
        best = max(paper_scores, default=0.0)
        rel = best * REL_FLOOR
        # 历史条目没有 AI 主观分，只能用规则分口径（MIN_SCORE_RULE）模拟，
        # 用融合阈值 0.58 去比规则分会把整库误判成大面积不合格
        survive[sum(1 for s in paper_scores if s >= MIN_SCORE_RULE and s >= rel)] += 1

    scores.sort()
    n = len(scores)
    pct = lambda p: scores[min(int(n * p), n - 1)] if n else 0
    print("=" * 74)
    print("知识点打分审计  |  版本 %s  |  文件 %d 篇 / %d 条" % (KP_VERSION, len(bysha), n))
    print("=" * 74)
    print("分数分位  p10 %.3f  p25 %.3f  p50 %.3f  p75 %.3f  p90 %.3f"
          % (pct(0.10), pct(0.25), pct(0.50), pct(0.75), pct(0.90)))
    for th in (0.30, MIN_SCORE_RULE, 0.50, 0.60):
        c = sum(1 for s in scores if s < th)
        print("  低于 %.2f：%d 条（%.1f%%）" % (th, c, 100.0 * c / max(n, 1)))
    print("每篇条数分布：", dict(sorted(per_paper.items())))
    print("按新规则重选后条数分布（仅阈值模拟，不含补抽）：", dict(sorted(survive.items())))
    print("类型分布：", dict(types.most_common()))
    print("复用模式分布：", dict(reuse_cnt.most_common()))
    print("同篇重复对（≥%.2f）：%d" % (DUP_SIM, len(dups)))
    low.sort()
    print("-" * 74)
    print("低分样例（前 %d 条，最可能属于凑数）" % show)
    for sc, fn, ti, bd in low[:show]:
        print("  %.3f  %s  %s" % (sc, fn, ti))
        print("         %s" % bd.replace("\n", " "))
    return 0


def show_coverage(sha12):
    """打印某篇的覆盖槽位与现有知识点命中情况。"""
    import glob
    txt = BASE / "state" / "text" / ("%s.txt" % sha12)
    hits = glob.glob(str(BASE / "state" / "text" / ("%s*.txt" % sha12)))
    if not txt.exists() and hits:
        txt = Path(hits[0])
    if not txt.exists():
        print("找不到原文:", sha12)
        return 1
    source = txt.read_text(encoding="utf-8", errors="ignore")
    kps = [_read_kp(f) for f in sorted(glob.glob(
        str(BASE / "content" / "kpoints" / ("kp-%s-*.md" % sha12))))]
    slots = claim_slots(source)
    mark_coverage(kps, slots)
    rep = coverage_report(kps, slots)
    print("sha %s  知识点 %d 条  槽位 %d  覆盖 %d（%.0f%%）"
          % (sha12, len(kps), rep["slots"], rep["covered"], rep["rate"] * 100))
    for i, s in enumerate(slots, 1):
        flag = "OK " if s.get("covered_by") else "GAP"
        print("  %s #%d %s" % (flag, i, s["text"][:110]))
        if s.get("covered_by"):
            print("         ← %s" % "；".join(s["covered_by"])[:90])
    return 0


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="知识点候选筛选引擎")
    sub = ap.add_subparsers(dest="cmd")
    a1 = sub.add_parser("audit-existing", help="给已有知识点打分")
    a1.add_argument("--limit", type=int, default=0)
    a1.add_argument("--show", type=int, default=12)
    a2 = sub.add_parser("coverage", help="查看某篇的覆盖情况")
    a2.add_argument("sha12")
    a3 = sub.add_parser("score", help="给一条文本打分（调试用）")
    a3.add_argument("--title", default="")
    a3.add_argument("--body", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "audit-existing":
        return audit_existing(args.limit, args.show)
    if args.cmd == "coverage":
        return show_coverage(args.sha12)
    if args.cmd == "score":
        print(json.dumps(score_candidate({"title": args.title, "body": args.body},
                                         ), ensure_ascii=False, indent=2))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
