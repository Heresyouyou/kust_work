# -*- coding: utf-8 -*-
"""任务队列模块 —— 一次看三条队列

1) 统一任务队列（当前文件队列）
   数据在 web_app.py 的内存 `jobs` / `QUEUE` 里，**翻译与图像拼合共用同一条
   串行通道**（拼合也吃 GPU，两条独立通道互不相识时会互相抢显存/内存）。
   本模块**不反向 import web_app** —— 那会造成循环导入（web_app → modules →
   web_app）。改用和门户一致的「注入回调」模式：web_app 把自己的只读快照函数
   `_queue_snapshot` 通过 `init_queue(app, get_queue=...)` 传进来。
   模块侧只读，绝不改队列状态。

2) 标题翻译队列
   数据来自 journal_title_zh.py 离线跑批落盘的 journal_title_zh_status.json
   （键：state / progress / coverage_pct / cached / pending / recent_failed ...）。

页面：GET /queue （旧入口 /translate/queue 302 跳转，书签不失效）
接口：GET /api/queue  → {'ok', 'doc': {...}, 'title': {...}}
      （旧 /api/translate/queue 保留同义）
兼容：/translate/title-status 302 → /queue
"""
import json
from pathlib import Path

from flask import Blueprint, jsonify, redirect

from portal.templating import render_html

queue_bp = Blueprint('queue', __name__)

_DIR = Path(__file__).parent
_TEMPLATE = _DIR / 'templates' / 'queue.html'

_deps = {'data_dir': None, 'get_queue': None}


def init_queue(app, data_dir=None, get_queue=None):
    """注册任务队列模块。

    Args:
        data_dir:   PostNews 根目录（读 journal_title_zh_status.json）
        get_queue:  web_app 注入的统一任务队列只读快照函数（可为空 → 页面显示不可用）
    """
    _deps['data_dir'] = Path(data_dir) if data_dir else None
    _deps['get_queue'] = get_queue
    app.register_blueprint(queue_bp)
    print('[模块] 任务队列 → GET /queue')


# ── 数据源 ────────────────────────────────────────────────

def _read_doc_queue():
    """统一任务队列快照（注入回调；没注入或抛错都降级为空态，不影响页面）"""
    fn = _deps.get('get_queue')
    if not callable(fn):
        return {'ok': False, 'unavailable': True, 'queue_size': 0,
                'stats': {}, 'items': [],
                'message': '未接入统一任务队列（web_app 未注入快照）'}
    try:
        d = fn()
        if isinstance(d, dict):
            d.setdefault('items', [])
            d.setdefault('stats', {})
            return d
        return {'ok': False, 'queue_size': 0, 'stats': {}, 'items': []}
    except Exception as e:
        return {'ok': False, 'queue_size': 0, 'stats': {}, 'items': [],
                'error': str(e)}


def _read_title_status():
    """标题翻译状态（journal_title_zh_status.json）"""
    root = _deps.get('data_dir')
    if not root:
        return {'ok': True, 'state': 'never', 'running': False,
                'message': '未配置数据目录'}
    p = Path(root) / 'journal_title_zh_status.json'
    try:
        if p.exists():
            s = json.loads(p.read_text(encoding='utf-8'))
            s.setdefault('ok', True)
            s['running'] = (s.get('state') == 'running')
            return s
    except Exception as e:
        return {'ok': False, 'state': 'error', 'running': False,
                'message': '标题状态文件解析失败：%s' % e}

    return {'ok': True, 'state': 'never', 'running': False,
            'message': '尚未运行过标题翻译（journal_title_zh.py）',
            'coverage_pct': None, 'cached': None, 'pending': None,
            'translated_this_run': None, 'failed_this_run': None}


# ── 路由 ──────────────────────────────────────────────────

@queue_bp.route('/queue')
def queue_page():
    return render_html(_TEMPLATE)


@queue_bp.route('/api/queue')
def api_queue():
    doc = _read_doc_queue()
    title = _read_title_status()
    active = bool((doc.get('stats') or {}).get('active')) or bool(title.get('running'))
    return jsonify({'ok': True, 'doc': doc, 'title': title, 'active': active})


# ── 旧入口兼容 ────────────────────────────────────────────

@queue_bp.route('/translate/queue')
def legacy_queue_page():
    return redirect('/queue', code=302)


@queue_bp.route('/api/translate/queue')
def legacy_api_queue():
    """旧前端/书签用的接口，与 /api/queue 同义。"""
    return api_queue()


@queue_bp.route('/translate/title-status')
def legacy_title_status_page():
    return redirect('/queue', code=302)


@queue_bp.route('/api/translate/title-status')
def legacy_api_title_status():
    return jsonify(_read_title_status())
