# -*- coding: utf-8 -*-
"""探测反爬相关库可用性（哪些方案路线在技术上可行）。"""
import importlib
import sys

MODS = ['curl_cffi', 'httpx', 'httpcore', 'patchright', 'playwright',
        'requests', 'tls_client', 'cloudscraper', 'undetected_chromedriver',
        'h2', 'brotli']
print('python:', sys.executable)
for m in MODS:
    try:
        mod = importlib.import_module(m)
        print('  OK  %-24s %s' % (m, getattr(mod, '__version__', '')))
    except Exception as e:
        print('  --  %-24s %s' % (m, type(e).__name__))
