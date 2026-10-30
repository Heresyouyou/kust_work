# -*- coding: utf-8 -*-
"""论文一键翻译服务（5556 侧编排层）。

定位：GeoKB 只读 API 之上薄薄一层「提交翻译 / 查询状态 / 供译文文件」。
实际翻译由 5555 PostNews 翻译队列执行（/api/upload 入队、/api/status/<job_id>
查状态，与 queue 页面同一套逻辑）；本模块负责：

  1. 任务登记  state/translation_tasks.json（key = 规范化 rel_path，
     同一篇 PDF 的 paper 条目与它的 kpoints 天然收敛到同一任务）
  2. 后端检测  NAS 同目录是否已有 <stem>.zh.mono.pdf / .zh.dual.pdf
     （目录列表带 60s TTL 缓存，不拖慢 /api/search 响应）
  3. 完成回搬  5555 队列 done → 把译文从 5555 translated/ 复制进 NAS
     原文同目录，命名 <stem>.zh.mono.pdf 与 <stem>.zh.dual.pdf（两者默认都产出，
     下载时由用户自选版本）
  4. 后台对账  守护线程每 15s 巡检 active 任务；5556 重启后从状态文件恢复

约定（与用户确认过）：
  - 译文命名沿用翻译模块的 .zh.mono.pdf；双语对照版 .zh.dual.pdf 默认一并
    生成归档（无需用户勾选），下载论文时自选版本
  - 有译文 / 有进行中任务 → 前端按钮置灰；失败 → 恢复可点并带原因
  - 任务状态与 queue 页同源：能拿到 5555 的 job 状态就视为不可点击
"""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from flask import jsonify, request, send_file

import pdf_service

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(BASE, "state")
TASKS_PATH = os.path.join(STATE_DIR, "translation_tasks.json")

TRANSLATE_API = "http://127.0.0.1:5555"     # PostNews 翻译队列（本机回环）
UPLOAD_TIMEOUT = 180                        # 大 PDF 上传（最大 ~100MB）给足
STATUS_TIMEOUT = 10
FILE_TIMEOUT = 300                          # 译文回搬（大文件，NAS 写入）

ACTIVE = ("queued", "translating", "copying")
TERMINAL = ("done", "failed", "cancelled")

RECONCILE_INTERVAL = 15.0                   # 后台巡检间隔（秒）

_lock = threading.RLock()
_tasks_cache = {"data": None, "at": 0.0}
_TASKS_TTL = 1.0

# ── 目录列表缓存（NAS listdir 慢，带 TTL）────────────────────────
_dir_cache = {}
_DIR_TTL = 60.0

# ── 本机回环绝不开系统代理（memory 铁律：HTTP_PROXY 会让回环请求变成
#    绝对 URI → 404；5555 的 BabelDOC 就是这么静默返回原文的）──────
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def log(msg):
    print("[translate_service] %s" % msg, flush=True)


# ══════════════════════════ 任务状态存取 ══════════════════════

def _norm_rel(rel):
    """任务 key：去掉 papers_repo/ 前缀、统一分隔符、小写。"""
    if not rel:
        return ""
    r = str(rel).replace("\\", "/").lstrip("/")
    if r.lower().startswith("papers_repo/"):
        r = r[len("papers_repo/"):]
    return r.lower()


def _load_tasks():
    with _lock:
        if _tasks_cache["data"] is not None and \
                time.time() - _tasks_cache["at"] < _TASKS_TTL:
            return _tasks_cache["data"]
        try:
            with open(TASKS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        _tasks_cache["data"] = data
        _tasks_cache["at"] = time.time()
        return data


def _save_tasks(data):
    with _lock:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = TASKS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, TASKS_PATH)
        _tasks_cache["data"] = data
        _tasks_cache["at"] = time.time()


# ══════════════════════════ NAS 译文检测 ══════════════════════

def _list_dir(d):
    """目录文件名集合（带 TTL 缓存；不存在/不可达返回 None）。"""
    key = str(d)
    now = time.time()
    hit = _dir_cache.get(key)
    if hit and now - hit[0] < _DIR_TTL:
        return hit[1]
    try:
        names = set(os.listdir(d))
    except Exception:
        names = None
    _dir_cache[key] = (now, names)
    return names


def _nas_paths(rel):
    """rel → (原文 abs, mono abs, dual abs)。原文不存在返回 (None,None,None)。"""
    orig = pdf_service.resolve_pdf(rel)
    if not orig:
        return None, None, None
    d = os.path.dirname(orig)
    stem = Path(os.path.basename(orig)).stem
    mono = os.path.join(d, stem + ".zh.mono.pdf")
    dual = os.path.join(d, stem + ".zh.dual.pdf")
    return orig, mono, dual


def translation_files(rel):
    """NAS 同目录检测：{has_mono, has_dual, mono_path, dual_path}。"""
    orig, mono, dual = _nas_paths(rel)
    if not orig:
        return {"has_mono": False, "has_dual": False,
                "mono_path": None, "dual_path": None}
    names = _list_dir(os.path.dirname(orig))
    has_mono = (os.path.basename(mono) in names) if names is not None \
        else os.path.exists(mono)
    has_dual = (os.path.basename(dual) in names) if names is not None \
        else os.path.exists(dual)
    return {"has_mono": bool(has_mono), "has_dual": bool(has_dual),
            "mono_path": mono if has_mono else None,
            "dual_path": dual if has_dual else None}


def entry_pdf_rel(eid):
    """条目的首个 PDF 相对路径；没有返回 None。papers 与 kpoints 通用。"""
    rels, _ = pdf_service.entry_pdf_paths(eid)
    if not rels:
        return None
    return rels[0]


def rel_of_hit(hit):
    """从检索 hit 直接取 PDF rel（hits 已带 sources，免查库）。"""
    for s in (hit.get("sources") or []):
        if isinstance(s, dict) and s.get("path"):
            return s["path"]
    return None


def translation_info(eid_or_rel, is_rel=False):
    """完整检测：文件 + 任务状态。给 API 响应用。"""
    rel = eid_or_rel if is_rel else entry_pdf_rel(eid_or_rel)
    if not rel:
        return None
    files = translation_files(rel)
    t = _load_tasks().get(_norm_rel(rel))
    job = None
    if t and t.get("status") in ACTIVE:
        job = {"status": t["status"], "job_id": t.get("job_id"),
               "progress": t.get("progress", 0), "message": t.get("message", "")}
    elif t and t.get("status") in ("failed", "cancelled") and not files["has_mono"]:
        job = {"status": t["status"], "error": t.get("error")}
    out = {"has_mono": files["has_mono"], "has_dual": files["has_dual"],
           "job": job, "rel_path": rel}
    return out


# ══════════════════════════ 5555 通信 ══════════════════════

def _post_upload(filename, data):
    """multipart 提交到 5555 /api/upload。filename 用 NAS 原文 basename，
    让缓存键与产物命名天然对齐。返回 {job_id,...} 或 {'cache_hit':True,...}。"""
    boundary = "----GeoKBT%s" % uuid.uuid4().hex
    head = (
        "--%s\r\n"
        "Content-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
        "Content-Type: application/pdf\r\n\r\n" % (boundary, filename)
    ).encode("utf-8")
    tail = ("\r\n--%s--\r\n" % boundary).encode("ascii")
    body = head + data + tail
    req = urllib.request.Request(TRANSLATE_API + "/api/upload", data=body,
                                 method="POST")
    req.add_header("Content-Type",
                   "multipart/form-data; boundary=%s" % boundary)
    with _opener.open(req, timeout=UPLOAD_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_status(job_id):
    """查 5555 任务状态。返回 (status_dict|None, reachable)。
    reachable=False 表示 5555 不可达（不是 404）——调用方应稍后重试。"""
    try:
        with _opener.open("%s/api/status/%s" % (TRANSLATE_API, job_id),
                          timeout=STATUS_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8")), True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, True            # 任务不存在（5555 重启丢内存）
        return None, False
    except Exception:
        return None, False


def _download(job_field_url, dest):
    """从 5555 拉译文（/translated/...）写 NAS 同目录。.part + 原子替换。

    任何失败路径都清理 .part 残留（半截文件会被下次检测误判成已有译文）。

    ⚠️ job_field_url 的文件名带论文标题（中文常见，如
    /translated/2026-10-17_郯庐断裂带….zh.mono.pdf），urllib 只收 ASCII，
    裸拼会 UnicodeEncodeError → 回搬永远失败、任务卡在 copying。
    先按 UTF-8 百分号编码（% 进 safe，不会二次编码已有序列）。
    """
    tmp = dest + ".part"
    try:
        url = urllib.parse.quote(TRANSLATE_API + job_field_url,
                                 safe=":/?&=%#")
        with _opener.open(url, timeout=FILE_TIMEOUT) as r:
            data = r.read()
        if not data or data[:5] != b"%PDF-":
            raise RuntimeError("译文内容不是 PDF（%d 字节）" % len(data or b""))
        with open(tmp, "wb") as f:
            f.write(data)
        if os.path.getsize(tmp) != len(data):
            raise RuntimeError("写入大小不一致")
        os.replace(tmp, dest)
        return len(data)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass
        raise


def _cache_probe(name):
    """POST 5555 /api/check：按文件名查缓存译文 URL。
    返回 {mono_url, dual_url}（没有的字段为 None）；不可用返回 None。"""
    try:
        req = urllib.request.Request(
            TRANSLATE_API + "/api/check",
            data=json.dumps({"name": name}).encode("utf-8"), method="POST")
        req.add_header("Content-Type", "application/json")
        with _opener.open(req, timeout=15) as r:
            d = json.loads(r.read().decode("utf-8"))
        if d.get("cache_hit"):
            return {"mono_url": d.get("translated_url"),
                    "dual_url": d.get("dual_url")}
    except Exception as e:
        log("cache_probe %s err: %s" % (name, e))
    return None


# ══════════════════════════ 提交 / 对账 ══════════════════════

def submit_translation(eid, dual=False):
    """提交一键翻译。返回 (result, http_status)。"""
    rel = entry_pdf_rel(eid)
    if not rel:
        return {"ok": False, "error": "no_pdf",
                "message": "该条目没有关联 PDF 原文"}, 400
    orig = pdf_service.resolve_pdf(rel)
    if not orig:
        return {"ok": False, "error": "pdf_missing",
                "message": "PDF 原文在 NAS 上不存在: %s" % rel}, 404

    files = translation_files(rel)
    if files["has_mono"]:
        # 已有中文译文。唯一例外：勾了双语对照版且 NAS 上还没有 .zh.dual.pdf
        # → 允许「补归档双语版」（不进翻译队列，从 5555 缓存回捞）。
        if not (dual and not files["has_dual"]):
            return {"ok": False, "error": "already_translated",
                    "message": "该论文已有中文译文",
                    "translation": translation_info(rel, is_rel=True)}, 409

    key = _norm_rel(rel)
    tasks = _load_tasks()
    t = tasks.get(key)
    if t and t.get("status") in ACTIVE:
        return {"ok": False, "error": "in_progress",
                "message": "该论文正在翻译中",
                "translation": translation_info(rel, is_rel=True)}, 409

    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    if files["has_mono"]:
        # ── 补归档双语版路径：不进队列，直接建对账任务回捞 dual ──
        task = {"job_id": None, "status": "copying", "dual": True,
                "eid": eid, "name": os.path.basename(orig), "rel": rel,
                "mono_url": None, "dual_url": None, "only_dual": True,
                "progress": 100, "message": "正在归档双语对照版",
                "created_at": now, "updated_at": now, "error": None}
        tasks[key] = task
        _save_tasks(tasks)
        # 不同步回搬（dual 文件可能很大）→ 后台对账线程 15s 内接手
        out = dict(task)
        out["ok"] = True
        out["translation"] = translation_info(rel, is_rel=True)
        return out, 200

    # 读取 NAS 原文并提交 5555 队列
    try:
        with open(orig, "rb") as f:
            data = f.read()
    except Exception as e:
        return {"ok": False, "error": "read_failed",
                "message": "读取 NAS 原文失败: %s" % e}, 500
    try:
        res = _post_upload(os.path.basename(orig), data)
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
            msg = body.get("error") or str(e.code)
        except Exception:
            msg = "HTTP %s" % e.code
        return {"ok": False, "error": "upload_rejected",
                "message": "翻译队列拒绝: %s" % msg}, 502
    except Exception as e:
        return {"ok": False, "error": "upload_failed",
                "message": "翻译队列不可用: %s" % e}, 502

    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    if res.get("cache_hit"):
        # 5555 已有该文献译文（缓存命中，立即 done）
        task = {"job_id": None, "status": "copying", "dual": bool(dual),
                "eid": eid, "name": os.path.basename(orig), "rel": rel,
                "mono_url": res.get("mono_url"), "dual_url": res.get("dual_url"),
                "progress": 100, "message": "译文已存在，正在归档到 NAS",
                "created_at": now, "updated_at": now, "error": None}
    else:
        task = {"job_id": res.get("job_id"), "status": "queued",
                "dual": bool(dual), "eid": eid, "name": os.path.basename(orig),
                "rel": rel, "mono_url": None, "dual_url": None,
                "progress": 0, "message": "已进入翻译队列",
                "created_at": now, "updated_at": now, "error": None}
    tasks[key] = task
    _save_tasks(tasks)
    log("submit %s → job=%s dual=%s (%s)" % (key, task["job_id"], dual, eid))

    if task.get("job_id"):
        # 正常入队：同步刷一次状态（纯查询，10s 超时内），让响应带上实时进度
        _reconcile_one(key)
    # 缓存命中 / 补归档双语：回搬涉及大文件复制，交给后台对账线程（≤15s 延迟），
    # 绝不阻塞 HTTP 请求
    out = dict(task)
    out["ok"] = True
    out["translation"] = translation_info(rel, is_rel=True)
    return out, 200


def poll_translation(eid):
    """实时查询（前端轮询用）：先触发一次对账，再返回检测结果。

    ⚠️ 轮询只允许做「状态刷新」（查 5555 /api/status），复制回搬一律由后台
    对账线程执行 —— 大文件复制可能持续数十秒，若放在请求路径上会拖死轮询。
    """
    rel = entry_pdf_rel(eid)
    if not rel:
        return {"ok": False, "error": "no_pdf"}, 400
    key = _norm_rel(rel)
    _reconcile_one(key, allow_copy=False)
    info = translation_info(rel, is_rel=True) or {}
    tasks = _load_tasks()
    t = tasks.get(key)
    info["task"] = None
    if t:
        info["task"] = {k: t.get(k) for k in
                        ("status", "job_id", "progress", "message", "error",
                         "dual", "created_at", "updated_at")}
    info["ok"] = True
    return info, 200


_copying = set()          # 正在回搬的 key（防并发复制；仅后台线程会进入）


def _reconcile_one(key, allow_copy=True):
    """把单个 active 任务与 5555 实况同步；done 则回搬译文到 NAS。

    allow_copy=False（轮询场景）：只查状态，不触发回搬。
    """
    tasks = _load_tasks()
    t = tasks.get(key)
    if not t or t.get("status") not in ACTIVE:
        return
    changed = False
    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    if t["status"] == "copying":
        if allow_copy and key not in _copying:
            _copying.add(key)
            try:
                _copy_back(key, t)
            finally:
                _copying.discard(key)
            changed = True
        # 否则：已有复制在进行（或调用方不允许复制）→ 原样返回
    else:
        if not t.get("job_id"):
            # 没有 job_id 的 copying 任务（缓存命中路径）交给后台回搬
            if allow_copy:
                t["status"] = "copying"
                changed = True
        else:
            st, reachable = _get_status(t["job_id"])
            if not reachable:
                return                      # 5555 暂时不可达，下轮再说
            if st is None:
                t["status"] = "failed"
                t["error"] = "翻译任务已不存在（翻译服务可能已重启），请重试"
                t["updated_at"] = now
                changed = True
            elif st.get("status") in ("done",):
                t["status"] = "copying"
                t["mono_url"] = st.get("mono_url") or t.get("mono_url")
                t["dual_url"] = st.get("dual_url") or t.get("dual_url")
                t["updated_at"] = now
                changed = True
                _copy_back(key, t)
            elif st.get("status") in ("failed", "cancelled"):
                t["status"] = "failed"
                t["error"] = st.get("error") or ("翻译%s" %
                                                 ("失败" if st["status"] == "failed" else "被取消"))
                t["updated_at"] = now
                changed = True
            else:
                # queued / translating：刷新进度与文案
                prog = st.get("progress") or st.get("percent") or 0
                if prog != t.get("progress") or st.get("message") != t.get("message"):
                    t["progress"] = prog
                    t["message"] = st.get("message") or t.get("message")
                    t["updated_at"] = now
                    changed = True
    if changed:
        _save_tasks(tasks)


def _copy_back(key, t):
    """把 5555 译文复制进 NAS 原文同目录（mono 必备；dual 仅勾选时）。"""
    rel = t.get("rel")
    orig = pdf_service.resolve_pdf(rel)
    if not orig:
        t["status"] = "failed"
        t["error"] = "NAS 上找不到原文，无法确定译文落点"
        return
    d = os.path.dirname(orig)
    stem = Path(os.path.basename(orig)).stem
    mono_dst = os.path.join(d, stem + ".zh.mono.pdf")
    dual_dst = os.path.join(d, stem + ".zh.dual.pdf")

    ok_mono = os.path.exists(mono_dst) and os.path.getsize(mono_dst) > 0
    if not ok_mono:
        url = t.get("mono_url")
        if not url:
            # mono_url 缺失（任务刚 done 还没拿到）→ 重试下一轮
            t["status"] = "copying"
            t["message"] = "等待译文就绪"
            return
        try:
            _download(url, mono_dst)
            ok_mono = True
        except Exception as e:
            t["status"] = "copying"      # NAS/网络抖动 → 重试，不判失败
            t["message"] = "回搬译文到 NAS 失败，重试中: %s" % e
            t["error"] = None
            log("copy_back mono failed %s: %s" % (key, e))
            return

    if t.get("dual"):
        ok_dual = os.path.exists(dual_dst) and os.path.getsize(dual_dst) > 0
        if not ok_dual:
            url = t.get("dual_url")
            if not url:
                # 任务响应里没带 dual_url（缓存命中/补归档路径）→ 问 5555 缓存
                probe = _cache_probe(t.get("name") or os.path.basename(orig))
                url = (probe or {}).get("dual_url")
            if url:
                try:
                    _download(url, dual_dst)
                except Exception as e:
                    log("copy_back dual failed %s: %s" % (key, e))
            elif t.get("only_dual"):
                t["status"] = "failed"
                t["error"] = "5555 上找不到双语对照版源文件（可能已过 48h 回收期），" \
                             "请取消勾选后重新翻译"
                return

    t["status"] = "done"
    t["progress"] = 100
    t["message"] = "已完成，译文已归档到 NAS"
    t["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    # 目录缓存作废，让检测立即看到新文件
    _dir_cache.pop(str(d), None)
    log("done %s → %s" % (key, mono_dst))


def _reconcile_loop():
    while True:
        try:
            for key in list(_load_tasks().keys()):
                try:
                    _reconcile_one(key)
                except Exception as e:
                    log("reconcile %s err: %s" % (key, e))
        except Exception as e:
            log("reconcile loop err: %s" % e)
        time.sleep(RECONCILE_INTERVAL)


_thread_started = False


def start_background():
    """启动后台对账线程（幂等；app __main__ 调用）。"""
    global _thread_started
    if _thread_started:
        return
    _thread_started = True
    threading.Thread(target=_reconcile_loop, name="translate-reconcile",
                     daemon=True).start()
    log("background reconcile thread started")


# ══════════════════════════ 路由注册 ══════════════════════

# ══════════════════════════ 处理器（可被别名路由复用）══════════════
# 抽成模块级函数而非内嵌闭包：journal_view 要挂 `/geokb/api/entry/<eid>/...`
# 别名路径，好让门户那份 geokb_translate.js（内部写死 /geokb/api/... ）
# 在本服务原样可用 —— 这样翻译交互脚本两端同一份文件、行为必然一致，
# 不必复制一份改路径（复制就必然漂移）。

def submit_translation_endpoint(eid):
    """一键翻译：提交到 5555 队列。已有译文/进行中 → 409。

    默认同时产出中文版（.zh.mono.pdf）与双语对照版（.zh.dual.pdf），
    用户下载时自选版本；body 里的 dual 字段仅为兼容保留、不再生效。
    """
    body = request.get_json(silent=True) or {}
    dual = True  # 恒生成双语对照版（用户 2026-10-16 确认默认行为）
    try:
        res, code = submit_translation(eid, dual=dual)
    except Exception as e:
        res, code = {"ok": False, "error": "internal",
                     "message": str(e)}, 500
    return jsonify(res), code


def translation_status_endpoint(eid):
    """译文检测 + 任务状态（前端轮询；后端在 response 里做好全部检测）。"""
    res, code = poll_translation(eid)
    return jsonify(res), code


def pdf_variant_endpoint(eid, variant):
    """译文 PDF：variant=mono（中文版）| dual（双语对照版）。
    文件与原文同目录（<stem>.zh.mono.pdf / .zh.dual.pdf）。"""
    variant = (variant or "").lower()
    if variant not in ("mono", "dual"):
        return jsonify({"ok": False, "error": "bad_variant",
                        "message": "variant 只能是 mono / dual"}), 400
    rel = entry_pdf_rel(eid)
    if not rel:
        return jsonify({"ok": False, "error": "no_pdf"}), 404
    files = translation_files(rel)
    want = files["mono_path"] if variant == "mono" else files["dual_path"]
    if not want:
        return jsonify({"ok": False, "error": "translation_missing",
                        "message": "该论文没有%s译文" %
                        ("中文" if variant == "mono" else "双语对照")}), 404
    inline = str(request.args.get("inline", "")).lower() in ("1", "true", "yes")
    resp = send_file(want, mimetype="application/pdf",
                     as_attachment=not inline,
                     download_name=os.path.basename(want),
                     conditional=True)
    resp.headers["Access-Control-Expose-Headers"] = \
        "Content-Disposition, Content-Length"
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


def register(app):
    app.add_url_rule(
        "/api/entry/<eid>/translate", "api_entry_translate",
        submit_translation_endpoint, methods=["POST"])
    app.add_url_rule(
        "/api/entry/<eid>/translation", "api_entry_translation",
        translation_status_endpoint, methods=["GET"])
    app.add_url_rule(
        "/api/entry/<eid>/pdf/<variant>", "api_entry_pdf_variant",
        pdf_variant_endpoint, methods=["GET"])

    log("routes registered: /api/entry/<eid>/translate|translation|pdf/<variant>")
