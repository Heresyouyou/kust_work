"""台账迁移 + eid 回填（一次性修复脚本，纯本地操作，不调任何 API）。

背景
----
1. NAS 上的 `S:/GeoKB/papers_repo/index.jsonl` 被 QTS 侧**独占锁定**：
   `open(..., 'a')` 与 `os.replace()` 均返回 PermissionError / WinError 5，
   持续数分钟不释放。台账只是索引用途，没必要冒写入风险。
2. journals 列表要判「已归档」并挂阅读/翻译入口，需要 eid = paper-<sha1前12>，
   而早期台账记录没写这个字段。

做法
----
- 主副本迁到本地 `D:/ZSK/geokb/state/papers_index.jsonl`（本地盘，写入无阻）。
- 把 NAS 旧台账合并进来，顺带给缺失的条目补 eid（读 PDF 分块算 sha1）。
- NAS 旧文件保持只读不动（等锁释放后由人工决定是否清理）。

用法：
    python backfill_ledger_eid.py --dry-run
    python backfill_ledger_eid.py
"""
import argparse
import hashlib
import json
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
LEGACY = Path('S:/GeoKB/papers_repo/index.jsonl')
LOCAL = BASE / 'state' / 'papers_index.jsonl'


def eid_of(repo_path):
    """读 PDF 分块算 sha1；文件不在/读不到返回 None。"""
    p = str(repo_path or '')
    if not p:
        return None
    try:
        h = hashlib.sha1()
        with open(p, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
        return f'paper-{h.hexdigest()[:12]}'
    except OSError:
        return None


def read_jsonl(path):
    try:
        raw = path.read_text(encoding='utf-8', errors='replace')
    except OSError as e:
        print(f'  [warn] 读不到 {path.name}: {e}')
        return []
    out = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    src = read_jsonl(LEGACY)
    local = read_jsonl(LOCAL) if LOCAL.exists() else []
    print(f'NAS 旧台账 {len(src)} 条 · 本地台账 {len(local)} 条')

    # 合并：按 norm_title 去重，后出现者覆盖（本地优先，其后才是 NAS 兜底）
    def key(m):
        import re
        return re.sub(r'\W+', '', (m.get('title') or '').lower())[:80]

    merged = {}
    for m in src + local:
        k = key(m)
        if k:
            merged[k] = m

    n_set, n_miss = 0, 0
    for m in merged.values():
        if m.get('eid'):
            continue
        e = eid_of(m.get('repo_path'))
        if e:
            m['eid'] = e
            n_set += 1
        else:
            n_miss += 1
    print(f'合并后 {len(merged)} 条 · 本次补 eid {n_set} 条 · 文件缺失 {n_miss} 条')

    if args.dry_run:
        return 0

    LOCAL.parent.mkdir(parents=True, exist_ok=True)
    payload = '\n'.join(json.dumps(m, ensure_ascii=False)
                        for m in merged.values()) + '\n'
    for attempt in range(3):
        try:
            LOCAL.write_text(payload, encoding='utf-8')
            print(f'本地台账已写入: {LOCAL} ({len(merged)} 行)')
            return 0
        except OSError as e:
            print(f'  [warn] 写入失败 {attempt + 1}/3: {e}')
            time.sleep(1.5)
    print('  [error] 本地台账写入失败')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
