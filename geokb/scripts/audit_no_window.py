"""审计：全库 subprocess 调用点里，哪些没有 creationflags=_NO_WIN（会弹黑窗）。

判定口径
---------
父进程若**没有控制台**（pythonw 拉起的服务、计划任务的隐藏会话、以及任何
带 CREATE_NO_WINDOW 的子孙），它 spawn 子进程时子进程会**分配一个新控制台**
→ 桌面上闪一个 cmd/python 黑窗。带 CREATE_NO_WINDOW 则给隐藏控制台，后代继承。

所以：`subprocess.run/Popen` 缺 creationflags 就是潜在弹窗点。
`capture_output=True` **不能**防止弹窗（它只重定向 stdout，不决定控制台）。

用法：python scripts/audit_no_window.py [--fix]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = BASE / "scripts"

# 抓 subprocess.run(...) / subprocess.Popen(...) / subprocess.call(...) 的完整括号体
CALL = re.compile(r"subprocess\.(run|Popen|call|check_output|check_call)\s*\(", re.S)


def paren_body(text: str, start: int) -> str:
    """从 start（指向 '(' 之后）开始，配平括号取整段调用参数。"""
    depth = 1
    i = start
    n = len(text)
    while i < n and depth:
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch in "\"'":
            q = ch
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == q:
                    break
                i += 1
        i += 1
    return text[start:i - 1]


def scan(path: Path):
    try:
        src = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    if "subprocess" not in src:
        return []
    lines = src.splitlines()
    out = []
    for m in CALL.finditer(src):
        body = paren_body(src, m.end())
        if "creationflags" in body:
            continue
        ln = src[:m.start()].count("\n") + 1
        # 取调用首行做摘要
        seg = lines[ln - 1].strip() if ln - 1 < len(lines) else ""
        nxt = lines[ln].strip() if ln < len(lines) else ""
        out.append((path.name, ln, (seg + " " + nxt)[:130]))
    return out


def main():
    fix = "--fix" in sys.argv
    hits = []
    for p in sorted(SCRIPTS.glob("*.py")):
        hits += scan(p)

    print("缺少 creationflags 的 subprocess 调用：%d 处" % len(hits))
    by_file = {}
    for f, ln, seg in hits:
        by_file.setdefault(f, []).append((ln, seg))
    for f in sorted(by_file):
        print("\n  %s  (%d 处)" % (f, len(by_file[f])))
        for ln, seg in by_file[f]:
            print("    %4d  %s" % (ln, seg))
    if not fix:
        print("\n（加 --fix 自动补 creationflags=_NO_WIN）")
        return
    print("\n--fix 未在此脚本实现：补丁需按文件逐个核对上下文，")
    print("   请用专用补丁脚本，避免误改已经手工处理过的行。")


if __name__ == "__main__":
    main()
