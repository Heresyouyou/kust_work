#!/usr/bin/env python
"""geokb MCP 信任键工具 —— 让桌面端自定义 MCP 免点击即被信任。

原理（逆向自 WorkBuddy `main/tar.js` + `main/server.js`）：

    transport = toTransport(raw)     # stdio: {kind, command, args, env, cwd?}
    material  = command | sorted(args).join(',') | sorted(env_keys).join(',') |
                (cwd ? [cwd] : [])
    key       = sha256(material) + '::' + serverName

写入 `%USERPROFILE%/.workbuddy/mcp-approvals.json`，值为毫秒时间戳。
注意两条硬事实：
  1. **env 只取键名、不取值** → 改 `GEOKB_MCP_ROLE` 的值不会让信任失效；
     但增删一个环境变量键，或改动 command/args/cwd，都必须重新授信。
  2. 宿主仅在**启动时** `trust.load()`；改完必须重启 WorkBuddy 才生效
     （运行中的 `mcpRefresh` 只重读 mcp.json，不重读信任表）。

用法：
    python scripts/mcp_trust_key.py geokb-ingest          # 只算，不写
    python scripts/mcp_trust_key.py geokb-ingest --apply  # 写入信任表（带备份）
    python scripts/mcp_trust_key.py --list                # 列出全部服务与授权状态
"""
import argparse
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

CONFIG_DIR = Path.home() / ".workbuddy"
MCP_FILE = CONFIG_DIR / "mcp.json"
APPROVALS = CONFIG_DIR / "mcp-approvals.json"


def norm_args(raw):
    args = raw.get("args") or []
    if not isinstance(args, list):
        return []
    out = []
    for a in args:
        if isinstance(a, (str, int, float, bool)):
            out.append(str(a))
    return out


def norm_env_keys(raw):
    env = raw.get("env") or {}
    if not isinstance(env, dict):
        return []
    keys = []
    for k, v in env.items():
        if isinstance(v, (str, int, float, bool)):
            keys.append(k)
    return sorted(keys)


def trust_material(raw):
    """与 JS trustMaterial() 完全等价的实现。"""
    if raw.get("url") and not raw.get("command"):
        from urllib.parse import urlparse
        u = urlparse(str(raw["url"]))
        return "%s://%s" % (u.scheme, u.netloc) if u.scheme and u.netloc else str(raw["url"])
    parts = [
        str(raw.get("command") or ""),
        ",".join(sorted(norm_args(raw))),
        ",".join(norm_env_keys(raw)),
    ]
    cwd = raw.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        parts.append(cwd)
    return "|".join(parts)


def trust_key(name, raw):
    digest = hashlib.sha256(trust_material(raw).encode("utf-8")).hexdigest()
    return "%s::%s" % (digest, name), digest


def load_servers():
    if not MCP_FILE.exists():
        raise SystemExit("找不到 %s" % MCP_FILE)
    data = json.loads(MCP_FILE.read_text(encoding="utf-8"))
    return data.get("mcpServers") or {}


def load_approvals():
    if not APPROVALS.exists():
        return {}
    try:
        return json.loads(APPROVALS.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise SystemExit("mcp-approvals.json 解析失败：%s" % e)


def cmd_list():
    servers = load_servers()
    appr = load_approvals()
    print("%-22s %-10s %s" % ("SERVER", "TRUSTED", "KEY"))
    for name, raw in servers.items():
        key, digest = trust_key(name, raw)
        print("%-22s %-10s %s…::%s" % (name, "YES" if key in appr else "NO",
                                       digest[:12], name))
    stale = [k for k in appr if k.split("::", 1)[1] not in servers]
    if stale:
        print("\n[提示] 信任表中有 %d 条已无对应服务的孤立记录（不影响使用）。" % len(stale))
    return 0


def cmd_show(name):
    servers = load_servers()
    if name not in servers:
        raise SystemExit("mcp.json 里没有名为 %s 的服务" % name)
    key, digest = trust_key(name, servers[name])
    appr = load_approvals()
    print("server         :", name)
    print("trust material :", trust_material(servers[name]))
    print("configHash     :", digest)
    print("approval key   :", key)
    print("trusted        :", "YES" if key in appr else "NO")
    return 0


def cmd_apply(name):
    servers = load_servers()
    if name not in servers:
        raise SystemExit("mcp.json 里没有名为 %s 的服务" % name)
    key, digest = trust_key(name, servers[name])
    print("trust material :", trust_material(servers[name]))
    print("configHash     :", digest)
    print("approval key   :", key)

    appr = load_approvals()
    if key in appr:
        print("\n已存在信任记录，无需重复写入。")
        return 0

    if APPROVALS.exists():
        bak = APPROVALS.with_name(
            "mcp-approvals.json.bak-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(APPROVALS, bak)
        print("backup         :", bak.name)

    appr[key] = int(time.time() * 1000)
    APPROVALS.parent.mkdir(parents=True, exist_ok=True)
    APPROVALS.write_text(json.dumps(appr, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    again = load_approvals()
    if key not in again:
        raise SystemExit("写入后回读失败")
    print("写入成功 → %s" % APPROVALS)
    print("\n【必须重启 WorkBuddy】信任表只在启动时加载，重启后该服务即为已信任。")
    return 0


def main():
    ap = argparse.ArgumentParser(description="计算 / 写入 WorkBuddy 自定义 MCP 信任键")
    ap.add_argument("server", nargs="?", help="mcp.json 里的服务名")
    ap.add_argument("--apply", action="store_true", help="写入信任表（带备份）")
    ap.add_argument("--list", action="store_true", help="列出全部服务与授权状态")
    args = ap.parse_args()

    if args.list:
        return cmd_list()
    if not args.server:
        ap.print_help()
        return 2
    return cmd_apply(args.server) if args.apply else cmd_show(args.server)


if __name__ == "__main__":
    sys.exit(main())
