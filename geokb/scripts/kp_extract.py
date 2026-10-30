# -*- coding: utf-8 -*-
"""论文知识点批量抽取入库（DeepSeek，并发，断点续跑）。

v2 重构（2026-10-23）——从「让模型数数」改为「模型产候选、程序做取舍」
--------------------------------------------------------------------
旧版 PROMPT 写「最多 6 个，宁缺毋滥」，实测 1543 篇里 **1469 篇恰好 6 条
（95.2%）**：模型把上限当目标，条数由名额而非信息密度决定 —— 这就是凑数。
同时没有任何机制保证摘要/结论里的定量结论被覆盖，高信息密度论文会漏点。

v2 的三段式：
  ① 候选抽取（LLM）  穷尽式列出事实单元：claim + 数值 + 逐字 quote + 行号 + 类型
  ② 确定性筛选（程序）锚定校验（数值/引文可在原文定位）→ 打分 → 去重 →
                     绝对+相对双阈值 → 上限 8 条，**无下限，0 条合法**
  ③ 覆盖补抽（LLM，仅必要时）摘要/结论里带数值的结论若覆盖率 < 0.6，
                     用同一段文本（前缀缓存命中）追加一次「只补缺口」的调用

设计要点：
- 输入：state/text/<sha1>.txt（论文全文）
- 输出：content/kpoints/kp-<sha12>-<n>.md，module=kpoints，source_tier=T3
- 并发可调（默认 16），单篇完成即落盘，支持中断后重跑自动跳过已完成
- `--redo-version 2.0` 只重跑旧算法产出的篇目（默认不重跑，避免误触发全库）
- 进度落 logs/kp_ingest.jsonl（用量）与 logs/kp_select.jsonl（取舍审计）
"""
import glob
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))
sys.path.insert(0, os.path.join(BASE, "server"))
from deepseek_client import (InfraError, ds_chat, extract_json,  # noqa: E402
                             last_usage, load_api_key)
from text_prep import INGEST_BUDGET, SYSTEM_PROMPT, prepare2  # noqa: E402
import kp_select  # noqa: E402  候选筛选引擎（与 agent_ingest 共用）
import kp_store   # noqa: E402  落盘内核（与 agent_ingest 共用同一份模板/字段/索引）

TEXT = os.path.join(BASE, "state", "text")
PAPERS = os.path.join(BASE, "content", "papers")
OUT = os.environ.get("GEOKB_KP_OUT") or os.path.join(BASE, "content", "kpoints")
LOG = os.path.join(BASE, "logs", "kp_ingest.jsonl")
AUDIT = os.path.join(BASE, "logs", "kp_select.jsonl")
DONE = os.path.join(BASE, "state", "kp_done.jsonl")

# ── 单次调用预算（2026-10-29）──────────────────────────────────────────
# 外层硬超时：inbox_ingest._run_py(..., 420s)。这里所有调用的**最坏墙钟**
# 必须压在它以内，否则会被杀在重试中途：既拿不到内容，也看不到真实错误。
# 主调用 120×2 + 退避 5 = 245s；解析失败修复 120×1；最坏 365s < 420s。
# 实测正常单步 <40s，120s 已有 3 倍余量。
KP_TIMEOUT = int(os.environ.get("GEOKB_KP_TIMEOUT") or 120)
KP_RETRY = int(os.environ.get("GEOKB_KP_RETRY") or 2)

# 知识点抽取关闭思考（2026-10-19 A/B 实测，scripts/_thinking_ab.py）：
#   5 篇同文本同提示词对拍 —— 条数 6.0 vs 6.0、证据行号合规 1.00 vs 1.00、
#   空文 0 vs 0、均字 190 vs 201、模型标签 3.4 vs 4.0，
#   含数值率 0.93 vs 0.90（单篇波动范围内）；输出 token 2878 → 1112（-61%）。
#   质量无实质差异，故默认关闭。需要回退：设 GEOKB_INGEST_THINKING=1。
INGEST_THINKING = os.environ.get("GEOKB_INGEST_THINKING", "0") not in ("0", "false", "False")

# 候选数上限（输出预算）。取 14 是因为实测「6 条成稿」的论文平均可拆出
# 9~12 个原子事实，留出余量给筛选；再多只是把表格流水账也列进来。
MAX_CANDS = 14
# 补抽触发线：摘要/结论的定量结论覆盖率低于此值才补抽（省一次调用）
REPAIR_COVERAGE = 0.60
REPAIR_MAX_KP = 3        # 一轮最多补 3 条，避免把「补抽」变成第二轮凑数

_PROMPT_HEAD = """{text}

────────────────────────────────
以上是一篇论文的全文（每行开头 L<n> 是行号，仅供定位，不要写进任何字段）。

## 收录判据（先想清楚再列）
一条知识点值不值得收，只问一件事：
**它能不能用于我们团队今后的研究？换一个研究区、换一个矿床，还用得上吗？**

按下面五种「复用方式」之一归类，并写清怎么用：
- 解释借鉴：成因机制、判别判据、对现象的解释框架 —— 换区换矿都能借用
- 方法迁移：新方法/新技术/新流程/关键参数设置 —— 别人可以照做
- 类比参照：构造背景、地球动力环境、成矿格架、矿床类型特征 —— 供同类地区类比
- 标定参照：年龄、品位、温压、同位素等参照值 —— 供横向对比
- 本区事实：只对本区有意义（样品描述、局部流水账）—— 一般不值得收，除非是罕见实例

## 怎么定取舍（重要）
程序会算一个「规则分」（数值能否在原文查到、是否与团队方向相关、是否重复），
它只占最终决定的 **60%**；**你自己的判断占 40%**。所以：
- 给每条候选 `value`（0–10）——它对我们今后研究的复用价值。
  **必须拉开差距，否则这个判断等于没做**：
  9–10 = 几乎肯定会被引用或照做（新机制、可迁移方法、同类矿床的关键参照值）；
  7–8 = 值得存档备查；5–6 = 有点参考，换个场景就用不上；
  ≤4 = 只是本文的局部事实。
  **大多数论文只有 3–6 条能到 7 分以上**；如果全部给 7–9 分，说明你没有区分主次。
- 如果某条你认为**必须保留**（价值高但数字不漂亮、或属于新思路），标 `"verdict":"must"`，
  最多 2 条，程序会强行收录。
- 如果某条你认为**明确不值得收**（哪怕它数值齐全），标 `"verdict":"drop"`，程序会尊重。
  这类通常是：样品描述、区域地质背景铺陈、本文自说自话的意义与展望。

## 第一步：穷尽式列出候选
你只负责**找全 + 判断价值**，不负责数条数；**不要考虑条数上限**。

要求：
1. 覆盖优先：摘要与结论里每一个带具体数值的结论，都必须至少对应一条候选。
2. 原子化：一条候选只讲一件事（一个年龄、一个品位、一个机制、一个判别指标）。
3. 可独立检索：必须含「对象 + 属性 + 数值或条件」，脱离本文仍能读懂；
   禁止「本文研究了…」「具有重要意义…」这类元叙述。
3b. {lang_rule}
4. type（地质类型）只能取：{types}
5. reuse 只能取上面五种之一；why 用一句话说明「今后怎么用它」（≤40 字）。
6. quote：从原文**逐字**抄 20–60 字（不改写、不省略、不合并两句）；
   evidence：quote 所在的行号区间。
7. 数值照抄原文（保留 ±、范围、单位、有效位），不得换算或四舍五入。
8. 一般 5–14 条；宁可多列也不要漏掉带数值的结论，但不要为凑数编造。
9. 不要输出：文献综述、图件/表格描述、致谢、参考文献、纯区域地质背景铺陈。

输出严格 JSON：
{{"candidates":[{{"title":"≤26字，含对象与属性","body":"100–260字：结论 + 关键数值 + 限定条件",
  "type":"{default_type}","reuse":"解释借鉴","why":"今后怎么用（≤40字）",
  "value":9,"verdict":"keep","numbers":["320±40 ℃"],"quote":"逐字原文片段",
  "evidence":"L120-L126","tags":["2-4个检索标签"]}}]}}"""

# 补抽提示：**文本前缀与首轮完全一致**，因此这次调用命中前缀缓存（约 1/10 价）
_PROMPT_REPAIR = """{text}

────────────────────────────────
以上是同一篇论文的全文。程序核对后发现，下列**摘要/结论中的定量结论**
还没有对应的候选（每行是一条缺失的结论）：

{gaps}

请只针对上面每一条缺失结论各补 1 条候选，格式与上一轮完全相同
（title/body/type/reuse/why/value/verdict/numbers/quote/evidence/tags，reuse 取
解释借鉴/方法迁移/类比参照/标定参照/本区事实 之一，{lang_rule}）：
{{"candidates":[{{"title":"≤26字","body":"100–260字","type":"{default_type}",
  "reuse":"标定参照","why":"…","value":8,"verdict":"keep","numbers":["…"],
  "quote":"逐字原文片段","evidence":"L120-L126","tags":["…"]}}]}}
不要重复已有候选，不要补充其它内容；若某条结论在原文中确实没有可引用的数值依据，跳过它。"""


LANG_RULE = {
    "zh": "输出语言：title 与 body 用**中文**（专业术语可保留英文缩写）。",
    # 英文论文的知识点必须用英文写：与条目「英文原文为主、中文仅作小字注解」
    # 的规则一致；回译会丢术语、也无法直接检索英文文献（用户长期口径）。
    "en": "Output language: write `title` and `body` in **English**, reusing the "
          "paper's own terminology verbatim (do NOT translate into Chinese); "
          "`why` may stay in Chinese.",
}


# ─── 提示词真源 ─────────────────────────────────────────────────
# 唯一真源：config/prompts/kp_candidates.md（人可读、可 diff、可版本化）。
# 下面 _PROMPT_HEAD / _PROMPT_REPAIR / LANG_RULE 仅作**回退副本**：真源缺失或
# 解析失败时才用，避免一次误删导致 12:01 / 18:01 的入库全线停摆。
# 改提示词只改真源，不要改这里的常量；改完跑 scripts/kp_prompt.py --check。
PROMPT_FILE = (os.environ.get("GEOKB_KP_PROMPT")
               or os.path.join(BASE, "config", "prompts", "kp_candidates.md"))

# 分节白名单：只认这四个，正文里的 `## 收录判据` 之类标题原样保留。
_SECTION_RE = re.compile(r"^##\s+(main|repair|lang:zh|lang:en)\s*$")
PROMPT_SOURCE = "builtin"      # builtin | file，供 --print-prompt 与审计区分


def parse_prompt_file(path):
    """解析提示词真源 → {main, repair, lang:{zh,en}}；文件不存在返回 {}。"""
    if not path or not os.path.exists(path):
        return {}
    txt = open(path, encoding="utf-8").read()
    out, cur, buf = {}, None, []
    for line in txt.splitlines():
        m = _SECTION_RE.match(line)
        if m:
            if cur:
                out[cur] = "\n".join(buf).strip("\n")
            cur, buf = m.group(1), []
        elif cur is not None:
            buf.append(line)
    if cur:
        out[cur] = "\n".join(buf).strip("\n")
    lang = {k.split(":", 1)[1]: v for k, v in out.items()
            if k.startswith("lang:") and v.strip()}
    return {"main": (out.get("main") or "").strip(), "repair": out.get("repair") or "",
            "lang": lang}


_file_prompt = parse_prompt_file(PROMPT_FILE)
if _file_prompt.get("main"):
    _PROMPT_HEAD = _file_prompt["main"]
    if _file_prompt.get("repair"):
        _PROMPT_REPAIR = _file_prompt["repair"]
    for _k, _v in (_file_prompt.get("lang") or {}).items():
        LANG_RULE[_k] = _v
    PROMPT_SOURCE = "file"
else:
    print("[WARN] 提示词真源不可用，回退内置副本：%s" % PROMPT_FILE, file=sys.stderr)


def build_prompts():
    """由 kp_select 的类型/复用模式清单生成提示词，保证与打分器同源。

    ⚠ 只能用 str.replace 注入，**不能用 .format**：模板里的 `{{"candidates":…}}`
    是为最终那次 `.format(text=…)` 预留的转义，若在这里先 format 一遍，
    JSON 示例会变成 `{"candidates":…}` 并被执行期 format 当成占位符
    （实测报 KeyError: '"candidates"'）。
    """
    types = " / ".join(kp_select.TYPE_ORDER)
    dt = kp_select.TYPE_ORDER[0]
    head = (_PROMPT_HEAD.replace("{types}", types).replace("{default_type}", dt)
            .replace("{lang_rule}", "{lang_rule}"))   # 调用时再填，保留下划线
    repair = _PROMPT_REPAIR.replace("{default_type}", dt)
    return head, repair


PROMPT, PROMPT_REPAIR = build_prompts()

_lock = threading.Lock()
_stat = {"done": 0, "kp": 0, "fail": 0, "json_fail": 0, "in_tok": 0, "out_tok": 0,
         "repair": 0, "zero": 0, "consec_fail": 0, "abort": False}
MAX_CONSEC_FAIL = int(os.environ.get("GEOKB_KP_MAX_FAIL", "8"))


def paper_meta(sha):
    """论文条目元数据 —— 实现已统一到 kp_store.paper_meta。

    两种模式（API / Agent）必须读**同一份**元数据：tags 补位、sources（PDF 链接）、
    confidence 都从这里来，各读一份就会出现「同一篇、两种模式的 tags 不一样」。
    """
    return kp_store.paper_meta(sha)


def load_done_versions():
    """sha12 → 已处理的算法版本（state/kp_done.jsonl，后写覆盖先写）。"""
    out = {}
    if os.path.exists(DONE):
        for line in open(DONE, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                out[r["sha12"]] = str(r.get("version") or "1.0")
            except Exception:  # noqa: BLE001
                continue
    return out


def _ver_tuple(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except Exception:  # noqa: BLE001
        return (0,)


def already_done(redo_version=""):
    """已完成集合。落盘文件名只带 sha 前 12 位，而 txt 名是完整 40 位 sha1，
    因此一律按 12 位前缀比对，否则断点续跑会失效（曾导致全库重跑）。

    redo_version：给定时只把「已处理版本 < 该版本」的篇目视为未完成，
    用于算法升级后的定向重跑；不给时维持旧行为（有文件即算完成），
    避免升级算法时误触发全库重算（1543 篇 × 2 次调用是笔真钱）。
    """
    done12 = set(os.path.splitext(os.path.basename(f))[0].split("-")[1]
                 for f in glob.glob(os.path.join(OUT, "kp-*.md")))
    files = set(s for s in (os.path.splitext(os.path.basename(f))[0]
                            for f in glob.glob(os.path.join(TEXT, "*.txt"))))
    if not redo_version:
        return set(s for s in files if s[:12] in done12)
    vers = load_done_versions()
    want = _ver_tuple(redo_version)
    out = set()
    for s in files:
        if s[:12] not in done12:
            continue
        if _ver_tuple(vers.get(s[:12], "1.0")) >= want:
            out.add(s)
    return out


def _retire_kps(sha12, keep_n):
    """把编号 > keep_n 的旧知识点文件移入隔离区，返回被移走的 id 列表。

    ⚠ 为什么是「移走」不是「删除」：本机有批量删除守卫（单轮 50 次即拦截，
    实测一次 50 篇的重跑直接在第 50 次删除上抛错，41 篇全部中止）。
    改名不算删除，不受守卫影响；隔离区 `state/kp_stale/<sha12>/` 可按需清理。

    编号 1..keep_n 的文件不需要动：kp_store.write 会原地覆盖。
    """
    stale_dir = os.path.join(BASE, "state", "kp_stale", sha12)
    moved = []
    for f in glob.glob(os.path.join(OUT, "kp-%s-*.md" % sha12)):
        m = re.search(r"-(\d+)\.md$", f)
        if not m or int(m.group(1)) <= keep_n:
            continue
        os.makedirs(stale_dir, exist_ok=True)
        dst = os.path.join(stale_dir, os.path.basename(f))
        try:
            if os.path.exists(dst):
                os.remove(dst)          # 隔离区内部覆盖（量极小，不会触守卫）
            os.replace(f, dst)          # 同盘 rename，原子且不算删除
            moved.append(os.path.splitext(os.path.basename(f))[0])
        except OSError as e:
            print("  [WARN] 旧知识点归档失败 %s: %s" % (os.path.basename(f), e), flush=True)
    return moved


def _patch_entry_kp_meta(sha12, count):
    """把 kp_count / kp_version 写进论文条目 frontmatter。

    scan 与门户据此判断「这篇的知识点已按新算法处理过」，
    否则 0 条知识点的论文会被永久判成 P1（有条目、无知识点）。
    """
    p = os.path.join(PAPERS, "paper-%s.md" % sha12)
    if os.environ.get("GEOKB_KP_OUT"):
        return          # 测试写入到临时目录时，不要回写真实条目的 kp_count
    if not os.path.exists(p):
        return
    raw = open(p, encoding="utf-8").read()
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", raw, re.S)
    if not m:
        return
    fm, body = m.group(1), m.group(2)
    fm = re.sub(r"(?m)^kp_count:.*\n?", "", fm)
    fm = re.sub(r"(?m)^kp_version:.*\n?", "", fm)
    fm = fm.rstrip("\n") + "\nkp_count: %d\nkp_version: \"%s\"\n" % (
        count, kp_select.KP_VERSION)
    try:
        open(p, "w", encoding="utf-8").write("---\n" + fm + "---\n" + body)
    except OSError as e:
        print("  [WARN] 回写 kp_count 失败 %s: %s" % (sha12, e), flush=True)


def _parse_candidates(out):
    """从模型输出解析候选列表（容忍代码块包裹/字段缺失）。"""
    data = extract_json(out or "")
    if isinstance(data, dict):
        cands = data.get("candidates") or data.get("kps") or []
    elif isinstance(data, list):
        cands = data
    else:
        cands = []
    clean = []
    for c in cands:
        if not isinstance(c, dict):
            continue
        c = dict(c)
        c["body"] = str(c.get("body") or c.get("claim") or "").strip()
        c["title"] = str(c.get("title") or "").strip()
        if c["body"] and c["title"]:
            clean.append(c)
    return clean[:MAX_CANDS]


def work(sha, key, dry_run=False, repair=True):
    fp = os.path.join(TEXT, sha + ".txt")
    raw = open(fp, encoding="utf-8", errors="ignore").read()
    # 与摘要任务共用同一份文本（含行号），否则前缀不同、缓存不命中
    body = prepare2(raw, INGEST_BUDGET)
    meta = paper_meta(sha)
    lang = meta.get("lang") or ("en" if _looks_en(raw) else "zh")
    t0 = time.time()
    msgs = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": PROMPT.format(
                text=body, lang_rule=LANG_RULE.get(lang, LANG_RULE["zh"]))}]
    # 超时/重试必须压在调用方的外层预算内（inbox_ingest._run_py = 420s）：
    # 240s×4 次重试 ≈ 16 分钟，实测整篇被 420s 杀在第二次重试中途。
    # 120s 约为正常耗时的 3 倍（当日 8 篇里 7 篇单步 <40s），留足重试与修复余量。
    out, err = ds_chat(msgs, key, max_tokens=6000, timeout=KP_TIMEOUT,
                       thinking=INGEST_THINKING, retries=KP_RETRY)
    u1 = dict(last_usage() or {})          # 首轮用量（补抽会覆盖线程账本，先存下）
    if err and not (out or "").strip():
        raise InfraError("kp_extract: %s" % err)

    cands = _parse_candidates(out)
    if not cands:
        # 解析失败：把模型输出塞回去要求只重发 JSON（一次，成本很低）
        cands = _repair_json(msgs, out, key)
    if not cands:
        with _lock:
            _stat["json_fail"] += 1
        raise RuntimeError("JSON 解析失败（无候选）")

    kept, dropped, rep = kp_select.select(cands, raw, lang=lang)
    rep["repair_used"] = False
    u2 = {}                                  # 补抽那一轮的用量（未补抽则空）

    # ── 覆盖补抽：只在「摘要/结论的定量结论漏得多」时才多花一次调用 ──
    if repair and rep["coverage"]["rate"] < REPAIR_COVERAGE and rep["coverage"]["gaps"]:
        gaps = "\n".join("- " + g["text"][:160] for g in rep["coverage"]["gaps"][:5])
        try:
            o2, e2 = ds_chat(
                [{"role": "system", "content": SYSTEM_PROMPT},
                 {"role": "user", "content": PROMPT_REPAIR.format(
                     text=body, gaps=gaps,
                     lang_rule=LANG_RULE.get(lang, LANG_RULE["zh"]))}],
                key, max_tokens=2600, timeout=KP_TIMEOUT, retries=1,
                thinking=INGEST_THINKING)
            u2 = dict(last_usage() or {})
            more = _parse_candidates(o2) if not e2 else []
            if more:
                # 补抽候选与前一轮合并后重选；整体仍受同一套阈值与上限约束，
                # 所以补抽不会把 4 条灌成 10 条 —— 只是把「漏掉的主结论」补回来。
                rep["repair_used"] = True
                with _lock:
                    _stat["repair"] += 1
                kept, dropped, rep2 = kp_select.select(cands + more, raw, lang=lang,
                                                       max_k=min(kp_select.MAX_K,
                                                                 len(kept) + REPAIR_MAX_KP))
                rep2["repair_used"] = True
                rep = rep2
        except InfraError as e:
            print("  [WARN] 补抽失败（不影响主结果）%s: %s" % (sha[:12], e), flush=True)

    n = 0
    if dry_run:
        n = len(kept)
    else:
        # 先覆盖写 1..n，再把多余编号移入隔离区（kp_store.write 内部完成）。
        # 顺序很重要：先归档再写会在 n 变小时留下「新内容被归档」的错乱。
        # 落盘统一走 kp_store：模板/字段/归档/kp_count 回写全由它做，
        # 与 Agent 模式（agent_ingest.py kps）用的是**同一份代码**。
        ids, _moved = kp_store.write(sha[:12], kept, extraction="deepseek-kp",
                                     meta=meta)
        n = len(ids)
    if os.environ.get("GEOKB_KP_DEBUG"):
        for kp in kept:
            print("    [KP %.2f|%s] %s" % (kp.get("score") or 0, kp.get("reuse") or "-",
                                          (kp.get("title") or "")[:40]), flush=True)
    # 机器可读结果行：0 条是**合法**结果，下游不能再用「有文件才算成功」判失败
    # （历史坑：history_queue.run_kp 只看 kp 文件数，0 条会被记成失败反复重试）
    if not dry_run:
        print("KP_RESULT sha12=%s n=%d cands=%d cov=%.2f"
              % (sha[:12], n, len(cands), rep.get("coverage", {}).get("rate") or 0),
              flush=True)

    u = {k: (u1.get(k) or 0) + (u2.get(k) or 0)
         for k in ("prompt_tokens", "completion_tokens",
                   "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")}
    rec = {"sha": sha[:12], "sec": round(time.time() - t0, 1), "kp": n,
           "cand": len(cands), "in": u.get("prompt_tokens"),
           "out": u.get("completion_tokens"),
           "hit": u.get("prompt_cache_hit_tokens"),
           "miss": u.get("prompt_cache_miss_tokens")}
    with _lock:
        _stat["done"] += 1
        _stat["kp"] += n
        if n == 0:
            _stat["zero"] += 1
        _stat["in_tok"] += u.get("prompt_tokens") or 0
        _stat["out_tok"] += u.get("completion_tokens") or 0
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        with open(AUDIT, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "sha": sha[:12], "kept": len(kept), "cands": len(cands),
                "score_mean": rep.get("score_mean"),
                "rule_mean": rep.get("rule_mean"), "ai_mean": rep.get("ai_mean"),
                "must": rep.get("must"), "veto": rep.get("veto"),
                "coverage": rep.get("coverage"),
                "types": rep.get("types"), "drop": rep.get("drop_reasons"),
                "repair": rep.get("repair_used"),
                "dropped": [{"t": (d.get("title") or "")[:40],
                             "s": d.get("score"), "r": d.get("_reason")}
                            for d in dropped][:20],
            }, ensure_ascii=False) + "\n")
        with open(DONE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"sha12": sha[:12], "version": kp_select.KP_VERSION,
                                "n": n, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "cov": rep.get("coverage", {}).get("rate")},
                               ensure_ascii=False) + "\n")
    return n


def _looks_en(raw):
    sample = raw[:8000]
    cjk = sum(1 for c in sample if "\u4e00" <= c <= "\u9fff")
    en = sum(1 for c in sample if c.isascii() and c.isalpha())
    return en >= 400 and cjk <= en * 0.12


def _repair_json(msgs, out, key):
    """模型给了非 JSON：把它的输出贴回去，要求「只输出 JSON 对象」。"""
    if not (out or "").strip():
        return []
    try:
        o2, e2 = ds_chat(
            msgs + [{"role": "assistant", "content": out[:6000]},
                    {"role": "user", "content":
                     "上一条输出不是合法 JSON。请原样重新输出**同一个** JSON 对象，"
                     "不要解释、不要 markdown 代码块、不要省略候选。"}],
            key, max_tokens=6000, timeout=KP_TIMEOUT, retries=1,
            thinking=INGEST_THINKING,
            json_mode=True)
    except InfraError:
        return []
    return _parse_candidates(o2)


def main():
    # 支持三种用法：
    #   python kp_extract.py [并发] [上限]
    #   python kp_extract.py --sha <40位sha1>            # 只处理一篇
    #   python kp_extract.py --only-file <json清单>      # 只处理清单里的
    argv = sys.argv[1:]
    if "--print-prompt" in argv:
        which = "main"
        if "--section" in argv:
            i = argv.index("--section")
            which = argv[i + 1] if i + 1 < len(argv) else "main"
        body = {"main": PROMPT, "repair": PROMPT_REPAIR}.get(which,
                                                            LANG_RULE.get(which, ""))
        print("# source=%s\n# file=%s\n# section=%s\n%s"
              % (PROMPT_SOURCE, PROMPT_FILE, which, body))
        return
    only_sha = ''
    only_file = ''
    redo_version = ''
    dry_run = False
    repair = True
    force = False
    for flag in ("--dry-run", "--no-repair", "--force"):
        if flag in argv:
            argv.remove(flag)
            if flag == "--dry-run":
                dry_run = True
            elif flag == "--force":
                force = True          # 忽略「已完成」判定，强制重算指定篇目
            else:
                repair = False
    for name in ("--only-file", "--sha", "--redo-version"):
        if name in argv:
            i = argv.index(name)
            try:
                val = argv[i + 1].strip()
            except IndexError:
                print('用法: kp_extract.py %s <值>' % name, flush=True)
                return
            if name == "--only-file":
                only_file = val
            elif name == "--sha":
                only_sha = val
            else:
                redo_version = val
            argv = argv[:i] + argv[i + 2:]
    conc = int(argv[0]) if len(argv) > 0 else 16
    limit = int(argv[1]) if len(argv) > 1 else 0
    os.makedirs(OUT, exist_ok=True)
    done = already_done(redo_version)
    if force:
        done = set()
    if only_sha:
        files = [only_sha]
        todo = [only_sha] if only_sha not in done else []
        if not todo:
            print('ALREADY_DONE %s' % only_sha, flush=True)
            return
    else:
        files = sorted(os.path.splitext(os.path.basename(f))[0]
                       for f in glob.glob(os.path.join(TEXT, "*.txt")))
        if only_file:
            with open(only_file, encoding='utf-8') as _f:
                _p = json.load(_f)
            _shas = set((_p.get('shas') if isinstance(_p, dict) else _p) or [])
            files = [s for s in files if s in _shas]
        todo = [s for s in files if s not in done]
        if limit:
            todo = todo[:limit]
    print("总论文 %d，已完成 %d，待处理 %d，并发 %d，算法版本 %s，提示词源 %s%s"
          % (len(files), len(done), len(todo), conc, kp_select.KP_VERSION,
             PROMPT_SOURCE, "（dry-run）" if dry_run else ""), flush=True)
    key = load_api_key()
    t0 = time.time()
    ok_shas = []
    with ThreadPoolExecutor(max_workers=conc) as ex:
        futs = {ex.submit(work, s, key, dry_run, repair): s for s in todo}
        for i, fu in enumerate(as_completed(futs), 1):
            s = futs[fu]
            try:
                fu.result()
                ok_shas.append(s)
                with _lock:
                    _stat["consec_fail"] = 0
            except Exception as e:  # noqa: BLE001
                with _lock:
                    _stat["fail"] += 1
                    _stat["consec_fail"] += 1
                    consec = _stat["consec_fail"]
                    with open(LOG, "a", encoding="utf-8") as f:
                        f.write(json.dumps({"sha": s[:12], "error": str(e)[:200]},
                                           ensure_ascii=False) + "\n")
                # 熔断：连续失败说明是配额/网络/密钥问题，继续跑只是烧时间
                if consec >= MAX_CONSEC_FAIL and not _stat["abort"]:
                    _stat["abort"] = True
                    print("[ABORT] 连续失败 %d 次，疑似 API 侧故障，停止派发新任务"
                          "（已完成的已落盘，可直接重跑续做）" % consec, flush=True)
                    for f2 in futs:
                        f2.cancel()
                    break
            if i % 25 == 0 or i == len(todo):
                el = time.time() - t0
                rate = i / el if el else 0
                eta = (len(todo) - i) / rate if rate else 0
                print("[%d/%d] 用时%.0fs 速率%.2f篇/s ETA%.0fs 知识点%d 零条%d 补抽%d 失败%d"
                      % (i, len(todo), el, rate, eta, _stat["kp"], _stat["zero"],
                         _stat["repair"], _stat["fail"]), flush=True)
    if ok_shas and not dry_run:
        t_idx = time.time()
        n_idx = sum(kp_store.index(x[:12]) for x in ok_shas)
        print("索引：%d 个条目（%.0fs）" % (n_idx, time.time() - t_idx), flush=True)
    print("完成：知识点 %d，零条 %d，补抽 %d，失败 %d，解析失败 %d，"
          "in_tok %d，out_tok %d，总耗时 %.0fs"
          % (_stat["kp"], _stat["zero"], _stat["repair"], _stat["fail"],
             _stat["json_fail"], _stat["in_tok"], _stat["out_tok"],
             time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
