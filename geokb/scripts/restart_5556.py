import psutil, urllib.request, json, time

# 找 5556 监听进程
target = None
for c in psutil.net_connections('tcp'):
    if c.status == psutil.CONN_LISTEN and c.laddr.port == 5556:
        try:
            p = psutil.Process(c.pid)
            cmd = ' '.join(p.cmdline())
        except Exception:
            continue
        if 'geokb' in cmd.lower() or 'app.py' in cmd or 'waitress' in cmd.lower():
            target = p
            print('5556 pid=%d cmd=%s' % (p.pid, cmd[:140]))
            break
if not target:
    print('未找到 5556 服务进程')
    raise SystemExit(1)

# 记录父进程（守护）
parent = target.parent()
print('父进程(守护): pid=%s name=%s' % (parent.pid if parent else None,
                                        parent.name() if parent else 'None'))
target.terminate()
print('已 terminate，等待守护拉起新进程...')
for i in range(40):
    time.sleep(3)
    try:
        with urllib.request.urlopen('http://127.0.0.1:5556/openapi.json', timeout=5) as r:
            print('%ds 后 /openapi.json -> %d' % ((i + 1) * 3, r.status))
            break
    except Exception as e:
        if i % 4 == 3:
            print('%ds 仍在等待... (%s)' % ((i + 1) * 3, type(e).__name__))
else:
    print('等待超时')
