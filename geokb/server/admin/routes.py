# -*- coding: utf-8 -*-
"""GeoKB 本地管理端路由（5554）。

仅监听 127.0.0.1，需要 admin token 登录。
负责：Markdown CRUD、索引重建、外部 feed 自更新。
"""
import json
import os
import re
import sqlite3
import subprocess
import threading
from datetime import datetime

from flask import Blueprint, redirect, render_template, request, session, url_for

GEOKB_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GEOKB_DB = os.path.join(GEOKB_BASE, "index", "geokb.db")
GEOKB_CONTENT = os.path.join(GEOKB_BASE, "content")
GEOKB_TOKEN_FILE = os.path.join(GEOKB_BASE, "config", "admin_token.txt")
GEOKB_CHANGELOG = os.path.join(GEOKB_BASE, "changelog.jsonl")
GEOKB_VENV_PYTHON = os.path.join(GEOKB_BASE, "venv", "Scripts", "python.exe")
GEOKB_REBUILD_SCRIPT = os.path.join(GEOKB_BASE, "scripts", "rebuild_index.py")
GEOKB_SELF_UPDATE_SCRIPT = os.path.join(GEOKB_BASE, "scripts", "ingest_external_feeds.py")
GEOKB_LOGS = os.path.join(GEOKB_BASE, "logs")
os.makedirs(GEOKB_LOGS, exist_ok=True)

admin_bp = Blueprint(
    "admin",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static",
)


def _load_token():
    """读取 GeoKB 管理 token。"""
    if os.path.exists(GEOKB_TOKEN_FILE):
        with open(GEOKB_TOKEN_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("TOKEN="):
                    return line.split("=", 1)[1].strip()
    return None


def _admin_required():
    """未登录则返回重定向响应。"""
    if not session.get("geokb_admin"):
        return redirect(url_for("admin.admin_login"))
    return None


def _get_conn():
    """仅连接 SQLite，不加载 sqlite-vec 扩展；只读/写普通表。"""
    return sqlite3.connect(GEOKB_DB, timeout=10)


def _parse_frontmatter(path):
    """极简 frontmatter 解析：返回 dict + body。"""
    if not os.path.exists(path):
        return None, ""
    text = open(path, encoding="utf-8").read()
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return None, text
    fm = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            try:
                v = json.loads(v)
            except Exception:
                pass
        fm[k] = v
    return fm, m.group(2).strip()


def _write_entry(eid, title, module, tags, summary, confidence, status, sources, body):
    """写 Markdown 源文件；目录不存在则创建。"""
    mod_dir = os.path.join(GEOKB_CONTENT, module)
    os.makedirs(mod_dir, exist_ok=True)
    path = os.path.join(mod_dir, f"{eid}.md")
    now = datetime.now().strftime("%Y-%m-%d")
    tags = tags or []
    sources = sources or []
    front = f"""---
id: {eid}
title: {title}
module: {module}
tags: {json.dumps(tags, ensure_ascii=False)}
summary: {summary}
confidence: {confidence}
status: {status}
updated: {now}
sources: {json.dumps(sources, ensure_ascii=False)}
---

{body}
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(front)
    return path


def _log_action(eid, action):
    """记录变更日志。"""
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "eid": eid,
        "action": action,
        "agent": "geokb-admin-5554",
    }
    with open(GEOKB_CHANGELOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _call_rebuild(eid, action):
    """写日志并触发 GeoKB 索引重建（异步子进程，不阻塞响应）。"""
    _log_action(eid, action)
    if not os.path.exists(GEOKB_VENV_PYTHON) or not os.path.exists(GEOKB_REBUILD_SCRIPT):
        return
    try:
        subprocess.Popen(
            [GEOKB_VENV_PYTHON, GEOKB_REBUILD_SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        pass


# ─── 登录 / 登出 ───────────────────────────────────────────────
@admin_bp.get("/admin")
def admin_login():
    if session.get("geokb_admin"):
        return redirect(url_for("admin.admin_dashboard"))
    return render_template("geokb_admin/login.html")


@admin_bp.post("/admin/login")
def admin_do_login():
    token = (request.form.get("token") or "").strip()
    if token and token == _load_token():
        session["geokb_admin"] = True
        return redirect(url_for("admin.admin_dashboard"))
    return render_template("geokb_admin/login.html", error="Token 无效"), 401


@admin_bp.get("/admin/logout")
def admin_logout():
    session.pop("geokb_admin", None)
    return redirect(url_for("admin.admin_login"))


# ─── Dashboard ─────────────────────────────────────────────────
@admin_bp.get("/admin/dashboard")
def admin_dashboard():
    redir = _admin_required()
    if redir:
        return redir
    conn = _get_conn()
    try:
        stats = {
            "total": conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0],
            "active": conn.execute("SELECT COUNT(*) FROM entries WHERE status='active'").fetchone()[0],
            "deprecated": conn.execute("SELECT COUNT(*) FROM entries WHERE status='deprecated'").fetchone()[0],
            "deleted": conn.execute("SELECT COUNT(*) FROM entries WHERE status='deleted'").fetchone()[0],
        }
        recent = conn.execute(
            "SELECT id, title, module, status, updated FROM entries ORDER BY updated DESC LIMIT 10"
        ).fetchall()
        recent = [dict(zip(("id", "title", "module", "status", "updated"), r)) for r in recent]
        changelog = []
        if os.path.exists(GEOKB_CHANGELOG):
            for ln in open(GEOKB_CHANGELOG, encoding="utf-8"):
                if ln.strip():
                    try:
                        changelog.append(json.loads(ln))
                    except Exception:
                        pass
                    if len(changelog) >= 20:
                        break
        mod_dist = conn.execute(
            "SELECT module, COUNT(*) n FROM entries WHERE status='active' GROUP BY module ORDER BY n DESC"
        ).fetchall()
        return render_template(
            "geokb_admin/dashboard.html",
            stats=stats,
            recent=recent,
            changelog=changelog,
            mod_dist=mod_dist,
            api_url="http://127.0.0.1:5556",
        )
    finally:
        conn.close()


# ─── 条目列表 / 搜索 / 过滤 ─────────────────────────────────────
@admin_bp.get("/admin/entries")
def admin_entries():
    redir = _admin_required()
    if redir:
        return redir
    q = (request.args.get("q") or "").strip().lower()
    module = (request.args.get("module") or "").strip()
    status = (request.args.get("status") or "").strip()
    conn = _get_conn()
    try:
        sql = (
            "SELECT id, title, module, confidence, status, updated FROM entries "
            "WHERE 1=1"
        )
        args = []
        if module:
            sql += " AND module = ?"
            args.append(module)
        if status:
            sql += " AND status = ?"
            args.append(status)
        sql += " ORDER BY updated DESC"
        rows = conn.execute(sql, args).fetchall()
        rows = [dict(zip(("id", "title", "module", "confidence", "status", "updated"), r)) for r in rows]
        if q:
            rows = [r for r in rows if q in r["id"].lower() or q in r["title"].lower() or q in r["module"].lower()]
        modules = [m[0] for m in conn.execute(
            "SELECT DISTINCT module FROM entries ORDER BY module"
        ).fetchall()]
        return render_template("geokb_admin/entries.html", rows=rows, modules=modules,
                               q=q, module=module, status=status)
    finally:
        conn.close()


# ─── 编辑 / 新建 ───────────────────────────────────────────────
@admin_bp.get("/admin/entry/new")
def admin_entry_new():
    redir = _admin_required()
    if redir:
        return redir
    return render_template("geokb_admin/edit.html", entry=None)


@admin_bp.get("/admin/entry/<eid>/edit")
def admin_entry_edit(eid):
    redir = _admin_required()
    if redir:
        return redir
    conn = _get_conn()
    try:
        r = conn.execute(
            "SELECT id, title, module, tags, summary, confidence, status, updated, sources, path, body "
            "FROM entries WHERE id = ?",
            (eid,),
        ).fetchone()
        if not r:
            return render_template("geokb_admin/error.html", message=f"条目 {eid} 不存在"), 404
        keys = ("id", "title", "module", "tags", "summary", "confidence", "status", "updated", "sources", "path", "body_markdown")
        d = dict(zip(keys, r))
        for k in ("tags", "sources"):
            try:
                d[k] = json.loads(d[k] or "[]")
            except (json.JSONDecodeError, TypeError):
                d[k] = []
        return render_template("geokb_admin/edit.html", entry=d)
    finally:
        conn.close()


@admin_bp.post("/admin/entry/save")
def admin_entry_save():
    redir = _admin_required()
    if redir:
        return redir
    try:
        eid = (request.form.get("id") or "").strip()
        title = (request.form.get("title") or "").strip()
        module = (request.form.get("module") or "").strip()
        body = (request.form.get("body") or "").strip()
        if not eid or not title or not module or not body:
            return render_template("geokb_admin/error.html", message="id / title / module / body 不能为空"), 400
        if not re.match(r"^[a-z0-9_-]+$", module):
            return render_template("geokb_admin/error.html", message="模块 slug 只能包含小写字母、数字、-、_"), 400

        tags = [t.strip() for t in (request.form.get("tags") or "").split(",") if t.strip()]
        summary = (request.form.get("summary") or "").strip()
        confidence = (request.form.get("confidence") or "C").strip().upper()[:1]
        status = (request.form.get("status") or "active").strip()
        sources_raw = (request.form.get("sources") or "").strip()
        sources = []
        if sources_raw:
            try:
                sources = json.loads(sources_raw)
                if not isinstance(sources, list):
                    sources = []
            except json.JSONDecodeError:
                sources = [{"name": s.strip()} for s in sources_raw.split("\n") if s.strip()]

        path = os.path.join(GEOKB_CONTENT, module, f"{eid}.md")
        is_new = not os.path.exists(path)
        _write_entry(eid, title, module, tags, summary, confidence, status, sources, body)
        _call_rebuild(eid, "create" if is_new else "save")
        # 保存后跳到 5555 公共详情页
        return redirect(f"http://127.0.0.1:5555/geokb/entry/{eid}")
    except Exception as e:
        return render_template("geokb_admin/error.html", message=f"保存失败: {e}"), 500


@admin_bp.post("/admin/entry/<eid>/delete")
def admin_entry_delete(eid):
    redir = _admin_required()
    if redir:
        return redir
    conn = _get_conn()
    try:
        r = conn.execute("SELECT path FROM entries WHERE id = ?", (eid,)).fetchone()
        if not r:
            return render_template("geokb_admin/error.html", message=f"条目 {eid} 不存在"), 404
        path = r[0]
        text = open(path, encoding="utf-8").read()
        if re.search(r"^status:\s*\S+", text, re.M):
            text = re.sub(r"^status:\s*\S+", "status: deleted", text, flags=re.M)
        else:
            text = text.replace("---\n", "---\nstatus: deleted\n", 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        _call_rebuild(eid, "delete")
        return redirect(url_for("admin.admin_entries"))
    except Exception as e:
        return render_template("geokb_admin/error.html", message=f"删除失败: {e}"), 500
    finally:
        conn.close()


@admin_bp.post("/admin/entry/<eid>/restore")
def admin_entry_restore(eid):
    redir = _admin_required()
    if redir:
        return redir
    conn = _get_conn()
    try:
        r = conn.execute("SELECT path FROM entries WHERE id = ?", (eid,)).fetchone()
        if not r:
            return render_template("geokb_admin/error.html", message=f"条目 {eid} 不存在"), 404
        path = r[0]
        text = open(path, encoding="utf-8").read()
        text = re.sub(r"^status:\s*deleted", "status: active", text, flags=re.M)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        _call_rebuild(eid, "restore")
        return redirect(url_for("admin.admin_entries"))
    except Exception as e:
        return render_template("geokb_admin/error.html", message=f"恢复失败: {e}"), 500
    finally:
        conn.close()


# ─── 索引重建 ──────────────────────────────────────────────────
@admin_bp.post("/admin/rebuild")
def admin_rebuild():
    redir = _admin_required()
    if redir:
        return redir
    _call_rebuild("*", "manual")
    return redirect(url_for("admin.admin_dashboard"))


# ─── 自更新（外部 feed 摄入）────────────────────────────────────
@admin_bp.get("/admin/self-update")
def admin_self_update_page():
    redir = _admin_required()
    if redir:
        return redir
    return render_template("geokb_admin/error.html", message="请使用 POST /admin/self-update 触发"), 405


@admin_bp.post("/admin/self-update")
def admin_self_update_run():
    """触发 PostNews → GeoKB 自更新管线。"""
    redir = _admin_required()
    if redir:
        return redir
    if not os.path.exists(GEOKB_VENV_PYTHON) or not os.path.exists(GEOKB_SELF_UPDATE_SCRIPT):
        return render_template("geokb_admin/error.html", message="自更新脚本未找到"), 500

    def _run():
        log_path = os.path.join(GEOKB_LOGS, f"self_update_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
        with open(log_path, "w", encoding="utf-8") as log:
            log.write(f"# self-update started at {datetime.now().isoformat()}\n")
            try:
                proc = subprocess.Popen(
                    [GEOKB_VENV_PYTHON, GEOKB_SELF_UPDATE_SCRIPT],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                for line in proc.stdout:
                    log.write(line)
                    log.flush()
                proc.wait()
                log.write(f"# exit code: {proc.returncode}\n")
            except Exception as e:
                log.write(f"# exception: {e}\n")
        _log_action("*", f"self-update-finished:{log_path}")

    threading.Thread(target=_run, daemon=True).start()
    return redirect(url_for("admin.admin_dashboard"))


def init_admin(app):
    """在 5554 Flask app 注册 GeoKB 管理蓝图。"""
    app.register_blueprint(admin_bp)
    print("[模块] GeoKB 本地管理端已注册 → /admin/*")
