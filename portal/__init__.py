# -*- coding: utf-8 -*-
"""kust.work 门户层（方案 B：模块化单体）

职责：
  - 提供根路径 `/` 的欢迎主页
  - 维护功能入口注册表（registry.py）
  - 提供健康状态 API 给首页状态灯

与其他模块的关系：
  门户只负责「入口 + 状态展示」，不承载业务逻辑。
  新功能以 Blueprint 形式挂在 modules/ 下，页面带前缀、API 留在根路径。
"""
from .registry import MODULES, get_groups, get_modules, get_online_modules
from .routes import init_portal, portal_bp

__all__ = [
    'MODULES',
    'get_modules',
    'get_online_modules',
    'get_groups',
    'portal_bp',
    'init_portal',
]
