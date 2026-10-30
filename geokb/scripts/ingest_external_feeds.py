# -*- coding: utf-8 -*-
"""
PostNews 成品 → GeoKB 知识库后置管线（DeepSeek 版）。

每日执行一次，挂在 journal_auto_push.py 与 daily_run.py 之后：
    python ingest_external_feeds.py

流程：
  1. 读取 journal_pushed.json / journal_inbox.json / news.json
  2. 关键词规则筛选命中条目（避免浪费 API token）
  3. 期刊论文：下载 PDF 到 papers_repo（arXiv / CrossRef / Unpaywall）
     - 下载成功 → 全文 DeepSeek 提取 → content/papers/（与语料管线同 schema，SHA1 幂等）
     - 下载失败 → 题录 DeepSeek 提取 → content/external-feed/
  4. 新闻：题录 DeepSeek 提取 → content/external-feed/
  5. 重建 GeoKB 索引

纪律：DeepSeek 内容失败 → 规则兜底（confidence=C）；
     infra 故障 → 不标记 processed，次日重跑，绝不写垃圾。
"""
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
POSTNEWS = Path(r'D:/PostNews')
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'server'))
sys.path.insert(0, str(POSTNEWS))  # journal_pdf_fetch 门面（强 handler 集）

from deepseek_client import InfraError, ds_chat, extract_json, load_api_key  # noqa: E402
from keyword_filter import is_relevant  # noqa: E402
from pdf_downloader import download  # noqa: E402
from pdf_corpus_ingest import (MIN_TEXT_CHARS, detect_lang, ds_extract,  # noqa: E402
                               extract_text, sha1_of, smart_truncate,
                               write_entry)
import ingest  # noqa: E402
import ingest_policy  # noqa: E402  入库策略（默认只下载，不入库）

from paths import repo as _repo  # noqa: E402

# ─── PDF 优先：强 handler 集（2026-10-16 接入）────────────────────
# 旧逻辑只调 pdf_downloader.download()（arXiv / DOI→CrossRef→Unpaywall），
# 对中文期刊全部落空 → 大量论文只能以「外部订阅」题录进库，丢全文。
# journal_pdf_fetch 门面包了 12 个出版社 handler（含岩石学报/地质学报/
# 地球科学/大地构造与成矿学等中文刊），必须先试它，再退回通用下载器。
try:
    from journal_pdf_fetch import fetch_pdf as _fetch_pdf  # noqa: E402
    _FETCH_AVAILABLE = True
except Exception as _e:  # noqa: BLE001
    _fetch_pdf = None
    _FETCH_AVAILABLE = False
    print(f'[warn] journal_pdf_fetch 不可用（{_e}），PDF 优先降级为通用下载器',
          flush=True)

PAPERS_REPO = Path(_repo())
STATE_FILE = BASE / 'index' / 'external_feed_state.json'
FEED_DIR = BASE / 'content' / 'external-feed'
PAPERS_DIR = BASE / 'content' / 'papers'

INFRA_ABORT_AFTER = 5  # 连续 infra 故障超过此次数则中止本次运行


# ─── PDF 优先：期刊论文下载（2026-10-16 源头治理）──────────────────
def _save_pdf_to_repo(data, journal, title, date, source='', pdf_url='', url=''):
    """把 PDF bytes 落到 NAS by_journal 并登记 index.jsonl。

    复用 pdf_downloader._save_pdf 的目录约定，保证与既有仓一致。
    NAS index.jsonl 偶发 SMB 锁（QTS 侧），登记失败不影响主流程。
    """
    import pdf_downloader as _pdl
    jpath = _pdl._save_pdf(data, journal or 'unknown', title, date, '_fetch')
    meta = {
        'title': title, 'url': url, 'journal': journal, 'date': date,
        'pdf_url': pdf_url, 'repo_path': jpath, 'size': len(data),
        'source': source or 'fetch', 'indexed_at': datetime.now().isoformat(),
    }
    try:
        _pdl._append_index(meta)
    except Exception as e:
        print(f'      [warn] index.jsonl 登记失败（不影响入库）: {e}', flush=True)
    return jpath, meta


def _fetch_journal_pdf(item, date):
    """期刊论文 PDF 优先下载。

    顺序：① 强 handler 集（12 个出版社，覆盖 14 个目标期刊 + 岩石学报）
          ② 通用下载器（arXiv / DOI→CrossRef→Unpaywall）
    返回 (pdf_path, pdf_meta) 或 (None, reason_str)。
    绝不抛异常——下载失败是常态，应由调用方走题录兜底。
    """
    title = item.get('title', '')
    url = item.get('url', '')
    journal = item.get('journal', 'unknown')
    reason = ''

    # ① 强 handler 集
    if _FETCH_AVAILABLE and url:
        try:
            r = _fetch_pdf(journal, title, url, date=date,
                            doi=item.get('doi') or '')
            if r.get('status') == 'ok' and r.get('pdf_bytes'):
                jpath, meta = _save_pdf_to_repo(
                    r['pdf_bytes'], journal, title, date,
                    source=r.get('source', ''), pdf_url=r.get('pdf_url', ''),
                    url=url)
                print(f"      PDF[{r.get('source')}]: {jpath}", flush=True)
                return jpath, meta
            reason = f"{r.get('status')}: {str(r.get('note', ''))[:100]}"
        except Exception as e:
            reason = f'fetch_err: {type(e).__name__}: {str(e)[:80]}'

    # ② 通用下载器（arXiv / DOI→Unpaywall）
    if url:
        try:
            d = download(title, url, journal, date)
            if d.get('ok') and d.get('pdf_path'):
                print(f"      PDF[generic]: {d['pdf_path']}", flush=True)
                return d['pdf_path'], (d.get('meta') or {})
            reason = (reason + f" | generic: {d.get('reason', '')}").strip(' |')
        except Exception as e:
            reason = (reason + f" | generic_err: {e}").strip(' |')

    return None, reason or 'no url'


def _is_manual_entry(path):
    """条目是否为门户 journals 页「下载 PDF」按钮即时建档（extraction: manual）。

    此类条目只有题录（confidence C），每日管线碰到同一篇时应升级为
    全文 DeepSeek 提取并覆盖写，而不是当普通已有条目跳过。
    """
    try:
        head = Path(path).read_text(encoding='utf-8')[:800]
        m = re.search(r'^extraction:\s*(\S+)', head, re.M)
        return bool(m and m.group(1).strip('\'"') == 'manual')
    except Exception:
        return False


def _id(title, url):
    return hashlib.md5(f"{title}|{url}".encode()).hexdigest()[:12]


def load_state():
    if STATE_FILE.exists():
        try:
            return json.load(open(STATE_FILE, encoding='utf-8'))
        except Exception:
            pass
    return {'processed': set()}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    out = {'processed': sorted(state.get('processed', set())),
           'last_run': datetime.now().isoformat()}
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


def _read_json(path):
    if not path.exists():
        return {}
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return {}


def collect_journal_items():
    pushed = _read_json(POSTNEWS / 'journal_pushed.json')
    items = []
    for journal, arts in pushed.items():
        for a in arts:
            if not a.get('title'):
                continue
            items.append({
                'type': 'journal',
                'journal': journal,
                'title': a['title'],
                'url': a.get('url', ''),
                'date': a.get('pushed_at', '')[:10] or datetime.now().strftime('%Y-%m-%d'),
                'source': f"期刊推送：{journal}",
            })
    return items


def collect_news_items():
    news = _read_json(POSTNEWS / 'news.json')
    items = []
    for n in news.get('news', []):
        if not n.get('title'):
            continue
        items.append({
            'type': 'news',
            'journal': n.get('cat', 'news'),
            'title': n['title'],
            'summary': n.get('summary', ''),
            'url': n.get('url', ''),
            'date': n.get('pub_date', datetime.now().strftime('%Y-%m-%d')),
            'source': f"地矿早报：{n.get('source', '新闻')}",
        })
    return items


# ─── DeepSeek 题录元数据提取（无 PDF 的新闻/非OA论文）──────────
FEED_META_PROMPT = """从以下{itype}题录中提取结构化信息，输出 JSON：
{{
  "title_zh": "中文标题（原文是中文则照抄）",
  "summary_cn": "120字内中文摘要",
  "summary_en": "English summary within 120 chars（英文条目必填，中文条目留空串）",
  "tags_en": ["3-6 English topic tags（英文条目必填，中文条目留空数组）"],
  "core_methods": ["核心方法（能从题录判断的）"],
  "study_area": "研究区域（无则填'待补充'）",
  "application_scenarios": ["应用场景，如：找矿预测、成矿理论、行业动态、方法参考"],
  "entities": ["矿种/矿床类型/关键实体"],
  "tags": ["3-6个中文主题标签"]
}}
无法确定的字段填 null 或空数组，禁止编造。

标题：{title}
摘要：{summary}
来源：{source}"""


def ds_feed_metadata(item, api_key):
    content, err = ds_chat(
        [{"role": "system", "content": "你是地矿信息结构化提取助手，只输出 JSON。"},
         {"role": "user", "content": FEED_META_PROMPT.format(
             itype="期刊论文" if item['type'] == 'journal' else "行业新闻",
             title=item['title'], summary=item.get('summary', '')[:1500],
             source=item['source'])}],
        api_key, max_tokens=800)
    if err:
        return None, err
    obj = extract_json(content)
    if obj and (obj.get("summary_cn") or obj.get("title_zh")):
        return obj, None
    return None, "JSON 解析失败"


def rule_metadata(title, summary, source, hits):
    """DeepSeek 内容失败时的规则兜底（confidence=C）。"""
    text = f"{title} {summary} {source}"
    area_patterns = [
        (r'华南', '华南'), (r'三江', '三江成矿带'),
        (r'青藏高原|Qinghai-Tibet|Tibetan', '青藏高原'),
        (r'云[南北]|Yunnan', '云南'), (r'西藏|Xizang|Tibet', '西藏'),
        (r'四川|Sichuan', '四川'), (r'青海|Qinghai', '青海'),
        (r'新疆|Xinjiang', '新疆'), (r'广西|Guangxi', '广西'),
        (r'贵州|Guizhou', '贵州'), (r'湖南|Hunan', '湖南'),
        (r'江西|Jiangxi', '江西'), (r'湖北|Hubei', '湖北'),
        (r'内蒙古|Inner Mongolia', '内蒙古'), (r'柴达木|Qaidam', '柴达木盆地'),
    ]
    study_areas = list({n for p, n in area_patterns if re.search(p, text, re.I)})[:4]
    entity_patterns = [
        (r'斑岩铜矿|porphyry copper', '斑岩铜矿'), (r'矽卡岩|skarn', '矽卡岩矿床'),
        (r'钨矿|tungsten|wolframite|scheelite', '钨矿'), (r'锡矿|tin|cassiterite', '锡矿'),
        (r'钼矿|molybdenum|molybdenite|辉钼矿', '钼矿'), (r'铜矿|copper deposit|黄铜矿', '铜矿'),
        (r'锂矿|lithium|卤水锂', '锂矿'), (r'稀土|REE|rare earth', '稀土'),
        (r'金矿|gold|造山型金矿', '金矿'), (r'铅锌|Pb-Zn|galena|sphalerite', '铅锌矿'),
        (r'铁-铌-稀土|Nb-REE|白云鄂博', '铁-铌-稀土'),
    ]
    entities = list({n for p, n in entity_patterns if re.search(p, text, re.I)})[:8]
    method_patterns = [
        (r'U-Pb|锆石|zircon|monazite', '同位素年代学'),
        (r'同位素|isotope', '同位素地球化学'),
        (r'流体包裹体|fluid inclusion', '流体包裹体'),
        (r'LA-ICP-MS|MC-ICP-MS', 'LA/MC-ICP-MS'),
        (r'CNN|machine learning|remote sensing|机器学习', '机器学习/遥感'),
    ]
    methods = list({n for p, n in method_patterns if re.search(p, text, re.I)})[:4]
    summary_cn = summary if summary and any('一' <= c <= '鿿' for c in summary) else title
    return {
        'tags': list(set([h for h in hits if h] + entities))[:10],
        'summary_cn': summary_cn[:120],
        'entities': entities,
        'key_method': '、'.join(methods) if methods else '文献综述/地质分析',
        'study_area': '、'.join(study_areas) if study_areas else '待补充',
        'application': '与团队研究方向相关，可作为文献追踪',
    }


def pdf_status_line(pdf_info, pdf_reason=''):
    """题录条目里的 PDF 状态说明——写清为什么没拿到全文，便于追溯。"""
    if pdf_info and pdf_info.get('ok'):
        return '已下载并入 papers'
    if pdf_info and pdf_info.get('pdf_path'):
        return f"PDF 已存 NAS 但全文提取失败（{pdf_info['pdf_path']}）"
    if not pdf_reason:
        return '未下载/非OA'
    r = str(pdf_reason)
    # 源站侧原因（非下载器问题）与抓取侧原因分开表述
    if r.startswith(('no_fulltext', 'not_pdf_office')):
        return f'源站无全文PDF（{r}）'
    return f'未下载（{r[:120]}）'


def _is_english_item(item):
    """题录条目是否英文为主（按原始标题判定：无汉字且有足够英文字母）。"""
    t = item.get('title', '') or ''
    cjk = sum(1 for c in t if '一' <= c <= '鿿')
    en = sum(1 for c in t if c.isascii() and c.isalpha())
    return cjk == 0 and en >= 15


def write_feed_entry(item, meta, uid, date, pdf_info, hits, pdf_reason=''):
    """external-feed 条目（题录级，无全文）。

    2026-10-17 英文优先规则：英文条目以英文原标题/英文摘要为词条核心，
    中文译名以 <small> 小字附后；中文条目维持原结构。
    """
    eid = f"feed-{uid}"
    FEED_DIR.mkdir(parents=True, exist_ok=True)
    path = FEED_DIR / f"{eid}.md"
    sources = [{'name': item['source'], 'url': item.get('url', ''), 'accessed': date}]
    pdf_meta = (pdf_info.get('meta') or {}) if pdf_info else {}
    if pdf_meta.get('repo_path'):
        sources.append({'name': '本地PDF', 'path': pdf_meta['repo_path'], 'accessed': date})
    tags = list(set([item.get('journal', ''), item.get('type', '')]
                    + meta.get('tags', []) + meta.get('entities', [])))
    tags = [t for t in tags if t][:12]
    methods = meta.get('core_methods') or []
    if isinstance(methods, list):
        methods_str = '、'.join(methods) if methods else meta.get('key_method', '待补充')
    else:
        methods_str = str(methods)
    apps = meta.get('application_scenarios') or []
    apps_str = '、'.join(apps) if isinstance(apps, list) and apps else meta.get('application', '可作为文献追踪')
    title_zh = meta.get('title_zh') or item['title']
    summary_cn = meta.get('summary_cn', item.get('summary', ''))
    zh_block = f"""## 中文翻译

<small>**标题**：{title_zh}</small>

<small>**摘要**：{summary_cn or '（未能提取）'}</small>

<small>**实体**：{', '.join(meta.get('entities', [])) if meta.get('entities') else '待补充'}</small>

<small>**方法**：{methods_str}</small>

<small>**区域**：{meta.get('study_area', '待补充')}</small>

<small>**应用**：{apps_str}</small>"""

    if _is_english_item(item):
        # ── 英文条目：英文为核心，中文译名小字 ───────────────────
        summary_en = (meta.get('summary_en') or '').strip()
        if not summary_en:
            s = item.get('summary', '') or ''
            summary_en = s if s and detect_lang(s) == 'en' else title_zh
        tags_en = [t for t in (meta.get('tags_en') or []) if t]
        if not tags_en:
            tags_en = tags  # LLM 未给英文标签时退回中文标签（保底可检索）
        body = f"""---
id: {eid}
title: {json.dumps(item['title'], ensure_ascii=False)}
module: external-feed
lang: en
tags: {json.dumps(tags_en[:12], ensure_ascii=False)}
summary: {json.dumps(summary_en[:200], ensure_ascii=False)}
confidence: C
status: active
updated: {date}
sources: {json.dumps(sources, ensure_ascii=False)}
---

## 来源
- **类型**: {item['type']}
- **期刊/来源**: {item.get('journal', '')}
- **原始标题**: {item['title']}
- **原始 URL**: {item.get('url', '')}
- **收录日期**: {date}

## Summary

{summary_en}

{zh_block}

## 备注
- 关键词命中: {', '.join(hits)}
- PDF 状态: {pdf_status_line(pdf_info, pdf_reason)}
"""
    else:
        body = f"""---
id: {eid}
title: {json.dumps(title_zh, ensure_ascii=False)}
module: external-feed
tags: {json.dumps(tags, ensure_ascii=False)}
summary: {json.dumps((summary_cn or '')[:120], ensure_ascii=False)}
confidence: C
status: active
updated: {date}
sources: {json.dumps(sources, ensure_ascii=False)}
---

## 来源
- **类型**: {item['type']}
- **期刊/来源**: {item.get('journal', '')}
- **原始标题**: {item['title']}
- **原始 URL**: {item.get('url', '')}
- **收录日期**: {date}

## 中文摘要
{summary_cn}

## 关键实体
{', '.join(meta.get('entities', [])) if meta.get('entities') else '待补充'}

## 核心方法
{methods_str}

## 研究区域
{meta.get('study_area', '待补充')}

## 应用场景/意义
{apps_str}

## 备注
- 关键词命中: {', '.join(hits)}
- PDF 状态: {pdf_status_line(pdf_info, pdf_reason)}
"""
    with open(path, 'w', encoding='utf-8') as f:
        f.write(body)
    return eid


NO_PDF_FILE = BASE / 'state' / 'journal_no_pdf.jsonl'


def _record_no_pdf(item, reason):
    """记一条「今天没下到 PDF」的待补条目，供人工下载时对照。

    只写清单，**不写台账、不建知识库条目**（2026-10-21 口径：没全文就不入库）。
    清单是用户的工作表：打开它挑要下的，下完投进 inbox/papers 即可。
    """
    try:
        NO_PDF_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(NO_PDF_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps({
                'ts': datetime.now().isoformat(timespec='seconds'),
                'title': item.get('title', ''),
                'journal': item.get('journal', ''),
                'url': item.get('url', ''),
                'date': item.get('date', ''),
                'reason': (reason or '')[:120],
            }, ensure_ascii=False) + '\n')
    except OSError:
        pass


def process_item(item, state, api_key, dry_run=False):
    """返回 (status_dict, infra_hit)。"""
    uid = _id(item['title'], item['url'])
    if uid in state['processed']:
        return {'status': 'skipped', 'reason': 'already_processed'}, False

    ok, score, hits = is_relevant(item['title'], item.get('summary', ''), item['source'])
    if not ok:
        state['processed'].add(uid)
        return {'status': 'filtered', 'reason': 'no_keyword_match', 'score': score}, False

    print(f"  [命中 {score}] {item['title'][:60]}... hits={hits}", flush=True)
    date = item.get('date', datetime.now().strftime('%Y-%m-%d'))

    # 期刊：PDF 优先。成功则走全文 DeepSeek → papers 模块；
    # 失败（付费墙/验证码/源站无全文）才退「外部订阅」题录。
    pdf_path = None
    pdf_reason = ''
    if not dry_run and item['type'] == 'journal' and item.get('url'):
        pdf_path, pdf_reason = _fetch_journal_pdf(item, date)
        if pdf_path:
            try:
                digest = sha1_of(pdf_path)
                paper_file = PAPERS_DIR / f"paper-{digest[:12]}.md"
                if paper_file.exists() and not _is_manual_entry(paper_file):
                    print(f"      已存在语料条目 paper-{digest[:12]}，跳过提取", flush=True)
                else:
                    if paper_file.exists():
                        print(f"      条目为门户按钮建档（manual），升级为全文提取", flush=True)
                    # 入库闸门（2026-10-20）：本脚本属于**每日管线（daily_poster）**
                    # 的外部订阅余下链路，daily_poster 允许自动入库；一旦该 scope
                    # 被关掉，就只下载 PDF、不调 LLM、不建词条，等用户统一安排。
                    if not ingest_policy.enabled('daily_poster'):
                        ingest_policy.queue_pending({
                            'eid': f"paper-{digest[:12]}",
                            'title': item['title'], 'journal': item.get('journal', ''),
                            'pdf': pdf_path, 'url': item.get('url', ''),
                            'doi': item.get('doi') or '', 'from': 'external',
                        })
                        state['processed'].add(uid)
                        print(f"      ⇢ 只下载模式：PDF 已落 NAS，待入库", flush=True)
                        return {'status': 'downloaded_only', 'pdf': pdf_path}, False
                    text = extract_text(pdf_path, digest)
                    if len(text) >= MIN_TEXT_CHARS:
                        obj, err = ds_extract(text, digest, api_key)   # 内部已统一 prepare，勿再截断
                        if obj:
                            meta_repo = {'title': item['title'],
                                         'journal': item.get('journal', ''),
                                         'date': date, 'repo_path': pdf_path}
                            eid = write_entry(digest, meta_repo, obj, 'llm')
                            print(f"      → papers 条目 {eid} ✓", flush=True)
                            state['processed'].add(uid)
                            return {'status': 'created_paper', 'eid': eid}, False
                        print(f"      全文提取失败（{err}），转题录提取", flush=True)
            except InfraError:
                raise  # 交给主循环：不标记 processed，次日重跑
            except Exception as e:
                print(f"      全文处理异常（{e}），转题录提取", flush=True)
        else:
            print(f"      PDF 下载失败: {pdf_reason}", flush=True)

    # 2026-10-21 用户口径：**当天没拿到 PDF 的期刊论文一律不入库**。
    # 旧行为会继续走到 write_feed_entry，建一条只有题目、没有全文的
    # external-feed 条目 —— 知识库里长期留空壳，journals 页判「已归档」
    # 还要在四处代码里绕开它。现在改为：只记待补清单，等人工投放。
    # 新闻条目（type=news）不受影响：它本来就没有 PDF。
    if item['type'] == 'journal' and not dry_run and not pdf_path:
        _record_no_pdf(item, pdf_reason)
        state['processed'].add(uid)
        print("      ⇢ 未下到 PDF，不入库；已记入 state/journal_no_pdf.jsonl "
              "（等人工投放到 inbox/papers）", flush=True)
        return {'status': 'no_pdf_skipped', 'reason': pdf_reason}, False

    if dry_run:
        return {'status': 'dry_run', 'eid': f"feed-{uid}", 'score': score}, False

    # PDF 已落 NAS 但全文提取失败时，题录里要能追溯到本地文件
    pdf_info = {'pdf_path': pdf_path} if pdf_path else None

    # 题录级 DeepSeek 提取（新闻 / 非OA论文 / 全文失败）
    meta, err = ds_feed_metadata(item, api_key)
    if not meta:
        print(f"      题录提取失败（{err}），规则兜底", flush=True)
        meta = rule_metadata(item['title'], item.get('summary', ''), item['source'], hits)

    eid = write_feed_entry(item, meta, uid, date, pdf_info, hits, pdf_reason)
    state['processed'].add(uid)
    return {'status': 'created', 'eid': eid, 'score': score}, False


def main(dry_run=False):
    print(f"[{datetime.now().isoformat()}] 外部订阅源 → GeoKB 开始（DeepSeek）", flush=True)
    FEED_DIR.mkdir(parents=True, exist_ok=True)

    state = load_state()
    state['processed'] = set(state.get('processed', []))

    items = collect_journal_items() + collect_news_items()
    print(f"共读取 {len(items)} 条外部条目（期刊+新闻）", flush=True)

    api_key = None
    if not dry_run:
        api_key = load_api_key()

    results = []
    infra_streak = 0
    for item in items:
        try:
            r, _ = process_item(item, state, api_key, dry_run=dry_run)
            results.append(r)
            infra_streak = 0
        except InfraError as e:
            infra_streak += 1
            print(f"  [infra] DeepSeek 故障（{e}），本条留待次日重跑", flush=True)
            results.append({'status': 'infra_pending', 'error': str(e)})
            if infra_streak >= INFRA_ABORT_AFTER:
                print(f"  [infra] 连续 {infra_streak} 次故障，中止本次运行", flush=True)
                break
        except Exception as e:
            print(f"处理失败: {item.get('title', '')[:50]}... 错误: {e}", flush=True)
            results.append({'status': 'error', 'error': str(e)})

    created = sum(1 for r in results if r.get('status') == 'created')
    created_paper = sum(1 for r in results if r.get('status') == 'created_paper')
    no_pdf = sum(1 for r in results if r.get('status') == 'no_pdf_skipped')
    filtered = sum(1 for r in results if r.get('status') == 'filtered')
    skipped = sum(1 for r in results if r.get('status') == 'skipped')
    pending = sum(1 for r in results if r.get('status') == 'infra_pending')
    errors = sum(1 for r in results if r.get('status') == 'error')
    print(f"完成: feed新建 {created}, papers新建 {created_paper}, "
          f"没下到PDF不入库 {no_pdf}, 过滤 {filtered}, "
          f"已存在 {skipped}, 留待重跑 {pending}, 失败 {errors}", flush=True)

    if not dry_run:
        save_state(state)
        print("重建 GeoKB 索引...", flush=True)
        try:
            ingest.rebuild(embed=True)
            print("索引重建完成", flush=True)
        except Exception as e:
            print(f"索引重建失败: {e}", flush=True)

    print(f"[{datetime.now().isoformat()}] 结束", flush=True)


if __name__ == '__main__':
    dry = '--dry-run' in sys.argv
    main(dry_run=dry)
