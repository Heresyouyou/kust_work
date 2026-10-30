#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""B 方案落地（修订）：对地学前缘乱码 PDF 做 OCR，产出可入库的纯文本。

为什么不是字形映射
------------------
字形指纹方案实测覆盖率只有 5%，根因三条：
  1. **字重不同**：标题用 19.7pt 的标题宋，页眉/正文用 7.8pt 正文字宋，
     同一字体对象里两种字形的轮廓不同（「西」的 IoU 只有 0.55，最佳候选还是「两」）。
  2. **字典覆盖不足**：4128 字里缺「羌 / 陆 / 缘 / 造」这类地学常用字。
  3. **每页字体子集不同**：FzBookMaker8 在第 1 页装的是数字（0x21→"2"），
     别的页装汉字，跨页缓存会串味。
结论：字形这条路只能解页眉、作者、收稿日期这类小字号，正文靠不住。OCR 才是正解。

用法：
  python scripts/esf_ocr_text.py --limit 1          # 试 1 篇
  python scripts/esf_ocr_text.py --all --workers 4  # 全量（多进程）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
AUDIT = BASE / "state" / "esf_font_audit.json"
OUTDIR = BASE / "state" / "esf_text"
LOCAL = BASE / "state" / "_esf_local"   # PDF 本地副本，避开 NAS IO 与多进程争抢
DPI = 200


def ocr_pdf(pdf: Path, ocr) -> tuple[str, dict]:
    import numpy as np
    import pymupdf

    doc = pymupdf.open(pdf)
    pages = []
    nbox = 0
    try:
        for pno in range(doc.page_count):
            page = doc[pno]
            pix = page.get_pixmap(dpi=DPI)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n == 4:
                img = img[:, :, :3]
            img = np.ascontiguousarray(img[:, :, ::-1])     # RGB → BGR
            # ⚠️ use_cls=False：跳过「文字方向分类」这一步。
            # 实测同一页 9.54s → 3.91s（**2.4 倍**），识别框数量完全一致（46/46）。
            # 地学前缘的 PDF 全是正向排版，没有横倒页，跳过方向判断没有风险。
            # 不跳的话 573 篇约 10 小时，跳了约 4 小时。
            res, _ = ocr(img, use_cls=False)
            if not res:
                continue
            # 按 y 聚行：同一行内按 x 排序
            items = []
            for it in res:
                box, txt = it[0], it[1]
                ys = [p[1] for p in box]
                xs = [p[0] for p in box]
                items.append((min(ys), min(xs), txt))
            items.sort()
            rows, cur, cur_y = [], [], None
            for y, x, txt in items:
                if cur_y is None or abs(y - cur_y) <= 12:
                    cur.append((x, txt))
                    cur_y = y if cur_y is None else cur_y
                else:
                    rows.append("".join(t for _x, t in sorted(cur)))
                    cur = [(x, txt)]
                    cur_y = y
            if cur:
                rows.append("".join(t for _x, t in sorted(cur)))
            pages.append("\n".join(rows))
            nbox += len(res)
    finally:
        doc.close()
    return "\n\n".join(pages), {"pages": doc.page_count if False else len(pages), "boxes": nbox}


def _worker(args):
    """多进程入口：子进程内自建 OCR（模型不能跨进程共享）。

    ⚠️ 必须在这里把 onnxruntime 的线程数压到 2：它默认吃满所有逻辑核，
    4 个进程 × 16 线程互相抢，实测单篇从 140 秒涨到 379 秒（越并行越慢）。
    环境变量要在 import 之前设，import 之后再改无效。
    """
    eid, path = args
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    os.environ.setdefault("ORT_NUM_THREADS", "2")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")
    os.environ.setdefault("MKL_NUM_THREADS", "2")
    from rapidocr_onnxruntime import RapidOCR
    ocr = RapidOCR()
    t0 = time.time()
    try:
        txt, st = ocr_pdf(Path(path), ocr)
    except Exception as e:
        return (eid, 0.0, 0, str(e)[:100], time.time() - t0)
    out = OUTDIR / ("%s.txt" % eid)
    out.write_text("### OCR 还原（地学前缘，方正书版无 ToUnicode）\n\n" + txt, encoding="utf-8")
    cjk = sum(1 for ch in txt if "\u3400" <= ch <= "\u9fff")
    return (eid, time.time() - t0, cjk, "", st["pages"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--eids", default="")
    a = ap.parse_args()

    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    bad = [x for x in audit["items"] if x["kind"] == "A_bm_only"]
    if a.eids:
        want = set(a.eids.split(","))
        bad = [x for x in bad if x.get("eid") in want]
    if a.limit:
        bad = bad[:a.limit]
    OUTDIR.mkdir(parents=True, exist_ok=True)
    print("待 OCR %d 篇" % len(bad), flush=True)

    def _pdf_of(x):
        """优先用本地副本：NAS 上 6 个进程同时读会互相拖慢（实测内存 85%/CPU 34%）。"""
        eid = x.get("eid") or Path(x["path"]).stem[:20]
        lp = LOCAL / ("%s.pdf" % eid)
        return eid, (str(lp) if lp.exists() else x["path"])

    jobs = [_pdf_of(x) for x in bad]
    # 断点续跑：已有产物的直接跳过（OCR 很贵，重启不该白干）
    before = len(jobs)
    jobs = [(e, p2) for e, p2 in jobs if not (OUTDIR / ("%s.txt" % e)).exists()]
    if before != len(jobs):
        print("跳过已完成 %d 篇，待处理 %d 篇" % (before - len(jobs), len(jobs)), flush=True)
    t0 = time.time()
    ok = bad_n = 0

    if a.workers <= 1:
        os.environ.setdefault("OMP_NUM_THREADS", "4")
        from rapidocr_onnxruntime import RapidOCR
        ocr = RapidOCR()
        for i, (eid, path) in enumerate(jobs, 1):
            try:
                txt, st = ocr_pdf(Path(path), ocr)
            except Exception as e:
                print("  %d/%d %s 失败 %s" % (i, len(jobs), eid, type(e).__name__), flush=True)
                bad_n += 1
                continue
            (OUTDIR / ("%s.txt" % eid)).write_text(
                "### OCR 还原（地学前缘，方正书版无 ToUnicode）\n\n" + txt, encoding="utf-8")
            cjk = sum(1 for ch in txt if "\u3400" <= ch <= "\u9fff")
            ok += 1
            print("  %d/%d %s｜%d 页｜%d 框｜中文 %d 字｜%.0fs（累计 %.0fs）"
                  % (i, len(jobs), eid, st["pages"], st["boxes"], cjk,
                     time.time() - t0 - (t0 - t0), time.time() - t0), flush=True)
    else:
        import multiprocessing as mp
        with mp.Pool(a.workers) as pool:
            for i, (eid, dt, cjk, err, npg) in enumerate(
                    pool.imap_unordered(_worker, jobs), 1):
                if err:
                    print("  %d/%d %s 失败 %s" % (i, len(jobs), eid, err), flush=True)
                    bad_n += 1
                else:
                    ok += 1
                    print("  %d/%d %s｜%.0fs｜中文 %d 字" % (i, len(jobs), eid, dt, cjk), flush=True)

    print("\n完成 %d 篇｜失败 %d｜总用时 %.1f 分钟｜产物 %s"
          % (ok, bad_n, (time.time() - t0) / 60, OUTDIR))
    return 0


if __name__ == "__main__":
    sys.exit(main())
