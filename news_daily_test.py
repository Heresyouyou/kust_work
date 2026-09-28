# -*- coding: utf-8 -*-
"""「今日早报」海报取图回归测试（纯 Python，用 Flask test_client，无需浏览器）

历史故障（2026-09-14）：
  daily_run.py 推送完成后会删掉 output/ 里的海报工作副本（防微信客户端锁定），
  门户只扫 output/ → 当天海报被删后就回退到很早的遗留海报
  → 页面「海报一直是 9 月 8 日的」。

修复（两轮）：
  第一轮 —— daily_run.py 推送前另存到 daily_archive/posters/；门户两处都扫，
            并按 latest_daily 记录日期取图；_age_days 兼容 'YYYYMMDD'。
  第二轮 —— 海报改为**直接生成到 NAS 共享**
            （\\\\<nas-host>\\<share>
            本地 output/ 不再留存海报；NAS 不可达时自动降级本地。
            门户按 poster_store 给的顺序扫描（NAS 优先 → 本地兜底 → 历史遗留）。

用法：
  cd D:\\PostNews\\Translate && "D:\\Python 3.12.4\\python.exe" news_daily_test.py
"""
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import web_app                      # noqa: E402
from modules.news import routes as nr  # noqa: E402

PASS = FAIL = 0
FAILS = []


def check(ok, label, detail=''):
    global PASS, FAIL
    if ok:
        PASS += 1
        print('  ✔ ' + label)
    else:
        FAIL += 1
        FAILS.append(label)
        print('  ✘ ' + label + ('   → ' + detail if detail else ''))


def _load_fresh_poster_store(tag):
    """单独载入一份 poster_store，用于改配置做降级实验（不污染正式实例）。"""
    import importlib.util
    f = Path(nr._root()) / 'poster_store.py'
    spec = importlib.util.spec_from_file_location('poster_store_' + tag, str(f))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    client = web_app.app.test_client()
    root = nr._root()
    print('数据目录:', root)

    print('\n【日期解析（两种格式都要认）】')
    today = datetime.now()
    d8 = today.strftime('%Y%m%d')
    diso = today.strftime('%Y-%m-%d')
    check(nr._age_days(d8) == 0, "age_days('%s') == 0（8 位数字格式）" % d8,
          'got %r' % nr._age_days(d8))
    check(nr._age_days(diso) == 0, "age_days('%s') == 0（ISO 格式）" % diso,
          'got %r' % nr._age_days(diso))
    y = (today - timedelta(days=3)).strftime('%Y%m%d')
    check(nr._age_days(y) == 3, 'age_days(3 天前) == 3', 'got %r' % nr._age_days(y))
    check(nr._age_days('') is None, '空串 → None')
    check(nr._age_days('乱码') is None, '无法解析 → None（不抛异常）')
    check(nr._age_days(None) is None, 'None → None')

    print('\n【海报存储配置（poster_store，唯一定义处）】')
    ps = nr._load_poster_store()
    check(ps is not None, '门户能载入 PostNews 根的 poster_store.py')
    info = nr._poster_dir_hint()
    check(bool(info), '拿到存储快照: %s' % ('nas_ok=%s' % info.get('nas_ok')))
    check(info.get('nas_unc', '').startswith('\\\\'),
          'NAS 共享是 UNC 路径（不是 S: 盘符）: %s' % info.get('nas_unc'))
    check('Daily_Poster' in (info.get('nas_dir') or ''),
          'NAS 目录含 Daily_Poster: %s' % info.get('nas_dir'))
    check(bool(info.get('nas_ok')), 'NAS 当前可用（nas_ok）',
          'reason=%r' % info.get('reason'))
    check(str(info.get('write_dir', '')).startswith('\\\\'),
          '写入目录 = NAS（nas_ok=True 时）: %s' % info.get('write_dir'))

    print('\n【扫描目录顺序（NAS 优先，本地兜底）】')
    dirs = nr._poster_dirs()
    strs = [str(d) for d in dirs]
    check(len(dirs) >= 1, '扫描目录非空（%d 个）' % len(dirs))
    if dirs and info.get('nas_ok'):
        check(nr._is_remote(dirs[0]),
              '首个扫描目录就是 NAS（优先级最高）: %s' % dirs[0])
    check(any(not nr._is_remote(d) for d in dirs) or not info.get('nas_ok'),
          '本地兜底目录也在扫描列表里（NAS 掉线时仍能看图）')

    print('\n【is_remote 判定】')
    check(nr._is_remote('\\\\<nas-host>\\<share>') is True,
          'UNC 路径 → True')
    check(nr._is_remote(str(root / 'output' / 'x.png')) is False,
          '本地路径 → False')
    check(nr._is_remote(None) is False, 'None → False（不抛异常）')

    print('\n【按日期取图】')
    p_today = nr._poster_for_date8(d8)
    check(p_today is not None, '能取到当天海报（%s）' % d8,
          '未找到 → 说明当天海报缺失或没搬上 NAS')
    if p_today:
        check('daily_poster_' + d8 in p_today.name,
              '文件名含当天日期: %s' % p_today.name)
        check(nr._is_remote(p_today),
              '命中 NAS 上的持久海报（本地 output/ 已不再留海报）',
              'path=%s' % p_today)

    print('\n【最新海报 = 记录日期那天】')
    rec = nr._latest_record()
    rec8 = nr._date8(rec.get('date') or '')
    latest = nr._latest_poster()
    check(latest is not None, '取到最新海报')
    if latest and rec8:
        check(nr._date8(latest.name) == rec8,
              '最新海报日期 == latest_daily 记录日期（%s）' % rec8,
              '海报=%s 记录=%s' % (nr._date8(latest.name), rec8))

    print('\n【接口 /api/news/latest】')
    d = client.get('/api/news/latest').get_json() or {}
    check(d.get('ok') is True, 'ok == True')
    check(d.get('is_today') is True, 'is_today == True（原先恒为 False）',
          'got %r / age_days=%r' % (d.get('is_today'), d.get('age_days')))
    check(d.get('age_days') == 0, 'age_days == 0', 'got %r' % d.get('age_days'))
    check(bool(d.get('poster_url')), 'poster_url 非空')
    check(bool(d.get('poster_name')), 'poster_name 非空')
    check(d.get('poster_is_record_date') is True,
          'poster_is_record_date == True（这张就是当天的）',
          'poster_name=%r' % d.get('poster_name'))
    if rec8:
        check(nr._date8(d.get('poster_name') or '') == rec8,
              'poster_name 日期 == 记录日期')
    check(d.get('poster_remote') is True,
          'poster_remote == True（这张图取自 NAS）',
          'poster_remote=%r' % d.get('poster_remote'))
    check((d.get('poster_store') or {}).get('nas_ok') is True,
          '接口里带存储快照 poster_store.nas_ok')

    print('\n【接口 /daily/poster】')
    r = client.get('/daily/poster')
    check(r.status_code == 200, 'HTTP 200', 'got %s' % r.status_code)
    body = r.get_data()
    check(len(body) > 10000, '返回了真实图片字节（%d B）' % len(body))
    check(body[:8] == b'\x89PNG\r\n\x1a\n', 'PNG 魔数正确')
    cd = r.headers.get('Content-Disposition') or ''
    check(('daily_poster_' + d8) in cd if d8 else True,
          '响应文件名是当天海报: %s' % cd)
    # 与 NAS 上的源文件逐字节等长（证明读的确实是 NAS 那份）
    if p_today and p_today.exists():
        check(len(body) == p_today.stat().st_size,
              '响应字节数 == NAS 源文件字节数（%d）' % p_today.stat().st_size,
              'got %d' % len(body))

    print('\n【接口 /api/daily/archive】')
    ents = (client.get('/api/daily/archive').get_json() or {}).get('entries') or []
    check(len(ents) > 0, '归档列表非空（%d 条）' % len(ents))
    if ents and rec8:
        top = ents[0]
        check(top.get('date8') == rec8, '首条是最新日期 %s' % rec8,
              'got %s' % top.get('date8'))
        check(bool(top.get('poster')), '首条带海报名: %s' % top.get('poster'))
    check(len(ents) >= 5, '历史海报仍可见（NAS 上留存，%d 条）' % len(ents))

    print('\n【★ 降级：NAS 不可达时必须退回本地，不能报错】')
    mod = _load_fresh_poster_store('dn')
    orig_host, orig_unc = mod.NAS_HOST, mod.NAS_UNC
    try:
        mod.NAS_HOST = '203.0.113.1'          # TEST-NET-3，必然不可达
        mod.NAS_UNC = r'\\203.0.113.1\NOPE'
        t0 = time.time()
        ok = mod.nas_available(force=True)
        dt = time.time() - t0
        check(ok is False, 'NAS 探测结果 = False', 'got %r' % ok)
        check(dt < 10, '探测在 10s 内返回（不会把页面拖死）: %.2fs' % dt)
        check(mod.write_dir() == mod.LOCAL_DIR,
              '写入降级到本地: %s' % mod.write_dir())
        rd = [str(x) for x in mod.read_dirs()]
        check(any(not x.startswith('\\\\') for x in rd),
              '读取目录仍含本地兜底: %s' % rd)
        check(mod.is_remote(mod.LOCAL_DIR) is False,
              '本地兜底目录不被判为远端（不会被误删）')
    finally:
        mod.NAS_HOST, mod.NAS_UNC = orig_host, orig_unc

    print('\n【★ 应急覆盖：POSTNEWS_POSTER_DIR 环境变量】')
    mod2 = _load_fresh_poster_store('env')
    tmp = Path(os.environ.get('TEMP') or '/tmp') / '_poster_store_probe'
    old = os.environ.get(mod2.ENV_VAR)
    try:
        os.environ[mod2.ENV_VAR] = str(tmp)
        check(mod2.write_dir() == str(tmp),
              '设了 %s 就走它: %s' % (mod2.ENV_VAR, mod2.write_dir()))
        check(mod2.nas_available(force=True) is True,
              '覆盖模式下不再探测 NAS（offline 演练可用）')
    finally:
        if old is None:
            os.environ.pop(mod2.ENV_VAR, None)
        else:
            os.environ[mod2.ENV_VAR] = old

    print('\n【海报文件名白名单（防路径穿越）】')
    check(nr._POSTER_RE.match('daily_poster_20260914.png') is not None, '合法名通过')
    check(nr._POSTER_RE.match('daily_poster_20260914_120000.png') is not None,
          '带时间戳后缀也通过')
    check(nr._POSTER_RE.match('../evil.png') is None, '路径穿越被拒')
    check(nr._POSTER_RE.match('daily_poster_2026091.png') is None, '日期位数不足被拒')

    print('\n' + '=' * 62)
    print('  结果：%d 通过 / %d 失败' % (PASS, FAIL))
    if FAILS:
        print('  失败项：')
        for f in FAILS:
            print('    · ' + f)
    print('=' * 62)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
