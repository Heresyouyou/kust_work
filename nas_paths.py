# -*- coding: utf-8 -*-
"""NAS 路径唯一真相（2026-10-31 新增）

为什么必须有这一层
------------------
`S:` 不是"一个盘"，而是 **Windows 登录会话 + UAC 令牌** 的私有财产：

  - `explorer.exe`（Medium 完整性）看得见 —— 所以你在资源管理器里一切正常；
  - `supervisor.py` / `web_app.py` 因为计划任务 `RunLevel=HighestAvailable`
    跑在 **High（提权）**，而 `EnableLinkedConnections` 未设置（=0）
    → **提权进程看不到 S:**；
  - 服务 / SYSTEM / 登录前启动的任务更是永远看不到。

症状极其隐蔽：`Path('S:/GeoKB/...')` **不报错**，只是 `.exists()` 与
`.is_file()` 恒为 False —— 期刊 PDF 回源静默 404、归档状态全判成「未归档」，
日志里一个异常都看不到（2026-10-31 实测：真实存在的 1130 KB PDF
经 `/api/journals/pdf/<rel>` 返回 `404 not_found`，同一时刻普通身份 shell 能直接读）。

结论
----
**业务代码一律用本模块的 UNC 常量，绝不写盘符。**
盘符只保留给人浏览，且必须以**普通身份**挂载（提权挂的只有提权进程看得见，
资源管理器反而看不见 —— 两个方向都不通用）。

可用环境变量 `POSTNEWS_NAS_SHARE` 覆盖共享根（换网段 / 换机器时用）。
"""
from __future__ import annotations

import os
from pathlib import Path

# ★ 用 IP，不用主机名。
#   本机 hosts 把 `kust.work` 硬锁到 <lan-ip>，要经 <lan-ip> 网关中转，
#   且该地址 ICMP 100% 丢包（仅 445 可达）——排障时极易误判成"NAS 挂了"。
#   <lan-ip> 才是同网段直连（PTR=COTH），内容完全一致。
_DEFAULT_SHARE = '\\\\<nas-host>\\<share>'


def _share() -> Path:
    raw = os.environ.get('POSTNEWS_NAS_SHARE', '').strip()
    return Path(raw) if raw else Path(_DEFAULT_SHARE)


# ── 共享根与常用子目录（全部是 UNC，可安全跨令牌/跨会话/跨服务使用）──
NAS_SHARE = _share()
GEOKB_ROOT = NAS_SHARE / 'GeoKB'
PAPERS_REPO = GEOKB_ROOT / 'papers_repo'
ADDITION = PAPERS_REPO / 'Addition'
TRANSLATOR_PDFS = NAS_SHARE / 'Translator_PDFs'
DAILY_POSTER = NAS_SHARE / 'Daily_Poster'
SOFTWARE_DIR = NAS_SHARE / '常用软件'

#: 说明：本模块**不提供** `S:` 版本常量 —— 盘符是令牌私有财产，
#: 任何以它为默认值的路径都会在提权进程里静默失效。要临时验证请自行用 UNC。


def as_dict() -> dict:
    """给 /health、诊断脚本用的可序列化快照。"""
    return {
        'share': str(NAS_SHARE),
        'geokb_root': str(GEOKB_ROOT),
        'papers_repo': str(PAPERS_REPO),
        'addition': str(ADDITION),
        'translator_pdfs': str(TRANSLATOR_PDFS),
        'daily_poster': str(DAILY_POSTER),
        'source': 'env:POSTNEWS_NAS_SHARE' if os.environ.get('POSTNEWS_NAS_SHARE') else 'default',
    }


if __name__ == '__main__':
    import time
    print('NAS 路径（唯一真相）')
    for k, v in as_dict().items():
        print(f'  {k:16} = {v}')
    print()
    for label, p in (('papers_repo', PAPERS_REPO), ('Addition', ADDITION),
                     ('Translator_PDFs', TRANSLATOR_PDFS), ('Daily_Poster', DAILY_POSTER)):
        t = time.time()
        try:
            ok = p.is_dir()
            n = len(list(p.iterdir())) if ok else 0
            print(f'  {label:16} is_dir={ok!s:5} 条目={n:<4} {time.time()-t:.2f}s')
        except Exception as e:
            print(f'  {label:16} FAIL {type(e).__name__}: {e}  {time.time()-t:.2f}s')
