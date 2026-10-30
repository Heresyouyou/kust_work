# -*- coding: utf-8 -*-
"""论文全文的统一预处理入口（摘要任务与知识点任务共用）。

为什么必须共用（2026-10-19）：
    DeepSeek 的自动前缀缓存只认「完全相同的 messages 前缀」，命中价是未命中的
    1/50。此前两个任务各截各的（摘要 smart_truncate 5000 / 知识点 [:12000] 字符），
    指令又都在全文前面，导致实测缓存命中率仅 **1.5%**，输入侧几乎全按最贵档付费。

    现在统一为：同一段清洗后文本 + 同一 system + 全文前置、指令后置。
    这样第二个任务能命中第一个任务留下的 KV 前缀，输入计费降到约 1/10。

对外只暴露三个函数：
    clean(raw)              去参考文献/致谢/页眉页脚/重复行
    truncate(text, budget)  头部 60% + 结论段 40%（token 预算）
    prepare(raw, budget)    clean + truncate，返回可直接拼进 prompt 的文本
"""
import re
from collections import Counter

# ─── 两个任务共用的 system（必须逐字节相同才能命中前缀缓存）────
SYSTEM_PROMPT = (
    "你是地矿学科文献处理助手。严格按用户要求输出一个 JSON 对象，"
    "不要输出任何其他文字、解释或 markdown 代码块。"
)

# 两个任务共用的文本预算（token）。**必须两处引用同一个常量**，否则文本不同、
# 前缀不匹配、缓存失效。
# 定 10000 的依据（scripts/_budget_locator.py，2026-10-19）：
#   bg=5000/8000/10000 的知识点质量指标与旧版 12000 字符硬截断持平
#   （含数值率 0.50/0.42/0.42 vs 旧版 0.42，单篇方差极大故视为等价），
#   而 bg=10000 送入约 6,600–7,300 token，与旧版实测 6,766 token 等价，
#   但覆盖到了结论段（旧版只能看到全文前 17%）。
INGEST_BUDGET = 10000

# ─── 章节/区块识别 ────────────────────────────────────────────
RE_REFS = re.compile(
    r"(?im)^\s*(references|reference list|bibliography|acknowledg\w*|"
    r"data availability|conflicts? of interest|author contributions|"
    r"funding|supplementary materials|参考文献|致\s*谢)\s*[:.]?\s*$", re.I)
RE_CONCL = re.compile(
    r"(?im)^\s*(?:\d+\.?\s*)?(conclusions?|summary and conclusions?|"
    r"concluding remarks|discussion and conclusions?|结\s*论|结论与讨论)\s*[:.]?\s*$", re.I)
RE_BOILER = re.compile(
    r"(?i)(doi\.org/|https?://creativecommons|©\s*\d{4}|open access|"
    r"licensee|basel, switzerland|mdpi|elsevier|springer|frontiers\s+in|"
    r"received:|accepted:|published:|academic editor:|citation:|copyright:|"
    r"creative commons|terms and conditions|all rights reserved|"
    r"第\s*\d+\s*卷\s*第\s*\d+\s*期|收稿日期|修回日期|责任编辑|中图分类号)")
RE_CJK = re.compile(r"[\u4e00-\u9fff]")


def est_tokens(text):
    """CJK≈1 token/字；ASCII 实测 3.0 chars/token（不是 4）。"""
    cjk = len(RE_CJK.findall(text))
    return int(cjk + (len(text) - cjk) / 3.0)


def clean(raw):
    """L1 切尾部参考文献/致谢 → L2 去页眉页脚 → L3 去高频重复行。"""
    cut = len(raw)
    for m in RE_REFS.finditer(raw):
        if m.start() > len(raw) * 0.45:      # 只认后半段出现的，避免误伤正文引用讨论
            cut = min(cut, m.start())
    t = raw[:cut]

    keep = []
    for ln in t.split("\n"):
        s = ln.strip()
        if len(s) < 3:
            continue
        if RE_BOILER.search(s):
            continue
        keep.append(s)

    cnt = Counter(keep)
    keep = [ln for ln in keep if cnt[ln] < 3 or len(ln) > 60]
    return "\n".join(keep)


def truncate(text, budget=5000):
    """头部 60% + 结论段 40%。预算内原样返回，超出则两头取。

    ⚠ head 不能写死字符上限：早期版本用 text[:14000]，导致 budget 调到 12000
    token 时 head 仍只吃进 14,000 字符（英文约 4,700 token），预算形同虚设。
    现按比例取头部 65% 字符，再由 cut 按 token 精确收敛。
    """
    if est_tokens(text) <= budget:
        return text
    head_budget = int(budget * 0.6)
    tail_budget = budget - head_budget

    def cut(s, b):
        while s and est_tokens(s) > b:
            s = s[: int(len(s) * 0.85)]
        return s

    head = cut(text[: int(len(text) * 0.65)], head_budget)
    hits = list(RE_CONCL.finditer(text))
    if hits:
        # 结论段可能在 90% 之后；给 12,000 字符窗口，由 cut 收敛到 tail_budget
        seg = text[hits[-1].start(): hits[-1].start() + 12000]
        tail = "\n\n[结论段节选]\n" + cut(seg, tail_budget)
    else:
        tail = "\n\n[文末节选]\n" + cut(text[-12000:], tail_budget)
    return head + tail


def numbered(text):
    """加行号版（知识点任务用它做证据引用）。

    ⚠ 必须由**两个任务共用**：行号会让文本内容变化（每行多 "L123:"），
    若只给知识点任务加行号，它的前缀就与摘要任务不同，缓存立刻失效。
    故 prepare() 统一输出行号版，摘要任务也吃同一份文本（多付约 8% 字符，
    但换来输入计费降一个量级，净赚）。
    """
    lines = [l for l in text.split("\n") if l.strip()]
    return "\n".join("L%d:%s" % (i + 1, l) for i, l in enumerate(lines))


def prepare(raw, budget=5000):
    """论文原文 → 可直接拼进 prompt 的统一文本（行号版，两任务共用）。

    两个 LLM 任务必须用**同一次调用的同一个返回值**，否则前缀不同、缓存失效。
    预算先按纯文本 92% 截断，给行号前缀留余量。
    """
    return numbered(truncate(clean(raw), int(budget * 0.92)))


# ═══════════════════════════════════════════════════════════════════════
# 句子级筛选（2026-10-20）：替代硬截断，用更少的 token 保住更多事实
#
# 实测（scripts/_budget_probe.py，20 篇真实文献）：
#   方案              预算      关键数值保留率（中位）
#   现有 head+tail    10000     30%      ← 中间大段结果/表格全被砍掉
#   硬截断             4500     18%
#   句子筛选           4500     87%      ← token 只用 45%，事实反而更全
#
# 为什么保留率能反超：论文的关键事实（年龄、品位、同位素、坐标、矿物名）
# 集中在「结果/结论/摘要」的少数句子里。按「含数值、含结论动词、段落位置」
# 给句子打分再按预算取，比按字符位置切更能捞到证据句；而 `sorted(keep)`
# 保证输出仍是原文顺序，不吃掉上下文。
#
# 默认关闭（GEOKB_PREP=select 开启），未做 A/B 前不得改生产行为。
# ═══════════════════════════════════════════════════════════════════════
import os as _os

PREP_MODE = _os.environ.get("GEOKB_PREP", "headtail").strip().lower()
SELECT_BUDGET = int(_os.environ.get("GEOKB_SELECT_BUDGET", "4500"))

# 带单位的数值最值钱（年龄/品位/深度/温度/比例）
RE_NUM = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|wt\s*%|ppm|ppb|ppt|Ma|Ga|ka|ka\b|km|cm|mm|um|µm|nm|"
    r"°C|℃|K\b|g/cm3|g/t|Mt|t\b|km2|m\b|m/s|MPa|GPa|bar|kmol|mol|mg/L|‰|δ)", re.I)
# 结论性/事实性动词
RE_EVID = re.compile(
    r"(?i)\b(result|results|conclusion|conclusions|indicate[sd]?|suggest\w*|"
    r"show(?:s|ed)?|reveal\w*|therefore|thus|we\s+find|imply|implies|"
    r"consistent\s+with|suggesting|indicating|constrain\w*|yield(?:s|ed)?|"
    r"dated|age\s+of|grade|content|enrich\w*|mineraliz\w*|isotop\w*)\b|"
    r"(结果|结论|表明|显示|指示|因此|认为|揭示|限定|年龄|定年|品位|含量|"
    r"富集|成矿|同位素|蚀变|矿化)")
# 应整段丢弃的噪声块
RE_DROP = re.compile(
    r"(?i)^\s*(fig(?:ure)?\.?\s*\d|table\s*\d|scheme\s*\d|appendix|"
    r"supplementary|highlights?\b|graphical\s+abstract)|^\s*图\s*\d|^\s*表\s*\d")

_SPLIT = re.compile(r"(?<=[.!?;。；！？])\s+")

# 区段定位（用于 select_sentences 的两段式分配）
RE_ABS_HDR = re.compile(
    r"(?im)^[ 	]*(?:a\s*b\s*s\s*t\s*r\s*a\s*c\s*t|摘\s*要)\s*[:.．]?\s*(.*)$")
RE_ABS_END = re.compile(
    r"(?im)^\s*(?:keywords?|key\s*words?|index\s+terms|introduction|"
    r"1\.?\s+introduction|关键词|关键字|引言|一\s*、\s*引\s*言)\s*[:.．]?\s*$")
RE_REF_HDR = re.compile(
    r"(?im)^\s*(?:references|bibliography|acknowledg\w*|参考文献|致\s*谢)\s*[:.．]?\s*$")


def _sentences(cleaned):
    """按行→句切分，过滤图注/表注等低信息段落。"""
    out = []
    for ln in cleaned.split("\n"):
        s0 = ln.strip()
        if not s0 or RE_DROP.search(s0):
            continue
        for s in _SPLIT.split(s0):
            s = s.strip()
            if len(s) >= 25:            # 太短的多是标题残片/页眉
                out.append(s)
    return out


def _score(i, s, n):
    """句子信息量打分。位置权重让摘要区与结论区优先。"""
    sc = 0.0
    if RE_NUM.search(s):
        sc += 2.0                       # 带单位的数值：最强的证据信号
    if RE_EVID.search(s):
        sc += 1.5
    if 60 <= len(s) <= 600:             # 正常长句；超长句往往是表格糊成一行
        sc += 0.5
    elif len(s) > 1200:
        sc -= 1.0
    rel = i / max(n - 1, 1)
    if rel < 0.15:
        sc += 1.0                       # 摘要/引言区
    if rel > 0.80:
        sc += 1.2                       # 结论区
    return sc


def select_sentences(text, budget=4500):
    """按信息量挑句，预算内尽量多装事实；输出保持原文顺序。

    与 truncate() 的区别：truncate 按字符位置切（头部 65% + 结论段），
    本函数按「区段优先 + 证据密度」切。

    ⚠ 2026-10-20 修正：纯全局打分在「含数值句特别多」的篇目上会失手 ——
    单篇可能有 500+ 个数值句，光靠 "含数值 +2 分" 无法区分主次，
    预算装不下就随机丢，实测个别篇有效数值召回掉到 0.12。
    改为两段式：**摘要段与结论段先整段保住**（这两段正是事实最密处，
    也是旧 head+tail 方案唯一真正吃到的东西），剩余预算再按密度全局挑。
    """
    segs = _segments(text)
    parts = []                          # [(start_char, text)]
    used = 0
    cap = int(budget * 0.92)
    for key, share in (('head', 0.14), ('abstract', 0.32), ('concl', 0.30)):
        r = segs.get(key)
        if not r:
            continue
        s, e = r
        k = _fit_sentences(text[s:e], int(cap * share))
        if k:
            parts.append((s, k))
            used += est_tokens(k)

    rest = cap - used
    if rest > 150:
        sents = _sentences_with_pos(text, skip=list(segs.values()))
        if sents:
            n = len(sents)
            order = sorted(range(n), key=lambda i: -_score(i, sents[i][2], n))
            got, u2 = [], 0
            for i in order:
                t = est_tokens(sents[i][2])
                if u2 + t > rest:
                    continue
                got.append(sents[i])
                u2 += t
            if got:
                parts.append((got[0][0], '\n'.join(x[2] for x in got)))

    if not parts:
        return truncate(text, budget)
    parts.sort(key=lambda x: x[0])       # 还原原文顺序
    return "\n".join(t for _, t in parts)


def _fit_sentences(seg, budget):
    """区段内按原顺序尽量装满（不重排，保上下文）。"""
    out, used = [], 0
    for s in _SPLIT.split(seg):
        s = s.strip()
        if len(s) < 20:
            continue
        t = est_tokens(s)
        if used + t > budget:
            break
        out.append(s)
        used += t
    return '\n'.join(out)


def _segments(text):
    """返回 {name: (start, end)} 字符区间。

    三个候选区段，都是「事实最密」的地方：
      head     篇首段 —— 有些刊（Elements 等）正文直接从摘要段落开始，
                          没有 "Abstract" 标题行；中文刊常见中英文双摘要，
                          中文摘要在最前面。所以篇首必须单独保。
      abstract 带 "Abstract/摘要" 标题的摘要段
      concl    结论段
    """
    out = {}
    m = RE_ABS_HDR.search(text)
    if m:
        e = RE_ABS_END.search(text, m.start(1))
        stop = e.start() if e else min(len(text), m.start(1) + 6000)
        out['abstract'] = (m.start(1), stop)
        if m.start() > 800:                 # 摘要前还有内容（中文摘要/图摘要）
            out['head'] = (0, min(m.start(), 3500))
    else:
        # 无标题摘要：把篇首当作等效摘要段（标题+作者+摘要通常都在前 4~5k 字符）
        out['head'] = (0, min(len(text), 4500))
    hits = list(RE_CONCL.finditer(text))
    if hits:
        h = hits[-1]
        e = RE_REF_HDR.search(text, h.end())
        stop = e.start() if e else min(len(text), h.end() + 6000)
        out['concl'] = (h.end(), stop)
    return out


def _sentences_with_pos(text, skip=()):
    """带字符位置的句子列表，跳过已选中的区段。"""
    out = []
    pos = 0
    for ln in text.split("\n"):
        s0 = ln.strip()
        st = pos
        pos += len(ln) + 1
        if not s0 or RE_DROP.search(s0):
            continue
        if any(a <= st < b for a, b in (skip or ())):
            continue
        for s in _SPLIT.split(s0):
            s = s.strip()
            if len(s) >= 25:
                out.append((st, st, s))
    return out


def prepare2(raw, budget=None, mode=None):
    """可切换的预处理入口（A/B 用）。

    mode='block'    → 段落/块级筛选（推荐，省 token 且不断逻辑链）
    mode='select'   → 单句筛选（已知会打散逻辑，仅留作对照）
    mode='headtail' → 旧行为，逐字节等价于 prepare()
    两个 LLM 任务必须用**同一 mode 的同一返回值**，否则前缀缓存失效。
    """
    m = (mode or PREP_MODE).strip().lower()
    if m == "block":
        # 生产调用方传的是 INGEST_BUDGET(10000)，那是 head+tail 的预算口径；
        # block 模式有自己的预算（默认 4500），不能沿用 10000 否则等于没筛。
        b = budget if (budget and budget != INGEST_BUDGET) else BLOCK_BUDGET
        return numbered(select_blocks(clean(raw), b))
    if m == "select":
        return numbered(select_sentences(clean(raw), budget or SELECT_BUDGET))
    return prepare(raw, budget or INGEST_BUDGET)


# ═══════════════════════════════════════════════════════════════════════
# 块级筛选（2026-10-20，C 方案第二版）
#
# 上一版按「单句」打分取舍，文本覆盖率 77% 却只换来输出召回 0.629。
# 原因不是漏了事实，而是**切碎了逻辑**：一个 3 句的论证被选中第 1、3 句、
# 丢掉第 2 句，模型看到的是互不衔接的断言，不敢写进 key_content。
#
# 本版把「原子单元」从句子放大到**段落**：
#   * PDF 折行先拼回真段落（折行 ≠ 段落，这点是上一版没做的）
#   * 以整段为单位取舍，段内逻辑完整
#   * 超长段落才在段内降级到句级，且保留前 5 句的连续窗（不跳选）
#   * 未选中的区间插入 […略…]，让模型知道这是节选而非全文
# ═══════════════════════════════════════════════════════════════════════

BLOCK_BUDGET = int(_os.environ.get("GEOKB_BLOCK_BUDGET", "4500"))
OMIT = "\n[…略…]\n"

# 行末是这些标点 → 视为一句/一段的自然结束
RE_END_PUNCT = re.compile(r'[.!?;:。！？；：…]["\')）】》”’]?$')
_CJK_TAIL = re.compile(r'[\u4e00-\u9fff。，、；：！？）】》”’]$')
_CJK_HEAD = re.compile(r'^[\u4e00-\u9fff（【《“‘]')


def reflow_paragraphs(cleaned):
    """把 PDF 折行拼回段落，返回段落字符串列表。

    为什么必须做：state/text 里的「行」是排版折行（中位行长 22–60 字符），
    一个自然段跨 3–10 行。若拿行当段落，段落级筛选会退化回「挑折行」，
    切点全落在句中——正是上一版失败的原因。

    断段的三条依据（任一命中即断开）：
      1. 上一行以句末标点结尾，且长度达到中位行长的 55% 以上
         （满行 + 句号 = 自然段末；只有句号但很短的多是并列小标题）
      2. 上一行明显短（< 55% 中位行长）→ 标题/段末残行
      3. 当前行以明显的小节编号开头（1. / 2.1 / 一、 / （1））
    """
    lines = [l.strip() for l in cleaned.split("\n") if l.strip()]
    if not lines:
        return []
    lens = sorted(len(l) for l in lines)
    med = lens[len(lens) // 2] or 40
    thresh = med * 0.55

    paras, buf = [], []
    for ln in lines:
        if buf:
            prev = buf[-1]
            starts_section = bool(RE_SECTION_HDR.match(ln))
            ended = bool(RE_END_PUNCT.search(prev)) and len(prev) >= thresh
            short = len(prev) < thresh
            if ended or short or starts_section:
                paras.append(buf)
                buf = []
        buf.append(ln)
    if buf:
        paras.append(buf)

    out = []
    for p in paras:
        s = ""
        for ln in p:
            if not s:
                s = ln
            elif _CJK_TAIL.search(s) or _CJK_HEAD.match(ln):
                s += ln                     # 中文折行不加空格
            else:
                s += " " + ln
        out.append(s)
    return out


# 小节标题：1. / 2.1 / 3.2.1 / 一、 / （1） / 罗马数字
RE_SECTION_HDR = re.compile(
    r'^\s*(?:\d+(?:\.\d+)*\.?|[一二三四五六七八九十]+[、.]|'
    r'[（(]\s*\d+\s*[）)]|[IVX]{2,}\.?)'
    r'\s*\S')


def _para_blocks(paras, max_chars=1100):
    """段落 → 块。超长段落在段内切分母块（仍在句末切），每块不超过 max_chars。"""
    blocks = []
    for p in paras:
        if len(p) <= max_chars:
            blocks.append(p)
            continue
        cur = ""
        for s in _SPLIT.split(p):
            if not s:
                continue
            if cur and len(cur) + len(s) + 1 > max_chars:
                blocks.append(cur)
                cur = s
            else:
                cur = (cur + " " + s) if cur else s
        if cur:
            blocks.append(cur)
    return blocks


def _block_score(b, rel, is_head, is_concl, is_abs):
    """块的信息密度打分（按每 100 token 归一，避免长块天然占优）。"""
    tk = max(est_tokens(b), 1)
    nums = len(RE_NUM.findall(b))
    evid = len(RE_EVID.findall(b))
    sc = (nums * 2.0 + evid * 1.5) * 100.0 / tk
    if is_abs:
        sc += 6.0                           # 摘要 = 全文事实提纲，最值钱
    if is_concl:
        sc += 4.0
    if is_head:
        sc += 2.0
    if rel > 0.85:
        sc += 1.0                           # 末尾区常是结论/讨论收束
    if len(b) < 120:
        sc -= 1.0                           # 碎片（残留标题）
    return sc


def select_blocks(text, budget=4500):
    """段落级筛选：整段取舍，保逻辑完整；按预算贪心装入。

    与 select_sentences 的差别是**原子单元**：
      句级 → 同一论证的 3 句可被拆散，模型读到互不衔接的断言
      段级 → 要么整段给，要么整段不给，段内连贯
    """
    paras = reflow_paragraphs(text)
    if not paras:
        return truncate(text, budget)
    blocks = _para_blocks(paras)
    if not blocks:
        return truncate(text, budget)

    # 短文直接给全文：全文不超过预算 1.8 倍时，筛掉的那点 token 换不来质量。
    # 实测 8f0c7a3e（全文 8706 tok）按 4500 筛，数值覆盖只有 0.57；
    # 这类篇目的数值均匀分布、没有「高密度段」，块级打分挑不出主次。
    full = est_tokens(text)
    if full <= budget * 1.8:
        return text

    # 区段标记：摘要/结论/篇首的块直接加权
    segs = _segments(text)
    abs_rng, concl_rng, head_rng = (segs.get('abstract'), segs.get('concl'),
                                    segs.get('head'))

    # 段落起始在原文中的偏移（reflow 会丢位置，用顺序+长度重建近似值）
    total = sum(len(b) for b in blocks) or 1
    pos, meta = 0, []
    for i, b in enumerate(blocks):
        rel = pos / total
        # 用「块文本是否出现在摘要/结论区间内」的近似：按相对位置判
        is_abs = is_concl = is_head = False
        if abs_rng:
            a0, a1 = abs_rng[0] / max(len(text), 1), abs_rng[1] / max(len(text), 1)
            is_abs = a0 <= rel <= a1
        if concl_rng:
            c0, c1 = concl_rng[0] / max(len(text), 1), concl_rng[1] / max(len(text), 1)
            is_concl = c0 <= rel <= c1
        if head_rng:
            h0, h1 = head_rng[0] / max(len(text), 1), head_rng[1] / max(len(text), 1)
            is_head = h0 <= rel <= h1
        meta.append((_block_score(b, rel, is_head, is_concl, is_abs),
                     est_tokens(b), is_abs, is_concl))
        pos += len(b)

    n = len(blocks)
    order = sorted(range(n), key=lambda i: -meta[i][0])

    # 贪心装入：高分块优先；装不下的大块尝试段内句级降级
    chosen, used = {}, 0
    cap = int(budget * 0.95)

    # ⚠ 曾试过给摘要/结论区独立配额（顺序全选 38%/24%），实测**无收益**：
    #   abstract_en 逐字可查 5/6 → 4/6，数值召回 0.902 → 0.898。
    #   原因是摘要块的 is_abs 判定靠「块累计长度 / 全文长度」近似原文位置，
    #   精度不够；配额一开，判错的块会挤掉真正的高分块。
    #   故维持纯分数竞争（摘要块本身已有 +6.0 加分），不做位置配额。
    for i in order:
        sc, tk = meta[i][0], meta[i][1]
        if i in chosen:
            continue
        if used + tk <= cap:
            chosen[i] = blocks[i]
            used += tk
            continue
        # 大块装不下：按句取前段（保序、连续，不跳选）
        room = cap - used
        if room < 200:
            continue
        part, u = [], 0
        for s in _SPLIT.split(blocks[i]):
            s = s.strip()
            if not s:
                continue
            t = est_tokens(s)
            if u + t > room:
                break
            part.append(s)
            u += t
        if len(part) >= 2:                  # 至少 2 句才值得留
            chosen[i] = " ".join(part)
            used += u

    if not chosen:
        return truncate(text, budget)

    # 补缝：被跳过的孤立单块，若前后都选中且预算允许，补回来（减少跳跃）
    idx = sorted(chosen)
    for a, b in zip(idx, idx[1:]):
        if b - a == 2 and (b - 1) not in chosen:
            j = b - 1
            if used + meta[j][1] <= cap * 1.04:
                chosen[j] = blocks[j]
                used += meta[j][1]

    out, prev = [], -1
    for i in sorted(chosen):
        if prev >= 0 and i != prev + 1:
            out.append(OMIT)
        out.append(chosen[i])
        prev = i
    return "\n".join(out)
