# -*- coding: utf-8 -*-
"""
Oracle 面板：测试连接、列出当前用户的表、执行 SQL 查询并展示结果。
所有数据库操作在工作线程中执行，避免阻塞 UI。
连接信息请在【设置】面板配置（瘦模式，无需安装 Oracle 客户端）。
"""
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QListWidget,
    QAbstractItemView,
)
from core.logging.logger import get_logger

log = get_logger("ui.oracle")

# 查询结果最大行数，防止大数据量撑爆内存
_QUERY_LIMIT = 5000


class _OracleWorker(QThread):
    """在工作线程中执行 Oracle 操作，通过信号回传结果。"""
    finished_ok = Signal(object)     # 成功：回传结果（tuple/list/str）
    finished_err = Signal(str)       # 失败：回传错误消息

    def __init__(self, fn, *args, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._args = args

    def run(self):
        try:
            result = self._fn(*self._args)
            self.finished_ok.emit(result)
        except Exception as e:
            self.finished_err.emit(str(e))


class OraclePanel(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self._ctx = ctx
        self._oc = None
        self._worker = None  # 当前运行的 worker 引用，防止 GC

        root = QVBoxLayout(self)
        info = QLabel("连接信息请在【设置】面板配置（瘦模式，无需安装 Oracle 客户端）。双击下方表名可快速生成查询。")
        info.setWordWrap(True)
        root.addWidget(info)

        bar = QHBoxLayout()
        self.btn_test = QPushButton("测试连接")
        self.btn_test.clicked.connect(self._test)
        self.btn_tables = QPushButton("列出我的表")
        self.btn_tables.clicked.connect(self._tables)
        bar.addWidget(self.btn_test)
        bar.addWidget(self.btn_tables)
        bar.addStretch(1)
        root.addLayout(bar)

        root.addWidget(QLabel("表列表："))
        self.tbl_list = QListWidget()
        self.tbl_list.itemDoubleClicked.connect(self._fill_sql)
        root.addWidget(self.tbl_list)

        root.addWidget(QLabel("SQL（查询语句，支持命名绑定 :name 或位置绑定 :1）："))
        self.sql = QTextEdit()
        self.sql.setPlaceholderText("SELECT * FROM your_table WHERE id = :1")
        self.sql.setMaximumHeight(100)
        root.addWidget(self.sql)
        self.btn_run = QPushButton("执行查询")
        self.btn_run.clicked.connect(self._run)
        root.addWidget(self.btn_run)

        root.addWidget(QLabel("结果（最多显示 %d 行）：" % _QUERY_LIMIT))
        self.result = QTableWidget(0, 0)
        self.result.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.result.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.result, 1)

    def _client(self):
        from core.data.oracle_client import OracleClient
        if self._oc is None:
            self._oc = OracleClient(self._ctx.config)
        return self._oc

    def _start_worker(self, fn, *args):
        """启动工作线程执行 fn，防止重复启动。"""
        if self._worker and self._worker.isRunning():
            QMessageBox.warning(self, "提示", "上一次操作尚未完成，请稍候。")
            return
        # 禁用按钮防止重复操作
        for btn in (self.btn_test, self.btn_tables, self.btn_run):
            btn.setEnabled(False)
        self._worker = _OracleWorker(fn, *args, parent=self)
        self._worker.finished_ok.connect(self._on_worker_done)
        self._worker.finished_err.connect(self._on_worker_error)
        self._worker.start()

    def _restore_buttons(self):
        for btn in (self.btn_test, self.btn_tables, self.btn_run):
            btn.setEnabled(True)

    def _on_worker_done(self, result):
        """工作线程成功完成，result 是回调返回值。"""
        self._restore_buttons()
        # 子类/调用方通过 _pending_action 确定如何处理结果
        action = getattr(self, "_pending_action", None)
        if action == "test":
            ok, msg = result
            QMessageBox.information(self, "Oracle 连接测试", msg)
        elif action == "tables":
            names = result
            self.tbl_list.clear()
            self.tbl_list.addItems(names)
            QMessageBox.information(self, "表列表", "共 %d 个表。" % len(names))
        elif action == "query":
            rows = result
            self._fill_result(rows)

    def _on_worker_error(self, err_msg):
        self._restore_buttons()
        action = getattr(self, "_pending_action", None)
        title = {"test": "连接测试失败", "tables": "查询表失败", "query": "查询失败"}.get(action, "操作失败")
        QMessageBox.critical(self, title, err_msg)

    def _fill_sql(self, item):
        self.sql.setPlainText("SELECT * FROM " + item.text())

    def _test(self):
        self._pending_action = "test"
        self._start_worker(lambda: self._client().test_connection())

    def _tables(self):
        self._pending_action = "tables"
        self._start_worker(lambda: self._client().list_tables())

    def _run(self):
        sql = self.sql.toPlainText().strip()
        if not sql:
            return
        self._pending_action = "query"
        self._start_worker(lambda: self._client().query(sql, limit=_QUERY_LIMIT))

    def _fill_result(self, rows):
        """填充查询结果到表格（已在工作线程拿到数据，UI 填充很快）。"""
        self.result.clear()
        if not rows:
            self.result.setColumnCount(0)
            self.result.setRowCount(0)
            QMessageBox.information(self, "查询结果", "无数据。")
            return
        headers = list(rows[0].keys())
        self.result.setColumnCount(len(headers))
        self.result.setHorizontalHeaderLabels(headers)
        self.result.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for j, h in enumerate(headers):
                self.result.setItem(i, j, QTableWidgetItem(str(r.get(h, ""))))
