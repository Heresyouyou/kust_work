# -*- coding: utf-8 -*-
"""今日早报模块（页面带前缀，API 留根路径）"""
from .routes import init_news, news_bp

__all__ = ['news_bp', 'init_news']
