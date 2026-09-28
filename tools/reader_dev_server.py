# -*- coding: utf-8 -*-
"""阅读器旁路开发服务：在备用端口跑一份完整应用，用来验收 /reader。

为什么需要它
------------
web_app.py 的端口是常量 5555，且启动时会拿单实例 PID 锁 ——
生产实例正跑着的时候，直接起第二份会被锁拒掉，而杀掉生产实例会让
kust.work 整站中断（它由计划任务托管的提升进程拉起，不是随手能补的）。

本脚本**只 import 不执行 __main__**：模块级代码会建好 app、注册门户与全部
业务模块，但不会走 `_acquire_flask_lock()` 与端口占用检查，
于是可以在 5566 这类备用端口上跑一份完全一样的站点做验收，
生产实例毫发无损。

用法
----
    "D:\\Python 3.12.4\\python.exe" tools/reader_dev_server.py            # 默认 5566
    "D:\\Python 3.12.4\\python.exe" tools/reader_dev_server.py --port 5577

验收要点（浏览器打开，或用 agent-browser 驱动）：
    http://127.0.0.1:5566/reader?file=/uploads/<原文>.pdf&peer=/translated/<译文>.pdf
"""
import argparse
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=5566)
    ap.add_argument('--host', default='127.0.0.1')
    args = ap.parse_args()

    from web_app import app  # 只 import：不触发 __main__ 里的锁与端口检查

    print('=' * 58)
    print(f'  阅读器旁路实例（不影响 5555 上的生产实例）')
    print(f'  http://{args.host}:{args.port}/reader')
    print(f'  自检  http://{args.host}:{args.port}/api/reader/info')
    print('=' * 58)

    try:
        import waitress
        waitress.serve(app, host=args.host, port=args.port, threads=6)
    except ImportError:
        app.run(host=args.host, port=args.port, threaded=True, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
