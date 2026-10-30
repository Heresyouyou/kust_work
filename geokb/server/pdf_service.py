# -*- coding: utf-8 -*-
"""PDF 原文服务：下载 / 在线预览 / 全文文本。

路径策略：条目 frontmatter 的 sources[].path 是相对路径（如
`papers_repo/by_journal/MINERALS/2025/xxx.pdf`），这里用 PDF_ROOT 拼成绝对路径。
PDF_ROOT 优先取环境变量 GEOKB_PDF_ROOT，其次 config/paths.yaml，最后按
已知候选依次探测（NAS 优先，本地回退），这样 PDF 搬家后无需改动任何条目。
"""
import json
import os
import re
import sqlite3

from flask import Response, jsonify, request, send_file

import ingest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(BASE, "config", "paths.yaml")
TEXT_DIR = os.path.join(BASE, "state", "text")

# 公网访问源：与 agent_docs.ORIGIN / retriever.PUBLIC_ORIGIN 共用同一环境变量
ORIGIN = os.environ.get("GEOKB_PUBLIC_ORIGIN", "https://sjck.kust.work")

# 候选根：NAS 优先（盘符 → UNC），本地旧路径仅作最后回退
# 注意：服务进程（守护进程拉起）未必看得到交互用户的盘符映射，
# 因此 UNC 必须作为一等候选，不能只依赖 S:。
UNC_ROOT = "//192.168.10.1/KUST_SJCK_PUBLIC/GeoKB/papers_repo"
CANDIDATE_ROOTS = [
    os.environ.get("GEOKB_PDF_ROOT"),
    "S:/GeoKB/papers_repo",
    "S:/papers_repo",
    UNC_ROOT,
    "D:/PostNews/papers_repo",
]


def pdf_root():
    """确定 PDF 根路径。优先级：环境变量 > config/paths.yaml > 候选探测。"""
    env = os.environ.get("GEOKB_PDF_ROOT")
    if env and os.path.isdir(env):
        return env.replace("\\", "/")
    try:
        import yaml
        if os.path.exists(CONFIG):
            cfg = yaml.safe_load(open(CONFIG, encoding="utf-8").read()) or {}
            p = cfg.get("pdf_root")
            if p and os.path.isdir(p):
                return str(p).replace("\\", "/")
    except Exception:
        pass
    for c in CANDIDATE_ROOTS:
        if c and os.path.isdir(c):
            return c.replace("\\", "/")
    return UNC_ROOT


def resolve_pdf(rel):
    """相对路径 -> 绝对路径，并做路径穿越防护。"""
    if not rel:
        return None
    rel = str(rel).replace("\\", "/").lstrip("/")
    # 兼容：有些 sources.path 已带 papers_repo 前缀之外的绝对盘符
    if re.match(r"^[A-Za-z]:", rel):
        return rel if os.path.isfile(rel) else None
    if rel.startswith("papers_repo/"):
        rel = rel[len("papers_repo/"):]
    if ".." in rel.split("/"):
        return None
    root = pdf_root()
    for cand in (os.path.join(root, rel), os.path.join(root, "by_journal", rel)):
        if os.path.isfile(cand):
            return cand
    return None


def entry_pdf_paths(eid):
    """返回条目的 PDF 相对路径列表（通常 1 个）。"""
    conn = ingest.get_db()
    try:
        r = conn.execute("SELECT sources, module, path FROM entries WHERE id = ?", (eid,)).fetchone()
        if not r:
            return None, None
        try:
            srcs = json.loads(r[0] or "[]")
        except (json.JSONDecodeError, TypeError):
            srcs = []
        rels = [s.get("path") for s in srcs if isinstance(s, dict) and s.get("path")]
        return rels, r[2]
    finally:
        conn.close()


def register(app):
    @app.get("/api/entry/<eid>/pdf")
    def api_entry_pdf(eid):
        """下载 PDF 原文。?inline=1 改为浏览器内联打开。"""
        rels, _ = entry_pdf_paths(eid)
        if rels is None:
            return jsonify({"ok": False, "error": "entry_not_found",
                            "message": "条目不存在: %s" % eid}), 404
        if not rels:
            return jsonify({"ok": False, "error": "no_pdf",
                            "message": "该条目没有关联 PDF 原文"}), 404
        abs_p = resolve_pdf(rels[0])
        if not abs_p:
            return jsonify({"ok": False, "error": "pdf_missing",
                            "message": "PDF 文件不存在或已迁移: %s（当前根: %s）"
                                       % (rels[0], pdf_root())}), 404
        inline = str(request.args.get("inline", "")).lower() in ("1", "true", "yes")
        resp = send_file(abs_p, mimetype="application/pdf",
                         as_attachment=not inline,
                         download_name=os.path.basename(abs_p),
                         conditional=True)
        resp.headers["Access-Control-Expose-Headers"] = "Content-Disposition, Content-Length"
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp

    @app.get("/api/entry/<eid>/pdf/meta")
    def api_entry_pdf_meta(eid):
        """PDF 元信息：是否存在、大小、当前根路径。给 Agent 判断是否值得下载。"""
        rels, _ = entry_pdf_paths(eid)
        if rels is None:
            return jsonify({"ok": False, "error": "entry_not_found"}), 404
        out = []
        for rel in rels:
            abs_p = resolve_pdf(rel)
            out.append({
                "rel_path": rel,
                "available": bool(abs_p),
                "bytes": os.path.getsize(abs_p) if abs_p else None,
                # 完整公网 URL：Agent 可直接转述给终端用户，无需自己拼域名
                "download_url": "%s/api/entry/%s/pdf" % (ORIGIN, eid),
                "preview_url": "%s/api/entry/%s/pdf/preview" % (ORIGIN, eid),
            })
        return jsonify({"ok": True, "data": {
            "entry_id": eid, "pdf_root": pdf_root(), "files": out}})

    @app.get("/api/entry/<eid>/pdf/preview")
    def api_entry_pdf_preview(eid):
        """在线预览页：用浏览器内置 PDF 查看器内嵌显示，无需外部 CDN。"""
        rels, _ = entry_pdf_paths(eid)
        if not rels:
            return jsonify({"ok": False, "error": "no_pdf"}), 404
        abs_p = resolve_pdf(rels[0])
        if not abs_p:
            return jsonify({"ok": False, "error": "pdf_missing",
                            "message": "PDF 不存在: %s" % rels[0]}), 404
        html = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>PDF 预览 - %(name)s</title>
<style>
 html,body{margin:0;height:100%%;background:#1b1b1b;color:#eee;
   font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
 .bar{display:flex;align-items:center;gap:12px;padding:8px 14px;background:#232323;
   border-bottom:1px solid #333;font-size:13px}
 .bar a{color:#7ec6ff;text-decoration:none}
 .bar .sp{flex:1}
 iframe,object{border:0;width:100%%;height:calc(100%% - 41px);background:#525659}
</style></head><body>
<div class="bar"><b>%(name)s</b><span class="sp"></span>
<a href="/api/entry/%(eid)s/pdf">下载原文</a>
<a href="/api/entry/%(eid)s">条目详情</a></div>
<object data="/api/entry/%(eid)s/pdf?inline=1" type="application/pdf">
  <iframe src="/api/entry/%(eid)s/pdf?inline=1"></iframe>
</object>
</body></html>""" % {"name": os.path.basename(abs_p), "eid": eid}
        # 注意：mimetype 里不要带 charset，Flask 会再拼一次导致重复
        return Response(html, mimetype="text/html")

    @app.get("/api/entry/<eid>/fulltext")
    def api_entry_fulltext(eid):
        """论文全文文本（抽取缓存）。比下载 PDF 更省 token，优先用它。"""
        sha = None
        if eid.startswith("paper-"):
            sha = eid[len("paper-"):]
        elif eid.startswith("kp-"):
            sha = eid[3:].rsplit("-", 1)[0]
        if not sha:
            return jsonify({"ok": False, "error": "unsupported_entry"}), 400
        # state/text 下文件名是完整 sha1，按前缀匹配
        hit = None
        if os.path.isdir(TEXT_DIR):
            for fn in os.listdir(TEXT_DIR):
                if fn.startswith(sha) and fn.endswith(".txt"):
                    hit = os.path.join(TEXT_DIR, fn)
                    break
        if not hit:
            return jsonify({"ok": False, "error": "fulltext_missing",
                            "message": "该条目无全文文本缓存"}), 404
        limit = int(request.args.get("limit", 0))
        text = open(hit, encoding="utf-8", errors="ignore").read()
        truncated = False
        if limit and len(text) > limit:
            text = text[:limit]
            truncated = True
        return jsonify({"ok": True, "data": {
            "entry_id": eid, "chars": len(text), "truncated": truncated, "text": text}})

    @app.get("/api/pdf")
    def api_pdf_by_path():
        """按相对路径取 PDF，如 /api/pdf?path=papers_repo/by_journal/..."""
        rel = request.args.get("path") or ""
        abs_p = resolve_pdf(rel)
        if not abs_p:
            return jsonify({"ok": False, "error": "pdf_missing",
                            "message": "未找到: %s（当前根: %s）" % (rel, pdf_root())}), 404
        inline = str(request.args.get("inline", "")).lower() in ("1", "true", "yes")
        return send_file(abs_p, mimetype="application/pdf",
                         as_attachment=not inline,
                         download_name=os.path.basename(abs_p), conditional=True)

    @app.get("/api/pdf/status")
    def api_pdf_status():
        """存储状态：根路径、可达性、PDF 总数与占用。供运维/Agent 自检。"""
        root = pdf_root()
        n = 0
        size = 0
        reachable = os.path.isdir(root)
        seen = set()
        if reachable:
            for dp, _dn, fn in os.walk(root):
                for f in fn:
                    if not f.lower().endswith(".pdf"):
                        continue
                    p = os.path.join(dp, f)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    # by_journal / by_date 是同一批文件的硬链接，按 inode 去重
                    key = (st.st_dev, st.st_ino)
                    if key in seen:
                        continue
                    seen.add(key)
                    size += st.st_size
                    n += 1
        return jsonify({"ok": True, "data": {
            "pdf_root": root, "reachable": reachable,
            "pdf_files": n, "total_bytes": size,
            "total_gb": round(size / 2 ** 30, 2)}})
