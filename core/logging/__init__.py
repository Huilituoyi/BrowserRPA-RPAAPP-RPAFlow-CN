# -*- coding: utf-8 -*-
"""日志子系统。"""
from .logger import (
    setup_logging, get_logger, set_level, get_emitter, get_log_file, LogEmitter,
)

__all__ = ["setup_logging", "get_logger", "set_level", "get_emitter",
           "get_log_file", "LogEmitter"]
