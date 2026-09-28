# -*- coding: utf-8 -*-
"""任务打卡模块（游戏化备考工作台 · 多角色 · 团队共享）

面向证书考试与专业赛事：任务时间轴（报名 / 准考证 / 考试 / 查分四节点 + 倒计时）
+ 阶段任务打卡（按月科目 / 重点 / 完成率 / 补卡）+ 番茄钟与学习标签
+ 桌宠 / 经验等级 / 章节进度 / 连续打卡徽章。

多角色：
    一个浏览器用 cookie（kust_tasks_role）记住「我上次在哪个角色」，
    角色清单与角色数据存在服务端（modules/tasks/data/store.json），
    kust.work 是内网域名、无需登录 —— 同一团队成员之间可以互相切到对方的角色查看同一份数据。

示例角色：
    库里常驻一个 `demo` 角色（「示例角色 · 参考」）供新同学对照，但它**不写 cookie**、
    也不出现在任何自动跳转里 —— cookie 为空时照旧弹「创建角色」引导，示例角色只是
    列在弹窗的角色清单中等人自己点进去。

页面：
    GET  /tasks                            工作台页面
接口：
    GET  /api/tasks/assets/<path:rel>      前端脚本与样式
    GET  /api/tasks/roles                  角色清单
    POST /api/tasks/roles                  新建角色 {name, avatar}
    POST /api/tasks/roles/<id>/active      记住当前角色（写 cookie）
    PATCH/DELETE /api/tasks/roles/<id>     改名换头像 / 删除角色
    POST /api/tasks/demo                   恢复/重置示例角色（不写 cookie）
    GET  /api/tasks/state?role=&rev=       读角色数据（rev 相同则只回 changed:false）
    PUT  /api/tasks/state                  整份快照回写（带 rev 与本地已删 id）
    GET  /api/tasks/health                 自检
"""
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_file

from portal.templating import render_html

from . import store

tasks_bp = Blueprint('tasks', __name__)

_DIR = Path(__file__).parent
_TEMPLATE = _DIR / 'templates' / 'tasks.html'
_ASSETS = (_DIR / 'assets').resolve()
_MIME = {
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.html': 'text/html; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
}


def init_tasks(app, **_ignored):
    """注册任务打卡模块（页面 + 角色/数据同步接口）。"""
    app.register_blueprint(tasks_bp)
    print('[模块] 任务打卡 → GET /tasks  ·  角色数据落盘 %s' % store.STORE)
    # 库里常驻一个示例角色供参考（幂等）；**不写 cookie**，所以不会有人被默认丢进它
    try:
        seeded = store.ensure_demo()
        print('[模块] 任务打卡 → 示例角色「%s」%s（仅供参考，不自动进入）'
              % (store.DEMO_NAME, '已重建' if seeded.get('created') else '已就位'))
    except Exception as exc:                      # 播种失败不该拖垮整个站点
        print('[模块] 任务打卡 → 示例角色播种失败（不影响使用）：%r' % (exc,))


# ── 页面与静态资源 ──────────────────────────────────────────────────────

@tasks_bp.route('/tasks')
def tasks_page():
    return render_html(_TEMPLATE)


@tasks_bp.route('/api/tasks/assets/<path:rel>')
def tasks_asset(rel):
    """白名单式静态服务：只允许 assets/ 目录下的文件，防目录穿越。"""
    try:
        target = (_ASSETS / rel).resolve()
        target.relative_to(_ASSETS)  # 越界会抛 ValueError
    except (ValueError, OSError):
        return Response('/* denied */\n', status=403, content_type='text/plain; charset=utf-8')

    if not target.is_file():
        return Response('/* not found: %s */\n' % rel,
                        status=404, content_type='text/plain; charset=utf-8')

    resp = send_file(target, conditional=True)
    # 直接赋 header，不用 mimetype=（Werkzeug 会再追加一个 charset，得到畸形头）
    resp.headers['Content-Type'] = _MIME.get(target.suffix.lower(), 'application/octet-stream')
    # 源码在迭代，改完立刻能刷出来：不走 after_request 的 1h 强缓存
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


# ── 同步接口 ────────────────────────────────────────────────────────────

def _fail(err):
    body = {'ok': False, 'code': err.code, 'error': err.message}
    return jsonify(body), err.status


def _cookied(payload, role_id):
    """把「当前角色」写进 cookie。改角色必须换 cookie，否则刷新会串台。"""
    resp = jsonify(payload)
    resp.set_cookie(store.COOKIE_NAME, role_id, max_age=store.COOKIE_MAX_AGE,
                    path='/', samesite='Lax')
    return resp


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@tasks_bp.route('/api/tasks/roles', methods=['GET'])
def tasks_roles():
    """角色清单。`active` 由 cookie 决定 —— 前端用它判断要不要弹「创建角色」。"""
    want = (request.args.get('role') or '').strip()
    if not want:
        want = (request.cookies.get(store.COOKIE_NAME) or '').strip()
    roles = store.list_roles()
    known = {r['id'] for r in roles}
    return jsonify({
        'ok': True,
        'roles': roles,
        'active': want if want in known else None,
        'servedAt': store.now_iso(),
    })


@tasks_bp.route('/api/tasks/roles', methods=['POST'])
def tasks_role_create():
    body = _body()
    try:
        role = store.create_role(body.get('name'), body.get('avatar'))
    except store.StoreError as err:
        return _fail(err)
    return _cookied({'ok': True, 'role': role, 'roles': store.list_roles()}, role['id'])


@tasks_bp.route('/api/tasks/roles/<role_id>/active', methods=['POST'])
def tasks_role_active(role_id):
    try:
        role = store.get_role(role_id)
    except store.StoreError as err:
        return _fail(err)
    return _cookied({'ok': True, 'role': role}, role['id'])


@tasks_bp.route('/api/tasks/roles/<role_id>', methods=['PATCH'])
def tasks_role_update(role_id):
    body = _body()
    try:
        role = store.update_role(role_id, body.get('name'), body.get('avatar'))
    except store.StoreError as err:
        return _fail(err)
    return jsonify({'ok': True, 'role': role, 'roles': store.list_roles()})


@tasks_bp.route('/api/tasks/roles/<role_id>', methods=['DELETE'])
def tasks_role_delete(role_id):
    try:
        role = store.delete_role(role_id)
    except store.StoreError as err:
        return _fail(err)
    resp = jsonify({'ok': True, 'removed': role, 'roles': store.list_roles()})
    # 删掉的正好是本机当前角色 → 顺手清掉 cookie，让下次刷新回到「创建角色」引导
    if (request.cookies.get(store.COOKIE_NAME) or '') == role_id:
        resp.delete_cookie(store.COOKIE_NAME, path='/')
    return resp


@tasks_bp.route('/api/tasks/demo', methods=['POST'])
def tasks_demo_restore():
    """恢复示例角色（参考样例）。

    body.force=false：不存在才建（被删了想找回来）
    body.force=true ：用初始示例数据重置（示例被改乱了想还原）
    两条路径都**不写 cookie** —— 恢复完还要用户自己去角色条点进去。
    """
    body = _body()
    try:
        result = store.ensure_demo(force=bool(body.get('force')))
    except store.StoreError as err:
        return _fail(err)
    return jsonify({'ok': True, 'roles': store.list_roles(), **result})


@tasks_bp.route('/api/tasks/state', methods=['GET'])
def tasks_state_get():
    role_id = (request.args.get('role') or request.cookies.get(store.COOKIE_NAME) or '').strip()
    try:
        state = store.get_state(role_id)
    except store.StoreError as err:
        return _fail(err)

    since = (request.args.get('rev') or '').strip()
    if since and since == str(state['rev']):
        # 轮询快路径：没变就只回一个 rev，不再传几万个字符的快照
        return jsonify({'ok': True, 'rev': state['rev'], 'changed': False,
                        'updatedAt': state['updatedAt']})
    return jsonify({'ok': True, 'changed': True, 'role': role_id,
                    'rev': state['rev'], 'updatedAt': state['updatedAt'],
                    'data': state['data']})


@tasks_bp.route('/api/tasks/state', methods=['PUT'])
def tasks_state_put():
    body = _body()
    role_id = (body.get('role') or request.cookies.get(store.COOKIE_NAME) or '').strip()
    try:
        result = store.put_state(role_id, body.get('rev'), body.get('data'),
                                 body.get('removed'))
    except store.StoreError as err:
        return _fail(err)
    return jsonify({'ok': True, 'role': role_id, **result})


@tasks_bp.route('/api/tasks/health')
def tasks_health():
    info = store.stats()
    info['ok'] = True
    return jsonify(info)
