# -*- coding: utf-8 -*-
"""列出当前所有 Python 进程及其命令行，筛出疑似 LLM 入库/翻译任务。"""
import psutil

KEY = ('pdf_corpus_ingest', 'kp_extract', 'history_queue', 'ingest_external',
       'migrate_feed', 'daily_run', 'daily_archive', 'backfill',
       'translate_service', 'journal_pdf', 'oa_thirdparty', 'journal_auto_push',
       'deepseek', 'embed', 'token_saving', 'block_probe')

hits, others = [], 0
for p in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time', 'ppid']):
    try:
        info = p.info
        cl = info.get('cmdline') or []
        s = ' '.join(cl)
        low = s.lower()
        if not any(k in low for k in ('python',)):
            continue
        if any(k in low for k in KEY):
            hits.append((info['pid'], info['ppid'], info['create_time'], s))
        else:
            others += 1
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        continue

import datetime
print('匹配到 %d 个相关进程（其他 python 进程 %d 个）' % (len(hits), others))
for pid, ppid, ct, s in sorted(hits, key=lambda x: x[2]):
    t = datetime.datetime.fromtimestamp(ct).strftime('%H:%M:%S')
    print('  pid=%-7d ppid=%-7d start=%s  %s' % (pid, ppid, t, s[:160]))
if not hits:
    print('  （无）')
