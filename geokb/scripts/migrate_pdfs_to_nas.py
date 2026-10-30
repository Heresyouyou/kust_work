# -*- coding: utf-8 -*-
"""把论文仓库 PDF 从本地盘迁移到 NAS（S:）。【历史脚本，2026-10-16 起停用 by_date】

⚠️ by_date 已停用：全量对账（749/749 MD5 一致）确认它与 by_journal 内容完全重复，
   且早已停止同步。NAS 上的目录已改名 by_date.deprecated，观察一周后删除。
   本脚本保留仅为历史存档，不要再运行 —— 需要补迁文件时只跑 by_journal 阶段。

原设计（存档）：
- by_journal 为主副本（并发复制，带重试）
- by_date 复用硬链接（os.link）保持与原结构一致；SMB 不支持时退化为复制
- 全程断点续跑：目标文件已存在且大小一致则跳过
- 校验阶段逐文件比对大小，并抽样 MD5
"""
import hashlib
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

SRC = r"D:/PostNews/papers_repo"
DST = r"S:/GeoKB/papers_repo"
LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "logs", "pdf_migrate.jsonl")
CONC = 8

_stat = {"copied": 0, "linked": 0, "skipped": 0, "failed": 0, "bytes": 0}
_lock = __import__("threading").Lock()


def rel_files(root, sub):
    base = os.path.join(root, sub)
    out = []
    for dp, _dn, fn in os.walk(base):
        for f in fn:
            p = os.path.join(dp, f)
            out.append((os.path.relpath(p, base), p))
    return out


def copy_one(rel, sp):
    dp = os.path.join(DST, "by_journal", rel)
    os.makedirs(os.path.dirname(dp), exist_ok=True)
    try:
        ss = os.path.getsize(sp)
        if os.path.exists(dp) and os.path.getsize(dp) == ss:
            with _lock:
                _stat["skipped"] += 1
            return "skip"
        tmp = dp + ".part"
        for attempt in range(3):
            try:
                with open(sp, "rb") as f, open(tmp, "wb") as g:
                    shutil.copyfileobj(f, g, 1024 * 1024)
                os.replace(tmp, dp)
                if os.path.getsize(dp) != ss:
                    raise IOError("size mismatch %d != %d" % (os.path.getsize(dp), ss))
                with _lock:
                    _stat["copied"] += 1
                    _stat["bytes"] += ss
                return "copy"
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
    except Exception as e:
        with _lock:
            _stat["failed"] += 1
        return "fail:%s" % str(e)[:80]


def _journal_map():
    """by_journal 下 basename -> 绝对路径。"""
    m = {}
    base = os.path.join(DST, "by_journal")
    for dp, _dn, fn in os.walk(base):
        for f in fn:
            m.setdefault(f, os.path.join(dp, f))
    return m


def link_one(rel, sp, jmap=None):
    """by_date 视图：优先硬链接到 by_journal 对应文件，失败则复制。

    注意 by_date 与 by_journal 的文件命名规则不同：
      by_journal/<期刊>/<年>/<date>_<title>.pdf
      by_date/<date>/<期刊>_<date>_<title>.pdf      ← 多一个期刊前缀
    因此必须按「去掉期刊前缀后的 basename」在 by_journal 中反查，不能直接套用同一 rel。
    """
    dp = os.path.join(DST, "by_date", rel)
    name = os.path.basename(rel)
    jp = None
    for k, v in (jmap or {}).items():
        if name == k or name.endswith("_" + k):
            jp = v
            break
    if not jp:
        # 回退：从源 by_date 文件本身复制（内容与 by_journal 等价）
        jp = sp
    os.makedirs(os.path.dirname(dp), exist_ok=True)
    if os.path.exists(dp) and os.path.getsize(dp) == os.path.getsize(jp):
        with _lock:
            _stat["skipped"] += 1
        return
    try:
        os.link(jp, dp)
        with _lock:
            _stat["linked"] += 1
        return
    except OSError:
        pass
    try:
        shutil.copy2(jp, dp)
        with _lock:
            _stat["copied"] += 1
    except Exception:
        with _lock:
            _stat["failed"] += 1


def md5(p, limit=None):
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def phase_journal():
    items = rel_files(SRC, "by_journal")
    print("by_journal 待迁移 %d 个文件" % len(items), flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=CONC) as ex:
        futs = {ex.submit(copy_one, r, p): r for r, p in items}
        for i, fu in enumerate(as_completed(futs), 1):
            fu.result()
            if i % 50 == 0 or i == len(items):
                el = time.time() - t0
                print("  [%d/%d] %.0fs 已传 %.2f GB 速率 %.1f MB/s"
                      % (i, len(items), el, _stat["bytes"] / 2 ** 30,
                         _stat["bytes"] / 2 ** 20 / el if el else 0), flush=True)
    return len(items)


def phase_date():
    # ⚠️ 2026-10-16：by_date 影子目录已停用（与 by_journal 全量重复且停止同步），
    # NAS 上已改名 by_date.deprecated。此函数仅存档，不再调用。
    raise RuntimeError("by_date 已停用：见文件头说明，迁移只跑 phase_journal()")


def verify(sample=25):
    print("\n=== 校验 ===")
    items = rel_files(SRC, "by_journal")
    bad = []
    for r, sp in items:
        dp = os.path.join(DST, "by_journal", r)
        if not os.path.exists(dp) or os.path.getsize(dp) != os.path.getsize(sp):
            bad.append(r)
    print("大小比对：源 %d，不一致 %d" % (len(items), len(bad)))
    import random
    random.seed(7)
    picks = random.sample(items, min(sample, len(items)))
    mism = []
    for r, sp in picks:
        dp = os.path.join(DST, "by_journal", r)
        if md5(sp) != md5(dp):
            mism.append(r)
    print("MD5 抽样 %d 个：不一致 %d" % (len(picks), len(mism)))
    if bad:
        print("  缺失/不一致样例:", bad[:5])
    return len(bad), len(mism)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    if mode in ("all", "copy"):
        n = phase_journal()
        # phase_date() 已停用（2026-10-16，by_date 与 by_journal 全量重复）
        print("迁移完成：复制 %d，硬链接 %d，跳过 %d，失败 %d，传输 %.2f GB"
              % (_stat["copied"], _stat["linked"], _stat["skipped"], _stat["failed"],
                 _stat["bytes"] / 2 ** 30), flush=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "stat": dict(_stat)}, ensure_ascii=False) + "\n")
    if mode in ("all", "verify"):
        b, m = verify()
        sys.exit(1 if (b or m) else 0)
