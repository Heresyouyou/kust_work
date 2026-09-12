# -*- coding: utf-8 -*-
"""把运行时工作区的 Web 层源码同步进本仓库。

为什么用同步脚本而不是手工拷贝
------------------------------
运行时在 `D:\\PostNews\\Translate`（Flask 从那里启动，导入路径才对得齐），
本仓库只做**版本管理**。两边如果靠手工拷贝，迟早会漂移。
所以固定一份「要收哪些文件」的清单在这里，每次改完跑一遍即可。

    python tools/sync_from_workspace.py            # 同步
    python tools/sync_from_workspace.py --check    # 只比对差异，不写盘
"""

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

SRC = Path(r'D:\PostNews\Translate')
DST = Path(__file__).resolve().parent.parent      # kust_work/

# (源相对路径, 目标相对路径) —— 目录会递归拷贝，自动跳过 __pycache__ / *.pyc
ITEMS = [
    ('web_app.py',                      'web_app.py'),
    ('index.html',                      'index.html'),

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
    ('modules/palette/routes.py',       'modules/palette/routes.py'),
    ('modules/palette/palettes.json',   'modules/palette/palettes.json'),
    ('modules/palette/templates/palette.html',
                                        'modules/palette/templates/palette.html'),

    ('GEO_TRANSLATE_ARCHITECTURE.md',   'docs/ARCHITECTURE.md'),
    ('PORTAL_README.md',                'docs/PORTAL.md'),
    ('KUSTWORK_PORTAL_DESIGN.md',       'docs/PORTAL_DESIGN.md'),

    ('theme_dom_test.js',               'tests/theme_dom_test.js'),
    ('portal_journals_dom_test.js',     'tests/portal_journals_dom_test.js'),
    ('_make_theme_previews.py',         'tools/make_theme_previews.py'),
]


def _ignore(_dir, names):
    return {n for n in names if n == '__pycache__' or n.endswith(('.pyc', '.pyo'))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='只比对，不写盘')
    args = ap.parse_args()

    if not SRC.is_dir():
        print('找不到源目录：%s' % SRC)
        return 1

    copied = same = missing = 0
    for src_rel, dst_rel in ITEMS:
        s, d = SRC / src_rel, DST / dst_rel
        if not s.exists():
            print('  缺失（跳过）：%s' % src_rel)
            missing += 1
            continue
        if d.exists() and s.is_file() and filecmp.cmp(s, d, shallow=False):
            same += 1
            continue
        if args.check:
            print('  有差异：%s' % dst_rel)
            copied += 1
            continue
        d.parent.mkdir(parents=True, exist_ok=True)
        if s.is_dir():
            if d.exists():
                shutil.rmtree(d)
            shutil.copytree(s, d, ignore=_ignore)
        else:
            shutil.copy2(s, d)
        print('  → %s' % dst_rel)
        copied += 1

    verb = '有差异' if args.check else '已同步'
    print('\n%s %d 项，无变化 %d 项，源缺失 %d 项' % (verb, copied, same, missing))
    return 0


if __name__ == '__main__':
    sys.exit(main())
