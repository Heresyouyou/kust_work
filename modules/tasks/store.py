# -*- coding: utf-8 -*-
"""任务打卡 · 角色与工作台数据的服务端存储（落盘 JSON）。

kust.work 是内网域名、无需登录：同一团队成员共用一个角色清单，
浏览器用 cookie 记住「我上次在哪个角色」，因此不同机器各看自己的角色，
但也能互相切到对方的角色上查看同一份数据。

为什么是 JSON 而不是 sqlite：
    角色数是十几个量级，条目数几千量级，整份快照读写完全够用；
    而且这份文件要能直接进 git 看 diff ——「谁什么时候建的角色」一眼可见。

并发模型（与 story/store.py 一致）：
    单进程 Flask（threaded）→ 一把 RLock 串行化「读-改-写」，
    写盘走「同目录临时文件 + os.replace」，断电/并发都不会留半个文件。

同步模型（客户端推整份快照，服务端保证不丢数据）：
    · 客户端带 `rev` 上来；`rev` 与库里一致 → 直接采纳（单写者快路径）
    · `rev` 落后 → 按 `_id` 求并集（同 id 以客户端为准），避免两端同时写时互相覆盖
    · 客户端把「本地已删除的 id」一起带上来 → 落进墓碑，防止另一端手里的旧快照把它复活
    · 墓碑保留 30 天后回收（够覆盖任何真实的写冲突窗口）

示例角色（见文件末「示例角色」一节）：
    库里固定保留一个 `demo` 角色供参考，但**不写 cookie、也不自动进入** ——
    浏览器 cookie 为空时照旧弹「创建角色」引导，示例角色只是出现在弹窗的角色列表里，
    由用户自己决定要不要点进去看。
"""
import copy
import datetime
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

_DIR = Path(__file__).resolve().parent
DATA_DIR = _DIR / 'data'
STORE = DATA_DIR / 'store.json'

SCHEMA_VERSION = 2

# 一个角色下允许被同步的数据键（tab / view / form_draft 属于「本机视图状态」，不参与同步，
# 否则同一个人在两台机器上一个在看桌宠、一个在看时间轴都会互相打架）
SYNC_KEYS = ('nodes', 'checkins', 'badges', 'focus', 'pet',
             'goals', 'goalLogs', 'goalNotes')   # 后三个属于「学习目标」视图

# 容量闸门：防止前端出 bug 时撑爆 JSON（内网也不该无上限）
MAX_ROLES = 80
MAX_LIST = 4000          # 单个列表类集合（nodes / checkins / focus）的最大条数
MAX_STR = 4000           # 单个字符串字段的最大长度
MAX_DEPTH = 8            # JSON 嵌套深度
TOMB_KEEP_DAYS = 30

COOKIE_NAME = 'kust_tasks_role'
COOKIE_MAX_AGE = 31536000   # 1 年

AVATARS = ('🎓', '🐱', '🐶', '🐼', '🦊', '🐯', '🐰', '🐻', '🦉', '🐧',
           '🚀', '⚔️', '🔮', '🏆', '🌱', '🍀')

# 示例角色：固定 id，库里常驻一份，供新同学参考格式（不自动进入）
DEMO_ID = 'demo'
DEMO_NAME = '示例角色 · 参考'
DEMO_AVATAR = '🎓'
DEMO_SEED_FLAG = 'demoSeeded'      # 仅作「已播种过」的记录（方便运维看 json）；
                                   # 行为上不靠它判断 —— 见 ensure_demo()：只要角色缺失就补回

_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$')

_lock = threading.RLock()


class StoreError(Exception):
    """带 HTTP 语义的业务错误（路由层直接转成 JSON 错误体）。"""

    def __init__(self, message, status=400, code='bad_request'):
        super(StoreError, self).__init__(message)
        self.message = message
        self.status = status
        self.code = code


# ── 底层读写 ────────────────────────────────────────────────────────────

def _empty_doc():
    return {'version': SCHEMA_VERSION, 'updated_at': _now_iso(), 'roles': []}


def _now_iso():
    return time.strftime('%Y-%m-%dT%H:%M:%S')


# 路由层要拿它做时间戳（对外暴露一个不带下划线的别名）
now_iso = _now_iso


def _load():
    """读盘。文件不存在 / 损坏都按空库处理（不是错误，首次启动就是这样）。"""
    try:
        raw = STORE.read_text(encoding='utf-8')
    except OSError:
        return _empty_doc()
    if not raw.strip():
        return _empty_doc()
    try:
        doc = json.loads(raw)
    except ValueError:
        # 损坏：留个 .broken 现场再重来，不静默丢掉用户数据
        try:
            STORE.replace(STORE.with_suffix('.json.broken-%s' % time.strftime('%Y%m%d_%H%M%S')))
        except OSError:
            pass
        return _empty_doc()
    if not isinstance(doc, dict) or not isinstance(doc.get('roles'), list):
        return _empty_doc()
    doc.setdefault('version', SCHEMA_VERSION)
    return doc


def _save(doc):
    """原子写：同目录临时文件 → os.replace，避免读到半个 JSON。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    doc['version'] = SCHEMA_VERSION
    doc['updated_at'] = _now_iso()
    payload = json.dumps(doc, ensure_ascii=False, indent=1) + '\n'
    fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), prefix='.store-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(payload)
        os.replace(tmp, STORE)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# ── 清洗与合并 ──────────────────────────────────────────────────────────

def _clean(value, depth=0):
    """把任意来自前端的 JSON 收成一个「安全、可序列化、有长度上限」的副本。"""
    if depth > MAX_DEPTH:
        return None
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_STR]
    if isinstance(value, list):
        out = []
        for item in value[:MAX_LIST]:
            cleaned = _clean(item, depth + 1)
            if cleaned is not None:
                out.append(cleaned)
        return out
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                continue
            cleaned = _clean(item, depth + 1)
            if cleaned is not None:
                out[key[:64]] = cleaned
        return out
    return None


def _clean_data(raw):
    """只保留白名单里的同步键，且保证类型正确（空/缺省给安全默认值）。"""
    raw = raw if isinstance(raw, dict) else {}
    data = {}
    for key in SYNC_KEYS:
        value = _clean(raw.get(key))
        if key in ('nodes', 'checkins', 'focus', 'goals', 'goalLogs'):
            data[key] = value if isinstance(value, list) else []
            data[key] = [x for x in data[key] if isinstance(x, dict)]
            data[key] = data[key][:MAX_LIST]
        else:                                   # badges / pet
            data[key] = value if isinstance(value, dict) else {}
    return data


def _merge_list(server_items, client_items):
    """按 `_id` 求并集：客户端顺序为主，同 id 以客户端为准，服务端独有的追加在后。"""
    merged = []
    seen = set()
    for item in client_items:
        item_id = item.get('_id')
        if item_id:
            seen.add(item_id)
        merged.append(item)
    for item in server_items:
        item_id = item.get('_id')
        if item_id and item_id in seen:
            continue
        merged.append(item)
    # 无 _id 的条目（理论上不该有）也要保住，去重交给上面的 seen
    return merged


def _prune_tomb(tomb):
    cutoff = time.time() - TOMB_KEEP_DAYS * 86400
    for key in ('nodes', 'checkins'):
        bucket = tomb.get(key)
        if not isinstance(bucket, dict):
            tomb[key] = {}
            continue
        tomb[key] = {k: v for k, v in bucket.items() if isinstance(v, (int, float)) and v >= cutoff}


def _blank_role(role_id, name, avatar):
    return {
        'id': role_id,
        'name': name,
        'avatar': avatar,
        'created_at': _now_iso(),
        'updated_at': _now_iso(),
        'rev': 0,
        'data': _clean_data({}),
        'tomb': {'nodes': {}, 'checkins': {}},
    }


def _public(role):
    """角色对外视图（不含大块数据）。"""
    data = role.get('data') or {}
    role_id = role.get('id')
    return {
        'id': role_id,
        'name': role.get('name') or '未命名角色',
        'avatar': role.get('avatar') or AVATARS[0],
        'demo': role_id == DEMO_ID,          # 前端据此打「示例」标记，并避免误当成自己的角色
        'createdAt': role.get('created_at') or '',
        'updatedAt': role.get('updated_at') or '',
        'rev': int(role.get('rev') or 0),
        'nodes': len(data.get('nodes') or []),
        'checkins': len(data.get('checkins') or []),
    }


def _find(doc, role_id):
    for role in doc.get('roles') or []:
        if role.get('id') == role_id:
            return role
    return None


def _need(doc, role_id):
    if not role_id or not _ID_RE.match(str(role_id)):
        raise StoreError('角色 id 不合法', 400, 'bad_role_id')
    role = _find(doc, role_id)
    if role is None:
        raise StoreError('角色不存在或已被删除', 404, 'role_not_found')
    return role


def _new_id(doc):
    base = 'r%s' % format(int(time.time() * 1000), 'x')
    taken = {r.get('id') for r in doc.get('roles') or []}
    cand, salt = base, 0
    while cand in taken:
        salt += 1
        cand = '%s-%d' % (base, salt)
    return cand


# ── 对外 API ────────────────────────────────────────────────────────────

def list_roles():
    with _lock:
        doc = _load()
        roles = doc.get('roles') or []
        # 自己的角色按建号时间排前面，示例角色固定压到最后（参考物不抢位置，但要看得见）
        roles.sort(key=lambda r: (r.get('id') == DEMO_ID, r.get('created_at') or ''))
        return [_public(r) for r in roles]


def get_role(role_id):
    with _lock:
        return _public(_need(_load(), role_id))


def create_role(name=None, avatar=None):
    with _lock:
        doc = _load()
        roles = doc.setdefault('roles', [])
        if len(roles) >= MAX_ROLES:
            raise StoreError('角色数量已达上限（%d 个），请先删除不用的角色' % MAX_ROLES,
                             409, 'too_many_roles')
        clean_name = _clean(name)
        clean_name = (clean_name or '').strip() if isinstance(clean_name, str) else ''
        if not clean_name:
            raise StoreError('请先给角色起个名字', 400, 'name_required')
        if len(clean_name) > 16:
            clean_name = clean_name[:16]
        clean_avatar = avatar if avatar in AVATARS else AVATARS[0]
        role = _blank_role(_new_id(doc), clean_name, clean_avatar)
        roles.append(role)
        _save(doc)
        return _public(role)


def update_role(role_id, name=None, avatar=None):
    with _lock:
        doc = _load()
        role = _need(doc, role_id)
        if name is not None:
            clean_name = _clean(name)
            clean_name = (clean_name or '').strip() if isinstance(clean_name, str) else ''
            if not clean_name:
                raise StoreError('角色名不能为空', 400, 'name_required')
            role['name'] = clean_name[:16]
        if avatar is not None:
            if avatar not in AVATARS:
                raise StoreError('头像不在可选范围内', 400, 'bad_avatar')
            role['avatar'] = avatar
        role['updated_at'] = _now_iso()
        role['rev'] = int(role.get('rev') or 0) + 1
        _save(doc)
        return _public(role)


def delete_role(role_id):
    """删除角色并连带清掉它的全部数据（不可恢复）。允许删到 0 个 —— 前端会回到「创建角色」引导。"""
    with _lock:
        doc = _load()
        role = _need(doc, role_id)
        doc['roles'] = [r for r in (doc.get('roles') or []) if r.get('id') != role.get('id')]
        _save(doc)
        return _public(role)


def get_state(role_id):
    with _lock:
        role = _need(_load(), role_id)
        return {
            'rev': int(role.get('rev') or 0),
            'updatedAt': role.get('updated_at') or '',
            'data': _clean_data(role.get('data')),
        }


def put_state(role_id, rev, data, removed=None):
    """整份快照回写。返回落库后的最终状态（含服务端合并结果），客户端拿到后直接覆盖本地缓存。"""
    with _lock:
        doc = _load()
        role = _need(doc, role_id)
        server_rev = int(role.get('rev') or 0)
        incoming = _clean_data(data)
        removed = removed if isinstance(removed, dict) else {}

        try:
            client_rev = int(rev)
        except (TypeError, ValueError):
            client_rev = -1

        merged = False
        if client_rev == server_rev:
            new_data = incoming
        else:
            merged = True
            server_data = _clean_data(role.get('data'))
            new_data = dict(incoming)
            for key in ('nodes', 'checkins', 'goals', 'goalLogs'):
                new_data[key] = _merge_list(server_data.get(key) or [], incoming.get(key) or [])
            for key in ('badges', 'focus', 'pet'):
                # 这三类没有稳定 id，无法逐条合并：谁后写谁生效
                new_data[key] = incoming.get(key) or server_data.get(key) or {}

        tomb = role.setdefault('tomb', {'nodes': {}, 'checkins': {}})
        now = time.time()
        for key, data_key in (('nodes', 'nodes'), ('checkins', 'checkins')):
            gone = removed.get(key)
            gone = gone if isinstance(gone, list) else []
            for item_id in gone[:MAX_LIST]:
                if isinstance(item_id, str) and item_id:
                    tomb.setdefault(key, {})[item_id[:64]] = now
        _prune_tomb(tomb)

        for key in ('nodes', 'checkins'):
            dead = set((tomb.get(key) or {}).keys())
            if dead:
                new_data[key] = [x for x in new_data[key] if x.get('_id') not in dead]

        role['data'] = new_data
        role['rev'] = server_rev + 1
        role['updated_at'] = _now_iso()
        _save(doc)
        return {
            'rev': role['rev'],
            'updatedAt': role['updated_at'],
            'merged': merged,
            'staleRev': client_rev if merged else None,
            'data': _clean_data(role['data']),
        }


def stats():
    with _lock:
        doc = _load()
        roles = doc.get('roles') or []
        return {
            'roles': len(roles),
            'demo': _find(doc, DEMO_ID) is not None,
            'store': str(STORE),
            'exists': STORE.exists(),
            'bytes': STORE.stat().st_size if STORE.exists() else 0,
        }


def clear_all():
    """清空全部角色与数据（保留原子写语义）。仅给验收脚本/运维用，不挂路由。"""
    with _lock:
        _save(_empty_doc())


# ── 示例角色（参考样例） ────────────────────────────────────────────────
#
# 为什么放服务端播种、而不是前端播种：
#   前端播种跟着浏览器走 —— 换台机器打开又变成空白，各人看到的样例还不一样。
#   放服务端，一个团队共用同一份参考样例，点进去就能看到「填满的」工作台长什么样。
#
# 为什么不写 cookie、不自动进入：
#   产品要求是「保留一个示例角色用于参考，但不要默认进入」。所以它只是一条普通记录，
#   出现在「创建角色」弹窗的角色列表里，等用户自己点进去；cookie 为空时照旧弹创建引导。

DEMO_BASELINE_YEAR = 2026      # 下列日程写在 2026 年，跨年后整体平移年份（见 _demo_data）

# 五条真实日程（报名网站与日期均取自各考试官方安排），覆盖：已报名×3 + 未报名×2、
# 通用证书×4 + 专业赛事×1、报名进行中 / 准考证进行中 / 考试临近 / 查分临近 各态齐全
DEMO_NODES = (
    {
        'name': '中小学教师资格考试（笔试）', 'short': '教资', 'type': '通用证书',
        'site': 'https://ntce.neea.edu.cn', 'siteName': '中小学教师资格考试网',
        'place': '本校教务处 · 大学生活动中心 201', 'fee': 140,
        'regStart': '2026-07-04', 'regEnd': '2026-07-11',
        'tickStart': '2026-10-19', 'tickEnd': '2026-10-30',
        'exam': '2026-10-31', 'score': '2026-12-09', 'registered': True,
        'plan': [
            {'month': '2026-08', 'subject': '综合素质', 'focus': '职业理念 + 法律法规 · 选择题高频考点'},
            {'month': '2026-09', 'subject': '教育知识与能力', 'focus': '德育原则 · 学习理论 · 简答辨析高频'},
            {'month': '2026-10', 'subject': '真题冲刺', 'focus': '近 5 年真题套卷 · 材料分析题模板'},
        ],
    },
    {
        'name': '全国计算机等级考试二级', 'short': '计算机二级', 'type': '通用证书',
        'site': 'https://ncre-bm.neea.cn', 'siteName': 'NCRE 报名系统',
        'place': '第 3 机房楼 B302', 'fee': 80,
        'regStart': '2026-06-20', 'regEnd': '2026-07-05',
        'tickStart': '2026-09-15', 'tickEnd': '2026-09-25',
        'exam': '2026-09-26', 'score': '2026-11-10', 'registered': True,
        'plan': [
            {'month': '2026-08', 'subject': '选择题', 'focus': '公共基础知识 500 题 · 每日 50 题'},
            {'month': '2026-09', 'subject': '操作题', 'focus': 'Word / Excel / PPT 真题套卷 · 每日 1 套'},
        ],
    },
    {
        'name': '大学英语四级考试（CET-4）', 'short': '四级', 'type': '通用证书',
        'site': 'https://cet-bm.neea.edu.cn', 'siteName': 'CET 报名系统',
        'place': '全国大学英语四六级考试报名网', 'fee': 30,
        'regStart': '2026-09-15', 'regEnd': '2026-09-25',
        'tickStart': '2026-12-08', 'tickEnd': '2026-12-18',
        'exam': '2026-12-19', 'score': '2027-02-25', 'registered': True,
        'plan': [
            {'month': '2026-09', 'subject': '词汇', 'focus': '高频词汇 List 1-20 · 每日 100 词打卡'},
            {'month': '2026-10', 'subject': '听力', 'focus': '新闻听力 + 长对话 · 每日精听 1 套'},
            {'month': '2026-11', 'subject': '阅读 + 翻译', 'focus': '仔细阅读题型技巧 · 段落翻译真题'},
            {'month': '2026-12', 'subject': '写作 + 模考', 'focus': '写作模板 3 套 · 全真模考 2 次'},
        ],
    },
    {
        'name': '普通话水平测试', 'short': '普通话', 'type': '通用证书',
        'site': 'https://www.cltt.org', 'siteName': '国家普通话水平测试网',
        'place': '文学院语言实验室 304', 'fee': 50,
        'regStart': '2026-10-10', 'regEnd': '2026-10-17',
        'tickStart': '2026-11-07', 'tickEnd': '2026-11-13',
        'exam': '2026-11-14', 'score': '2026-12-14', 'registered': False,
        'plan': [
            {'month': '2026-10', 'subject': '朗读作品', 'focus': '60 篇作品朗读 · 轻声与儿化音专项'},
            {'month': '2026-11', 'subject': '命题说话', 'focus': '30 个话题提纲 · 3 分钟计时训练'},
        ],
    },
    {
        'name': '全国大学生广告艺术大赛', 'short': '大广赛', 'type': '专业赛事',
        'site': 'https://www.sun-aoa.org', 'siteName': '大广赛官网',
        'place': '线上提交 · 学校创新创业中心备案', 'fee': 0,
        'regStart': '2026-03-07', 'regEnd': '2026-05-11',
        'tickStart': '2026-05-21', 'tickEnd': '2026-05-31',
        'exam': '2026-06-20', 'score': '2026-09-03', 'registered': False,
        'plan': [
            {'month': '2026-03', 'subject': '选题 + 策略单', 'focus': '研读命题策略单 · 确定创意方向'},
            {'month': '2026-04', 'subject': '创意执行', 'focus': '平面 / 视频脚本产出初稿'},
            {'month': '2026-05', 'subject': '打磨提交', 'focus': '成品精修 · 规范提交材料'},
        ],
    },
)

# 打卡记录：(节点名, 距今天几天, 科目, 备注)
# 教资连打 4 天 → 点亮「三日之约」，累计 10 次 → 点亮「十次修行」（徽章由前端按数据自动推导）
DEMO_CHECKINS = (
    ('中小学教师资格考试（笔试）', 1, '综合素质', '职业理念材料分析 2 题 + 错题复盘'),
    ('中小学教师资格考试（笔试）', 2, '综合素质', '法律法规选择题 60 道'),
    ('中小学教师资格考试（笔试）', 3, '教育知识与能力', '德育原则简答 5 条 · 背诵默写'),
    ('中小学教师资格考试（笔试）', 4, '综合素质', '文化素养常识梳理'),
    ('全国计算机等级考试二级', 5, '操作题', 'Excel 函数与图表真题 1 套'),
    ('全国计算机等级考试二级', 6, '操作题', 'Word 长文档排版真题 1 套'),
    ('全国计算机等级考试二级', 7, '选择题', '公共基础知识 50 题'),
    ('大学英语四级考试（CET-4）', 8, '词汇', '高频词汇 List 11-12 · 100 词'),
    ('大学英语四级考试（CET-4）', 9, '词汇', '高频词汇 List 9-10 · 100 词'),
    ('普通话水平测试', 10, '朗读作品', '作品 12 号朗读 3 遍 · 录音回听'),
)

# 番茄专注记录：(标签, 距今天几天, 分钟)
DEMO_FOCUS = (
    ('教资', 1, 25),
    ('教资', 1, 25),
    ('计算机二级', 2, 25),
    ('四级', 5, 25),
    ('普通话', 8, 25),
)

# 匹配「2026-07-04」或「2026-07」这类日期串（示例数据里没有带年份的 URL）
_DEMO_DATE_RE = re.compile(r'\b(19|20)\d{2}-(\d{2})(?:-(\d{2}))?\b')


def _shift_years(value, delta):
    """把数据里的年份整体平移 delta 年，让示例日程跨年后依旧落在合理月份。"""
    if not delta:
        return value
    if isinstance(value, str):
        def repl(match):
            year = int(match.group(0)[:4]) + delta
            tail = match.group(0)[4:]
            return '%d%s' % (year, tail)
        return _DEMO_DATE_RE.sub(repl, value)
    if isinstance(value, list):
        return [_shift_years(item, delta) for item in value]
    if isinstance(value, dict):
        return {key: _shift_years(item, delta) for key, item in value.items()}
    return value


def _demo_data():
    """示例角色的初始快照。

    两种时间策略：
      · 任务日程写死在 DEMO_BASELINE_YEAR，播种时按当前年整体平移 —— 月份日号不变，
        所以「教资笔试 10 月底」「四级 12 月中」这些规律跨年仍然成立；
      · 打卡 / 专注 / 桌宠用「距今天 N 天」生成 —— 任何时候播种，看起来都是最近刚练过。
    """
    today = datetime.date.today()
    delta = max(0, today.year - DEMO_BASELINE_YEAR)

    nodes = []
    for idx, raw in enumerate(DEMO_NODES):
        item = _shift_years(copy.deepcopy(dict(raw)), delta)
        item['_id'] = 'demo-n%d' % (idx + 1)
        nodes.append(item)

    checkins = []
    for idx, (node, back, subject, note) in enumerate(DEMO_CHECKINS):
        day = today - datetime.timedelta(days=back)
        checkins.append({
            '_id': 'demo-c%d' % (idx + 1),
            'node': node,
            'date': day.strftime('%Y-%m-%d'),
            'subject': subject,
            'stage': day.strftime('%Y-%m'),      # 前端按月分组显示，这里预对齐
            'note': note,
        })

    focus = []
    for idx, (tag, back, minutes) in enumerate(DEMO_FOCUS):
        day = today - datetime.timedelta(days=back)
        focus.append({
            '_id': 'demo-f%d' % (idx + 1),
            'tag': tag,
            'date': day.strftime('%Y-%m-%d'),
            'minutes': minutes,
        })

    return {
        'nodes': nodes,
        'checkins': checkins,
        'badges': {},                                                  # 徽章由前端按打卡数据推导
        'focus': focus,
        'pet': {'fed': 3, 'pat': 5, 'last': today.strftime('%Y-%m-%d')},
    }


def ensure_demo(force=False):
    """确保库里存在示例角色（幂等，启动时调用）。

    force=False：已存在就原样返回 —— 用户可能已经在示例角色里试改过东西，不覆盖
    force=True ：用初始数据重置（角色已被删则重建）—— 「恢复示例角色」按钮用

    存在性语义：「库里要有一个可参考的样例」是产品要求，所以**角色缺失就补回**，
    而不是只播种一次。删掉它 → 下次重启（或点「恢复示例角色」）会重新出现。

    注意：无论哪条路径都**不碰 cookie**，示例角色永远只是个可选项。
    """
    with _lock:
        doc = _load()
        roles = doc.setdefault('roles', [])
        existing = _find(doc, DEMO_ID)

        if existing is not None and not force:
            return {'created': False, 'reset': False, 'role': _public(existing)}

        if existing is not None:
            existing['data'] = _clean_data(_demo_data())
            # 重置必须清墓碑：旧墓碑里存着 demo-n*/demo-c* 这些固定 id，
            # 不清掉的话刚写进去的示例条目会立刻被自己的墓碑过滤掉（=重建出空壳）
            existing['tomb'] = {'nodes': {}, 'checkins': {}}
            existing['name'] = DEMO_NAME
            existing['avatar'] = DEMO_AVATAR
            existing['rev'] = int(existing.get('rev') or 0) + 1
            existing['updated_at'] = _now_iso()
            _save(doc)
            return {'created': False, 'reset': True, 'role': _public(existing)}

        if len(roles) >= MAX_ROLES:
            raise StoreError('角色数量已达上限（%d 个），请先删除不用的角色' % MAX_ROLES,
                             409, 'too_many_roles')
        role = _blank_role(DEMO_ID, DEMO_NAME, DEMO_AVATAR)
        role['data'] = _clean_data(_demo_data())
        # rev 从 1 起：它已经有 1 版内容。若留 0，客户端「rev=0 且本地无缓存」的首拉
        # 会命中 GET /state 的 changed:false 快路径，直接拉回一版空数据（看板全空）。
        role['rev'] = 1
        roles.append(role)
        doc[DEMO_SEED_FLAG] = True      # 记录用，不参与判断
        _save(doc)
        return {'created': True, 'reset': False, 'role': _public(role)}
