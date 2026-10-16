# -*- coding: utf-8 -*-
"""把运行时工作区的 Web 层源码同步进本仓库。

为什么用同步脚本而不是手工拷贝
------------------------------
运行时在 `D:\\PostNews\\Translate`（Flask 从那里启动，导入路径才对得齐），
本仓库只做**版本管理**。两边如果靠手工拷贝，迟早会漂移。
所以固定一份「要收哪些文件」的清单在这里，每次改完跑一遍即可。

    python tools/sync_from_workspace.py            # 同步
    python tools/sync_from_workspace.py --check    # 只比对差异，不写盘

★ 收录边界（本仓库只收「文本源码 + 文档」）
------------------------------------------
**收录**：门户框架、11 个业务模块的 Python / 模板 / 前端源码、运维脚本、文档、测试。
**不收录**（三类，均按 `SKIP_*` 规则自动跳过并在末尾审计输出）：

| 类别 | 例子 | 为什么 |
|---|---|---|
| 凭据 | `modules/share/`（含内网明文用户名/口令） | 仓库是 public，**必须永不入仓** |
| 运行时数据 | `*/uploads/`、`*/results/`、`*/data/`、`modules/stitch/.workbuddy/` | 上传原件、拼接结果、打卡记录、开发产物 |
| 生成物 / 第三方 / 二进制 | `modules/story/docs/`（汇报页图）、`reader/assets/vendor/`（pdf.js）、各种 png/exe/ttf | 体积大、可再生；本仓库定位是**源码版本管理** |

> 因此克隆本仓库**不能直接跑起来**：第三方 vendor（pdf.js）与二进制资产需另行获取。
> 这是有意的取舍 —— 换来一个干净、可 diff、可长期维护的源码基线。

★ 公开仓库脱敏 + 发布守卫
本仓库是 **public** 的，同步时做两道防线：

1. **脱敏 REDACTIONS**：内网主机名 / 共享名 / 私网 IP / 内网账号 / **公网服务器地址** /
   **明文口令字面量** → 占位符。只作用于写入本仓库的内容，**工作区源文件不受影响**。
2. **发布守卫 GUARD**：脱敏后再扫一遍，命中高危形态（PEM 私钥块、疑似明文口令、服务器地址）
   就**拒绝写入该文件并报警** —— 防的是「我没预料到的新写法」。
"""

import argparse
import ipaddress
import re
import sys
from pathlib import Path

SRC = Path(r'D:\PostNews\Translate')
DST = Path(__file__).resolve().parent.parent      # kust_work/

# (源相对路径, 目标相对路径) —— **目录会递归拷贝**，按 SKIP_* 规则过滤
# 目录级条目是刻意的：以后模块里新增的文件会自动被收录，不必回来改清单。
ITEMS = [
    # ── Web 层入口与自有模块 ──
    ('web_app.py',                      'web_app.py'),
    ('index.html',                      'index.html'),
    ('bilingual.py',                    'bilingual.py'),      # 双语对照版拼接
    ('nas_archive.py',                  'nas_archive.py'),    # NAS 文献仓库（归档/检索/回收）

    # ── 门户框架 ──
    ('portal',                          'portal'),

    # ── 业务模块（★ 不含 modules/share —— 含明文凭据，永不发布）──
    ('modules/__init__.py',             'modules/__init__.py'),
    ('modules/archive',                 'modules/archive'),
    ('modules/journals',                'modules/journals'),
    ('modules/news',                   'modules/news'),
    ('modules/notes',                  'modules/notes'),       # 笔记整理
    ('modules/palette',                 'modules/palette'),
    ('modules/queue',                   'modules/queue'),      # 统一任务队列（展示页）
    ('modules/reader',                  'modules/reader'),     # 自研 canvas PDF 阅读器
    ('modules/stitch',                  'modules/stitch'),     # 显微图像拼合
    ('modules/story',                   'modules/story'),      # 汇报设计说明书
    ('modules/tasks',                   'modules/tasks'),      # 任务打卡
    ('modules/triangle',                'modules/triangle'),

    # ── 运维脚本 ──
    ('tools/nas_reaper.py',             'tools/nas_reaper.py'),
    ('tools/restart_web.py',            'tools/restart_web.py'),
    ('tools/supervisor.py',             'tools/supervisor.py'),
    ('tools/reader_dev_server.py',      'tools/reader_dev_server.py'),
    ('tools/schedule',                  'tools/schedule'),      # 本地定时回收（任务计划程序）
    ('tools/tasks_build',               'tools/tasks_build'),   # tasks 前端手写件的构建源
    ('tools/npg_check',                 'tools/npg_check'),
    ('_make_theme_previews.py',         'tools/make_theme_previews.py'),

    # ── 文档 ──
    ('GEO_TRANSLATE_ARCHITECTURE.md',   'docs/ARCHITECTURE.md'),
    ('PORTAL_README.md',                'docs/PORTAL.md'),
    ('KUSTWORK_PORTAL_DESIGN.md',       'docs/PORTAL_DESIGN.md'),
    ('CODE_REFERENCE.md',               'docs/CODE_REFERENCE.md'),
    ('WEB_INTEGRATION_CHANGES.md',      'docs/WEB_INTEGRATION_CHANGES.md'),
    ('requirements-lock.txt',           'requirements-lock.txt'),

    # ── 测试 ──
    # ★ 必须放在**仓库根**：这几个脚本按 `__dirname/portal/...`、`__dirname/modules/...`
    #   解析被测文件（`news_daily_test.py` 同理由 `sys.path` 定位同级模块）。
    #   搬到 tests/ 会让相对路径全部失效 —— 保持与工作区**同构**才能开箱即跑。
    ('theme_dom_test.js',               'theme_dom_test.js'),
    ('portal_journals_dom_test.js',     'portal_journals_dom_test.js'),
    ('queue_dom_test.js',               'queue_dom_test.js'),
    ('news_daily_test.py',              'news_daily_test.py'),
]

# 发布脱敏规则（只作用于写入本仓库的内容，**顺序敏感**）
#
# ★ 规则本身不能写死真实主机名 / 共享名 / IP —— 这个脚本也会被提交到公开仓库，
#   写死就等于把内网信息又泄漏一遍。所以一律用「模式匹配」，规则里只有通配符。
REDACTIONS = [
    # ① 内网 NAS 主机名形式：\\host\share\... 与 //host/share/...
    #    主机名 + **整条路径尾巴**一起抹掉（只抹主机名会残留 \共享名\目录\文件）
    (r'(?P<lead>\\{2})kust\.work(?P<sep>\\[^\s\'"<>|]+)',
     lambda m: '%s<nas-host>\\<share>' % m.group('lead')),
    (r'(?P<lead>//)kust\.work(?P<sep>/[^\s\'"<>|]+)',
     lambda m: '%s<nas-host>/<share>' % m.group('lead')),
    # ② ★ 公网服务器地址 → <server-ip>。用 `ipaddress` 判定，**只动真正的公网地址**：
    #    回环 / 私网 / 未指定 / 组播 / 保留段（含 203.0.113.x 这类文档用 TEST-NET）原样保留。
    #    （2026-09-29 实测：这条是为了 `portal/routes.py` 文档串里的公网 IP，
    #      以及 `tools/supervisor.py` 里 `root@<公网IP>` 形式的默认值。）
    (r'\b(?:\d{1,3}\.){3}\d{1,3}\b', lambda m: _public_ip(m.group(0))),
    # ③ 私网 IP（A/B/C 三类私有网段），写成通用模式，不出现真实地址
    (r'\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '<lan-ip>'),
    (r'\b192\.168\.\d{1,3}\.\d{1,3}\b', '<lan-ip>'),
    (r'\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b', '<lan-ip>'),
    # ④ ★ 只脱敏 IP 是不够的：紧随其后的**共享名与目录名**同样会泄漏内网结构
    #    （且源码里单反斜杠 `\192.168...` 与双反斜杠 `'\\\\192.168...'` 两种写法都有，
    #     原规则只覆盖双反斜杠 ⇒ 漏掉单反斜杠那批）。这里按「IP + 路径尾巴」整体抹掉。
    #    用**函数替换**而非字符串，绕开 re.sub 对反斜杠的二次转义歧义。
    (r'<lan-ip>(?P<sep>\\{1,2})[^\s\'"<>|]+',
     lambda m: '<nas-host>%s<share>' % m.group('sep')),
    (r'<lan-ip>(?P<sep>/{1,2})[^\s\'"<>|]+',
     lambda m: '<nas-host>%s<share>' % m.group('sep')),
    # ⑤ 内网账号名
    (r'/user:[A-Za-z0-9_\-]+', r'/user:<nas-user>'),
    # ⑥ ★ 明文口令 / 令牌 / 账号 字面量 → 占位符。两种出现形式都覆盖：
    #      a) 环境变量默认值：environ.get("XXX_PASS", "真口令")
    #      b) 直接赋值：      XXX_PASS = "真口令"
    #    按**变量名**判定（而不是猜值的形状），避免误伤普通字符串。
    #    （账号名也一并处理：`KUST_NAS_USER = "admin"` 泄漏的是账号，不只是口令。）
    (r'(?i)((?:get|getenv)\(\s*["\']?[A-Z0-9_]*(?:PASS|PASSWD|PASSWORD|SECRET|TOKEN|APIKEY|API_KEY|USER|ACCOUNT|CRED)[A-Z0-9_]*["\']?\s*,\s*["\'])([^"\'\n]+)(["\'])',
     r'\1<redacted-secret>\3'),
    (r'(?i)([A-Z0-9_]*(?:PASS|PASSWD|PASSWORD|SECRET|TOKEN|APIKEY|API_KEY|USERNAME|ACCOUNT|CRED)[A-Z0-9_]*\s*[:=]\s*["\'])([^"\'\n]{4,})(["\'])',
     r'\1<redacted-secret>\3'),
    # ⑦ 服务器面板路径（泄漏服务器目录结构与所用面板）
    (r'/www/server/[A-Za-z0-9_\-/.]*', '<server-panel-path>'),
]

# ── 发布守卫（兜底）──
# 脱敏之后再扫一遍；命中即**拒绝写入该文件**并报警。
# 防的是「没预料到的新写法」——宁可少收一个文件，也不能泄漏。
GUARD = [
    (r'BEGIN [A-Z ]*PRIVATE KEY', 'PEM 私钥块'),
    (r'(?i)[A-Z0-9_]*(?:PASS|PASSWD|PASSWORD|SECRET|TOKEN|APIKEY|API_KEY)[A-Z0-9_]*\s*[:=]\s*["\'][^"\'\n]{4,}["\']',
     '疑似明文口令/令牌'),
    (r'root@(?:\d{1,3}\.){3}\d{1,3}', '服务器 SSH 地址'),
]


def _public_ip(ip: str) -> str:
    """只把**真正的公网地址**换成 <server-ip>；其余（回环/私网/保留/文档段）原样返回。"""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if (a.is_private or a.is_loopback or a.is_unspecified
            or a.is_multicast or a.is_reserved or a.is_link_local):
        return ip
    return '<server-ip>'

# ── 过滤规则 ──
# 目录名（任一层命中即整棵子树跳过）
SKIP_DIRS = {
    '__pycache__', '.git', '.idea', '.vscode', 'node_modules',
    '.workbuddy',              # 开发产物（memory / 验证脚本 / 中间产物）
    'uploads', 'results',      # 运行时：上传原件 / 拼接结果
    'data',                    # 运行时用户数据（如 tasks/data/store.json）
    'vendor',                  # 第三方库（reader 的 pdf.js，需另行获取）
    'docs',                    # 模块内的生成物（story 的页图 / 提词稿）
}
# 文件名前缀（工作区里的历史备份目录）
SKIP_NAME_PREFIX = ('_story_backup_',)
# 后缀：运行时产物 + 二进制资产（本仓库只收文本）
SKIP_SUFFIX = {
    '.pyc', '.pyo', '.pyd', '.gguf', '.bin', '.pdf', '.pkl', '.npz', '.log',
    # 二进制资产
    '.png', '.jpg', '.jpeg', '.gif', '.ico', '.webp', '.bmp', '.tif', '.tiff',
    '.exe', '.dll', '.so', '.dylib',
    '.ttf', '.otf', '.woff', '.woff2', '.pfb', '.bcmap', '.wasm',
    '.zip', '.7z', '.tar', '.gz',
    '.mp4', '.mov', '.pptx', '.docx', '.xlsx',
}


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


def _guard_hits(text: str):
    """脱敏后的兜底检查：返回命中的高危形态描述列表（空 = 通过）。"""
    hits = []
    for pat, label in GUARD:
        for m in re.finditer(pat, text):
            snippet = m.group(0).replace('\n', ' ')[:60]
            hits.append('%s ← %s' % (label, snippet))
    return hits


def _skip_reason(p: Path, root: Path):
    """返回跳过原因；None 表示收录。原因字符串用于末尾审计输出。"""
    rel_parts = p.relative_to(root).parts
    for part in rel_parts[:-1]:                      # 目录层
        if part in SKIP_DIRS:
            return '目录 %s/' % part
        if part.startswith(SKIP_NAME_PREFIX):
            return '目录 %s/' % part
    name = p.name
    if name.startswith(SKIP_NAME_PREFIX):
        return '备份目录'
    if '.bak' in name:                               # 兼容 xx.py.bak_20260913_224816
        return '备份文件(.bak)'
    if p.suffix.lower() in SKIP_SUFFIX:
        return '非文本(%s)' % p.suffix.lower()
    return None


def _iter_files(s: Path):
    """产出待处理的文件；同时累加被跳过的目录用于审计。"""
    if s.is_file():
        yield s
        return
    for p in sorted(s.rglob('*')):
        if not p.is_file():
            continue
        yield p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='只比对，不写盘')
    ap.add_argument('--show-skip', action='store_true', help='逐条列出被跳过的文件')
    args = ap.parse_args()

    if not SRC.is_dir():
        print('找不到源目录：%s' % SRC)
        return 1

    copied = same = missing = redacted = skipped = nontext = 0
    skip_reasons = {}
    blocked = []

    for src_rel, dst_rel in ITEMS:
        s, d = SRC / src_rel, DST / dst_rel
        if not s.exists():
            print('  缺失（跳过）：%s' % src_rel)
            missing += 1
            continue

        for sf in _iter_files(s):
            reason = _skip_reason(sf, s) if s.is_dir() else None
            if reason is not None:
                skipped += 1
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                if args.show_skip:
                    print('  跳过[%s] %s' % (reason, sf.relative_to(SRC)))
                continue

            df = d if s.is_file() else d / sf.relative_to(s)
            try:
                src_text = _read(sf)
            except UnicodeDecodeError:
                nontext += 1
                skip_reasons['非文本(解码失败)'] = skip_reasons.get('非文本(解码失败)', 0) + 1
                print('  非文本（跳过）：%s' % sf.name)
                continue
            out_text = _redact(src_text)
            if out_text != src_text:
                redacted += 1

            # ── 发布守卫：脱敏后仍命中高危形态 → 拒绝写入 ──
            hits = _guard_hits(out_text)
            if hits:
                blocked.append(str(df.relative_to(DST)))
                print('  🚫 守卫拒绝写入 %s' % df.relative_to(DST))
                for h in hits[:4]:
                    print('       %s' % h)
                continue

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
    print('跳过 %d 项（非文本 %d）' % (skipped, nontext))
    for reason, n in sorted(skip_reasons.items(), key=lambda kv: -kv[1]):
        print('    - %-18s %d' % (reason, n))
    if skipped and not args.show_skip:
        print('  （想逐条核对跳过清单：加 --show-skip）')
    if blocked:
        print('\n🚫 发布守卫拦截 %d 个文件（**未写入**，请先修源文件）：' % len(blocked))
        for b in blocked:
            print('    - %s' % b)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
