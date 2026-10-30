# -*- coding: utf-8 -*-
"""触发 GeoKB 全量索引重建。

由 5555 门户管理端在保存/删除/恢复后调用，
运行在 GeoKB venv 中，因此可以使用 sqlite-vec / fastembed 等依赖。
"""
import os
import sys
import time

SERVER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server")
sys.path.insert(0, SERVER)
import ingest


if __name__ == "__main__":
    lock = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "index", ".rebuild.lock")
    if os.path.exists(lock):
        age = time.time() - os.path.getmtime(lock)
        if age < 60:
            print("rebuild skipped: another rebuild started recently")
            sys.exit(0)
    open(lock, "w").close()
    try:
        ingest.rebuild(embed=True)
        print("rebuild done")
    finally:
        try:
            os.remove(lock)
        except Exception:
            pass
