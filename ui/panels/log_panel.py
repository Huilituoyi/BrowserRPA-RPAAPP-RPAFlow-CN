# -*- coding: utf-8 -*-
"""
日志面板：
- 实时显示日志（订阅 LogEmitter.new_log）；
- 「显示级别」做即时过滤（只显示所选级别及以上），不影响实际记录；
- 主题：浅色 / 深色 / 跟随系统，避免背景与字色撞色；主题选择持久化。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QTextCursor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QComboBox, QLabel,
)

from core.logging.logger import get_log_file

# ---------- 级别数值（用于过滤比较）----------
_LEVEL_NUM = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}

_MAX_BUFFER = 5000  # 最多缓存条数，超过则丢弃最早的，防止内存膨胀


class LogPanel(QWidget):
    def __init__(self, emitter, config=None, parent=None):
        super().__init__(parent)
        self._emitter = emitter
        self._cfg = config
        self._buffer = []                 # 全部已收到的日志条目
        self._filter_level = "INFO"       # 显示级别（只过滤显示，不影响记录）
        # 主题：取已保存值，默认跟随系统
        saved = config.get("log", "theme", default="system") if config else "system"
        self._theme_choice = saved if saved in ("light", "dark", "system") else "system"

        root = QVBoxLayout(self)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("显示级别："))
        self.level = QComboBox()
        self.level.addItems(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        self.level.setCurrentText(self._filter_level)
        self.level.currentTextChanged.connect(self._on_filter_changed)
        bar.addWidget(self.level)

        bar.addWidget(QLabel("主题："))
        self.theme = QComboBox()
        self.theme.addItem("跟随系统", "system")
        self.theme.addItem("浅色", "light")
        self.theme.addItem("深色", "dark")
        self.theme.setCurrentIndex(self.theme.findData(self._theme_choice))
        self.theme.currentIndexChanged.connect(self._on_theme_changed)
        bar.addWidget(self.theme)

        bar.addStretch(1)
        self.btn_open = QPushButton("打开日志文件")
        self.btn_clear = QPushButton("清空显示")
        bar.addWidget(self.btn_open)
        bar.addWidget(self.btn_clear)
        root.addLayout(bar)

        self.view = QTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        root.addWidget(self.view, 1)

        self.btn_clear.clicked.connect(self._on_clear)
        self.btn_open.clicked.connect(self._open_log_file)

        if emitter is not None:
            emitter.new_log.connect(self._on_new_log)

        self._apply_theme()

    # ---------- 实际主题解析 ----------
    def _resolved_theme(self) -> str:
        if self._theme_choice == "system":
            try:
                scheme = QGuiApplication.styleHints().colorScheme()
                return "dark" if scheme == Qt.ColorScheme.Dark else "light"
            except Exception:
                return "light"
        return self._theme_choice

    def _themes(self) -> dict:
        """从配置 colors 段读取颜色，构建主题字典。"""
        c = self._cfg
        g = c.get if c else (lambda *a, **k: k.get("default"))
        debug = g("colors", "log_debug", default="#6B7280")
        info = g("colors", "log_info", default="#1F2937")
        warning = g("colors", "log_warning", default="#B45309")
        error = g("colors", "log_error", default="#DC2626")
        critical = g("colors", "log_critical", default="#7F1D1D")
        bg_light = g("colors", "log_bg_light", default="#FFFFFF")
        bg_dark = g("colors", "log_bg_dark", default="#1E1E1E")
        fg_light = g("colors", "log_fg_light", default="#1F2937")
        fg_dark = g("colors", "log_fg_dark", default="#D1D5DB")

        level_colors = {
            "DEBUG": debug, "INFO": info, "WARNING": warning,
            "ERROR": error, "CRITICAL": critical,
        }
        return {
            "light": {"bg": bg_light, "colors": {**level_colors, "INFO": fg_light}},
            "dark": {"bg": bg_dark, "colors": {**level_colors, "INFO": fg_dark}},
        }

    def _apply_theme(self):
        t = self._themes()[self._resolved_theme()]
        self.view.setStyleSheet(
            f"QTextEdit {{ background-color: {t['bg']}; color: {t['colors']['INFO']}; }}"
        )
        self._render()

    # ---------- 事件回调 ----------
    def _on_filter_changed(self, text: str):
        self._filter_level = text
        self._render()

    def _on_theme_changed(self, _idx: int):
        self._theme_choice = self.theme.currentData()
        if self._cfg is not None:
            self._cfg.set("log", "theme", self._theme_choice)
        self._apply_theme()

    def _on_clear(self):
        self._buffer.clear()
        self._render()

    def _on_new_log(self, d: dict):
        self._buffer.append(d)
        if len(self._buffer) > _MAX_BUFFER:
            # 丢弃最早的 10%，保持近期日志
            del self._buffer[: len(self._buffer) // 10]
        # 仅当该条通过过滤时才增量追加，避免每次全量重绘
        if _LEVEL_NUM.get(d.get("level"), 0) >= _LEVEL_NUM.get(self._filter_level, 0):
            self._append_line(d)

    # ---------- 渲染 ----------
    def _render(self):
        """按当前过滤级别与主题全量重绘。"""
        theme = self._themes()[self._resolved_theme()]
        colors = theme["colors"]
        threshold = _LEVEL_NUM.get(self._filter_level, 0)
        parts = []
        for d in self._buffer:
            if _LEVEL_NUM.get(d.get("level"), 0) < threshold:
                continue
            parts.append(self._line_html(d, colors))
        self.view.setHtml("<br>".join(parts))
        self.view.moveCursor(QTextCursor.MoveOperation.End)

    def _append_line(self, d: dict):
        theme = self._themes()[self._resolved_theme()]
        self.view.append(self._line_html(d, theme["colors"]))
        self.view.moveCursor(QTextCursor.MoveOperation.End)

    @staticmethod
    def _line_html(d: dict, colors: dict) -> str:
        color = colors.get(d.get("level"), colors["INFO"])
        msg = (d.get("message", "") or "").replace("<", "&lt;").replace(">", "&gt;")
        line = f"[{d.get('time', '')}] [{d.get('level', '')}] {msg}"
        return f'<span style="color:{color};white-space:pre;">{line}</span>'

    def _open_log_file(self):
        import os
        import subprocess
        path = get_log_file()
        if path and os.path.exists(path):
            try:
                os.startfile(path)  # Windows 用默认编辑器打开
            except Exception:
                subprocess.Popen(["notepad", path])
