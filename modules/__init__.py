# -*- coding: utf-8 -*-
"""功能模块集合 —— 每个模块一个 Blueprint

新增模块三步：
  1. 在 modules/ 下建目录 my_feature/
     routes.py 里暴露 `xxx_bp` 和 `init_xxx(app, **deps)`
  2. 在下面 import 并加进 _MODULES
  3. 在 portal/registry.py 追加一条入口记录

约定（重要）：
  页面路径带前缀（/archive、/daily、/journals、/palette），
  API 与静态资源沿用根路径（/api/...）。
  这条约定是为了兼容 GeoTranslate 前端写死的根绝对路径，不要随意改。

注册策略：
  每个模块单独 try/except —— 任何一个模块依赖缺失或写坏，
  只影响它自己的入口，不会把其他模块和翻译主服务一起拖下水。
"""
import importlib
import logging

logger = logging.getLogger('geo')

__all__ = ['register_all']


# (模块名, 包名, 注册函数名, 是否需要 data_dir)
# ★ 2026-10-29：改成惰性导入。原先 16 个 `from .x import init_x` 全是硬导入，
#   任何一个模块缺依赖（例如 story 需要 markdown_it）就让整个 `modules` 包
#   不可导入 —— 注释里承诺的「单独 try/except、互不拖累」根本没兑现，
#   连带 tools/ 下任何 `from modules.tr import ...` 也一起崩。
_MODULES = [
    ('译文归档', 'archive', 'init_archive', False),
    ('今日早报', 'news', 'init_news', True),
    ('期刊更新', 'journals', 'init_journals', True),
    ('科研配色', 'palette', 'init_palette', False),
    ('三角计算', 'triangle', 'init_triangle', False),
    ('任务打卡', 'tasks', 'init_tasks', False),
    ('笔记整理', 'notes', 'init_notes', False),
    ('论文导读', 'papers', 'init_papers', False),
    ('图像拼合', 'stitch', 'init_stitch', False),
    ('汇报说明书', 'story', 'init_story', False),
    ('知识库浏览', 'geokb_public', 'init_geokb_public', False),
    ('知识库管理', 'geokb_admin', 'init_geokb_admin', False),
    ('任务队列', 'queue', 'init_queue', True),
    ('PDF 阅读器', 'reader', 'init_reader', False),
    ('内网共享', 'share', 'init_share', False),
    ('常用软件', 'software', 'init_software', False),
]


def register_all(app, *, upload_dir=None, translated_dir=None, data_dir=None,
                 get_queue=None, submit_task=None, get_task=None, task_lane=None,
                 register_runner=None):
    """注册全部功能模块。目录由 web_app 注入，模块不自己去猜路径。

    get_queue:   web_app 注入的「统一任务队列」只读快照函数。
      队列数据在 web_app 的内存里，模块不能反向 import（会循环导入），
      所以和门户 get_active 一样走回调注入。
    submit_task / get_task / task_lane / register_runner:
      统一任务队列的**写/查/互斥/注册执行体**句柄，只给图像拼合（它要吃 GPU，
      必须和翻译排进同一条串行通道）。不给其他模块，避免队列被随意改写。
      ★ `register_runner` 不能漏：只注入 submit_task 的话，工单能入队但
        `_TASK_RUNNERS['stitch']` 为空 → 队列直接把任务标成
        「未注册的任务种类（kind=stitch）」并失败。
    """
    ok, failed = [], []

    for name, modname, funcname, need_data in _MODULES:
        try:
            mod = importlib.import_module('.' + modname, __name__)
            init_fn = getattr(mod, funcname)
            if name == '译文归档':
                init_fn(app, upload_dir=upload_dir, translated_dir=translated_dir)
            elif name == 'PDF 阅读器':
                # 阅读器的「最近文件」要能列原文与译文，故两个目录都注入
                init_fn(app, upload_dir=upload_dir, translated_dir=translated_dir)
            elif name == '论文导读':
                # 只借统一串行锁（翻译与手动翻译/拼合互斥），不占用统一队列
                init_fn(app, task_lane=task_lane)
            elif name == '图像拼合':
                init_fn(app, submit_task=submit_task, get_task=get_task,
                        task_lane=task_lane, register_runner=register_runner)
            elif name == '任务队列':
                init_fn(app, data_dir=data_dir, get_queue=get_queue)
            elif need_data:
                init_fn(app, data_dir=data_dir)
            else:
                init_fn(app)
            ok.append(name)
        except Exception as e:
            failed.append(name)
            logger.exception('❌ 模块「%s」注册失败（其余模块不受影响）: %s', name, e)

    logger.info('[模块] 注册完成：成功 %d 个（%s）%s',
                len(ok), '、'.join(ok) or '无',
                ('；失败：' + '、'.join(failed)) if failed else '')
    return app
