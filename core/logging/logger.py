# -*- coding: utf-8 -*-
"""
日志系统：
- 文件（按天轮转，每天一个日志文件）
- 控制台
- 推送到界面（日志面板订阅 LogEmitter.new_log）
"""
import logging
import logging.handlers
import os
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QObject, Signal

from config.settings import LOG_DIR, LOG_LEVEL, ensure_dirs


class LogEmitter(QObject):
    """把日志推送到界面（日志面板订阅它的 new_log 信号）。"""
    new_log = Signal(dict)   # {"time","level","logger","message"}


_emitter: Optional[LogEmitter] = None
_log_file: Optional[str] = None


class _UiLogHandler(logging.Handler):
    """将日志记录转发到 Qt 信号，供界面实时显示。"""
    def __init__(self, emitter: LogEmitter):
        super().__init__()
        self._emitter = emitter

    def emit(self, record: logging.LogRecord):
        try:
            self._emitter.new_log.emit({
                "time": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            })
        except Exception:
            # 日志转发失败不应影响主流程
            pass


def setup_logging(level: Optional[str] = None) -> LogEmitter:
    """
    初始化日志系统，必须在 QApplication 创建之后调用。
    返回 LogEmitter，UI 连接其 new_log 即可显示日志。
    """
    global _emitter, _log_file
    ensure_dirs()
    if _emitter is None:
        _emitter = LogEmitter()

    root = logging.getLogger()
    root.setLevel(getattr(logging, (level or LOG_LEVEL).upper(), logging.INFO))

    # 清理旧 handler，避免重复输出
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 文件 handler（按天轮转，每天一个日志文件）
    today = datetime.now().strftime("%Y-%m-%d")
    _log_file = os.path.join(LOG_DIR, f"rpaapp_{today}.log")
    fh = logging.handlers.TimedRotatingFileHandler(
        _log_file, when="midnight", interval=1,
        backupCount=30, encoding="utf-8",
    )
    fh.suffix = "%Y-%m-%d"  # 轮转后文件后缀格式
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # 控制台 handler
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # UI handler（推送到界面）
    root.addHandler(_UiLogHandler(_emitter))

    logging.getLogger("rpaapp").info("日志系统已初始化，日志文件：%s", _log_file)
    return _emitter


def set_level(level: str):
    """运行时调整日志级别。"""
    logging.getLogger().setLevel(getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str = "rpaapp") -> logging.Logger:
    return logging.getLogger(name)


def get_emitter() -> Optional[LogEmitter]:
    return _emitter


def get_log_file() -> Optional[str]:
    return _log_file
