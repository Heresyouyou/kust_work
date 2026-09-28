import io, os, sys, json, glob, time, atexit, ctypes
sys.path.insert(0, r"D:/PostNews/Translate")
from flask import Flask
from modules.stitch import init_stitch

# 本进程退出时，`submit` 里那个守护线程的 finally 会被直接掐断（生产服务是长驻进程，
# 不会遇到），于是上传的临时件会留在 uploads/。这里在退出时补一刀，但**只删本次
# 测试自己创建的 task_id 前缀**，避免误删线上服务正在用的临时件。
#
# 注意：atexit 回调跑在解释器收尾阶段，`__main__` 的全局名（含 `__file__`）可能已被
# 清空 → 一律用**默认参数**把路径与列表绑到函数对象上，别在回调里查全局。
TASK_IDS = []
UPLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'uploads')

def _clean_uploads(tids=TASK_IDS, d=UPLOADS_DIR):
    # 任务线程报 "done" 后它的 `finally` 才真正删盘，进程退出可能抢在前面 →
    # 有界重试几轮，别只扫一次就认输。
    n = 0
    for _ in range(12):
        left = []
        for tid in tids:
            for p in glob.glob(os.path.join(d, tid + '_*')):
                if ctypes.windll.kernel32.DeleteFileW(os.path.abspath(p)):
                    n += 1
                else:
                    left.append(p)
        if not left:
            break
        time.sleep(0.25)
    print(f"\n[cleanup] 清掉本次测试遗留的上传临时件 {n} 个，"
          f"uploads/ 现在剩 {len(os.listdir(d))} 个")

atexit.register(_clean_uploads)

app = Flask(__name__)
init_stitch(app)
c = app.test_client()

print("== health ==")
r = c.get('/api/stitch/health')
print(r.status_code, r.get_json())
# 前端「推荐」档位靠这个值收口（否则会推荐出面已超出内存的档位）
_mp = r.get_json().get('canvas_budget_mp')
print("canvas_budget_mp:", _mp, "(期望 4~300 MP)")
assert _mp and 4 <= _mp <= 300, "health 未暴露合理的内存预算"

print("\n== page ==")
r = c.get('/stitch')
print(r.status_code, len(r.data))

print("\n== assets guard ==")
print(c.get('/api/stitch/assets/../routes.py').status_code, '(expect 403/404)')
print(c.get('/api/stitch/assets/app.js').status_code, '(expect 200)')

SRC = os.path.join("d://science", "2\u7530\u575d\u5fc3\u94a8\u77ff", "\u955c\u4e0b", "ZK203-81.5-T2")
def post(paths, extra=None):
    data = {'files': [(io.BytesIO(open(p,'rb').read()), os.path.basename(p)) for p in paths]}
    if extra: data.update(extra)
    r = c.post('/api/stitch/submit', data=data, content_type='multipart/form-data')
    try:
        tid = r.get_json().get('task_id')
        if tid: TASK_IDS.append(tid)
    except Exception:
        pass
    return r

print("\n== submit: 2 overlapping (4-6,4-7) ==")
r = post([os.path.join(SRC,'4-6-A.jpg'), os.path.join(SRC,'4-7-A.jpg')],
         {'max_side':'0','engine':'auto'})
print(r.status_code, r.get_json())
tid = r.get_json()['task_id']

import time
for _ in range(120):
    t = c.get(f'/api/stitch/task/{tid}').get_json()
    if t['status'] != 'running': break
    time.sleep(1)
print("status:", t['status'], "pct:", t.get('percent'))
if t['status']=='done':
    res=t['result']; m=res['meta']
    print(f"  {res['width']}x{res['height']} {res['size_kb']}KB method={m['method']}")
    print(f"  groups={m['groups']} used={m['used_indices']}")
    print(f"  result url: {c.get(res['url']).status_code}")
    print(f"  download:   {c.get(res['url']+'/dl').status_code}")
else:
    print("  ERR:", t.get('error'))

print("\n== submit: NON-overlapping (1-0-A/B/C) ==")
r = post([os.path.join(SRC,f'1-0-{x}.jpg') for x in 'ABC'], {'max_side':'0'})
tid = r.get_json()['task_id']
for _ in range(180):
    t = c.get(f'/api/stitch/task/{tid}').get_json()
    if t['status'] != 'running': break
    time.sleep(1)
print("status:", t['status'])
print("error:", (t.get('error') or '')[:220])

print("\n== submit: 输出图幅档位 out_side=1024 ==")
r = post([os.path.join(SRC,'4-6-A.jpg'), os.path.join(SRC,'4-7-A.jpg')],
         {'max_side':'0','engine':'fallback','out_side':'1024'})
print(r.status_code)
tid = r.get_json()['task_id']
for _ in range(180):
    t = c.get(f'/api/stitch/task/{tid}').get_json()
    if t['status'] != 'running': break
    time.sleep(1)
print("status:", t['status'])
if t['status'] == 'done':
    res = t['result']; m = res['meta']
    print(f"  {res['width']}x{res['height']}  cap={m.get('output_side_cap')} "
          f"scale={m.get('output_scale')} (期望最长边<=1024)")
else:
    print("  ERR:", (t.get('error') or '')[:220])

print("\n== submit: 自定义图幅 out_side=6000（不在档位表里） ==")
r = post([os.path.join(SRC,'4-6-A.jpg'), os.path.join(SRC,'4-7-A.jpg')],
         {'max_side':'0','engine':'fallback','out_side':'6000'})
print(r.status_code)
tid = r.get_json()['task_id']
for _ in range(180):
    t = c.get(f'/api/stitch/task/{tid}').get_json()
    if t['status'] != 'running': break
    time.sleep(1)
print("status:", t['status'])
if t['status'] == 'done':
    res = t['result']; m = res['meta']
    print(f"  {res['width']}x{res['height']}  cap={m.get('output_side_cap')} "
          f"scale={m.get('output_scale')} (自定义值应原样接收)")
else:
    print("  ERR:", (t.get('error') or '')[:220])
