"""bilingual.py — 翻译完成后自动生成「双语拼接版」PDF

为什么独立成模块：
  1) web_app 的翻译线程、缓存命中路径、以及离线补生成脚本都要用它；
  2) 拼接逻辑与 Flask 无关，单独可测（tools/make_dual.py）。

拼接形态（mode）：
  lr  (默认) 左右对照：左=原文 右=译文，页宽 ×2，适合宽屏只读
  tb          上下对照：上=原文 下=译文，页高 ×2
  alt         逐页交替：原1、译1、原2、译2 … 页数 ×2，适合顺序打印

⚠️ 命名铁律（踩过坑）：
  BabelDOC 在 watermark_output_mode=NoWatermark 时，产物名会多一段
  `.no_watermark`，例如 `xxx_1a2b3c4d.no_watermark.zh.mono.pdf`。
  因此**绝不能硬拼** `f'{stem}.zh.mono.pdf'` —— 那样 exists() 恒为 False，
  双语版永远不会生成（这正是 dual 长期为 0 的根因）。
  一律用 find_translated() 的 glob 匹配。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger('bilingual')

MONO_SUFFIX = '.zh.mono.pdf'
DUAL_SUFFIX = '.zh.dual.pdf'
NO_WATERMARK = '.no_watermark'

MODES = ('lr', 'tb', 'alt')


# ────────────────────────── 命名 / 查找 ──────────────────────────

def mono_to_dual_name(mono_name: str) -> str:
    """译文文件名 → 双语文件名。

    'a_1a2b3c4d.no_watermark.zh.mono.pdf' → 'a_1a2b3c4d.zh.dual.pdf'
    'a.zh.mono.pdf'                       → 'a.zh.dual.pdf'
    """
    n = Path(mono_name).name
    if n.endswith(MONO_SUFFIX):
        n = n[: -len(MONO_SUFFIX)]
    else:                                    # 兜底：任意 *.pdf
        n = n[:-4] if n.lower().endswith('.pdf') else n
    # 去掉 BabelDOC 的 no_watermark 段（可能出现在中间任意位置）
    n = n.replace(NO_WATERMARK, '')
    n = n.replace('..', '.').strip('. ')
    return n + DUAL_SUFFIX


def find_translated(translated_dir, stem: str, kind: str = 'mono'):
    """在译文目录里按 stem 前缀找产物，兼容任意命名变体。

    返回 Path 或 None。多个匹配时取**最新修改**的那个（重翻后旧文件可能残留）。
    """
    suffix = MONO_SUFFIX if kind == 'mono' else DUAL_SUFFIX
    d = Path(translated_dir)
    if not d.is_dir():
        return None
    hits = [p for p in d.glob(f'{stem}*{suffix}') if p.is_file()]
    if not hits:
        return None
    hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0]


# ────────────────────────── 拼接 ──────────────────────────

def _shift_link(lk, dx: float = 0.0, dy: float = 0.0):
    """把链接矩形平移 (dx, dy)。原地改 dict 并返回。"""
    import fitz
    r = lk.get('from')
    if r is not None:
        lk['from'] = fitz.Rect(r.x0 + dx, r.y0 + dy, r.x1 + dx, r.y1 + dy)
    t = lk.get('to')
    if isinstance(t, fitz.Rect):
        lk['to'] = fitz.Rect(t.x0 + dx, t.y0 + dy, t.x1 + dx, t.y1 + dy)
    elif isinstance(t, fitz.Point):
        lk['to'] = fitz.Point(t.x + dx, t.y + dy)
    return lk


def make_bilingual(orig_pdf, mono_pdf, dual_pdf, mode: str = 'lr') -> bool:
    """把「原文 PDF」与「译文 PDF」拼成一份双语版。

    原子写出：先写 .tmp 再 os.replace，避免用户下载到生成一半的文件。
    失败只记日志不抛异常 —— 双语版是增强项，绝不能拖垮翻译主流程。
    """
    if mode not in MODES:
        logger.warning('未知 mode=%s，回退 lr', mode)
        mode = 'lr'

    orig_pdf, mono_pdf, dual_pdf = Path(orig_pdf), Path(mono_pdf), Path(dual_pdf)
    if not orig_pdf.exists():
        logger.warning('双语拼接跳过：原文不存在 %s', orig_pdf)
        return False
    if not mono_pdf.exists():
        logger.warning('双语拼接跳过：译文不存在 %s', mono_pdf)
        return False

    import time
    import fitz

    t0 = time.time()
    s1 = fitz.open(str(orig_pdf))
    s2 = fitz.open(str(mono_pdf))
    dst = fitz.open()
    try:
        n1, n2 = len(s1), len(s2)
        if n1 == 0 and n2 == 0:
            logger.warning('双语拼接跳过：两侧页数都为 0')
            return False

        ref = s1[0] if n1 else s2[0]
        page_w, page_h = ref.rect.width, ref.rect.height

        if mode == 'lr':
            dst_page = lambda: dst.new_page(width=page_w * 2, height=page_h)
        elif mode == 'tb':
            dst_page = lambda: dst.new_page(width=page_w, height=page_h * 2)
        else:                                   # alt
            dst_page = lambda: dst.new_page(width=page_w, height=page_h)

        pages = range(max(n1, n2)) if mode != 'alt' else range(max(n1, n2))

        for i in pages:
            if mode == 'alt':
                # 逐页交替：先原文页，再译文页（各自单独成页）
                for src, si in ((s1, i), (s2, i)):
                    if si < len(src):
                        pg = dst_page()
                        pg.show_pdf_page(fitz.Rect(0, 0, page_w, page_h), src, si)
                        for lk in src[si].get_links():
                            # 交替模式下内部跳转页码会失效，只保留外链 URI
                            if lk.get('uri'):
                                pg.insert_link(lk)
                continue

            pg = dst_page()

            # ① 原文：左右/上下模式下都放在 (0,0)，坐标不变
            if i < n1:
                pg.show_pdf_page(fitz.Rect(0, 0, page_w, page_h), s1, i)
                for lk in s1[i].get_links():
                    pg.insert_link(lk)

            # ② 译文：整块平移后贴到另一半
            if i < n2:
                dx, dy = (page_w, 0.0) if mode == 'lr' else (0.0, page_h)
                box = fitz.Rect(dx, dy, dx + page_w, dy + page_h)
                pg.show_pdf_page(box, s2, i)
                for lk in s2[i].get_links():
                    pg.insert_link(_shift_link(lk, dx, dy))

        dual_pdf.parent.mkdir(parents=True, exist_ok=True)
        tmp = dual_pdf.with_suffix(dual_pdf.suffix + '.tmp')
        dst.save(str(tmp), garbage=4, deflate=True)
        os.replace(str(tmp), str(dual_pdf))

        logger.info('✅ 双语版(%s): %s + %s → %s (%d 页, %.1fMB, %.1fs)',
                    mode, orig_pdf.name, mono_pdf.name, dual_pdf.name,
                    len(dst), dual_pdf.stat().st_size / 1e6, time.time() - t0)
        return True

    except Exception as e:
        logger.warning('双语拼接失败（非致命，不影响译文下载）: %s', e)
        # 清理可能的半截文件
        try:
            tmp = dual_pdf.with_suffix(dual_pdf.suffix + '.tmp')
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False
    finally:
        try:
            dst.close()
        except Exception:
            pass
        s1.close()
        s2.close()


def ensure_dual(orig_pdf, mono_pdf, dual_pdf=None, mode: str = 'lr', force: bool = False):
    """确保双语版存在。

    dual_pdf 省略时由 mono 文件名推导；已存在且非 force 则直接复用（省 IO）。
    返回 dual 的 Path，失败返回 None。
    """
    mono_pdf = Path(mono_pdf)
    if dual_pdf is None:
        dual_pdf = mono_pdf.parent / mono_to_dual_name(mono_pdf.name)
    dual_pdf = Path(dual_pdf)

    if dual_pdf.exists() and not force:
        return dual_pdf
    return dual_pdf if make_bilingual(orig_pdf, mono_pdf, dual_pdf, mode=mode) else None
