# -*- coding: utf-8 -*-
"""配置包：静态常量(settings) + 可读写运行时配置(AppConfig)。"""
from . import settings
from .settings import ensure_dirs, APP_NAME, APP_VERSION
from .app_config import AppConfig

__all__ = ["settings", "ensure_dirs", "APP_NAME", "APP_VERSION", "AppConfig"]
