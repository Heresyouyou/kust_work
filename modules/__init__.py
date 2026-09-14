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
import logging

from .archive import init_archive
from .journals import init_journals
from .news import init_news
from .palette import init_palette
from .queue import init_queue
from .share import init_share

logger = logging.getLogger('geo')

__all__ = ['register_all']


# (模块名, 注册函数, 是否需要 data_dir)
_MODULES = [
    ('译文归档', init_archive, False),
    ('今日早报', init_news, True),
    ('期刊列表', init_journals, True),
    ('科研配色', init_palette, False),
    ('翻译队列', init_queue, True),
    ('内网共享', init_share, False),
]


def register_all(app, *, upload_dir=None, translated_dir=None, data_dir=None, get_queue=None):
    """注册全部功能模块。目录由 web_app 注入，模块不自己去猜路径。

    get_queue: web_app 注入的「文献翻译队列」只读快照函数。
      队列数据在 web_app 的内存里，模块不能反向 import（会循环导入），
      所以和门户 get_active 一样走回调注入。
    """
    ok, failed = [], []

    for name, init_fn, need_data in _MODULES:
        try:
            if name == '译文归档':
                init_fn(app, upload_dir=upload_dir, translated_dir=translated_dir)
            elif name == '翻译队列':
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
