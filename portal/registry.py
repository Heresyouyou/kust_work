# -*- coding: utf-8 -*-
"""kust.work 门户 · 功能模块注册表

加一个新功能入口 = 往 MODULES 追加一条 dict。
不需要动 portal.html、不需要动 portal/routes.py。

字段说明：
  id      唯一标识（英文小写）。前端 JS 靠它定位卡片，如 data-mod="daily"
  group   分组名，同组卡片聚在一起，按首次出现顺序排列
  name    卡片标题
  desc    一句话说明（建议 ≤ 26 字，超出会占满两行）
  url     入口路径；status='coming' 时可留 None
  tag     右上角角标文案（可被前端 JS 动态改写）
  status  online（可点击）| beta（可点击，琥珀角标）| coming（置灰不可点）
  accent  True = 主推卡片（绿色描边高亮）
  live    True = 该卡片的内容由前端 JS 动态绑定（如今日早报的飞书链接）

排序规则：列表顺序即显示顺序，主推卡片请放在所在分组第一位。
"""

# ── 分组顺序 = 下面的首次出现顺序 ──
MODULES = [
    # ═══════════ 资讯与推送 ═══════════
    {
        'id': 'daily',
        'group': '资讯与推送',
        'name': '今日早报',
        'desc': '每日地矿资讯海报与新闻列表，点击查看',
        'url': '/daily',
        'tag': '今日',
        'status': 'online',
        'accent': True,
        'live': True,
    },
    {
        'id': 'dailyarchive',
        'group': '资讯与推送',
        'name': '早报归档',
        'desc': '按日期回看历史每日早报：新闻列表 + 日报海报',
        'url': '/daily/archive',
        'tag': '新增',
        'status': 'online',
    },
    {
        'id': 'pushlog',
        'group': '资讯与推送',
        'name': '推送日志',
        'desc': '查看自动推送任务的执行状态与失败重试',
        'url': None,
        'tag': '规划中',
        'status': 'coming',
    },

    # ═══════════ 科研工具 ═══════════
    {
        'id': 'journals',
        'group': '科研工具',
        'name': '期刊更新',
        'desc': '目标期刊首页直达，含本地抓取的最新文章链接',
        'url': '/journals',
        'tag': '可用',
        'status': 'online',
        'live': True,
        'accent': True,
    },
    {
        'id': 'translate',
        'group': '科研工具',
        'name': '文献翻译',
        'desc': '上传英文地学论文 PDF，输出中文对照译文',
        'url': '/translate',
        'tag': '可用',
        'status': 'online',
        'accent': True,
    },
    {
        'id': 'reader',
        'group': '科研工具',
        'name': 'PDF 阅读器',
        'desc': '自研内核：原文与译文左右同步滚动、同步缩放',
        'url': '/reader',
        'tag': '新增',
        'status': 'online',
    },
    {
        'id': 'palette',
        'group': '科研工具',
        'name': '科研配色',
        'desc': '顶刊与地学标准配色色卡，点击色号即可复制',
        'url': '/palette',
        'tag': '可用',
        'status': 'online',
    },
    {
        'id': 'transqueue',
        'group': '科研工具',
        'name': '任务队列',
        'desc': '统一串行队列：文献翻译与图像拼合在此排队，进度、位次与失败原因',
        'url': '/queue',
        'tag': '合并',
        'status': 'online',
    },
    {
        'id': 'archive',
        'group': '科研工具',
        'name': '译文归档',
        'desc': '按论文聚合近 48 小时的原文 / 中文 / 双语，点按钮即开 PDF',
        'url': '/archive',
        'tag': '可用',
        'status': 'online',
    },
    {
        'id': 'terms',
        'group': '科研工具',
        'name': '地学术语库',
        'desc': '术语对照表检索，查看命中率与扩充记录',
        'url': None,
        'tag': '规划中',
        'status': 'coming',
    },
    {
        'id': 'triangle',
        'group': '科研工具',
        'name': '三角计算',
        'desc': '输入任意 3 项，求解并绘制三角形（SSS/SAS/ASA）',
        'url': '/triangle',
        'tag': '新增',
        'status': 'online',
    },
    {
        'id': 'story',
        'group': '科研工具',
        'name': '汇报说明书',
        'desc': '汇报设计档案与 PPT 入口，顶部标签切换多份',
        'url': '/story',
        'tag': '新增',
        'status': 'online',
    },
    {
        'id': 'stitch',
        'group': '科研工具',
        'name': '图像拼合',
        'desc': '上传任意数量显微照片，自动特征匹配 + 全局优化 + 羽化融合',
        'url': '/stitch',
        'tag': '新增',
        'status': 'online',
    },

    # ═══════════ 个人效率 ═══════════
    {
        'id': 'tasks',
        'group': '个人效率',
        'name': '任务打卡',
        'desc': '证书考试 × 专业赛事：任务时间轴 + 阶段打卡 + 番茄钟，多角色切换 · 团队共享',
        'url': '/tasks',
        'tag': '新增',
        'status': 'online',
        'accent': True,
    },

    # ═══════════ 系统 ═══════════
    {
        'id': 'hardrefresh',
        'group': '系统',
        'name': '强制清缓存',
        'desc': '一键等效 Ctrl+Shift+R：跳过浏览器缓存、重新拉取全部资源',
        'url': None,
        'tag': '工具',
        'status': 'online',
        'action': 'hardreload',
    },
    {
        'id': 'share',
        'group': '系统',
        'name': '内网共享',
        'desc': '把 NAS 共享目录挂到本机 S 盘：一键脚本 / 命令复制 / 排错',
        'url': '/share',
        'tag': '新增',
        'status': 'online',
        'accent': True,
    },
    {
        'id': 'monitor',
        'group': '系统',
        'name': '运行监控',
        'desc': 'GPU 显存、推理吞吐、任务队列与错误汇总',
        'url': None,
        'tag': '规划中',
        'status': 'coming',
    },
    {
        'id': 'files',
        'group': '系统',
        'name': '文件柜',
        'desc': '共享目录浏览与文件下载',
        'url': None,
        'tag': '规划中',
        'status': 'coming',
    },
]


def get_modules():
    """返回全部模块（原样列表）。"""
    return list(MODULES)


def get_online_modules():
    """返回可点击的模块（online / beta）。"""
    return [m for m in MODULES if m.get('status') in ('online', 'beta') and m.get('url')]


def get_groups():
    """按首次出现顺序分组 → [(组名, [模块, ...]), ...]"""
    order, bucket = [], {}
    for m in MODULES:
        g = m.get('group') or '其他'
        if g not in bucket:
            bucket[g] = []
            order.append(g)
        bucket[g].append(m)
    return [(g, bucket[g]) for g in order]
