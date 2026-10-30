#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""GeoKB 入库 MCP 服务（stdio，零依赖，JSON-RPC 2.0 按行分隔）。

为什么自研而不装 SDK：本机是离线优先环境，venv 未装 `mcp`；MCP 的 stdio 传输
就是「一行一条 JSON-RPC」，工具面只有 initialize / tools/list / tools/call，
自己实现约 200 行，比引入依赖更可控，也避免与既有 Flask 服务的依赖冲突。

**stdout 专供协议**：导入的模块（jieba、kp_store 等）会 print，所以启动后立刻把
sys.stdout 指向 stderr，协议输出走单独的句柄，否则会污染 JSON-RPC 流。

角色（环境变量 GEOKB_MCP_ROLE）——**按职责拆分，互相拿不到对方的写工具**：
  dispatcher  父代理：status / next / batch / prep（只取批分发，**无 write/commit/evict，也不读正文**）
  worker      子代理：status / next / batch / read_text / write / commit / evict（**无 prep**，不能自己开批）
  reader      纯只读：status / next / batch / read_text
  admin       全部（人工运维；scan/fix/skip 等危险操作仍只留 CLI，故意不暴露）

注册示例（~/.workbuddy/mcp.json）：
  "geokb-ingest": {
    "command": "D:\\\\ZSK\\\\geokb\\\\venv\\\\Scripts\\\\python.exe",
    "args": ["D:\\\\ZSK\\\\geokb\\\\scripts\\\\geokb_mcp_server.py"],
    "env": {"GEOKB_MCP_ROLE": "worker"}
  }
"""
from __future__ import annotations

import json
import os
import sys
import traceback

PROTO = sys.stdout          # 协议专用句柄，必须在任何 import 之前抓住
sys.stdout = sys.stderr     # 之后所有 print 都进 stderr，不污染协议流

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(BASE, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(BASE, "scripts"))

ROLE = (os.environ.get("GEOKB_MCP_ROLE") or "worker").strip().lower()
SERVER_INFO = {"name": "geokb-ingest", "version": "1.0.0"}
DEFAULT_PROTO = "2025-06-18"


def send(obj: dict) -> None:
    PROTO.write(json.dumps(obj, ensure_ascii=False) + "\n")
    PROTO.flush()


def log(msg: str) -> None:
    sys.stderr.write("[geokb-mcp] %s\n" % msg)
    sys.stderr.flush()


# ──────────────────────────────────────────────────────────────────────
# 工具定义
# ──────────────────────────────────────────────────────────────────────
_SHA = {"type": "string", "description": "论文 sha12（12 位十六进制）"}

_R = frozenset({"reader", "dispatcher", "worker", "admin"})   # 只读状态类
_T = frozenset({"reader", "worker", "admin"})                 # 读正文（父代理不读正文）
_D = frozenset({"dispatcher", "admin"})                       # 取批（父代理专属）
_W = frozenset({"worker", "admin"})                           # 入库（子代理专属）

TOOLS = {
    "geokb_status": {
        "roles": _R,
        "description": ("断点诊断（唯一权威口径：磁盘事实）。返回 papers/kpoints/index 计数、"
                        "committed/incomplete/todo_P0/hot、在飞批次与 NEXT ACTION 序列。"
                        "任何时候接手先调它。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "fix": {"type": "boolean", "default": False,
                        "description": "按磁盘事实修正队列 bucket 标记（幂等）"},
            },
        },
        "fn": lambda a: _core().status(fix=bool(a.get("fix"))),
    },
    "geokb_next": {
        "roles": _R,
        "description": ("取 FIFO 队首的 P0 待办（按台账行号，最早积压先清）。"
                        "返回 sha/标题/期刊/是否有 wip/payload。P0 为空时 done=true。"),
        "inputSchema": {"type": "object", "properties": {}},
        "fn": lambda a: _core().next_task(),
    },
    "geokb_read_text": {
        "roles": _T,
        "description": ("读论文正文。mode=nav 取导航（标题/锚点/摘要结论区间/数值候选）；"
                        "mode=full 通读整篇（推荐，质量优先）；mode=slices 回看某段"
                        "（需 start/end，次数不限）。返回 text 与字符数。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sha": _SHA,
                "mode": {"type": "string", "enum": ["nav", "full", "slices"], "default": "nav"},
                "start": {"type": "integer", "description": "slices 起始行（1 基）"},
                "end": {"type": "integer", "description": "slices 结束行"},
            },
            "required": ["sha"],
        },
        "fn": lambda a: _core().read_text(a["sha"], a.get("mode") or "nav",
                                          int(a.get("start") or 0), int(a.get("end") or 0)),
    },
    "geokb_batch": {
        "roles": _R,
        "description": "查看在飞批次清单（batch_id / shas / status / outcome / pending）。",
        "inputSchema": {"type": "object", "properties": {}},
        "fn": lambda a: _core().batch_info(),
    },
    "geokb_prep": {
        "roles": _D,
        "description": ("取一批 P0 生成本批工作笔记并落批次清单（open_batch.json）。"
                        "若上一批仍是 open 会拒绝，需先 commit。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 6, "minimum": 1, "maximum": 50},
                "bucket": {"type": "string", "default": "P0"},
                "sha": {"type": "string", "description": "可选：定向 prep 某一篇（重试单篇时用）"},
                "force": {"type": "boolean", "default": False,
                          "description": "在飞批次未收口时强制扩批（慎用）"},
            },
        },
        "fn": lambda a: _core().prep(int(a.get("limit") or 6), a.get("bucket") or "P0",
                                     bool(a.get("force")), a.get("sha")),
    },
    "geokb_write": {
        "roles": _W,
        "description": ("写入一篇的条目与知识点（过 G1–G7 与数值校验）。"
                        "**只能写「在飞批次内」或「已 prep」的 sha**，否则服务端拒绝。"
                        "返回 ok/kp/soft/hard/error；硬错不入库。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sha": _SHA,
                "payload": {"type": "object", "additionalProperties": True,
                            "description": "完整 payload（字段见 SKILL.md §4）"},
            },
            "required": ["sha", "payload"],
        },
        "fn": lambda a: _core().write(a["sha"], a.get("payload") or {}),
    },
    "geokb_commit": {
        "roles": _W,
        "description": ("提交若干篇：建索引 + 登记台账 + 按磁盘事实收口批次。"
                        "对已 committed 的篇幂等。父代理在收齐子代理回执后调一次。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "shas": {"type": "array", "items": {"type": "string"}},
                "all_written": {"type": "boolean", "default": False,
                                "description": "提交所有 stage=written 的篇"},
            },
        },
        "fn": lambda a: _core().commit(a.get("shas"), bool(a.get("all_written"))),
    },
    "geokb_evict": {
        "roles": _W,
        "description": ("把已入库篇的 wip 笔记/payload/全文缓存移入 state/_cold（只移不删，"
                        "可 restore）。没有条目的篇会被安全闸门拒绝。"),
        "inputSchema": {
            "type": "object",
            "properties": {"shas": {"type": "array", "items": {"type": "string"}}},
            "required": ["shas"],
        },
        "fn": lambda a: _core().evict(a.get("shas") or []),
    },
}

KNOWN_ROLES = {"reader", "dispatcher", "worker", "admin"}


def _core():
    import ingest_core
    return ingest_core


def _visible_tools() -> dict:
    # 角色名写错时回退到最小权限 reader，避免「拼错一个字母就拿到写权限」
    role = ROLE if ROLE in KNOWN_ROLES else "reader"
    return {k: v for k, v in TOOLS.items() if role in v["roles"]}


def _tool_defs() -> list:
    return [{"name": k,
             "description": v["description"],
             "inputSchema": v["inputSchema"]}
            for k, v in _visible_tools().items()]


# ──────────────────────────────────────────────────────────────────────
# 请求处理
# ──────────────────────────────────────────────────────────────────────
def handle(msg: dict):
    mid = msg.get("id")
    method = msg.get("method") or ""
    params = msg.get("params") or {}
    is_notification = ("id" not in msg)

    if method == "initialize":
        pv = params.get("protocolVersion") or DEFAULT_PROTO
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": pv,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        }})
        log("initialize ok (role=%s, proto=%s)" % (ROLE, pv))
        return

    if method in ("notifications/initialized", "initialized"):
        return

    if method == "ping":
        if not is_notification:
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        return

    if method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid,
              "result": {"tools": _tool_defs()}})
        return

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = _visible_tools().get(name)
        if not tool:
            send({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": -32602,
                            "message": "未知工具或当前角色无权使用: %s（角色=%s）"
                                       % (name, ROLE)}})
            return
        try:
            result = tool["fn"](args)
        except Exception as e:  # noqa: BLE001
            log("tool %s 异常: %s" % (name, traceback.format_exc(limit=3)))
            result = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        text = json.dumps(result, ensure_ascii=False, indent=2)
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": text}],
            "isError": not bool(result.get("ok", False)),
        }})
        return

    if method in ("resources/list", "prompts/list"):
        key = "resources" if method.startswith("resources") else "prompts"
        send({"jsonrpc": "2.0", "id": mid, "result": {key: []}})
        return

    if not is_notification:
        send({"jsonrpc": "2.0", "id": mid,
              "error": {"code": -32601, "message": "未实现的方法: %s" % method}})


def main() -> int:
    log("start (role=%s, tools=%s)" % (ROLE, ",".join(_visible_tools())))
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:  # noqa: BLE001
            log("非法 JSON 行，已忽略")
            continue
        if isinstance(msg, list):
            for m in msg:
                handle(m)
        else:
            handle(msg)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        log("FATAL %s: %s" % (type(exc).__name__, exc))
        sys.exit(2)
