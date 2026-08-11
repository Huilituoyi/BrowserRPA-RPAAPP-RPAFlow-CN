# -*- coding: utf-8 -*-
"""设置面板：浏览器、录制、Oracle 连接配置，保存后即时应用到浏览器。"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QGroupBox, QLineEdit, QSpinBox,
    QDoubleSpinBox, QCheckBox, QPushButton, QHBoxLayout, QScrollArea, QMessageBox,
    QColorDialog,
)
from PySide6.QtGui import QColor
from core.logging.logger import get_logger

log = get_logger("ui.settings")


class _NoWheelSpinBox(QSpinBox):
    """不响应鼠标滚轮的 SpinBox，避免在滚动设置页时误改数值。"""
    def wheelEvent(self, event):
        event.ignore()


class _NoWheelDoubleSpinBox(QDoubleSpinBox):
    """不响应鼠标滚轮的 DoubleSpinBox，避免在滚动设置页时误改数值。"""
    def wheelEvent(self, event):
        event.ignore()


# 颜色项中文名映射
_COLOR_LABELS = {
    "code_keyword": "代码-关键字",
    "code_string": "代码-字符串",
    "code_comment": "代码-注释",
    "code_number": "代码-数字",
    "code_func": "代码-函数名",
    "code_default": "代码-默认文字",
    "code_bg": "代码-背景",
    "log_debug": "日志-调试",
    "log_info": "日志-信息",
    "log_warning": "日志-警告",
    "log_error": "日志-错误",
    "log_critical": "日志-严重",
    "highlight_bg": "高亮背景色",
}


class SettingsPanel(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self._ctx = ctx
        cfg = ctx.config

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        v = QVBoxLayout(inner)

        # ---- 浏览器设置 ----
        gb = QGroupBox("浏览器设置")
        f = QFormLayout(gb)
        self.home_url = QLineEdit(cfg.get("browser", "home_url", default=""))
        self.user_agent = QLineEdit(cfg.get("browser", "user_agent", default=""))
        self.vp_w = _NoWheelSpinBox(); self.vp_w.setRange(320, 4000)
        self.vp_w.setValue(int(cfg.get("browser", "viewport_width", default=1280)))
        self.vp_h = _NoWheelSpinBox(); self.vp_h.setRange(240, 4000)
        self.vp_h.setValue(int(cfg.get("browser", "viewport_height", default=800)))
        self.js = QCheckBox("启用 JavaScript")
        self.js.setChecked(cfg.get("browser", "javascript_enabled", default=True))
        self.img = QCheckBox("加载图片")
        self.img.setChecked(cfg.get("browser", "load_images", default=True))
        self.ssl = QCheckBox("忽略 SSL 错误（不安全，仅用于调试，修改后需重启程序生效）")
        self.ssl.setChecked(cfg.get("browser", "ignore_ssl_errors", default=False))
        self.incognito = QCheckBox("无痕模式（不保留 cookie/缓存/登录状态，每次打开都是全新页面）")
        self.incognito.setChecked(cfg.get("browser", "incognito", default=False))
        self.proxy = QLineEdit(cfg.get("browser", "proxy", default=""))
        self.proxy.setPlaceholderText("http://host:port（留空不使用）")
        self.timeout = _NoWheelSpinBox(); self.timeout.setRange(1000, 300000)
        self.timeout.setSingleStep(1000)
        self.timeout.setValue(int(cfg.get("browser", "timeout_ms", default=30000)))
        f.addRow("主页 URL", self.home_url)
        f.addRow("User-Agent", self.user_agent)
        f.addRow("视口宽度", self.vp_w)
        f.addRow("视口高度", self.vp_h)
        f.addRow("", self.js)
        f.addRow("", self.img)
        f.addRow("", self.ssl)
        f.addRow("", self.incognito)
        f.addRow("HTTP 代理", self.proxy)
        f.addRow("超时(毫秒)", self.timeout)
        v.addWidget(gb)

        # ---- 录制选项 ----
        gb2 = QGroupBox("录制选项")
        f2 = QFormLayout(gb2)
        self.rec_scroll = QCheckBox("记录页面滚动（默认关闭，避免噪声）")
        self.rec_scroll.setChecked(cfg.get("recorder", "record_scroll", default=False))
        self.rec_hover = QCheckBox("记录鼠标悬停")
        self.rec_hover.setChecked(cfg.get("recorder", "record_hover", default=False))
        f2.addRow("", self.rec_scroll)
        f2.addRow("", self.rec_hover)
        v.addWidget(gb2)

        # ---- 回放执行选项 ----
        gb4 = QGroupBox("回放执行选项（防止操作过快被封）")
        f4 = QFormLayout(gb4)
        self.delay_min = QDoubleSpinBox()
        self.delay_min.setRange(0.0, 300.0)
        self.delay_min.setSingleStep(0.5)
        self.delay_min.setSuffix(" 秒")
        self.delay_min.setValue(float(cfg.get("runner", "step_delay_min", default=1.0)))
        self.delay_max = _NoWheelDoubleSpinBox()
        self.delay_max.setRange(0.0, 300.0)
        self.delay_max.setSingleStep(0.5)
        self.delay_max.setSuffix(" 秒")
        self.delay_max.setValue(float(cfg.get("runner", "step_delay_max", default=3.0)))
        f4.addRow("每步最小间隔", self.delay_min)
        f4.addRow("每步最大间隔", self.delay_max)
        self.headless = QCheckBox("隐藏浏览器窗口（无头模式，关闭后可在弹出的浏览器窗口看到操作过程）")
        self.headless.setChecked(cfg.get("runner", "headless", default=True))
        f4.addRow("", self.headless)
        v.addWidget(gb4)

        # ---- Oracle 连接 ----
        gb3 = QGroupBox("Oracle 连接（瘦模式 / 占位配置，请填入真实信息）")
        f3 = QFormLayout(gb3)
        self.o_host = QLineEdit(cfg.get("oracle", "host", default="localhost"))
        self.o_port = _NoWheelSpinBox(); self.o_port.setRange(1, 65535)
        self.o_port.setValue(int(cfg.get("oracle", "port", default=1521)))
        self.o_svc = QLineEdit(cfg.get("oracle", "service_name", default="ORCL"))
        self.o_user = QLineEdit(cfg.get("oracle", "username", default=""))
        self.o_pwd = QLineEdit(cfg.get("oracle", "password", default=""))
        self.o_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.o_table = QLineEdit(cfg.get("oracle", "table", default=""))
        self.o_table.setPlaceholderText("默认操作的表名（可选）")
        f3.addRow("主机", self.o_host)
        f3.addRow("端口", self.o_port)
        f3.addRow("服务名/SID", self.o_svc)
        f3.addRow("用户名", self.o_user)
        f3.addRow("密码", self.o_pwd)
        f3.addRow("默认表名", self.o_table)
        self.btn_test = QPushButton("测试连接")
        self.btn_test.clicked.connect(self._test_oracle)
        f3.addRow("", self.btn_test)
        v.addWidget(gb3)

        # ---- 外观与颜色 ----
        gb_colors = QGroupBox("外观与颜色")
        f_colors = QFormLayout(gb_colors)
        self._color_buttons = {}
        for key, label in _COLOR_LABELS.items():
            cur = cfg.get("colors", key, default="#000000")
            btn = QPushButton(cur)
            self._style_color_btn(btn, cur)
            btn.clicked.connect(lambda _, k=key, b=btn: self._pick_color(k, b))
            self._color_buttons[key] = btn
            f_colors.addRow(label, btn)
        v.addWidget(gb_colors)

        v.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        btns = QHBoxLayout()
        self.btn_save = QPushButton("保存设置并应用到浏览器")
        self.btn_save.clicked.connect(self._save)
        btns.addStretch(1)
        btns.addWidget(self.btn_save)
        outer.addLayout(btns)

    def _save(self):
        c = self._ctx.config
        c.set("browser", "home_url", self.home_url.text().strip())
        c.set("browser", "user_agent", self.user_agent.text().strip())
        c.set("browser", "viewport_width", self.vp_w.value())
        c.set("browser", "viewport_height", self.vp_h.value())
        c.set("browser", "javascript_enabled", self.js.isChecked())
        c.set("browser", "load_images", self.img.isChecked())
        c.set("browser", "ignore_ssl_errors", self.ssl.isChecked())
        c.set("browser", "incognito", self.incognito.isChecked())
        c.set("browser", "proxy", self.proxy.text().strip())
        c.set("browser", "timeout_ms", self.timeout.value())
        c.set("recorder", "record_scroll", self.rec_scroll.isChecked())
        c.set("recorder", "record_hover", self.rec_hover.isChecked())
        c.set("runner", "step_delay_min", self.delay_min.value())
        c.set("runner", "step_delay_max", self.delay_max.value())
        c.set("runner", "headless", self.headless.isChecked())
        c.set("oracle", "host", self.o_host.text().strip())
        c.set("oracle", "port", self.o_port.value())
        c.set("oracle", "service_name", self.o_svc.text().strip())
        c.set("oracle", "username", self.o_user.text().strip())
        c.set("oracle", "password", self.o_pwd.text())
        c.set("oracle", "table", self.o_table.text().strip())
        # 保存颜色设置
        for key, btn in self._color_buttons.items():
            c.set("colors", key, btn.text())
        if self._ctx.browser is not None:
            self._ctx.browser.apply_settings()
            self._ctx.browser.resize_browser(self.vp_w.value(), self.vp_h.value())
        tip = "已保存并应用。"
        if self.incognito.isChecked():
            tip += "\n\n提示：无痕模式需重启应用后生效。"
        QMessageBox.information(self, "设置", tip)

    # ---------- 颜色选择 ----------
    def _style_color_btn(self, btn: QPushButton, hex_val: str):
        """按钮背景设为颜色值，文字显示 hex 值。"""
        btn.setStyleSheet(
            f"QPushButton {{ background-color: {hex_val}; color: #000000; "
            f"border: 1px solid #888888; padding: 4px 12px; }}"
        )

    def _pick_color(self, key: str, btn: QPushButton):
        """弹出 QColorDialog 选色，选定后更新按钮样式。"""
        current = QColor(btn.text())
        color = QColorDialog.getColor(current, self, "选择颜色")
        if color.isValid():
            hex_val = color.name().upper()
            btn.setText(hex_val)
            self._style_color_btn(btn, hex_val)

    def _test_oracle(self):
        c = self._ctx.config
        c.set("oracle", "host", self.o_host.text().strip())
        c.set("oracle", "port", self.o_port.value())
        c.set("oracle", "service_name", self.o_svc.text().strip())
        c.set("oracle", "username", self.o_user.text().strip())
        c.set("oracle", "password", self.o_pwd.text())
        self.btn_test.setText("测试中…")
        self.btn_test.setEnabled(False)

        from PySide6.QtCore import QThread, Signal

        class _Tester(QThread):
            done = Signal(bool, str)
            def run(self_inner):
                try:
                    from core.data.oracle_client import OracleClient
                    ok, msg = OracleClient(c).test_connection()
                    self_inner.done.emit(ok, msg)
                except Exception as e:
                    self_inner.done.emit(False, str(e))

        def _on_done(ok, msg):
            self.btn_test.setText("测试连接")
            self.btn_test.setEnabled(True)
            QMessageBox.information(self, "Oracle 连接测试", msg)

        self._tester = _Tester(parent=self)
        self._tester.done.connect(_on_done)
        self._tester.start()
