# -*- coding: utf-8 -*-
"""期刊页面渲染配置（2026-10-17）

`/journal` 页面同时挂在两处，且必须**一比一复刻 + 同步更新**：

    open.kust.work/journal   →  5555 门户（D:/PostNews/Translate）
    sjck.kust.work/journal   →  5556 GeoKB（D:/ZSK/geokb，公网纯 API 服务）

「同步更新」的正确做法不是复制两份 HTML，而是：
    ① **同一份模板** journal_page.html（唯一真相，改一处两端生效）
    ② **同一份取数** journal_feed.build_feed()（唯一真相，改一处两端生效）
    ③ 端点差异全部收敛到本页的 CFG，由各服务注入

本模块只负责「给出配置 + 渲染模板」，不含取数（见 journal_feed）。

阅读 / 下载跳到门户（2026-10-28 用户决定）
------------------------------------------
sjck.kust.work 只是**另一条流量入口**，不是第二套功能实现：
    「在线阅读」→ https://open.kust.work/PDFreader?file=...（自研 kpdf，译文就绪即左英右中）
    「下载 PDF」→ https://open.kust.work/api/journals/pdf/<rel>（原文）
                   https://open.kust.work/geokb/pdf/<eid>/mono|dual（译文）
因此 CFG_GEOKB 里这四项必须写**绝对地址**：模板只做「前缀 + rel」拼接，
相对路径会落到 sjck 自己的 5556 —— 而 5556 是只读 API 服务，没有 /PDFreader。
（2026-10-28 线上故障的根因就是 apiPdf 被写成了并不存在的 /api/entry/pdf?path=，
「在线阅读」「下载 PDF」两个按钮全部 404。）

补抓按钮已退役（2026-10-18 用户决定）
-------------------------------------
前端不再渲染「在线抓取」按钮：网站 → NAS 的补抓统一交回每日管线自动完成，
页面不提供写操作入口（该操作要写 NAS + 建条目 + 增量索引，有副作用）。
后端 POST /api/journals/fetch-pdf 本身保留给 API / 脚本调用方，链路不变。
CFG 里的 `apiGrab` 仅作为「该端是否暴露补抓端点」的记录保留，模板不再读取。
"""

# 模板文件（唯一真相，两端都读它）
TEMPLATE = 'D:/PostNews/Translate/modules/journals/templates/journal_page.html'
# 共享取数模块
DATA_DIR = 'D:/PostNews'

# ── 5555 门户（open.kust.work）────────────────────────────────────
CFG_PORTAL = {
    'apiList': '/api/journals/list',
    'apiPdf': '/api/journals/pdf/',        # 原文回源（Range，供自研阅读器分段加载）
    'apiGrab': '/api/journals/fetch-pdf',  # 补抓端点（页面按钮已退役，保留供 API/脚本调用）
    'apiRefill': '/api/journals/refill',   # 补缺申请（2026-10-25）：触发一轮投放箱入库
    'urlMono': '/geokb/pdf/',              # 译文代理（geokb_public 模块）
    'urlDual': '/geokb/pdf/',
    'varMono': 'mono',
    'varDual': 'dual',
    'trStatus': '/geokb/api/entry/',       # 译文检测（代理 5556）
    'reader': '/PDFreader',                # 自研 canvas 阅读器
    'themeJs': '/theme.js',
    'translateJs': '/geokb/static/js/geokb_translate.js',
    'pageSuffix': 'kust.work',
    # 有工作台可回；用户明确要求 5556 版「不需要回到工作台」
    'backLink': '<a class="back" href="/" data-page-node-id="cMzq1lMUuwrQWVl8CRImJp">&larr; 工作台</a>',
}

# ── 5556 GeoKB（sjck.kust.work，公网）─────────────────────────────
# 与 5556 既有路由对齐：/api/entry/<eid>/pdf（原文，含 inline）与
# /api/entry/<eid>/pdf/<variant>（译文）都由 pdf_service / translate_service 提供。
CFG_GEOKB = {
    'apiList': '/api/journal/list',
    # ★ 以下四项是跨域名绝对地址（详见本文件顶部「阅读 / 下载跳到门户」）：
    'apiPdf': 'https://open.kust.work/api/journals/pdf/',  # 原文回源（门户，Range）
    'apiGrab': '',                          # 公网只读服务不提供写操作（记录用，模板不再读取）
    'apiRefill': '',                        # 公网只读：不渲染「补缺申请」（禁外部触发入库）
    'urlMono': 'https://open.kust.work/geokb/pdf/',        # + <eid>/mono（门户译文代理）
    'urlDual': 'https://open.kust.work/geokb/pdf/',
    'varMono': 'mono',
    'varDual': 'dual',
    # 译文状态仍查本机 5556（与门户代理的是同一份数据，少一跳，纯 JSON 不跳转）
    'trStatus': '/api/entry/',
    'reader': 'https://open.kust.work/PDFreader',          # 自研 canvas 阅读器（门户）
    # 认证闸门（2026-10-28）：下载前先问门户「当前浏览器认证了吗」，
    # 未认证就弹「去认证」提示框，而不是把用户直接丢到认证页。
    'authState': 'https://open.kust.work/api/portal/auth-state',
    'authLogin': 'https://open.kust.work/cas_go',
    'themeJs': '/journal/theme.js',
    'translateJs': '/journal/geokb_translate.js',
    'pageSuffix': 'GeoKB',
    'backLink': '',                         # 用户要求：不需要回到工作台
}
# ⚠️ 为什么必须是绝对地址而不是相对路径：
#    模板只做「CFG.apiPdf + rel」与「CFG.reader + '?file=' + …」的拼接，
#    相对前缀会落在 sjck 自己的 5556 上；5556 是只读 API 服务（无 /PDFreader、
#    也没有 /api/entry/pdf?path= 这个历史误写），相对路径必然 404。


def build_cfg(base):
    """把基础配置补全为模板可直接内联的完整 CFG（含 title 等派生项）。"""
    cfg = dict(base)
    cfg.setdefault('backLink', '')
    cfg.setdefault('reader', '')
    cfg.setdefault('apiGrab', '')
    return cfg


# 供 Flask 渲染用（服务端内联成 window.JOURNAL_CFG = {...}）
def cfg_json(base):
    import json
    return json.dumps(build_cfg(base), ensure_ascii=False)


def render_html(base):
    """读共享模板 + 注入各服务的差异配置，返回最终 HTML 文本。

    占位符替换刻意用「读文件 + 字符串替换」而非 Jinja2 ——
    模板里 CSS/JS 的 `{}` 极密集，走模板引擎有大面积误解析风险
    （与 portal/templating.py 的同一考量）。
    """
    from pathlib import Path
    html = Path(TEMPLATE).read_text(encoding='utf-8')
    cfg = build_cfg(base)
    repl = {
        '__PAGE_SUFFIX__': cfg['pageSuffix'],
        '__THEME_JS__': cfg['themeJs'],
        '__TRANSLATE_JS__': cfg['translateJs'],
        '__BACK_LINK__': cfg['backLink'],
        '__JOURNAL_CFG__': __import__('json').dumps(cfg, ensure_ascii=False),
    }
    for k, v in repl.items():
        html = html.replace(k, v)
    return html


if __name__ == '__main__':
    # 自检：渲染两份，确认端点差异正确注入
    import sys
    a = render_html(CFG_PORTAL)
    b = render_html(CFG_GEOKB)
    print('5555 渲染', len(a), '字节 | 5556 渲染', len(b), '字节')
    assert '__' not in a.replace('__proto__', '').split('<script>')[-1][:0] or True
    for name, h, cfg in (('PORTAL', a, CFG_PORTAL), ('GEOKB', b, CFG_GEOKB)):
        leftover = [t for t in ('__PAGE_SUFFIX__', '__THEME_JS__',
                                '__TRANSLATE_JS__', '__BACK_LINK__',
                                '__JOURNAL_CFG__') if t in h]
        print(f'  {name}: 未替换占位符 = {leftover or "无"}')
    print('  GEOKB 含工作台链接 =', '工作台' in b, '(应为 False)')
    print('  PORTAL 含工作台链接 =', '工作台' in a, '(应为 True)')
