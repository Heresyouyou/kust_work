"""夜间入库辅助：按 sha12 抽取全文并生成合并文本。

用法: python scripts/_night_text.py <sha12> [start] [end]
"""
import sys
sys.path.insert(0, 'scripts')
import json
import os
from pdf_corpus_ingest import extract_text


def queue_row(sha12):
    rows = [json.loads(l) for l in open('state/agent_ingest_queue.jsonl', encoding='utf-8')
            if l.strip()]
    for r in rows:
        if r['sha12'] == sha12:
            return r
    raise SystemExit('not in queue: ' + sha12)


def merged(sha12):
    out = 'state/text/_%s_merged.txt' % sha12
    if os.path.exists(out):
        return open(out, encoding='utf-8').read()
    r = queue_row(sha12)
    t = extract_text(r['path'], sha12)
    m = ''.join(l.strip() for l in t.splitlines() if l.strip())
    open(out, 'w', encoding='utf-8').write(m)
    return m


if __name__ == '__main__':
    sha = sys.argv[1]
    m = merged(sha)
    print('LEN', len(m), '| REF', m.rfind('参考文献'), '| PDFOK', bool(queue_row(sha)['path']))
    if len(sys.argv) >= 4:
        print(m[int(sys.argv[2]):int(sys.argv[3])])
