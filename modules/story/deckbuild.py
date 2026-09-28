# -*- coding: utf-8 -*-
"""PPTX → 网页放映页图（story 模块内的转换器）+ 一个极简后台任务表

和 `tools/pptx_to_deck.py` 的关系：
    那个是**命令行**工具（`argparse` + `SystemExit`），给运维手动跑；
    这里是**同一条链路**的库版本 —— 失败抛 `RuntimeError` 而不是退出进程，
    因为调用方是 Flask 请求 / 后台线程，进程不能死。

为什么放到模块内而不是 import 那个 CLI：
    ① CLI 的位置在仓库 tools/ 下，模块不该反向依赖它；
    ② CLI 用 `sys.exit` 报错，在 web 请求里会直接杀掉工作进程。

链路：soffice → PDF → PyMuPDF 栅格化 → Pillow 量化 PNG8（体积减半、肉眼无差）。

两个已在生产踩过的坑，这里都处理了：
  ① **必须独立 UserInstallation profile** —— 否则和用户已打开的 soffice 抢锁会静默失败。
     用 uuid 而不是 pid：Flask 是长驻进程，pid 复用会让两次构建撞同一个 profile。
  ② **必须清掉 PYTHONHOME / PYTHONPATH** —— soffice 自带一套 python，继承到这些变量会
     报 `Could not find platform independent libraries <prefix>` 并加载不出过滤器。
     这行告警本身无害，但真出错时它会盖住真正的原因，所以在源头掐掉。
"""
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
import re

SOFFICE_CANDIDATES = [
    r'C:\Program Files\LibreOffice\program\soffice.exe',
    r'C:\Program Files (x86)\LibreOffice\program\soffice.exe',
    '/usr/bin/soffice',
    '/usr/local/bin/soffice',
    '/Applications/LibreOffice.app/Contents/MacOS/soffice',
]

# 中文环境下 soffice 的输出是 GBK，直接 decode('utf-8') 会炸
_ENCODINGS = ('utf-8', 'gbk', 'latin-1')


def find_soffice():
    for p in SOFFICE_CANDIDATES:
        if os.path.isfile(p):
            return p
    exe = shutil.which('soffice') or shutil.which('libreoffice')
    if not exe:
        raise RuntimeError(
            '找不到 LibreOffice（soffice）—— 网页放映版需要它把 pptx 转成 PDF。'
            '装一个，或改 deckbuild.SOFFICE_CANDIDATES 里的路径。')
    return exe


def count_slides(pptx):
    """数 pptx 里有几页（纯读 zip，不依赖任何 Office 组件）。

    同时充当「这是个真 pptx 吗」的校验：传进来的若是改了扩展名的文件，
    这里就会因为找不到 ppt/slides/slideN.xml 而报错 —— 比转完才发现失败早得多。
    """
    try:
        with zipfile.ZipFile(pptx) as z:
            n = len([x for x in z.namelist()
                     if re.match(r'ppt/slides/slide\d+\.xml$', x)])
    except (zipfile.BadZipFile, OSError) as e:
        raise RuntimeError('不是有效的 .pptx（zip 打不开）：%s' % e)
    if not n:
        raise RuntimeError('这不是 .pptx 演示文稿（包里没有 ppt/slides/slide*.xml）')
    return n


def _decode(raw):
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, AttributeError):
            continue
    return repr(raw)


def pdf_from_pptx(pptx, outdir, timeout=600, log=None):
    """soffice 转 PDF，返回 PDF 路径。失败抛 RuntimeError。"""
    soffice = find_soffice()
    profile = os.path.join(tempfile.gettempdir(), 'lo_deck_%s' % uuid.uuid4().hex[:10])
    prof_uri = 'file:///' + os.path.abspath(profile).replace('\\', '/')

    env = dict(os.environ)
    for k in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONSTARTUP'):
        env.pop(k, None)

    cmd = [soffice, '--headless', '--norestore', '--invisible',
           '-env:UserInstallation=' + prof_uri,
           '--convert-to', 'pdf', '--outdir', outdir, pptx]

    if log:
        log('调用 LibreOffice 转换 PDF（首次冷启动约 10–20 秒）…')
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise RuntimeError('LibreOffice 转换超时（%d 秒）—— 文件过大或有损坏页' % timeout)
    except OSError as e:
        raise RuntimeError('启动 LibreOffice 失败：%s' % e)
    finally:
        shutil.rmtree(profile, ignore_errors=True)

    out = _decode(r.stdout or b'').strip()
    if log and out:
        for line in out.splitlines():
            if 'platform independent' in line:      # 已知无害噪声，不往界面上抛
                continue
            log('  ' + line.strip())

    pdf = os.path.join(outdir, os.path.splitext(os.path.basename(pptx))[0] + '.pdf')
    if not os.path.isfile(pdf):
        raise RuntimeError(
            'LibreOffice 没有产出 PDF —— 通常是文件被占用、或含它不支持的版式。'
            '原始输出：%s' % (out[:300] or '（空）'))
    return pdf


def pages_from_pdf(pdf, deckdir, width=2560, force=False, log=None):
    """PDF 逐页栅格化为 deck/pNN.png，返回页数。"""
    try:
        import fitz                                  # PyMuPDF
        from PIL import Image
    except ImportError as e:
        raise RuntimeError('缺少栅格化依赖（PyMuPDF / Pillow）：%s' % e)

    doc = fitz.open(pdf)
    try:
        total = doc.page_count
        os.makedirs(deckdir, exist_ok=True)
        if not force:
            have = [f for f in os.listdir(deckdir)
                    if f.startswith('p') and f.endswith('.png')]
            if len(have) == total:
                if log:
                    log('已有 %d 张页图，与 PDF 页数一致，跳过' % total)
                return total

        if log:
            log('栅格化 %d 页 → %s' % (total, deckdir))
        h_target = int(round(width * 9.0 / 16.0))
        for i in range(total):
            page = doc[i]
            w = float(page.rect.width) or 1.0
            # 多渲 2px 再重采样：PDF 页面 rect 常带小数（960.00946pt），
            # 直接乘 zoom 会得到 2561 宽
            zoom = (width + 2) / w
            pm = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            img = Image.frombytes('RGB', (pm.width, pm.height), pm.samples)
            if img.size != (width, h_target):
                img = img.resize((width, h_target), Image.LANCZOS)
            img = img.quantize(colors=256, method=Image.MEDIANCUT,
                               dither=Image.FLOYDSTEINBERG)
            img.save(os.path.join(deckdir, 'p%02d.png' % (i + 1)), optimize=True)
            if log and (i + 1) % 5 == 0:
                log('  已生成 %d / %d 页' % (i + 1, total))
        return total
    finally:
        doc.close()


def convert(pptx, deckdir, width=2560, force=True, timeout=600, log=None):
    """pptx → deck/pNN.png，返回页数。任何失败都抛 RuntimeError（带人话原因）。"""
    log = log or (lambda _m: None)
    n = count_slides(pptx)
    log('识别到 %d 页' % n)

    tmp = tempfile.mkdtemp(prefix='deck_pdf_')
    try:
        pdf = pdf_from_pptx(pptx, tmp, timeout=timeout, log=log)
        pages = pages_from_pdf(pdf, deckdir, width=width, force=force, log=log)
        if pages != n:
            log('注意：PDF 页数(%d) 与 pptx 页数(%d) 不一致，以 PDF 为准' % (pages, n))
        return pages
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── 极简后台任务表 ──────────────────────────────────────────────────────
# 转换要十几秒到一分钟，塞在请求里会让浏览器一直转圈；所以「发布」立刻返回，
# 转换丢进线程，前端轮询 /api/story/admin/job/<jid> 看进度。
# 只活在内存里：Flask 一重启任务表就空，符合「本来也没在跑」的事实。

_JOBS = {}
_JOB_ORDER = []
_LOCK = threading.Lock()
MAX_JOBS = 40
MAX_LOG = 120


def job_start(name, sid, target):
    """起一个后台任务。target(log) 的返回值进 result；抛异常则记 error。"""
    jid = uuid.uuid4().hex[:12]
    job = {'jid': jid, 'name': name, 'sid': sid, 'state': 'running',
           'log': [], 'result': None, 'error': None,
           'started': time.strftime('%H:%M:%S'), 'ended': None}
    with _LOCK:
        _JOBS[jid] = job
        _JOB_ORDER.append(jid)
        while len(_JOB_ORDER) > MAX_JOBS:
            _JOBS.pop(_JOB_ORDER.pop(0), None)

    def log(line):
        with _LOCK:
            job['log'].append(str(line))
            del job['log'][:-MAX_LOG]

    def run():
        try:
            job['result'] = target(log)
            job['state'] = 'done'
        except Exception as e:                       # noqa: BLE001 —— 线程里必须兜住
            job['error'] = str(e)
            job['state'] = 'error'
            log('❌ ' + str(e))
        finally:
            job['ended'] = time.strftime('%H:%M:%S')

    threading.Thread(target=run, daemon=True, name='deck-%s' % jid).start()
    return job


def job_get(jid):
    with _LOCK:
        j = _JOBS.get(jid)
        return dict(j, log=list(j['log'])) if j else None


def jobs_of(sid):
    """某个汇报最近的任务（新的在前）—— 页面刷新后还能看到刚才那次的进度。"""
    with _LOCK:
        out = [dict(_JOBS[i], log=list(_JOBS[i]['log']))
               for i in reversed(_JOB_ORDER) if _JOBS.get(i, {}).get('sid') == sid]
    return out
