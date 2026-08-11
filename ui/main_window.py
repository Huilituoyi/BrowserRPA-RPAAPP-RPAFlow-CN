# -*- coding: utf-8 -*-
"""
主窗口：左侧导航 + 右侧功能面板（QStackedWidget）。
注意：BrowserPanel 必须最先创建——它会初始化 ctx.browser 与 ctx.recorder 供其它面板使用。
"""
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QListWidget, QListWidgetItem,
    QStackedWidget, QMessageBox, QSystemTrayIcon, QStyle,
)

from config.settings import APP_NAME, APP_VERSION, ensure_dirs
from core.app_context import AppContext
from core.logging.logger import get_logger, get_emitter, get_log_file
from .panels.browser_panel import BrowserPanel
from .panels.codegen_panel import CodegenPanel
from .panels.data_panel import DataPanel
from .panels.tasks_panel import TasksPanel
from .panels.oracle_panel import OraclePanel
from .panels.settings_panel import SettingsPanel
from .panels.log_panel import LogPanel
from .panels.ocr_panel import OcrPanel

log = get_logger("ui.main")


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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        ensure_dirs()
        self.setWindowTitle("RPA 自动化助手 v1.0")
        self.setWindowIcon(create_app_icon())
        self.resize(1280, 820)

        self.ctx = AppContext()

        # 初始化任务调度（依赖 apscheduler，失败则降级）
        try:
            from core.tasks.scheduler import TaskScheduler
            self.ctx.scheduler = TaskScheduler(self.ctx.store, self.ctx.config)
            log.info("任务调度器已启动")
        except Exception as e:
            log.warning("任务调度初始化失败（定时任务不可用）：%s", e)

        # 系统托盘通知：任务完成时弹通知
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        self._tray.setVisible(True)
        if self.ctx.scheduler is not None:
            self.ctx.scheduler.status_changed.connect(self._on_task_status)

        central = QWidget()
        self.setCentralWidget(central)
        lay = QHBoxLayout(central)
        lay.setContentsMargins(4, 4, 4, 4)

        self.nav = QListWidget()
        self.nav.setFixedWidth(140)
        self.stack = QStackedWidget()
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        lay.addWidget(self.nav)
        lay.addWidget(self.stack, 1)

        # 浏览器面板必须最先创建（初始化 browser / recorder）
        self._add_panel("内置浏览器", BrowserPanel(self.ctx))
        self._add_panel("代码生成", CodegenPanel(self.ctx))
        self._add_panel("数据抓取", DataPanel(self.ctx))
        self._add_panel("定时任务", TasksPanel(self.ctx))
        self._add_panel("Oracle", OraclePanel(self.ctx))
        self._ocr_panel = OcrPanel(self.ctx)
        self._add_panel("OCR 服务", self._ocr_panel)
        self._add_panel("设置", SettingsPanel(self.ctx))
        self._add_panel("日志", LogPanel(get_emitter(), self.ctx.config))

        self.nav.setCurrentRow(0)

        self.statusBar().showMessage("日志文件：" + (get_log_file() or ""))
        self._build_menu()

    def _add_panel(self, name: str, widget: QWidget):
        item = QListWidgetItem(name)
        self.nav.addItem(item)
        self.stack.addWidget(widget)

    def _build_menu(self):
        mb = self.menuBar()
        fm = mb.addMenu("文件")
        fm.addAction("退出", self.close)

        hm = mb.addMenu("帮助")
        hm.addAction("关于", lambda: QMessageBox.about(
            self, "关于",
            APP_NAME + " " + APP_VERSION + "\n\n基于 PySide6 + Playwright 的 RPA 桌面应用。\n"
            "内置浏览器 · 操作录制 · 多语言脚本生成 · 数据抓取 · Excel 导出 · Oracle · 定时任务"
        ))

    def _on_task_status(self, task_id, status, info):
        """任务状态变化时通过系统托盘弹通知。"""
        if status == "success":
            self._tray.showMessage("RPA 自动化助手", "任务执行成功")
        elif status == "failed":
            self._tray.showMessage("RPA 自动化助手", "任务执行失败：" + (info or ""))

    def closeEvent(self, event):
        # 0. 关闭所有非模态子对话框（编辑任务等），防止主窗口关了对话框还留着
        from PySide6.QtWidgets import QApplication, QDialog
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QDialog) and w is not self:
                w.reject()
        # 1. 停止 OCR 服务，释放端口
        try:
            if hasattr(self, "_ocr_panel"):
                self._ocr_panel.cleanup()
        except Exception:
            pass
        # 2. 停止任务调度器
        if self.ctx.scheduler is not None:
            try:
                self.ctx.scheduler.shutdown()
            except Exception:
                pass
        # 3. 清理 QtWebEngine（Chromium 子进程），防止退出后残留白屏进程
        try:
            from PySide6.QtWebEngineCore import QWebEngineProfile
            for profile in QWebEngineProfile.defaultProfile(), :
                try:
                    profile.clearAllVisitedLinks()
                except Exception:
                    pass
            # 强制释放浏览器面板
            if hasattr(self.ctx, "browser"):
                try:
                    self.ctx.browser.page().deleteLater()
                except Exception:
                    pass
        except Exception:
            pass
        super().closeEvent(event)
        # 4. 确保整个应用退出（杀掉所有 Qt 子进程）
        QApplication.quit()
