# -*- coding: utf-8 -*-
"""
PDF 语料知识化管线（Phase 7，DeepSeek 版）

把 PDF 仓库（by_journal，现位于 NAS S:/GeoKB/papers_repo，见 scripts/paths.py）
下的全部 PDF 论文，经 DeepSeek API 结构化提取后生成 GeoKB 条目到 content/papers/。

Key 来源（GeoKB 入库专用，优先级从高到低）：
    环境变量 GEOKB_DEEPSEEK_API_KEY / GEOKB_DEEPSEEK_KEY
    → D:/ZSK/geokb/.deepseek_key.ingest
    → 旧约定 DEEPSEEK_API_KEY / DEEPSEEK_KEY → D:/PostNews/Translate/.deepseek_key
    所有请求带 HTTP-Tracking-Id = GEOKB_DS_TRACKING_ID（默认 5e62b29d-…）。

用法：
    python pdf_corpus_ingest.py --limit 3     # 小样本验证
    python pdf_corpus_ingest.py --full        # 全量，完成后自动重建索引

原则：可以慢，不能乱。断点续跑、失败留待重跑、绝不写垃圾。
"""
import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

import pymupdf

BASE = Path(__file__).resolve().parent.parent
# 无控制台的父进程里 spawn 子进程会弹黑窗（2026-10-26）
_NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
from paths import (addition as _addition, by_journal as _by_journal,  # noqa: E402
                   index_jsonl as _index_jsonl)

REPO = Path(_by_journal())
# 用户上传文献专属目录（2026-10-21）：与 by_journal 平级，不归属任何期刊
ADDITION = Path(_addition())
INDEX_JSONL = Path(_index_jsonl())
CONTENT_DIR = BASE / "content" / "papers"
STATE_DIR = BASE / "state"
TEXT_DIR = STATE_DIR / "text"
RAW_DIR = STATE_DIR / "llm_raw"
LOG_DIR = BASE / "logs"
STATE_FILE = STATE_DIR / "pdf_state.jsonl"
LOCK_FILE = STATE_DIR / "pdf_ingest.lock"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deepseek_client import (DS_TRACKING_ID, InfraError, ds_chat,  # noqa: E402
                            extract_json, last_usage, load_api_key)
from text_prep import INGEST_BUDGET, SYSTEM_PROMPT, prepare2  # noqa: E402  两任务共用同一 system 与文本预算

DS_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")  # V4.1-Flash 非思考模式
DS_MAX_TOKENS = 2400           # 英文论文双语 schema 字段更多（2026-10-17 英文优先规则）

MIN_TEXT_CHARS = 1500           # 低于此字数视为扫描版
MAX_PROMPT_TOKENS = INGEST_BUDGET  # 统一预算（与 kp_extract 必须一致，否则缓存失效）

# 摘要抽取关闭思考（2026-10-19 A/B 实测，scripts/_thinking_ab_digest.py）：
#   3 篇（中英各半）同文本同提示词对拍 —— 字段非空率 0.81 vs 0.81（完全一致）、
#   关键字段字数 604 vs 616、JSON 失败 0 vs 0；输出 token 1563 → 641（-59%）。
#   回退：设 GEOKB_INGEST_THINKING=1。
INGEST_THINKING = os.environ.get("GEOKB_INGEST_THINKING", "0") not in ("0", "false", "False")

# 离线/排障模式（GEOKB_INGEST_OFFLINE=1）：不调 DeepSeek，直接用正则兜底抽取。
# 用途：上传链路联调、无 API key 环境、扫描版论文。产物 confidence=C（与
# fallback 同级），entry 仍正常生成、索引/检索不受影响。
OFFLINE = os.environ.get("GEOKB_INGEST_OFFLINE", "") not in ("", "0", "false", "False")

# ─── 受控标签词表（同义归并）─────────────────────────────────
TAG_SYNONYMS = {
    "斑岩型铜矿": "斑岩铜矿", "porphyry copper": "斑岩铜矿", "斑岩铜钼矿": "斑岩铜矿",
    "斑岩型钼矿": "斑岩钼矿", "porphyry molybdenum": "斑岩钼矿",
    "矽卡岩型": "矽卡岩", "skarn": "矽卡岩",
    "浅成低温热液型": "浅成低温热液", "epithermal": "浅成低温热液",
    "锆石U-Pb定年": "锆石U-Pb", "U-Pb定年": "锆石U-Pb", "U-Pb dating": "锆石U-Pb",
    "Re-Os定年": "Re-Os", "流体包裹体研究": "流体包裹体",
    "蚀变分带": "蚀变", "钾化": "蚀变", "绢英岩化": "蚀变",
    "冈底斯带": "冈底斯", "Gangdese": "冈底斯", "西藏": "青藏高原",
    "找矿勘查": "找矿预测", "勘探": "找矿预测", "勘查": "找矿预测",
}

# ⚠ 模板结构：**全文在前、指令在后**。这是前缀缓存能复用的前提 ——
#   同一篇论文的「摘要」与「知识点」两次调用共享同一段文本前缀，
#   第二次的输入按缓存命中价计费（约为未命中的 1/50）。
USER_PROMPT_TMPL = """{text}

────────────────────────────────
以上是一篇论文的全文（每行开头 L<n> 是行号，仅供定位，不要把行号写进任何字段）。

从以上文本中提取结构化信息，输出 JSON，字段如下：
{{
  "title_en": "英文原标题（照抄，不要翻译）",
  "title_zh": "标题的中文译名",
  "key_content": "关键内容：2-4句中文，本文研究什么问题、用了什么证据、得到什么结论",
  "core_methods": ["核心方法1", "核心方法2"],
  "study_area": "研究区域（具体到带/省/矿区；综述类填'全球/综述'）",
  "application_scenarios": ["应用场景，如：找矿预测、成矿理论、方法参考、教学参考"],
  "deposit_types": ["矿床类型，如：斑岩铜矿、矽卡岩、浅成低温热液"],
  "commodities": ["矿种符号，如：Cu","Mo"],
  "tags": ["3-8个中文主题标签"]
}}
无法确定的字段填 null 或空数组，禁止编造。

数值要求：key_content 中必须写出原文给出的关键数值，保留原数值与单位（如
385±40℃、251±7℃、-7.55、1.58‰、2.07、120 Ma、3.5 g/t、12%），不要用
「中高温」「较高」「一定范围」这类概括词替代具体数字；原文没有的数值不得补写。

文本可能为节选（含 […略…] 标记），只依据实际出现的文字作答。"""

# ─── 英文论文专用提示词（2026-10-17 英文优先规则）────────────
# 英文论文入库以英文为核心词条：front matter 的 title/summary/tags 与正文
# 主体均为英文原文或英文提炼；中文译名只作小字注解附后，避免翻译偏差
# 主导词条、也避免二次翻译丢失信息。
EN_PROMPT_TMPL = """{text}

────────────────────────────────
The above is the full text of a paper (each line starts with L<n> as a line
number for locating evidence only - never write line numbers into any field).

Extract structured information from the above text. Output JSON with these fields:
{{
  "title_en": "original English title (verbatim, do not translate)",
  "abstract_en": "the English abstract, verbatim from the paper if present; otherwise a 2-4 sentence English summary of the paper",
  "key_content_en": "key content: 2-4 English sentences - what problem, what evidence, what conclusion",
  "core_methods_en": ["core method 1", "core method 2"],
  "study_area_en": "study area (belt/province/deposit; reviews: Global/Review)",
  "application_scenarios_en": ["e.g. mineral exploration, metallogenic theory, methodology reference"],
  "deposit_types_en": ["deposit types in English, e.g. porphyry copper, skarn, epithermal"],
  "commodities": ["commodity symbols, e.g. Cu, Mo, W"],
  "tags_en": ["3-8 English topic tags"],
  "title_zh": "Chinese translation of the title",
  "key_content_zh": "2-4 sentence Chinese summary of the key content",
  "core_methods_zh": ["核心方法中文名"],
  "study_area_zh": "研究区域中文名",
  "application_scenarios_zh": ["应用场景中文"],
  "deposit_types_zh": ["矿床类型中文名"],
  "tags_zh": ["3-8个中文主题标签"]
}}
Fill null or empty arrays for undeterminable fields. Never fabricate.

Number rule: key_content_en must state the key numeric values given in the text,
keeping the original numbers and units (e.g. 385±40 °C, 251±7 °C, -7.55,
2.07 permil, 120 Ma, 3.5 g/t, 12%). Do not replace specific numbers with vague
wording such as "moderately high" or "a range of"; never add numbers that are
absent from the text.

The text may be an excerpt (marked with [...omitted...]); answer only from
what actually appears."""

# ─── DeepSeek 接入（共享客户端）──────────────────────────────
def detect_lang(text):
    """判定论文主体语言：'en' 英文优先 / 'zh' 中文优先。

    英文论文（含中文图表标签、参考文献混排）按英文入库；
    中文期刊论文（常带英文摘要）不会误判——正文汉字密度远高于英文词量。
    """
    sample = text[:8000]
    cjk = sum(1 for c in sample if "\u4e00" <= c <= "\u9fff")
    en = sum(1 for c in sample if c.isascii() and c.isalpha())
    if en >= 400 and cjk <= en * 0.12:
        return "en"
    return "zh"


def ds_extract(raw, digest, api_key, title=''):
    """返回 (obj, err)。infra 故障抛 InfraError，上层标记 failed 留待重跑。

    2026-10-19 缓存改造：传入**原始全文**，由 text_prep.prepare 统一清洗+截断，
    知识点任务用同一份输出 → 两次调用共享前缀，第二次按缓存价计费。

    2026-10-21 离线模式：GEOKB_INGEST_OFFLINE=1 时绕过 API，走 fallback_extract
    （title 由调用方从台账传入，保证题录标题正确）。
    """
    if OFFLINE:
        return fallback_extract(raw, title or "untitled"), None
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    lang = detect_lang(raw)
    tmpl = EN_PROMPT_TMPL if lang == "en" else USER_PROMPT_TMPL
    msgs = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": tmpl.format(text=prepare2(raw, MAX_PROMPT_TOKENS))}]
    content, err = ds_chat(msgs, api_key, max_tokens=DS_MAX_TOKENS,
                           thinking=INGEST_THINKING)
    if err:
        return None, err
    (RAW_DIR / f"{digest}.json").write_text(content, encoding="utf-8")
    obj = extract_json(content)
    if not obj:
        # 解析失败：把模型输出贴回并要求「只重发 JSON」。
        # 旧行为是直接记 fallback（正则兜底 → 条目字段半空），一次修复
        # 的成本远低于丢掉整篇的抽取结果（2026-10-23）。
        try:
            c2, e2 = ds_chat(
                msgs + [{"role": "assistant", "content": (content or "")[:6000]},
                        {"role": "user", "content":
                         "上一条输出不是合法 JSON。请原样重新输出**同一个** JSON 对象，"
                         "不要解释、不要 markdown 代码块。"}],
                api_key, max_tokens=DS_MAX_TOKENS, thinking=INGEST_THINKING)
            if not e2:
                obj = extract_json(c2)
                if obj:
                    (RAW_DIR / f"{digest}.json").write_text(c2, encoding="utf-8")
        except InfraError:
            obj = None
    if obj and (obj.get("key_content") or obj.get("key_content_en")
                or obj.get("title_en")):
        obj["_lang"] = lang
        return obj, None
    return None, "JSON 解析失败或关键字段缺失"


# ─── 状态管理 ────────────────────────────────────────────────
def load_state():
    done = {}
    if STATE_FILE.exists():
        for line in open(STATE_FILE, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                done[rec["hash"]] = rec
            except Exception:
                pass
    return done


def append_state(rec):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def acquire_lock():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            os.kill(pid, 0)
            print(f"[FATAL] 已有实例运行 (pid={pid})，退出。", flush=True)
            sys.exit(2)
        except (ValueError, OSError):
            pass
    LOCK_FILE.write_text(str(os.getpid()))


def release_lock():
    try:
        LOCK_FILE.unlink()
    except OSError:
        pass


# ─── S2 元数据登记 ───────────────────────────────────────────
def load_repo_index():
    """PDF → 台账记录。两个来源合并：
      ① NAS papers_repo/index.jsonl（旧下载器写的；被 QTS 独占锁，只读）
      ② state/papers_index.jsonl（主台账：daily_archive / user_upload 写入处）**优先**
    2026-10-21：Addition 目录的用户上传记录只在 ② 里，不合并就会拿到
    「标题=文件名、journal=Addition、date 空」的退化 meta。
    """
    m = {}
    for idx in (INDEX_JSONL, STATE_DIR / "papers_index.jsonl"):
        if not idx.exists():
            continue
        try:
            with open(idx, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                        p = r.get("repo_path", "")
                        if p:
                            m[os.path.normcase(os.path.normpath(p))] = r
                    except Exception:
                        continue
        except OSError:
            continue
    return m


def sha1_of(path, chunk=1 << 20):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ─── S1 文本提取 ─────────────────────────────────────────────
def extract_text(pdf_path, digest):
    cache = TEXT_DIR / f"{digest}.txt"
    if cache.exists():
        return cache.read_text(encoding="utf-8", errors="ignore")
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(str(pdf_path))
    parts = [page.get_text("text") for page in doc]
    doc.close()
    text = "\n".join(parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    cache.write_text(text, encoding="utf-8")
    return text


# ─── S3 智能截断（token 感知）────────────────────────────────
def est_tokens(text):
    """粗估 token。系数按 logs/token_calibration.json 实测校准：
    CJK≈1 token/字；ASCII 实测 3.0 chars/token（旧值按 4 估，
    导致 MAX_PROMPT_TOKENS=5000 实际送进 5864 token，超预算 17%）。
    """
    cjk = sum(1 for c in text
              if "一" <= c <= "鿿" or "　" <= c <= "〿" or "＀" <= c <= "￯")
    return cjk + (len(text) - cjk) // 3


def smart_truncate(text, budget=MAX_PROMPT_TOKENS):
    if est_tokens(text) <= budget:
        return text
    head_budget = int(budget * 0.6)
    tail_budget = budget - head_budget

    def cut(s, b):
        while s and est_tokens(s) > b:
            s = s[: int(len(s) * 0.85)]
        return s

    head = cut(text[:12000], head_budget)
    m = list(re.finditer(r"(?im)^\s*(conclusions?|discussion and conclusions?|"
                         r" concluding remarks|结\s*论)\s*$", text))
    if m:
        seg = text[m[-1].start(): m[-1].start() + 8000]
        tail = "\n\n[CONCLUSIONS 节选]\n" + cut(seg, tail_budget)
    else:
        tail = "\n\n[文末节选]\n" + cut(text[-6000:], tail_budget)
    return head + tail


# ─── JSON 提取（使用 deepseek_client.extract_json）─────────────


def normalize_tags(tags):
    out = []
    for t in tags or []:
        if not isinstance(t, str):
            continue
        t = t.strip().strip("。；;，,")
        if not t:
            continue
        t = TAG_SYNONYMS.get(t, t)
        if t not in out:
            out.append(t)
    return out[:10]


# 关键词行标签：英文 Keywords / Key words / Index terms，中文 关键词 / 關鍵詞 / 关键字。
# 不锚定行首：地学前缘等刊的标签前面会带项目符号或空白。
KW_LABEL_RE = re.compile(
    r"(?im)(?:key\s*-?\s*words?|index\s+terms|关键词|關鍵詞|关键字)\s*[:：]?\s*"
)
# 关键词区段的终止标志：碰到这些就认为关键词行已结束
KW_STOP = re.compile(
    r"(?i)(?:editorial\s+handling|electronic\s+supplementary|"
    r"中图(?:法)?分类号|文献标志码|文章编号|收稿日期|修回日期|基金项目|作者简介|"
    r"\babstract\b|摘\s*要|引\s*言|主\s*题\s*词|\bintroduction\b|"
    r"doi\s*[:：]|https?://|@)"
)


def _kw_clean(seg: str) -> str:
    """归一化关键词片段：全角转半角、清私有区、抹掉 PDF 的换行污染。

    实测三种典型脏数据（2026-10-28 批量轻量入库 1408 篇时发现）：
      1. 地学前缘：每个词单独占一行、词间是 `\\n \\n` → 先把换行压成空格；
      2. 岩石学报：全角字母（Ｃｕ）＋私有区连字符（\\ue011）→ NFKC + 私有区换 `-`；
      3. Springer：关键词用 ` . ` 分隔（Spinel . Lamproite . …）。
    """
    # 私有区字符多是排版连字符/间隔号，换成 `-` 比直接删更能保住原词形
    seg = "".join("-" if 0xE000 <= ord(ch) <= 0xF8FF else ch for ch in seg)
    seg = unicodedata.normalize("NFKC", seg)
    seg = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", seg)
    seg = re.sub(r"\s*\n\s*", " ", seg)
    seg = re.sub(r"\s{2,}", " ", seg)
    return seg.strip(" .;；,，、:：")


def _kw_split(seg: str):
    """按实际出现的分隔符切分：优先分号，其次 Springer 的点分隔，最后逗号。"""
    if re.search(r"[;；]", seg):
        return re.split(r"[;；]", seg)
    if len(re.findall(r"\s\.\s?", seg)) >= 2:   # `A . B . C`，要求 ≥2 处以免误切 `et al.`
        return re.split(r"\s+\.\s*", seg)
    return re.split(r"[,，、]", seg)


def extract_keywords(text, limit=10):
    """从 PDF 首页抽作者关键词（纯规则，零 token，不调模型）。

    为什么需要这个
    --------------
    离线兜底（`GEOKB_INGEST_OFFLINE=1` → `fallback_extract`）原本把 `tags`
    写成空数组。而 `build_facet_index.py` 是靠 tags 反推 facets 的 ——
    tags 空 ⇒ facets 空 ⇒ 站点分面筛选看不到这批论文。
    2026-10-28 批量轻量入库 1408 篇时暴露，故补齐。

    只认「关键词 / Keywords」标签行，**不猜正文名词短语**：宁可少，不可脏。
    抽不到就返回空，条目自然落到 agent 的 P2/P3 桶去补。
    """
    if not text:
        return []
    head = text[:9000]
    best = []
    for m in KW_LABEL_RE.finditer(head):
        seg = head[m.end():m.end() + 600]
        cut = KW_STOP.search(seg)
        if cut:
            seg = seg[:cut.start()]
        seg = _kw_clean(seg)
        if len(seg) < 3:
            continue
        out = []
        for raw in _kw_split(seg):
            t = raw.strip(" .;；,，、:：·|")
            if len(t) < 2:
                continue
            # 中文关键词极少超 24 字，英文短语留到 50；再长基本是正文被粘进来
            cjk = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")
            if (cjk >= 4 and len(t) > 24) or len(t) > 50:
                continue
            if len(t.split()) > 8:                       # 关键词不会是一整句
                continue
            if re.search(r"[@/\\]|https?:", t):           # 单位地址 / 邮箱 / 链接
                continue
            # 「关键词行被正文粘连」的特征：句号、括号年份、引文式「某某等, 2023)」
            if re.search(r"。|\(\s*\d{4}|等[,，]\s*\d{4}|\d{4}\s*\)", t):
                continue
            if re.match(r"(?i)^(?:and|or|the|a|an|is|are|was|were|in|on|for|with|from)\b", t):
                continue
            if sum(ch.isdigit() for ch in t) > len(t) * 0.6:   # 页码 / 分类号
                continue
            if t not in out:
                out.append(t)
            if len(out) >= limit:
                break
        if len(out) > len(best):
            best = out
        if len(best) >= 4:                               # 够了，别再往正文里找
            break
    return best


def fallback_extract(text, title):
    m = re.search(r"(?is)abstract[:.\s]+(.{100,1200}?)(?:\n\s*\n|keywords|1\.\s*introduction)", text)
    abstract = (m.group(1).strip() if m else text[:600]).strip()
    abstract = re.sub(r"\s+", " ", abstract)[:500]
    kw = extract_keywords(text)
    if detect_lang(text) == "en":
        # 英文论文兜底：英文题录为核心，不留中文空壳
        return {
            "title_en": title, "title_zh": None, "_lang": "en",
            "abstract_en": abstract or "（扫描版或解析失败，仅保留题录）",
            "key_content_en": "", "core_methods_en": [],
            "study_area_en": None, "application_scenarios_en": [],
            "deposit_types_en": [], "commodities": [], "tags_en": kw,
            "key_content_zh": "", "core_methods_zh": [],
            "study_area_zh": None, "application_scenarios_zh": [],
            "deposit_types_zh": [], "tags_zh": kw,
        }
    return {
        "title_en": title, "title_zh": None, "_lang": "zh",
        "key_content": abstract or "（扫描版或解析失败，仅保留题录）",
        "core_methods": [], "study_area": None,
        "application_scenarios": [], "deposit_types": [],
        "commodities": [], "tags": kw,
    }


# ─── S5 条目生成 ─────────────────────────────────────────────
def normalize_tags_en(tags):
    """英文标签原样保留（不过 TAG_SYNONYMS 中文同义词表）。

    英文词条以英文为核心，作者原词（skarn / porphyry copper）即是检索入口。
    """
    out = []
    for t in tags or []:
        if not isinstance(t, str):
            continue
        t = t.strip().strip("。；;，,")
        if t and t not in out:
            out.append(t)
    return out[:10]


def sentence_safe_summary(text, limit=200):
    """在句子边界截断，绝不把句子和数字劈开。

    历史事故：此前用 key_content[:120] 硬截断，导致 97.6% 条目的 summary
    以逗号甚至半个数字结尾（如「约 0.7 」「对应 145 Ma、」），文末残缺。
    2026-10-17：句末标点集加入英文句点 '.'，英文摘要同样按句安全截断。
    """
    s = re.sub(r"\s{2,}", " ", (text or "").replace("\n", " ")).strip()
    if len(s) <= limit:
        return s
    window = s[:limit]
    # ① 优先在句末标点处收尾
    pos = max((window.rfind(ch) for ch in "。！？；!?;."), default=-1)
    if pos >= limit * 0.4:
        return window[:pos + 1]
    # ② 退而求其次在逗号/顿号处收尾，并显式标注省略
    pos2 = max((window.rfind(ch) for ch in "，,、"), default=-1)
    if pos2 >= limit * 0.4:
        return window[:pos2] + "……"
    return window + "……"


def yaml_str(s):
    """YAML front matter 安全的双引号字符串。

    与同文件 tags/sources 的 json.dumps 风格保持一致：YAML 1.1/1.2 均支持
    JSON 风格双引号 flow scalar，反斜杠/引号/换行都能正确转义。
    """
    return json.dumps(str(s or ""), ensure_ascii=False)


def _meta_guard(path, meta):
    """元数据红线（与 Agent 入库共用 scripts/meta_guard.py，规则只写一处）。

    2026-10-26 两次全库审计的教训：标题用了译名/哈希占位、出版日期精度不足，
    都是入库时没守。这里只**报警不阻断**（条目已落盘，中断反而更难收拾）；
    Agent 入库路径是在 commit 前拦截。
    """
    try:
        sys.path.insert(0, str(BASE / "scripts"))
        import meta_guard as _mg
        rel = str((meta or {}).get("repo_path") or "").replace("\\", "/")
        r = _mg.check_entry(path.stem, {"repo_path": rel})
        if r["errors"]:
            print("  [元数据红线] %s：%s" % (path.stem, "；".join(r["errors"])), flush=True)
        elif r["warns"]:
            print("  [元数据提示] %s：%s" % (path.stem, "；".join(r["warns"][:2])), flush=True)
    except Exception as e:
        print("  [元数据检查跳过] %s" % e, flush=True)


def write_entry(digest, meta, data, extraction, user_meta=None):
    """生成 papers 条目。

    2026-10-17 英文优先规则：data['_lang'] == 'en' 时英文为词条核心
    （title/summary/tags/正文主体均为英文），中文译名以 <small> 小字
    附后；中文论文维持原中文优先结构。门户 markdown 渲染保留行内 HTML，
    <small> 即呈现为小字。

    2026-10-21 user_meta（用户上传入口，scripts/user_upload.py 传入）：
    用户补充的文献信息（标题/作者/关键词/摘要/标签/DOI/备注），优先级高于
    LLM 提取值。所有用户文本进正文前 html.escape —— 门户 markdown
    保留行内 HTML，不转义等于给用户开 XSS 注入。
    """
    # PDF 文本层常带 C1 控制字符（0x7f–0x9f），YAML 会直接拒绝解析 → 整个 frontmatter 变砖
    # （2026-10-28 批量离线入库 1399 篇后，41 个条目因此无法生成 facets）。
    # 在这里对 data 的字符串/字符串列表统一清洗，比逐个字段补救可靠。
    _CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

    def _clean_val(v):
        if isinstance(v, str):
            return _CTRL.sub("", v)
        if isinstance(v, list):
            return [_CTRL.sub("", str(x)) for x in v]
        return v

    data = {k: _clean_val(v) for k, v in (data or {}).items()}

    eid = f"paper-{digest[:12]}"
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    path = CONTENT_DIR / f"{eid}.md"
    lang = data.get("_lang") or "zh"
    confidence = "B" if extraction == "llm" else "C"
    journal = meta.get("journal") or ""
    year = (meta.get("date") or "")[:4]
    # 展示用发布日期：day=01 视为「只知年月」，压缩到 YYYY-MM；连月份都不知则只留 YYYY
    _ds = str(meta.get("date") or "").strip()
    if meta.get("pub_date"):
        pub_date = str(meta["pub_date"]).strip()
    elif re.match(r"^\d{4}-\d{2}-\d{2}$", _ds):
        pub_date = _ds[:7] if _ds.endswith("-01") else _ds
    else:
        pub_date = year

    # ── 用户补充信息（转义 / 去重）──────────────────────────────
    def _u(s):
        s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", str(s or "")).strip()
        return html.escape(s, quote=False)

    def _ulist(items):
        out = []
        for x in (items or []):
            x = _u(x)
            if x and x not in out:
                out.append(x)
        return out

    um = user_meta or {}
    um_title = _u(um.get("title"))
    um_abstract = _u(um.get("abstract"))
    um_authors = _ulist(um.get("authors"))
    um_keywords = _ulist(um.get("keywords"))
    um_doi = _u(um.get("doi")) or _u(meta.get("doi"))
    um_tags = _ulist(um.get("tags"))
    um_remarks = _u(um.get("remarks"))

    if um:
        if lang == "en":
            info_lines = []
            if um_authors:
                info_lines.append("- Authors: " + "; ".join(um_authors))
            if um_keywords:
                info_lines.append("- Keywords: " + "; ".join(um_keywords))
            if um_doi:
                info_lines.append("- DOI: " + um_doi)
            if um_remarks:
                info_lines.append("- Remarks: " + um_remarks)
            info_lines.append("- Source: user upload, stored in the NAS `Addition` folder "
                              "(not affiliated with any journal)")
            info_block = "## Document Information\n\n" + "\n".join(info_lines) + "\n\n"
        else:
            info_lines = []
            if um_authors:
                info_lines.append("- 作者：" + "、".join(um_authors))
            if um_keywords:
                info_lines.append("- 关键词：" + "、".join(um_keywords))
            if um_doi:
                info_lines.append("- DOI：" + um_doi)
            if um_remarks:
                info_lines.append("- 备注：" + um_remarks)
            info_lines.append("- 来源：用户上传，存放于 NAS `Addition` 文件夹（不归属任何期刊）")
            info_block = "## 文献信息\n\n" + "\n".join(info_lines) + "\n\n"
        extra_fm = (f"authors: {json.dumps(um_authors, ensure_ascii=False)}\n"
                    f"keywords: {json.dumps(um_keywords, ensure_ascii=False)}\n"
                    f"origin: user_upload\n")
        if um_remarks:
            extra_fm += f"remarks: {json.dumps(um_remarks, ensure_ascii=False)}\n"
    else:
        info_block = ""
        extra_fm = ""
    # 相对路径基准 = 仓库根（REPO 的父级，即 <pdf_root>），保证跨盘符/UNC 不出错，
    # 且产出 papers_repo/... 前缀，与服务端 pdf_service.resolve_pdf 的约定一致。
    _rel = os.path.relpath(meta.get("repo_path") or "", str(REPO.parent)).replace("\\", "/")
    if not _rel.startswith("papers_repo/"):
        _rel = "papers_repo/" + _rel.lstrip("./")
    rel_path = _rel
    now = datetime.now().strftime("%Y-%m-%d")

    def md_list(items):
        items = [x for x in (items or []) if x]
        return "\n".join(f"- {x}" for x in items) if items else "- （未能提取）"

    def join_cn(items):
        return "、".join(str(x) for x in (items or []) if x) or "（未能提取）"

    if lang == "en":
        # ── 英文论文：英文为核心，中文译名小字注解 ─────────────────
        title = um_title or data.get("title_en") or data.get("title_zh") or meta.get("title") or eid
        title_zh = data.get("title_zh") or ""
        abstract = (um_abstract or data.get("abstract_en") or data.get("key_content_en") or "").strip()
        summary = sentence_safe_summary(abstract, 300) or title
        tags = normalize_tags_en(um_tags + list(data.get("tags_en") or []))
        for t in (data.get("commodities") or []):
            t = str(t).strip()
            if t and t not in tags:
                tags.append(t)
        for t in normalize_tags_en(data.get("deposit_types_en")):
            if t not in tags:
                tags.append(t)
        tags = tags[:12]

        body = info_block + f"""## Abstract

{abstract or '（未能提取）'}

## Key Content

{data.get('key_content_en') or '（未能提取）'}

## Core Methods

{md_list(data.get('core_methods_en'))}

## Study Area

{data.get('study_area_en') or '（未能提取）'}

## Application Scenarios

{md_list(data.get('application_scenarios_en'))}

## 中文翻译

<small>**标题**：{title_zh or '（未能提取）'}</small>

<small>**摘要**：{data.get('key_content_zh') or '（未能提取）'}</small>

<small>**方法**：{join_cn(data.get('core_methods_zh'))}</small>

<small>**区域**：{data.get('study_area_zh') or '（未能提取）'}</small>

<small>**应用**：{join_cn(data.get('application_scenarios_zh'))}</small>

<small>**矿床类型**：{join_cn(data.get('deposit_types_zh'))}</small>

## 来源

- 期刊：{journal or '未知'}（{year or '年份未知'}）
- 中文标题：{title_zh or '—'}
- 仓库文件：`{rel_path}`
"""
        front = f"""---
id: {eid}
title: {yaml_str(title)}
module: papers
lang: en
tags: {json.dumps(tags, ensure_ascii=False)}
summary: {yaml_str(summary)}
confidence: {confidence}
status: active
updated: {now}
sources: [{json.dumps({"name": journal or "papers_repo", "path": rel_path}, ensure_ascii=False)}]
journal: {yaml_str(journal)}
year: {yaml_str(year)}
pub_date: {yaml_str(pub_date)}
extraction: {extraction}
{extra_fm}---

{body}"""
        path.write_text(front, encoding="utf-8")
        _meta_guard(path, meta)
        return eid

    # ── 中文论文：中文优先（原结构不变）─────────────────────────
    title = um_title or data.get("title_zh") or data.get("title_en") or meta.get("title") or eid
    title_en = data.get("title_en") or meta.get("title") or ""
    tags = normalize_tags(um_tags + list(data.get("tags") or []))
    for lst_key in ("deposit_types", "commodities"):
        for t in normalize_tags(data.get(lst_key)):
            if t not in tags:
                tags.append(t)
    tags = tags[:12]
    summary = sentence_safe_summary(um_abstract or data.get("key_content"))

    body = info_block + f"""## 关键内容

{data.get('key_content') or '（未能提取）'}

## 核心方法

{md_list(data.get('core_methods'))}

## 研究区域

{data.get('study_area') or '（未能提取）'}

## 应用场景

{md_list(data.get('application_scenarios'))}

## 来源

- 期刊：{journal or '未知'}（{year or '年份未知'}）
- 英文标题：{title_en}
- 仓库文件：`{rel_path}`
"""
    front = f"""---
id: {eid}
title: {yaml_str(title)}
module: papers
tags: {json.dumps(tags, ensure_ascii=False)}
summary: {yaml_str(summary)}
confidence: {confidence}
status: active
updated: {now}
sources: [{json.dumps({"name": journal or "papers_repo", "path": rel_path}, ensure_ascii=False)}]
journal: {yaml_str(journal)}
year: {yaml_str(year)}
pub_date: {yaml_str(pub_date)}
extraction: {extraction}
{extra_fm}---

{body}"""
    path.write_text(front, encoding="utf-8")
    _meta_guard(path, meta)
    return eid


# ─── 主流程 ──────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 篇（验证用）")
    ap.add_argument("--only", default="", help="只处理指定 PDF 的绝对路径（队列逐篇消费用）")
    ap.add_argument("--only-file", default="",
                    help="只处理清单文件里列出的 PDF（每日管线用：只入库当天期刊更新新下载的论文）")
    ap.add_argument("--full", action="store_true", help="全量执行并在完成后重建索引")
    ap.add_argument("--rebuild", action="store_true", help="结束后重建索引")
    args = ap.parse_args()

    acquire_lock()
    stats = {"llm": 0, "fallback": 0, "needs_ocr": 0, "failed": 0, "skipped": 0}
    t0 = time.time()
    try:
        api_key = load_api_key()
        state = load_state()
        repo_index = load_repo_index()
        pdfs = sorted(REPO.rglob("*.pdf"))
        if ADDITION.exists():
            # 用户上传文献（Addition）与 by_journal 平级，一并纳入扫描，
            # 否则 --only <Addition 路径> 匹配不到任何文件、静默跳过（2026-10-21）。
            pdfs = sorted(set(pdfs) | set(ADDITION.rglob("*.pdf")))
        print(f"[S2] 仓库 PDF {len(pdfs)} 篇，已完成 {len(state)} 篇", flush=True)

        todo = []
        only = os.path.normcase(os.path.normpath(args.only)) if args.only else ""
        only_set = set()
        if args.only_file:
            with open(args.only_file, encoding="utf-8") as _f:
                _payload = json.load(_f)
            _paths = _payload.get("paths") if isinstance(_payload, dict) else _payload
            only_set = {os.path.normcase(os.path.normpath(str(x))) for x in (_paths or [])}
            print(f"[S2] 清单限定 {len(only_set)} 篇（只入库当日新下载）", flush=True)
        for p in pdfs:
            if only and os.path.normcase(os.path.normpath(str(p))) != only:
                continue
            if args.only_file and os.path.normcase(os.path.normpath(str(p))) not in only_set:
                stats["skipped"] += 1
                continue
            digest = sha1_of(p)
            if digest in state and state[digest].get("status") in ("done", "fallback", "needs_ocr"):
                stats["skipped"] += 1
                continue
            # status=failed 不跳过：可恢复故障，本轮重试
            key = os.path.normcase(os.path.normpath(str(p)))
            meta = repo_index.get(key) or {}
            if not meta:
                meta = {"title": p.stem,
                        "journal": p.parent.parent.name if p.parent.parent != REPO else p.parent.name,
                        "date": "", "repo_path": str(p)}
            meta["repo_path"] = str(p)
            todo.append((p, digest, meta))
            if args.limit and len(todo) >= args.limit:
                break

        print(f"[S2] 待处理 {len(todo)} 篇（跳过 {stats['skipped']} 篇），"
              f"模型={DS_MODEL} tracking={DS_TRACKING_ID}", flush=True)
        if not todo:
            # 清单模式下必须把本次结果（0 篇）落盘，否则下游 kp_extract 会
            # 误用上一次的 sha 清单，去拆已经拆过的旧论文。
            if args.only_file:
                try:
                    (STATE_DIR / "last_ingest_shas.json").write_text(json.dumps({
                        "date": datetime.now().date().isoformat(),
                        "shas": [],
                    }, ensure_ascii=False), encoding="utf-8")
                except Exception:
                    pass
            print("[DONE] 无待处理篇目", flush=True)
            return

        done_shas = []
        consec_infra = 0
        max_consec = int(os.environ.get("GEOKB_INGEST_MAX_FAIL", "8"))
        for i, (pdf, digest, meta) in enumerate(todo, 1):
            rec = {"hash": digest, "path": str(pdf),
                   "ts": datetime.now().isoformat(timespec="seconds")}
            try:
                text = extract_text(pdf, digest)
                if len(text) < MIN_TEXT_CHARS:
                    rec["status"] = "needs_ocr"
                    stats["needs_ocr"] += 1
                    print(f"[{i}/{len(todo)}] {pdf.name[:60]} → needs_ocr ({len(text)} chars)", flush=True)
                else:
                    try:
                        obj, err = ds_extract(text, digest, api_key,
                                              meta.get("title") or pdf.stem)
                    except InfraError as e:
                        rec.update(status="failed", error=f"infra: {e}")
                        stats["failed"] += 1
                        consec_infra += 1
                        print(f"[{i}/{len(todo)}] {pdf.name[:60]} → FAILED(infra，下轮重跑): {e}", flush=True)
                        append_state(rec)
                        # 熔断：连续 infra 失败说明配额/密钥/网络出了问题，
                        # 继续跑只是把整批都标成 failed（复审时看不出真因）。
                        if consec_infra >= max_consec:
                            print(f"[ABORT] 连续 {consec_infra} 次 infra 失败，停止本轮"
                                  f"（已完成 {len(done_shas)} 篇已落盘，修复后重跑续做）", flush=True)
                            break
                        continue
                    consec_infra = 0
                    _u = last_usage() or {}
                    rec.update(tok_in=_u.get("prompt_tokens"),
                               tok_out=_u.get("completion_tokens"),
                               tok_hit=_u.get("prompt_cache_hit_tokens"),
                               tok_miss=_u.get("prompt_cache_miss_tokens"))
                    # 用户上传文献（source=user_upload）：台账里的用户补充信息
                    # （标题/作者/关键词/摘要/标签）随 entry 一起落，优先于 LLM 值
                    um = meta if meta.get("source") == "user_upload" else None
                    if obj:
                        eid = write_entry(digest, meta, obj,
                                          "offline" if OFFLINE else "llm",
                                          user_meta=um)
                        rec.update(status="done", entry=eid)
                        stats["llm"] += 1
                        print(f"[{i}/{len(todo)}] {pdf.name[:60]} → "
                              f"{'offline' if OFFLINE else 'llm'} ✓", flush=True)
                    else:
                        data = fallback_extract(text, meta.get("title") or pdf.stem)
                        eid = write_entry(digest, meta, data, "fallback",
                                          user_meta=um)
                        rec.update(status="fallback", entry=eid, error=err)
                        stats["fallback"] += 1
                        print(f"[{i}/{len(todo)}] {pdf.name[:60]} → fallback ({err})", flush=True)
            except Exception as e:
                rec.update(status="failed", error=f"{type(e).__name__}: {e}")
                stats["failed"] += 1
                print(f"[{i}/{len(todo)}] {pdf.name[:60]} → FAILED: {e}", flush=True)
            append_state(rec)
            if rec.get("status") in ("done", "fallback"):
                done_shas.append(digest)

            if i % 10 == 0:
                elapsed = time.time() - t0
                rate = i / elapsed * 3600
                eta = (len(todo) - i) / (i / elapsed) / 60
                print(f"[PROGRESS] {i}/{len(todo)} 篇，{rate:.0f} 篇/时，ETA {eta:.0f} 分钟，"
                      f"llm={stats['llm']} fallback={stats['fallback']} "
                      f"ocr={stats['needs_ocr']} failed={stats['failed']}", flush=True)

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        report = {
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "elapsed_min": round((time.time() - t0) / 60, 1),
            "model": DS_MODEL,
            "tracking_id": DS_TRACKING_ID,
            "processed": len(todo), **stats,
        }
        (LOG_DIR / f"pdf_ingest_report_{datetime.now():%Y%m%d_%H%M%S}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[REPORT] {json.dumps(report, ensure_ascii=False)}", flush=True)
        # 供知识点抽取精确限定：只拆本轮入库的这几篇
        try:
            (STATE_DIR / "last_ingest_shas.json").write_text(json.dumps({
                "date": datetime.now().date().isoformat(),
                "shas": done_shas,
            }, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

        if args.full or args.rebuild:
            print("[S6] 重建 GeoKB 索引...", flush=True)
            subprocess.run(
                [sys.executable, str(BASE / "scripts" / "rebuild_index.py")],
                timeout=1800, creationflags=_NO_WIN)
            print("[S6] 索引重建完成", flush=True)
    finally:
        release_lock()


if __name__ == "__main__":
    main()
