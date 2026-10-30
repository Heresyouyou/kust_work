# -*- coding: utf-8 -*-
"""批量索引知识点（本地向量，零 API）。

为什么需要：`index_one_entry.py` 一次只处理一个条目，且每次都要重载 embedding
模型（约 1–2 秒）。重写一批论文的知识点后（如 50 篇 × 6~8 条 = 300+ 条），
逐条调用要十几分钟；本脚本一次装载模型、按 32 条一批嵌入，同样量级 1 分钟内完成。

用法：
    python scripts/index_bulk.py --sha 0e7dff06fae7                # 单篇的全部知识点
    python scripts/index_bulk.py --sha-file state/kp_redo_sample.json   # 清单里所有篇
    python scripts/index_bulk.py --sha-file x.json --with-paper         # 连论文条目一起
"""
import argparse
import glob
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "server"))
sys.path.insert(0, os.path.join(BASE, "scripts"))

import ingest  # noqa: E402
import index_one_entry as ioe  # noqa: E402

CONTENT = os.path.join(BASE, "content")


def collect(shas, with_paper=False):
    files = []
    for s in shas:
        s = s.strip()[:12]
        if with_paper:
            p = os.path.join(CONTENT, "papers", "paper-%s.md" % s)
            if os.path.exists(p):
                files.append(p)
        files.extend(sorted(glob.glob(os.path.join(CONTENT, "kpoints",
                                                   "kp-%s-*.md" % s))))
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sha", action="append", default=[])
    ap.add_argument("--sha-file", default="", help='JSON：{"shas":[...]} 或 [...]')
    ap.add_argument("--with-paper", action="store_true", help="连论文条目一起索引")
    ap.add_argument("--batch", type=int, default=32)
    args = ap.parse_args()

    shas = list(args.sha)
    if args.sha_file:
        payload = json.load(open(args.sha_file, encoding="utf-8"))
        shas += payload.get("shas") if isinstance(payload, dict) else payload
    if not shas:
        print("需要 --sha 或 --sha-file")
        return 2

    files = collect(shas, args.with_paper)
    print("待索引 %d 个条目（%d 篇）" % (len(files), len(shas)), flush=True)

    entries = []
    bad = 0
    for f in files:
        e = ingest.parse_entry(f)
        if not e:
            bad += 1
            continue
        entries.append(e)
    if bad:
        print("  [WARN] %d 个文件解析失败，已跳过" % bad, flush=True)

    conn = ingest.get_db()
    ingest.init_schema(conn)

    t0 = time.time()
    done = 0
    for i in range(0, len(entries), args.batch):
        chunk = entries[i:i + args.batch]
        vecs = list(ioe.embed_model().embed([ioe.embed_text(e) for e in chunk]))
        for e, v in zip(chunk, vecs):
            ioe.upsert(conn, e, vec=v)
            done += 1
        conn.commit()
        print("  %d/%d  %.0fs" % (done, len(entries), time.time() - t0), flush=True)
    print("完成：索引 %d 条，用时 %.0fs" % (done, time.time() - t0), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
