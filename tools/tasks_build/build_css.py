# -*- coding: utf-8 -*-
"""把复刻来的「RPG 时间轴」样式改造成 kust.work 主题变量体系。
（上游原件标题含「NPC」，本模块自 2026-09-21 起统一改称「RPG」。）

策略：
  - 所有色值 → var(--np-*) 语义变量（规则区不出现任何硬编码色值）
  - :root 给出深色默认值（/theme.js 缺失也完整可读）
  - :root[data-theme="light"] 给出浅色值（浅灰底 / 白卡片 / 金点缀）

配色对齐站点（portal/assets/theme.js + modules/triangle 的 :root）：
  品牌绿  light #2e7d4f  / dark #4a9e6a   （= 站点 --accent / --logo-bg 族）
  中性灰  light #f2f1ec…  / dark #1e1e1e…#333  （= 站点 #1f1f1f / #262626 / #2b2b2b / #2f2f2f 阶梯）
  金色    #f6a201 族 —— 奖励点缀，保持原设计
  状态色  报名=薄荷绿、逾期=玫红、阶段=蓝灰 —— 与品牌绿拉开层次
"""
import re
from pathlib import Path

SRC = Path(__file__).parent / 'extract' / 'page.css'
DST = Path(__file__).parent / 'out_css.css'

css = SRC.read_text(encoding='utf-8')

# ── 1. 先替换整段渐变（避免其中的 hex 被逐色替换） ──
GRADIENTS = [
    ('linear-gradient(135deg, #5b5bd6 0%, #7b4fd8 55%, #9a4fd8 100%)', 'var(--np-grad-hero)'),
    ('linear-gradient(135deg, #5b5bd6, #9a4fd8)', 'var(--np-grad-brand)'),
    ('linear-gradient(135deg, #2d2a3f, #4a3f7a)', 'var(--np-grad-ink)'),
    ('linear-gradient(135deg, #f6a201, #e8890c)', 'var(--np-grad-gold)'),
    ('linear-gradient(160deg, #f4f3fc, #eef0f6)', 'var(--np-grad-stage)'),
    ('linear-gradient(90deg, #5b5bd6, #12b886)', 'var(--np-grad-mix)'),
    ('linear-gradient(90deg, #5b5bd6, #9a4fd8)', 'var(--np-grad-brand90)'),
    ('linear-gradient(90deg, #d9d6ee, #b7b2e8, #d9d6ee)', 'var(--np-grad-track)'),
    ('linear-gradient(90deg, #ffd166, #ff9f43)', 'var(--np-grad-gold90)'),
]
for a, b in GRADIENTS:
    css = css.replace(a, b)

# ── 2. rgba 着色 / 阴影 ──
RGBA = [
    ('rgba(43, 32, 92, .12)', 'var(--np-sh-3)'),
    ('rgba(30, 25, 60, .5)', 'var(--np-scrim)'),
    ('rgba(30,25,60,.3)', 'var(--np-sh-6)'),
    ('rgba(43,32,92,.07)', 'var(--np-sh-1)'),
    ('rgba(43,32,92,.08)', 'var(--np-sh-2)'),
    ('rgba(43,32,92,.12)', 'var(--np-sh-3)'),
    ('rgba(43,32,92,.14)', 'var(--np-sh-4)'),
    ('rgba(43,32,92,.16)', 'var(--np-sh-5)'),
    ('rgba(91,91,214,.5)', 'var(--np-br-50)'),
    ('rgba(91,91,214,.45)', 'var(--np-br-45)'),
    ('rgba(91,91,214,.4)', 'var(--np-br-40)'),
    ('rgba(91,91,214,.35)', 'var(--np-br-35)'),
    ('rgba(91,91,214,.3)', 'var(--np-br-30)'),
    ('rgba(91,91,214,.25)', 'var(--np-br-25)'),
    ('rgba(91,91,214,.12)', 'var(--np-br-12)'),
    ('rgba(91,91,214,.08)', 'var(--np-br-08)'),
    ('rgba(255,209,102,.3)', 'var(--np-go-30)'),
    ('rgba(255,209,102,.25)', 'var(--np-go-25)'),
    ('rgba(246,162,1,.35)', 'var(--np-go-35)'),
    ('rgba(246,162,1,.3)', 'var(--np-go-30b)'),
    ('rgba(246,162,1,.12)', 'var(--np-go-12)'),
    ('rgba(230,140,10,.35)', 'var(--np-go-35b)'),
    ('rgba(18,184,134,.25)', 'var(--np-ok-25)'),
]
for a, b in RGBA:
    css = css.replace(a, b)

# ── 3. hex → 变量（#fff 按属性区分） ──
HEX = {
    '#5b5bd6': '--np-brand', '#9a4fd8': '--np-brand-2', '#7b4fd8': '--np-brand-violet',
    '#4a4ac6': '--np-brand-deep', '#4a3f7a': '--np-brand-plum',
    '#b7b2e8': '--np-brand-chip',
    '#eeecfb': '--np-brand-soft0', '#f4f3fc': '--np-brand-soft1', '#f1effb': '--np-brand-soft2',
    '#e9e7f4': '--np-brand-soft3', '#ecebf5': '--np-brand-soft4', '#eeedf7': '--np-brand-soft5',
    '#e0dcf5': '--np-brand-line1', '#ddd9ec': '--np-brand-line2', '#dcd9f0': '--np-brand-line3',
    '#d9d6ee': '--np-brand-line4', '#d8d5f0': '--np-brand-line5',
    '#2d2a3f': '--np-ink', '#4a4761': '--np-ink-2', '#6a6780': '--np-ink-3',
    '#8a86a0': '--np-ink-4', '#a09db5': '--np-ink-5', '#9a97b3': '--np-ink-6',
    '#b3b0c6': '--np-ink-7',
    '#fbfbfe': '--np-card-alt', '#f7f6fc': '--np-card-alt2', '#f5f4fa': '--np-card-alt3',
    '#f4f3fa': '--np-card-alt4', '#f1f0f7': '--np-card-alt5',
    '#eef0f6': '--np-bg',
    '#f6a201': '--np-gold', '#ff9f43': '--np-gold-2', '#e8890c': '--np-gold-3',
    '#b9720a': '--np-gold-4', '#ffd166': '--np-gold-5',
    '#fdf3dc': '--np-gold-bg', '#fffdf7': '--np-gold-bg2', '#fffcf3': '--np-gold-bg3',
    '#f2ddb0': '--np-gold-line', '#f5e8cc': '--np-gold-line2', '#f7e2b4': '--np-gold-line3',
    '#12b886': '--np-ok', '#0c8f68': '--np-ok-2', '#e6f7f1': '--np-ok-bg', '#c5ecdc': '--np-ok-line',
    '#e6336b': '--np-rose', '#d6336c': '--np-rose-2', '#fdeaf0': '--np-rose-bg',
    '#fff5f7': '--np-rose-bg2', '#fbdde6': '--np-rose-line',
    '#e0f5f2': '--np-teal-bg',
}


def repl_decl(m):
    prop, val = m.group(1), m.group(2)
    p = prop.lower()
    for c in set(re.findall(r'#[0-9a-fA-F]{3,8}', val)):
        cl = c.lower()
        if cl == '#fff':
            name = '--np-on-brand' if p == 'color' else '--np-card'
        else:
            name = HEX.get(cl)
        if name:
            val = val.replace(c, 'var(%s)' % name)
    return '%s:%s;' % (prop, val)


css = re.sub(r'([-a-zA-Z]+)\s*:([^;{}]*#[0-9a-fA-F]{3,8}[^;{}]*);', repl_decl, css)

leftover = sorted(set(re.findall(r'#[0-9a-fA-F]{3,8}', css)))
print('剩余未映射色值:', leftover)

# ── 4. 主题变量块 ──
#
# 品牌绿取站点主色：浅色 #2e7d4f（theme.js 的 --accent），深色 #4a9e6a（triangle 的 --accent）。
# 中性面取站点深灰阶梯（#1f1f1f / #262626 / #2b2b2b / #2f2f2f）与浅色暖白（#f7f6f3 系）。
DARK = {
    '--np-bg': '#1e1e1e', '--np-card': '#2a2a2a',
    '--np-card-alt': '#2e2e2e', '--np-card-alt2': '#2d2d2d', '--np-card-alt3': '#2e2e2e',
    '--np-card-alt4': '#2e2e2e', '--np-card-alt5': '#333333',
    '--np-ink': '#e8e4df', '--np-ink-2': '#c8c4bd', '--np-ink-3': '#a5a19a',
    '--np-ink-4': '#8a867f', '--np-ink-5': '#7a766f', '--np-ink-6': '#7a766f',
    '--np-ink-7': '#6f6b64', '--np-on-brand': '#ffffff',
    '--np-brand': '#4a9e6a', '--np-brand-2': '#63bb87', '--np-brand-violet': '#56ad78',
    '--np-brand-deep': '#3f8d5e', '--np-brand-plum': '#35513f', '--np-brand-chip': '#2f6b4c',
    '--np-brand-soft0': '#22392d', '--np-brand-soft1': '#1f3529', '--np-brand-soft2': '#22392d',
    '--np-brand-soft3': '#21372c', '--np-brand-soft4': '#21372c', '--np-brand-soft5': '#20352a',
    '--np-brand-line1': '#2f4a3c', '--np-brand-line2': '#2d4839', '--np-brand-line3': '#2f4a3c',
    '--np-brand-line4': '#2b4536', '--np-brand-line5': '#2c4738',
    '--np-gold': '#f6a201', '--np-gold-2': '#ffa94d', '--np-gold-3': '#f0a53c',
    '--np-gold-4': '#e0a53c', '--np-gold-5': '#ffd166',
    '--np-gold-bg': '#3a2f18', '--np-gold-bg2': '#322a16', '--np-gold-bg3': '#322a16',
    '--np-gold-line': '#5a4a22', '--np-gold-line2': '#4d3f1e', '--np-gold-line3': '#5a4a22',
    '--np-ok': '#2fd3a4', '--np-ok-2': '#4fe0b0', '--np-ok-bg': '#17322b', '--np-ok-line': '#24503f',
    '--np-rose': '#ff5c8a', '--np-rose-2': '#ff7aa0', '--np-rose-bg': '#3a1c28',
    '--np-rose-bg2': '#331a24', '--np-rose-line': '#55293a',
    '--np-teal-bg': '#1c2a33',
    '--np-sh-1': 'rgba(0,0,0,.45)', '--np-sh-2': 'rgba(0,0,0,.5)',
    '--np-sh-3': 'rgba(0,0,0,.55)', '--np-sh-4': 'rgba(0,0,0,.6)',
    '--np-sh-5': 'rgba(0,0,0,.6)', '--np-sh-6': 'rgba(0,0,0,.7)',
    '--np-scrim': 'rgba(8,12,9,.72)',
    '--np-br-50': 'rgba(74,158,106,.6)', '--np-br-45': 'rgba(74,158,106,.55)',
    '--np-br-40': 'rgba(74,158,106,.5)', '--np-br-35': 'rgba(74,158,106,.45)',
    '--np-br-30': 'rgba(74,158,106,.4)', '--np-br-25': 'rgba(74,158,106,.35)',
    '--np-br-12': 'rgba(74,158,106,.2)', '--np-br-08': 'rgba(74,158,106,.16)',
    '--np-go-30': 'rgba(255,209,102,.35)', '--np-go-25': 'rgba(255,209,102,.3)',
    '--np-go-35': 'rgba(246,162,1,.45)', '--np-go-30b': 'rgba(246,162,1,.4)',
    '--np-go-12': 'rgba(246,162,1,.18)', '--np-go-35b': 'rgba(230,140,10,.45)',
    '--np-ok-25': 'rgba(47,211,164,.28)',
    # 渐变（深色下略提亮，保持绿金气质）
    '--np-grad-hero': 'linear-gradient(135deg, #2f7d54 0%, #3d9463 55%, #4aa96f 100%)',
    '--np-grad-brand': 'linear-gradient(135deg, #357f57, #4aa96f)',
    '--np-grad-ink': 'linear-gradient(135deg, #232823, #2f4a3c)',
    '--np-grad-gold': 'linear-gradient(135deg, #f6a201, #e8890c)',
    '--np-grad-stage': 'linear-gradient(160deg, #2e2e2e, #1e1e1e)',
    '--np-grad-mix': 'linear-gradient(90deg, #4a9e6a, #2fd3a4)',
    '--np-grad-brand90': 'linear-gradient(90deg, #4a9e6a, #63bb87)',
    '--np-grad-track': 'linear-gradient(90deg, #2b4536, #2f6b4c, #2b4536)',
    '--np-grad-gold90': 'linear-gradient(90deg, #ffd166, #ff9f43)',
    # 门户顶栏共用的通用变量（与站内其它页面同名；值即 site 的深色默认）
    '--surface': '#1f1f1f', '--border': '#3a3a3a', '--text': '#e8e4df',
    '--muted': '#8a867f', '--logo-bg': '#2d6e46', '--logo-fg': '#e2f3e8',
}

LIGHT = {
    '--np-bg': '#f2f1ec', '--np-card': '#ffffff',
    '--np-card-alt': '#fdfcf9', '--np-card-alt2': '#f9f8f4', '--np-card-alt3': '#f6f5f1',
    '--np-card-alt4': '#f5f4f0', '--np-card-alt5': '#f2f1ed',
    '--np-ink': '#2a2926', '--np-ink-2': '#4b4a45', '--np-ink-3': '#6e6a63',
    '--np-ink-4': '#8b8781', '--np-ink-5': '#a5a199', '--np-ink-6': '#9d998f',
    '--np-ink-7': '#b5b1a8', '--np-on-brand': '#ffffff',
    '--np-brand': '#2e7d4f', '--np-brand-2': '#46a86f', '--np-brand-violet': '#3f9a63',
    '--np-brand-deep': '#256b43', '--np-brand-plum': '#35513f', '--np-brand-chip': '#a8d5bd',
    '--np-brand-soft0': '#e9f5ee', '--np-brand-soft1': '#eef7f2', '--np-brand-soft2': '#eaf5ef',
    '--np-brand-soft3': '#e3f0e8', '--np-brand-soft4': '#e6f2ea', '--np-brand-soft5': '#e9f4ee',
    '--np-brand-line1': '#cfe6da', '--np-brand-line2': '#c9e2d4', '--np-brand-line3': '#cbe4d6',
    '--np-brand-line4': '#c6e0d2', '--np-brand-line5': '#c8e1d3',
    '--np-gold': '#f6a201', '--np-gold-2': '#ff9f43', '--np-gold-3': '#e8890c',
    '--np-gold-4': '#b9720a', '--np-gold-5': '#ffd166',
    '--np-gold-bg': '#fdf3dc', '--np-gold-bg2': '#fffdf7', '--np-gold-bg3': '#fffcf3',
    '--np-gold-line': '#f2ddb0', '--np-gold-line2': '#f5e8cc', '--np-gold-line3': '#f7e2b4',
    '--np-ok': '#12b886', '--np-ok-2': '#0c8f68', '--np-ok-bg': '#e6f7f1', '--np-ok-line': '#c5ecdc',
    '--np-rose': '#e6336b', '--np-rose-2': '#d6336c', '--np-rose-bg': '#fdeaf0',
    '--np-rose-bg2': '#fff5f7', '--np-rose-line': '#fbdde6',
    '--np-teal-bg': '#e4eef7',
    '--np-sh-1': 'rgba(28,52,38,.07)', '--np-sh-2': 'rgba(28,52,38,.08)',
    '--np-sh-3': 'rgba(28,52,38,.12)', '--np-sh-4': 'rgba(28,52,38,.14)',
    '--np-sh-5': 'rgba(28,52,38,.16)', '--np-sh-6': 'rgba(20,38,28,.3)',
    '--np-scrim': 'rgba(20,38,28,.5)',
    '--np-br-50': 'rgba(46,125,79,.5)', '--np-br-45': 'rgba(46,125,79,.45)',
    '--np-br-40': 'rgba(46,125,79,.4)', '--np-br-35': 'rgba(46,125,79,.35)',
    '--np-br-30': 'rgba(46,125,79,.3)', '--np-br-25': 'rgba(46,125,79,.25)',
    '--np-br-12': 'rgba(46,125,79,.12)', '--np-br-08': 'rgba(46,125,79,.08)',
    '--np-go-30': 'rgba(255,209,102,.3)', '--np-go-25': 'rgba(255,209,102,.25)',
    '--np-go-35': 'rgba(246,162,1,.35)', '--np-go-30b': 'rgba(246,162,1,.3)',
    '--np-go-12': 'rgba(246,162,1,.12)', '--np-go-35b': 'rgba(230,140,10,.35)',
    '--np-ok-25': 'rgba(18,184,134,.25)',
    '--np-grad-hero': 'linear-gradient(135deg, #2e7d4f 0%, #38935f 55%, #46a86f 100%)',
    '--np-grad-brand': 'linear-gradient(135deg, #2e7d4f, #46a86f)',
    '--np-grad-ink': 'linear-gradient(135deg, #2a2926, #35513f)',
    '--np-grad-gold': 'linear-gradient(135deg, #f6a201, #e8890c)',
    '--np-grad-stage': 'linear-gradient(160deg, #f7f6f2, #f2f1ec)',
    '--np-grad-mix': 'linear-gradient(90deg, #2e7d4f, #12b886)',
    '--np-grad-brand90': 'linear-gradient(90deg, #2e7d4f, #46a86f)',
    '--np-grad-track': 'linear-gradient(90deg, #cfe6da, #a8d5bd, #cfe6da)',
    '--np-grad-gold90': 'linear-gradient(90deg, #ffd166, #ff9f43)',
    '--surface': '#ffffff', '--border': '#e2dfd8', '--text': '#2a2926',
    '--muted': '#6e6a63', '--logo-bg': '#2e7d4f', '--logo-fg': '#ffffff',
}

assert set(DARK) == set(LIGHT), set(DARK) ^ set(LIGHT)

head = ['/* ============================================================================',
        ' * 任务打卡 · 看板样式（由「大学 RPG 任务轴」复刻并接入 kust.work 主题体系）',
        ' *',
        ' * 深色为 :root 默认值（/theme.js 缺失也完整可读）；浅色由 theme.js 注入的',
        ' * :root[data-theme="light"] 触发。',
        ' * 配色对齐站点：品牌绿 #2e7d4f(浅) / #4a9e6a(深) = 门户 --accent 族，',
        ' * 中性面走站点深灰阶梯与浅色暖白，金色只做奖励点缀。',
        ' * 全部走 --np-* 变量，不写死色值 —— 深色模式不残留浅色块。',
        ' * ========================================================================== */',
        ':root {']
for k, v in DARK.items():
    head.append('  %s: %s;' % (k, v))
head.append('}')
head.append(':root[data-theme="light"] {')
for k, v in LIGHT.items():
    head.append('  %s: %s;' % (k, v))
head.append('}')
head.append('')

DST.write_text('\n'.join(head) + css, encoding='utf-8')
print('写入', DST, len((DST.read_text(encoding="utf-8"))), 'chars')
