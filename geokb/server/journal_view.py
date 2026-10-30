# -*- coding: utf-8 -*-
"""GeoKB /journal 子页面（2026-10-17）

在 5556（sjck.kust.work）上挂一个**与 open.kust.work/journal 一比一复刻**的
移动端友好页面，并与门户侧**同步更新**。

「同步更新」的实现方式（关键）
---------------------------
不是复制门户那份 HTML，而是三方共用：

    ① 取数：  D:/PostNews/journal_feed.py        ← 唯一取数真相
    ② 模板：  .../journals/templates/journal_page.html  ← 唯一模板真相
    ③ 端点：  D:/PostNews/journal_page.py 的 CFG_GEOKB ← 只声明差异

所以「一比一」是**结构上保证**的：两端渲染同一份模板、取同一份数据，
模板里没有任何硬编码路径，差异全部走服务端注入的 CFG。
改动布局/交互只需改模板一处，两端同时生效。

为什么不在这里提供 PDF（2026-10-28 用户决定）
--------------------------------------------
sjck.kust.work 是公网入口，且**没有身份闸门**；read/download 若在本机
（5556）直出，等于给所有人开了一条绕过 open.kust.work 统一身份认证的
下载通道。所以两端**统一跳门户**：

    「在线阅读」→ https://open.kust.work/PDFreader?file=...
    「下载 PDF」→ https://open.kust.work/api/journals/pdf/<rel>（原文）
                   https://open.kust.work/geokb/pdf/<eid>/mono|dual（译文）

端点差异仍只写在 journal_page.CFG_GEOKB 里（模板零硬编码）。
本模块原先新增过 `/api/journal/pdf/<rel>` 镜像路由，已随该决定**删除**：
页面不再使用它，留着就是一条无闸门的直下通道。

路由清单（本模块注册）
----------------------
    GET  /journal                     页面（移动端优先；无「回到工作台」）
    GET  /api/journal/list            与门户 /api/journals/list 同一载荷
    GET  /journal/theme.js            主题引擎（与门户同一份文件）
    GET  /journal/geokb_translate.js  一键翻译按钮脚本（与门户同一份文件）

写操作（补抓 PDF / 建条目 / 增量索引）**不在**本模块提供：
那是 5555 的 POST /api/journals/fetch-pdf 的职责，5556 是公网只读服务，
不暴露任何会写 NAS / 写索引的接口。
"""
import os
import sys

from flask import Response, jsonify

# PostNews 根：共享取数层 / 共享渲染层 / 静态资源都在那边
_POSTNEWS = os.environ.get("POSTNEWS_DIR", "D:/PostNews")
if _POSTNEWS not in sys.path:
    sys.path.insert(0, _POSTNEWS)

import journal_feed      # noqa: E402  共享取数层（唯一真相）
import journal_page      # noqa: E402  共享渲染层（唯一真相）

DATA_DIR = journal_page.DATA_DIR


def register(app):
    # ── 页面 ──────────────────────────────────────────────────────
    @app.get("/journal")
    def journal_page_view():
        """期刊列表页。移动端优先；按用户要求不提供「回到工作台」入口。

        渲染走共享模板 + CFG_GEOKB，因此与 open.kust.work/journal
        内容一致，仅端点与返回入口不同。
        """
        return Response(journal_page.render_html(journal_page.CFG_GEOKB),
                        mimetype="text/html")

    # ── 数据 ──────────────────────────────────────────────────────
    @app.get("/api/journal/list")
    def api_journal_list():
        """与门户 /api/journals/list **逐字段同一载荷**（同一 build_feed）。"""
        return jsonify(journal_feed.build_feed(DATA_DIR))

    # 原 `/api/journal/pdf/<rel>` 镜像路由已于 2026-10-28 删除：
    # 页面改为跳 open.kust.work（有统一身份认证闸门），本地再留一条
    # 直出 NAS 原文的路由 = 给所有人留了一条绕过闸门的下载通道。
    # 需要 PDF 的合法调用方走门户 /api/journals/pdf/<rel>（受 CAS 保护）。

    # ── 路径别名：让门户那份 geokb_translate.js 原样可用 ────────────
    # 一键翻译脚本 geokb_translate.js 是**同一份文件**（两端不 fork），它内部
    # 按门户约定写死了 /geokb/api/entry/<eid>/translate|translation。
    # 而 5556 自己的路由是 /api/entry/<eid>/translate|translation。
    # 与其复制修改 JS（那就必然漂移），不如在 5556 补上这两个别名 ——
    # 于是同一份脚本在两端行为完全一致，别名只在 5556 内部生效。
    import translate_service

    @app.post("/geokb/api/entry/<eid>/translate")
    def alias_translate_submit(eid):
        return translate_service.submit_translation_endpoint(eid)

    @app.get("/geokb/api/entry/<eid>/translation")
    def alias_translation_status(eid):
        return translate_service.translation_status_endpoint(eid)

    # ── 静态资源（与门户同一份文件，避免主题/翻译行为两端漂移）────
    @app.get("/journal/theme.js")
    def journal_theme_js():
        p = os.path.join(_POSTNEWS, "Translate", "portal", "assets", "theme.js")
        if not os.path.isfile(p):
            return jsonify({"ok": False, "error": "theme_js_missing"}), 404
        return Response(_read(p), mimetype="application/javascript")

    @app.get("/journal/geokb_translate.js")
    def journal_translate_js():
        p = os.path.join(_POSTNEWS, "Translate", "modules", "geokb_public",
                         "static", "js", "geokb_translate.js")
        if not os.path.isfile(p):
            return jsonify({"ok": False, "error": "translate_js_missing"}), 404
        return Response(_read(p), mimetype="application/javascript")

    app.logger.info("journal page registered: /journal | /api/journal/list")
    return app


_js_cache = {}


def _read(path):
    """静态资源按 mtime 缓存（与门户 templating.load_html 同策略）。"""
    mt = os.path.getmtime(path)
    hit = _js_cache.get(path)
    if hit and hit[0] == mt:
        return hit[1]
    with open(path, "r", encoding="utf-8") as f:
        body = f.read()
    _js_cache[path] = (mt, body)
    return body
