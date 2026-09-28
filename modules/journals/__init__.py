# -*- coding: utf-8 -*-
"""期刊列表模块（页面带前缀，API 留根路径）"""
from .routes import init_journals, journals_bp

__all__ = ['journals_bp', 'init_journals']
