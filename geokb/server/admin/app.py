# -*- coding: utf-8 -*-
"""GeoKB 本地管理端（5554）。

- 仅监听 127.0.0.1:5554，不对外暴露。
- 需要 admin token 登录。
- 提供 Markdown CRUD、索引重建、外部 feed 自更新。
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ADMIN = os.path.dirname(os.path.abspath(__file__))
# 让 admin 路由能 import 到 geokb server 的 ingest/retriever（用于扩展页）
sys.path.insert(0, os.path.join(BASE, "server"))

from flask import Flask, jsonify, request

from routes import init_admin

app = Flask(
    __name__,
    template_folder=os.path.join(ADMIN, "templates"),
    static_folder=os.path.join(ADMIN, "static"),
    static_url_path="/static",
)
app.secret_key = os.environ.get("GEOKB_ADMIN_SECRET", os.urandom(24).hex())


@app.before_request
def _local_only():
    """仅允许本地回环访问（经反向代理时按 X-Forwarded-For 第一道 IP 判断）。"""
    forwarded = request.headers.get("X-Forwarded-For", "")
    remote = request.remote_addr or ""
    # 无代理时直接看 remote；有代理时取最左段（客户端真实 IP）
    client = forwarded.split(",")[0].strip() if forwarded else remote
    if client not in ("127.0.0.1", "::1", "localhost"):
        return jsonify({"ok": False, "error": "forbidden", "message": "5554 仅允许本地访问"}), 403
    return None


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "geokb-admin", "port": 5554, "local_only": True})


init_admin(app)

if __name__ == "__main__":
    port = int(os.environ.get("GEOKB_ADMIN_PORT", "5554"))
    print(f"GeoKB Admin (local-only) serving on 127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, threaded=True)
