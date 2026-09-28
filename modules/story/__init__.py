# -*- coding: utf-8 -*-
"""汇报设计说明书模块（STORY.md 可视化 + 网页放映版 + 后台管理）"""
from .routes import init_story, story_bp
from .admin import admin_bp

__all__ = ['story_bp', 'admin_bp', 'init_story']
