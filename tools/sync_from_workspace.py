# -*- coding: utf-8 -*-
"""把运行时工作区的 Web 层源码同步进本仓库。

为什么用同步脚本而不是手工拷贝
------------------------------
运行时在 `D:\\PostNews\\Translate`（Flask 从那里启动，导入路径才对得齐），
本仓库只做**版本管理**。两边如果靠手工拷贝，迟早会漂移。
所以固定一份「要收哪些文件」的清单在这里，每次改完跑一遍即可。

    python tools/sync_from_workspace.py            # 同步
    python tools/sync_from_workspace.py --check    # 只比对差异，不写盘

★ 公开仓库脱敏
本仓库是 **public** 的，同步时会自动把内网信息替换成占位符（见 REDACTIONS）。
**工作区源文件不受影响** —— 脱敏只发生在写入本仓库这一步，所以运行时
该有的 UNC 兜底、真实路径都还在，只是不会泄漏到 GitHub。

★ 明确不收录
- `modules/share/`：含内网 NAS 的明文用户名/密码，**永不发布**
- 翻译引擎（`run_babeldoc.py` / `llama_*.py` / `babeldoc_translator.py`）：
  依赖 llama.cpp 与 7GB 模型，属翻译子系统，不属 Web 层
- 模型、日志、用户 PDF、数据文件
"""

import argparse
import re
import sys
from pathlib import Path

SRC = Path(r'D:\PostNews\Translate')
DST = Path(__file__).resolve().parent.parent      # kust_work/

# (源相对路径, 目标相对路径) —— 目录会递归拷贝，自动跳过 __pycache__ / *.pyc
ITEMS = [
    ('web_app.py',                      'web_app.py'),
    ('index.html',                      'index.html'),

    # ── Web 层自有模块（翻译服务直接 import 的功能模块）──
    ('bilingual.py',                    'bilingual.py'),        # 双语对照版拼接
    ('nas_archive.py',                  'nas_archive.py'),      # NAS 文献仓库（归档/检索/回收）

    ('portal/__init__.py',              'portal/__init__.py'),
    ('portal/registry.py',              'portal/registry.py'),
    ('portal/routes.py',                'portal/routes.py'),
    ('portal/assets.py',                'portal/assets.py'),
    ('portal/templating.py',            'portal/templating.py'),
    ('portal/templates/portal.html',    'portal/templates/portal.html'),
    ('portal/assets/theme.js',          'portal/assets/theme.js'),

    ('modules/__init__.py',             'modules/__init__.py'),
    ('modules/journals/routes.py',      'modules/journals/routes.py'),
    ('modules/journals/templates/journals.html',
                                        'modules/journals/templates/journals.html'),
    ('modules/archive/routes.py',       'modules/archive/routes.py'),
    ('modules/archive/templates/archive.html',
                                        'modules/archive/templates/archive.html'),
    ('modules/news/routes.py',          'modules/news/routes.py'),
    ('modules/news/templates/daily.html',
                                        'modules/news/templates/daily.html'),
    ('modules/news/templates/daily_archive.html',
                                        'modules/news/templates/daily_archive.html'),
    ('modules/palette/routes.py',       'modules/palette/routes.py'),
    ('modules/palette/palettes.json',   'modules/palette/palettes.json'),
    ('modules/palette/templates/palette.html',
                                        'modules/palette/templates/palette.html'),
    ('modules/queue/__init__.py',       'modules/queue/__init__.py'),
    ('modules/queue/templates/queue.html',
                                        'modules/queue/templates/queue.html'),

    # ── 运维脚本 ──
    ('tools/nas_reaper.py',             'tools/nas_reaper.py'),
    ('tools/schedule',                  'tools/schedule'),      # 本地定时回收（任务计划程序）
    ('_make_theme_previews.py',         'tools/make_theme_previews.py'),

    # ── 文档 ──
    ('GEO_TRANSLATE_ARCHITECTURE.md',   'docs/ARCHITECTURE.md'),
    ('PORTAL_README.md',                'docs/PORTAL.md'),
    ('KUSTWORK_PORTAL_DESIGN.md',       'docs/PORTAL_DESIGN.md'),
    ('CODE_REFERENCE.md',               'docs/CODE_REFERENCE.md'),
    ('requirements-lock.txt',           'requirements-lock.txt'),

    # ── 测试 ──
    ('theme_dom_test.js',               'tests/theme_dom_test.js'),
    ('portal_journals_dom_test.js',     'tests/portal_journals_dom_test.js'),
]

# 发布脱敏规则（只作用于写入本仓库的内容）
#
# ★ 规则本身不能写死真实主机名 / 共享名 / IP —— 这个脚本也会被提交到公开仓库，
#   写死就等于把内网信息又泄漏一遍。所以一律用「模式匹配」，规则里只有通配符。
REDACTIONS = [
    # 内网 NAS：\\host\share 与 //host/share 两种写法
    (r'//kust\.work/[A-Za-z0-9_\-]+', r'//<nas-host>/<share>'),
    (r'\\\\kust\.work\\[A-Za-z0-9_\-]+', r'\\\\<nas-host>\\<share>'),
    # 私网 IP（A/B/C 三类私有网段），写成通用模式，不出现真实地址
    (r'\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '<lan-ip>'),
    (r'\b192\.168\.\d{1,3}\.\d{1,3}\b', '<lan-ip>'),
    (r'\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b', '<lan-ip>'),
    # 内网账号名
    (r'/user:[A-Za-z0-9_\-]+', r'/user:<nas-user>'),
]

SKIP_DIRS = {'__pycache__', '.git', '.idea', '.vscode'}
SKIP_SUFFIX = {'.pyc', '.pyo', '.pyd', '.gguf', '.bin', '.pdf'}


def _read(p: Path) -> str:
    with open(p, 'r', encoding='utf-8', newline='') as f:   # newline='' 原样保留行尾
        return f.read()


def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, 'w', encoding='utf-8', newline='') as f:
        f.write(text)


def _redact(text: str) -> str:
    for pat, rep in REDACTIONS:
        text = re.sub(pat, rep, text)
    return text


def _iter_files(s: Path):
    if s.is_file():
        yield s
        return
    for p in sorted(s.rglob('*')):
        if not p.is_file():
            continue
        if SKIP_DIRS & set(p.parts):
            continue
        if p.suffix in SKIP_SUFFIX:
            continue
        yield p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='只比对，不写盘')
    args = ap.parse_args()

    if not SRC.is_dir():
        print('找不到源目录：%s' % SRC)
        return 1

    copied = same = missing = redacted = 0
    for src_rel, dst_rel in ITEMS:
        s, d = SRC / src_rel, DST / dst_rel
        if not s.exists():
            print('  缺失（跳过）：%s' % src_rel)
            missing += 1
            continue

        for sf in _iter_files(s):
            df = d if s.is_file() else d / sf.relative_to(s)
            try:
                src_text = _read(sf)
            except UnicodeDecodeError:
                print('  非文本（跳过）：%s' % sf.name)
                continue
            out_text = _redact(src_text)
            if out_text != src_text:
                redacted += 1

            if df.exists():
                try:
                    if _read(df) == out_text:
                        same += 1
                        continue
                except UnicodeDecodeError:
                    pass
            if args.check:
                print('  有差异：%s' % df.relative_to(DST))
                copied += 1
                continue
            _write(df, out_text)
            print('  → %s' % df.relative_to(DST))
            copied += 1

    verb = '有差异' if args.check else '已同步'
    print('\n%s %d 项，无变化 %d 项，源缺失 %d 项，脱敏 %d 项'
          % (verb, copied, same, missing, redacted))
    return 0


if __name__ == '__main__':
    sys.exit(main())
