"""探测校园网/图书馆各访问入口的连通性（DNS + TCP 443）。"""
import socket
import time

HOSTS = [
    'lib.kust.edu.cn',
    'ds.carsi.edu.cn',
    'w.libvpn.kust.edu.cn',
    'erms.cwkeji.cn',
    'www.sciencedirect.com',
    'pdf.sciencedirectassets.com',
]

for h in HOSTS:
    t = time.time()
    try:
        ip = socket.gethostbyname(h)
        dns_ms = round((time.time() - t) * 1000)
    except Exception as e:
        print(f'{h:<32} DNS FAIL  {type(e).__name__}')
        continue
    ok, tcp_ms = '-', '-'
    t0 = time.time()
    try:
        s = socket.create_connection((ip, 443), timeout=6)
        s.close()
        ok = 'OK'
        tcp_ms = round((time.time() - t0) * 1000)
    except Exception as e:
        ok = 'FAIL:' + type(e).__name__
    print(f'{h:<32} {ip:<16} dns={dns_ms}ms tcp443={ok} {tcp_ms}ms')
