# -*- coding: utf-8 -*-
"""
浏览器面板：内置浏览器 + 录制控制 + 快捷抓取首个表格 + 导出 Excel。
创建时实例化 BrowserWidget 与 Recorder 并挂到 ctx。
"""
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QVBoxLayout, QFileDialog, QMessageBox,
)

from core.browser.browser_widget import BrowserWidget
from core.recorder.recorder import Recorder
from core.data.scraper import extract_first_table
from core.data.excel_exporter import export as export_excel
from core.logging.logger import get_logger

log = get_logger("ui.browser")


class BrowserPanel(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self._ctx = ctx

        # 录制与抓取工具条
        bar = QHBoxLayout()
        self.btn_rec = QPushButton("开始录制")
        self.btn_rec.setCheckable(True)
        self.btn_rec.toggled.connect(self._on_rec_toggled)
        self.btn_clear = QPushButton("清空录制")
        self.btn_clear.clicked.connect(lambda: self._ctx.recorder.clear())
        self.btn_scrape = QPushButton("抓取首个表格")
        self.btn_scrape.clicked.connect(self._scrape_table)
        self.btn_export = QPushButton("导出 Excel")
        self.btn_export.clicked.connect(self._export_excel)
        for w in (self.btn_rec, self.btn_clear, self.btn_scrape, self.btn_export):
            bar.addWidget(w)
        bar.addStretch(1)

        # 内置浏览器 + 录制器
        self.browser = BrowserWidget(ctx.config)
        ctx.browser = self.browser
        ctx.recorder = Recorder(self.browser, ctx.config)
        ctx.recorder.state_changed.connect(self._on_state_changed)

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)
        root.addLayout(bar)
        root.addWidget(self.browser, 1)

    def _on_rec_toggled(self, checked: bool):
        if checked:
            self._ctx.recorder.start()
            self.btn_rec.setText("停止录制")
        else:
            self._ctx.recorder.stop()
            self.btn_rec.setText("开始录制")

    def _on_state_changed(self, recording: bool):
        # 录制状态被外部改变时同步按钮
        self.btn_rec.blockSignals(True)
        self.btn_rec.setChecked(recording)
        self.btn_rec.setText("停止录制" if recording else "开始录制")
        self.btn_rec.blockSignals(False)

    def _scrape_table(self):
        rows = extract_first_table(self.browser)
        self._ctx.last_scraped = rows
        QMessageBox.information(
            self, "抓取结果",
            f"已抓取 {len(rows)} 行数据。可在【数据抓取】面板查看，或直接点【导出 Excel】。"
        )

    def _export_excel(self):
        if not self._ctx.last_scraped:
            QMessageBox.warning(self, "导出 Excel", "没有可导出的数据，请先抓取。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出到 Excel", "", "Excel 文件 (*.xlsx)")
        if path:
            try:
                export_excel(self._ctx.last_scraped, path)
                QMessageBox.information(self, "导出成功", "已保存到：" + path)
            except Exception as e:
                QMessageBox.critical(self, "导出失败", str(e))
