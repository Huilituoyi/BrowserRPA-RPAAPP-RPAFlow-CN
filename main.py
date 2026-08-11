# -*- coding: utf-8 -*-
"""
RPAAPP 启动入口。
运行前请先安装依赖：
    pip install -r requirements.txt
    playwright install chromium
然后运行：
    python main.py
"""
import json
import os
import sys

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PySide6.QtWidgets import QApplication

from config.settings import ensure_dirs, APP_VERSION, CONFIG_FILE
from core.logging.logger import setup_logging
from ui.main_window import MainWindow


def _apply_ssl_flag():
    """在 QApplication 创建前，根据配置设置 Chromium 忽略 SSL 错误标志。
    此环境变量必须在 QtWebEngine 初始化前设置，运行时改动需重启生效。"""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("browser", {}).get("ignore_ssl_errors", False):
                old = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
                if "--ignore-certificate-errors" not in old:
                    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
                        (old + " ").lstrip() + "--ignore-certificate-errors"
                    )
    except Exception:
        pass


def create_app_icon() -> QIcon:
    """程序生成应用图标：蓝色圆角矩形 + 白色 "RPA" 文字。"""
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#2563eb"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 12, 12)
    p.setPen(QColor("white"))
    f = QFont("Arial", 14, QFont.Weight.Bold)
    p.setFont(f)
    p.drawText(QRect(2, 2, 60, 60), Qt.AlignmentFlag.AlignCenter, "RPA")
    p.end()
    return QIcon(pix)


def main():
    ensure_dirs()
    _apply_ssl_flag()  # 必须在 QApplication 创建前
    app = QApplication(sys.argv)
    app.setApplicationName("RPA 自动化助手")
    app.setApplicationVersion(APP_VERSION)
    app.setWindowIcon(create_app_icon())

    # 初始化日志系统（必须在 QApplication 之后）
    setup_logging()

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
