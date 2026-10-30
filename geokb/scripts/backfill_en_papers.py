# -*- coding: utf-8 -*-
"""英文论文条目回填：存量中文优先结构 → 英文优先（2026-10-17 用户规则）。

背景
----
2026-10-17 起，英文论文入库改为「英文为核心、中文译名小字附后」
（pdf_corpus_ingest.write_entry 的 lang=='en' 分支 + EN_PROMPT_TMPL）。
但存量约 745 篇英文论文是旧管线写入的中文优先结构——英文原文只躺在
正文末尾一行「英文标题」，摘要/方法/区域全是中文转写，正是用户要消除的
翻译偏差风险。本脚本按新规则原地回填。

做法（复用新管线，零新代码路径）：
  1. 扫描 content/papers/paper-*.md，跳过已带 lang: en 的条目；
  2. 由条目 id（SHA1 前 12 位）在 state/text/ 找全量文本缓存；
  3. detect_lang == 'en' → ds_extract(smart_truncate(text)) 走 EN_PROMPT_TMPL；
  4. write_entry 原地重写（eid 不变 → 索引/URL/收藏关系不变），
     并把旧 front matter 的扩展键（facets 等）合并回新 front matter；
  5. 中文论文跳过；无文本缓存的进 missing 清单（可另行 OCR/补抓）。

用法：
  python backfill_en_papers.py --dry-run      # 只统计，不调用 API
  python backfill_en_papers.py --limit 20     # 分批回填（前 20 篇）
  python backfill_en_papers.py                # 全量
  python backfill_en_papers.py --only <id12>  # 只回填指定条目
"""
import argparse
import io
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))
sys.path.insert(0, str(BASE / "server"))

import yaml  # noqa: E402

from deepseek_client import InfraError, load_api_key  # noqa: E402
from pdf_corpus_ingest import (detect_lang, ds_extract, smart_truncate,  # noqa: E402
                               write_entry)

PAPERS_DIR = BASE / "content" / "papers"
TEXT_DIR = BASE / "state" / "text"
WORKERS = 6               # DeepSeek 调用是延迟瓶颈（~12s/篇），并行提速
_lock = threading.Lock()

# write_entry 生成的 front matter 键；其余旧键（facets 等）回写时保留
KNOWN_KEYS = {"id", "title", "module", "lang", "tags", "summary", "confidence",
              "status", "updated", "sources", "journal", "year", "extraction"}


def split_entry(path):
    """返回 (front_matter_dict, body, raw)。"""
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", raw, re.S)
    if not m:
        return None, None, raw
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception:
        fm = {}
    return fm, m.group(2), raw


def find_text(eid12):
    """条目 id（前12位）→ state/text 全量 SHA1 缓存路径。"""
    hits = list(TEXT_DIR.glob(f"{eid12}*.txt"))
    return hits[0] if hits else None


def merge_extra_keys(path, old_fm):
    """把旧 front matter 的扩展键（facets 等）合并回重写后的条目。

    注意：必须插在 front matter 的收尾 `---` **之前**——插到其后就会掉进
    正文，YAML 直接失效。
    """
    extra = {k: v for k, v in (old_fm or {}).items() if k not in KNOWN_KEYS}
    if not extra:
        return
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"^(---\n.*?)(\n---\n)(.*)$", raw, re.S)
    if not m:
        return
    block = yaml.safe_dump(extra, allow_unicode=True, sort_keys=False).strip()
    path.write_text(m.group(1) + "\n" + block + m.group(2) + m.group(3),
                    encoding="utf-8")


def process_one(p, api_key, args, stats, missing, failed):
    """回填单篇。返回 'done'/'skip_*'/'fail'。线程安全（各写各的文件）。"""
    eid12 = p.stem.replace("paper-", "")
    fm, _body, _raw = split_entry(p)
    if not fm:
        with _lock:
            failed.append((eid12, "front matter 解析失败"))
            stats["fail"] += 1
        return "fail"
    if str(fm.get("lang", "")).lower() == "en":
        with _lock:
            stats["already_en"] += 1
        return "already_en"

    tp = find_text(eid12)
    if not tp:
        with _lock:
            stats["no_text"] += 1
            missing.append(eid12)
        return "no_text"
    text = tp.read_text(encoding="utf-8", errors="ignore")
    if detect_lang(text[:8000]) != "en":
        with _lock:
            stats["zh_skip"] += 1
        return "zh_skip"

    if args.dry_run:
        with _lock:
            stats["done"] += 1
            print(f"  [dry] {eid12} {str(fm.get('title', ''))[:40]}")
        return "done"

    try:
        obj, err = ds_extract(text, tp.stem, api_key)   # 内部已统一 prepare，勿再截断
    except InfraError as e:
        with _lock:
            stats["infra_abort"] += 1
            failed.append((eid12, f"infra: {e}"))
        print(f"  [infra] {eid12}: {e}")
        return "infra"
    if not obj:
        with _lock:
            stats["fail"] += 1
            failed.append((eid12, str(err)[:80]))
        print(f"  [fail] {eid12}: {err}")
        return "fail"

    # meta：journal/date/repo_path 取自旧条目（PDF 路径只用于生成相对路径串）
    src_path = ""
    for s in (fm.get("sources") or []):
        if isinstance(s, dict) and s.get("path"):
            src_path = s["path"]
            break
    if src_path and not re.match(r"^[A-Za-z]:", src_path):
        src_path = "S:/GeoKB/" + src_path.lstrip("/")
    meta = {"journal": fm.get("journal") or "",
            "date": (fm.get("year") or "") + "-01-01",
            "repo_path": src_path}
    try:
        write_entry(tp.stem, meta, obj, "llm")
        merge_extra_keys(p, fm)
        with _lock:
            stats["done"] += 1
            n = stats["done"]
        if n % 25 == 0:
            print(f"  ... 已完成 {n} 篇", flush=True)
        return "done"
    except Exception as e:  # noqa: BLE001
        with _lock:
            stats["fail"] += 1
            failed.append((eid12, f"{type(e).__name__}: {e}"))
        return "fail"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="", help="只回填指定条目 id（可不带 paper- 前缀）")
    ap.add_argument("--workers", type=int, default=WORKERS)
    args = ap.parse_args()

    # 入库闸门（2026-10-20 用户口径）：本脚本会调 LLM 重写词条 = 入库行为，
    # 只有 daily_poster 允许自动执行；其余一律等用户统一安排。
    import ingest_policy
    if not ingest_policy.enabled('backfill'):
        print('入库策略：backfill 未开启自动入库（state/ingest_policy.json）。'
              '如需执行请先 python scripts/ingest_policy.py --set backfill=on', flush=True)
        return 2

    entries = sorted(PAPERS_DIR.glob("paper-*.md"))
    if args.only:
        want = args.only.replace("paper-", "")
        entries = [p for p in entries if p.stem.replace("paper-", "") == want]
    stats = {"total": len(entries), "already_en": 0, "zh_skip": 0,
             "no_text": 0, "done": 0, "fail": 0, "infra_abort": 0}
    missing, failed = [], []

    api_key = None if args.dry_run else load_api_key()
    print(f"待扫描 {len(entries)} 篇，并发 {args.workers}", flush=True)

    if args.dry_run or args.workers <= 1:
        for p in entries:
            process_one(p, api_key, args, stats, missing, failed)
            if args.limit and stats["done"] >= args.limit:
                print(f"达到 --limit {args.limit}，停止")
                break
    else:
        done = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(process_one, p, api_key, args, stats, missing, failed): p
                    for p in entries}
            for fut in futs:
                fut.result()
                done += 1
                if args.limit and stats["done"] >= args.limit:
                    print(f"达到 --limit {args.limit}，停止（余下取消）", flush=True)
                    for f in futs:
                        f.cancel()
                    break

    print("\n===== 回填统计 =====")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    if missing:
        print(f"  无文本缓存 {len(missing)} 篇: {missing[:10]}{' ...' if len(missing) > 10 else ''}")
    if failed:
        print(f"  失败 {len(failed)} 篇: {failed[:10]}")
    print("\n后续：python -c \"import sys; sys.path.insert(0,'server'); import ingest; ingest.rebuild(embed=True)\"")
    print("      python scripts/build_facet_index.py   # 分面索引重建")


if __name__ == "__main__":
    main()
