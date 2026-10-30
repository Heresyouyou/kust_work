# -*- coding: utf-8 -*-
"""全库 URL 域名迁移：https://lib.kust.work → https://sjck.kust.work

背景：lib.kust.work 易被外部 AI 误判为"昆明理工大学图书馆"域名，
2026-10-15 起公网域名切换为 sjck.kust.work。库内 5163+ 条 sources URL
需同步迁移，否则历史链接全部失效。

安全设计：
  - 只做整串替换 "https://lib.kust.work" -> "https://sjck.kust.work"
  - 逐文件前置备份（仅备份将改动的文件）到 state/backup_rename_20261015/
  - 校验：替换后文件与替换前差异必须仅为域名字符串
"""
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT = os.path.join(BASE, "content")
BACKUP = os.path.join(BASE, "state", "backup_rename_20261015")
OLD = "https://lib.kust.work"
NEW = "https://sjck.kust.work"


def main():
    changed = []
    total_old = 0
    for root, _dirs, files in os.walk(CONTENT):
        for fn in files:
            if not fn.endswith(".md"):
                continue
            p = os.path.join(root, fn)
            try:
                text = open(p, encoding="utf-8").read()
            except Exception as e:
                print("读取失败: %s (%s)" % (p, e))
                sys.exit(1)
            n = text.count(OLD)
            if n == 0:
                continue
            new_text = text.replace(OLD, NEW)
            # 校验：差异必须仅为域名串
            if new_text.replace(NEW, OLD) != text:
                print("校验异常（差异超出域名串）: %s" % p)
                sys.exit(1)
            rel = os.path.relpath(p, CONTENT)
            bak = os.path.join(BACKUP, rel)
            os.makedirs(os.path.dirname(bak), exist_ok=True)
            shutil.copy2(p, bak)
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(new_text)
            changed.append((rel, n))
            total_old += n

    print("改动文件数: %d" % len(changed))
    print("替换 URL 总数: %d" % total_old)
    if changed:
        print("备份目录: %s" % BACKUP)
        print("样例:")
        for rel, n in changed[:3]:
            print("  %s (%d 处)" % (rel, n))
    # 复扫确认零残留
    left = 0
    for root, _dirs, files in os.walk(CONTENT):
        for fn in files:
            if fn.endswith(".md"):
                left += open(os.path.join(root, fn), encoding="utf-8").read().count(OLD)
    print("残留 lib.kust.work: %d" % left)
    if left:
        sys.exit(1)


if __name__ == "__main__":
    main()
