# -*- coding: utf-8 -*-
"""
OCR 服务面板：一键启停 ddddocr 验证码识别服务、配置端口、查看统计与请求历史、本地测试。

- 服务控制：端口配置、本机/局域网地址切换（一键设为本地端口=仅 127.0.0.1 访问，更安全）
- 输入输出：请求历史（每次请求的来源/类型/结果/耗时）；本地测试（选图→识别→看输出）
- 统计：OCR/滑块 成功失败计数
"""
import socket
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
    QLineEdit, QSpinBox, QPushButton, QComboBox, QTextEdit, QMessageBox,
    QFileDialog, QFrame, QCheckBox,
)

from core.ocr.ocr_server import OcrServer, LOCAL_HOST, LAN_HOST, DEFAULT_PORT
from core.logging.logger import get_logger

log = get_logger("ui.ocr")


class _NoWheelSpinBox(QSpinBox):
    """不响应鼠标滚轮、不显示上下箭头的 SpinBox。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)

    def wheelEvent(self, event):
        event.ignore()


_local_ip_cache = None

def _local_ip() -> str:
    """获取本机局域网 IP，带 1 秒超时，缓存结果避免重复检测阻塞 UI。"""
    global _local_ip_cache
    if _local_ip_cache:
        return _local_ip_cache
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        _local_ip_cache = ip
        return ip
    except Exception:
        return "127.0.0.1"


class OcrPanel(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self._ctx = ctx
        self._server = OcrServer()
        self._lan_ip = None  # 后台线程异步获取局域网 IP，避免阻塞 UI

        # 后台获取局域网 IP（不阻塞主线程）
        threading.Thread(target=self._fetch_lan_ip, daemon=True).start()

        # 从配置读取上次端口/地址
        cfg = self._ctx.config
        self._port_val = int(cfg.get("ocr", "port", default=DEFAULT_PORT))
        self._host_val = cfg.get("ocr", "host", default=LOCAL_HOST)

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(8, 8, 8, 8)

        # ---------- 服务控制 ----------
        gb_ctrl = QGroupBox("服务控制")
        f_ctrl = QFormLayout(gb_ctrl)

        addr_row = QHBoxLayout()
        self.host_combo = QComboBox()
        self.host_combo.addItem("仅本机访问（127.0.0.1，推荐）", LOCAL_HOST)
        self.host_combo.addItem("局域网可访问（0.0.0.0）", LAN_HOST)
        self.host_combo.setCurrentIndex(0 if self._host_val == LOCAL_HOST else 1)
        addr_row.addWidget(self.host_combo, 1)
        self.btn_local = QPushButton("一键设为本地端口")
        self.btn_local.setToolTip("把服务地址切换为仅本机 127.0.0.1，外部无法访问，更安全")
        self.btn_local.clicked.connect(self._set_local)
        addr_row.addWidget(self.btn_local)
        f_ctrl.addRow("监听地址", addr_row)

        port_row = QHBoxLayout()
        self.port = _NoWheelSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(self._port_val)
        port_row.addWidget(self.port)
        port_row.addStretch(1)
        self.btn_apply_port = QPushButton("保存端口")
        self.btn_apply_port.clicked.connect(self._save_port)
        port_row.addWidget(self.btn_apply_port)
        f_ctrl.addRow("端口", port_row)

        act_row = QHBoxLayout()
        self.lbl_ip = QLabel(f"本机 IP：{_local_ip()}")
        act_row.addWidget(self.lbl_ip)
        act_row.addStretch(1)
        self.btn_toggle = QPushButton("启动服务")
        self.btn_toggle.clicked.connect(self._toggle)
        act_row.addWidget(self.btn_toggle)
        self.lbl_status = QLabel("状态：已停止")
        self.lbl_status.setStyleSheet("color:#B45309;font-weight:bold;")
        act_row.addWidget(self.lbl_status)
        f_ctrl.addRow("", act_row)

        self.lbl_url = QLabel("服务地址：未启动")
        self.lbl_url.setStyleSheet("color:#2563eb;")
        f_ctrl.addRow("接口", self.lbl_url)

        # 默认开启开关
        auto_row = QHBoxLayout()
        self.chk_autostart = QCheckBox("应用启动时自动开启 OCR 服务")
        self.chk_autostart.setChecked(bool(cfg.get("ocr", "autostart", default=False)))
        self.chk_autostart.toggled.connect(self._on_autostart_toggled)
        auto_row.addWidget(self.chk_autostart)
        auto_row.addStretch(1)
        f_ctrl.addRow("自动启动", auto_row)

        root.addWidget(gb_ctrl)

        # ---------- 统计 + 本地测试（左右并排）----------
        mid = QHBoxLayout()

        gb_stats = QGroupBox("统计")
        fv = QVBoxLayout(gb_stats)
        self.lbl_stats = QLabel("等待服务…")
        self.lbl_stats.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.lbl_stats.setTextFormat(Qt.TextFormat.RichText)
        fv.addWidget(self.lbl_stats)
        self.btn_reset_stats = QPushButton("清零统计")
        self.btn_reset_stats.clicked.connect(self._reset_stats)
        fv.addWidget(self.btn_reset_stats, 0, Qt.AlignmentFlag.AlignLeft)
        mid.addWidget(gb_stats, 1)

        gb_test = QGroupBox("本地测试（输入图片 → 输出识别结果）")
        ft = QFormLayout(gb_test)
        self.test_img = QLineEdit()
        self.test_img.setPlaceholderText("选择验证码图片")
        self.btn_pick = QPushButton("选图…")
        self.btn_pick.clicked.connect(self._pick_img)
        ft.addRow("验证码图", self._hbox(self.test_img, self.btn_pick))

        self.test_target = QLineEdit()
        self.test_target.setPlaceholderText("滑块小图（target）")
        self.test_bg = QLineEdit()
        self.test_bg.setPlaceholderText("滑块背景图（background）")
        self.btn_pick_t = QPushButton("选图…")
        self.btn_pick_t.clicked.connect(lambda: self._pick_into(self.test_target))
        self.btn_pick_b = QPushButton("选图…")
        self.btn_pick_b.clicked.connect(lambda: self._pick_into(self.test_bg))
        ft.addRow("滑块小图", self._hbox(self.test_target, self.btn_pick_t))
        ft.addRow("滑块背景", self._hbox(self.test_bg, self.btn_pick_b))

        test_btns = QHBoxLayout()
        self.btn_test_ocr = QPushButton("测试 OCR")
        self.btn_test_ocr.clicked.connect(self._test_ocr)
        self.btn_test_slide = QPushButton("测试滑块")
        self.btn_test_slide.clicked.connect(self._test_slide)
        test_btns.addWidget(self.btn_test_ocr)
        test_btns.addWidget(self.btn_test_slide)
        ft.addRow("", test_btns)

        self.test_out = QTextEdit()
        self.test_out.setReadOnly(True)
        self.test_out.setMaximumHeight(90)
        ft.addRow("输出", self.test_out)
        mid.addWidget(gb_test, 2)

        root.addLayout(mid, 0)

        # ---------- 请求历史 ----------
        gb_hist = QGroupBox("请求历史（输入输出记录）")
        hv = QVBoxLayout(gb_hist)
        self.history = QTextEdit()
        self.history.setReadOnly(True)
        self.history.setStyleSheet("font-family: Consolas, monospace;")
        hv.addWidget(self.history)
        root.addWidget(gb_hist, 1)

        # ---------- 定时刷新（统计/历史）----------
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(500)

        # ---------- 自动启动（配置开启时，应用启动后自动开启服务）----------
        if self.chk_autostart.isChecked():
            QTimer.singleShot(300, self._autostart)

    # ---------- 辅助 ----------
    def _hbox(self, *widgets):
        box = QHBoxLayout()
        for w in widgets:
            box.addWidget(w)
        box.setStretch(0, 1)
        return box

    # ---------- 端口/地址 ----------
    def _save_port(self):
        port = self.port.value()
        self._ctx.config.set("ocr", "port", port)
        self._ctx.config.set("ocr", "host", self.host_combo.currentData())
        QMessageBox.information(self, "已保存", f"端口已保存为 {port}。下次启动服务时生效。")

    def _set_local(self):
        """一键设为本地端口：切换为仅本机访问。"""
        self.host_combo.setCurrentIndex(0)
        self._ctx.config.set("ocr", "host", LOCAL_HOST)
        self.lbl_url.setText("服务地址：已设为仅本机访问（127.0.0.1）")
        QMessageBox.information(self, "已设置", "已设为仅本机访问（127.0.0.1），外部设备无法访问。")

    def _on_autostart_toggled(self, checked: bool):
        """勾选/取消"应用启动时自动开启"。"""
        self._ctx.config.set("ocr", "autostart", bool(checked))

    def _autostart(self):
        """应用启动后自动启动 OCR 服务（模型加载慢，放后台线程）。"""
        def run():
            host = self.host_combo.currentData() or LOCAL_HOST
            port = self.port.value()
            try:
                self._server.start(host, port)
                log.info("OCR 服务已自动启动")
            except Exception as e:
                log.error("OCR 服务自动启动失败：%s", e, exc_info=True)
        threading.Thread(target=run, daemon=True).start()

    # ---------- 启停 ----------
    def _toggle(self):
        if self._server.is_running():
            self._server.stop()
        else:
            host = self.host_combo.currentData() or LOCAL_HOST
            port = self.port.value()
            try:
                self._server.start(host, port)
            except Exception as e:
                log.error("OCR 服务启动失败：%s", e, exc_info=True)
                QMessageBox.critical(self, "启动失败", str(e))
        self._update_status()

    def _update_status(self):
        if self._server.is_running():
            self.btn_toggle.setText("停止服务")
            self.lbl_status.setText("状态：运行中")
            self.lbl_status.setStyleSheet("color:#059669;font-weight:bold;")
            port = self._server.port
            lan = self._lan_ip  # 后台线程异步获取，可能为 None
            if lan and self._server.host == LAN_HOST:
                addr = f"服务地址：http://{lan}:{port}  （本机：http://127.0.0.1:{port}）"
            else:
                addr = f"服务地址：http://127.0.0.1:{port}"
            self.lbl_url.setText(
                addr + "\n"
                f"接口：POST /v1/ocr（字段 image）  POST /v1/slide（字段 target + background）"
            )
        else:
            self.btn_toggle.setText("启动服务")
            self.lbl_status.setText("状态：已停止")
            self.lbl_status.setStyleSheet("color:#B45309;font-weight:bold;")
            self.lbl_url.setText("服务地址：未启动")

    def _fetch_lan_ip(self):
        """后台线程：获取局域网 IP，成功后存入 self._lan_ip。"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(2)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            self._lan_ip = ip
        except Exception:
            self._lan_ip = None

    # ---------- 刷新统计与历史 ----------
    def _refresh(self):
        self._update_status()
        s = self._server.get_stats()
        total_ok = s["ocr_ok"] + s["slide_ok"]
        total_fail = s["ocr_fail"] + s["slide_fail"]
        self.lbl_stats.setText(
            f"<b>文字识别</b>：成功 <span style='color:#059669'>{s['ocr_ok']}</span> "
            f"/ 失败 <span style='color:#DC2626'>{s['ocr_fail']}</span><br><br>"
            f"<b>滑块识别</b>：成功 <span style='color:#059669'>{s['slide_ok']}</span> "
            f"/ 失败 <span style='color:#DC2626'>{s['slide_fail']}</span><br><br>"
            f"<b>合计</b>：成功 {total_ok} / 失败 {total_fail}"
        )
        # 历史只追加显示（倒序最近 30 条）
        hist = self._server.get_history()[-30:]
        lines = []
        for r in reversed(hist):
            line = f"[{r.get('time','')}] {r.get('type','')} | {r.get('ip','')} → {r.get('status','')}"
            if r.get("duration"):
                line += f" ({r['duration']})"
            if r.get("status") == "成功":
                line += f"\n    结果: {r.get('result','')}"
            elif r.get("error"):
                line += f"\n    错误: {r['error']}"
            elif r.get("result"):
                line += f"\n    信息: {r['result']}"
            lines.append(line)
        self.history.setPlainText("\n\n".join(lines))

    def _reset_stats(self):
        self._server.reset_stats()

    # ---------- 本地测试 ----------
    def _pick_img(self):
        """选择验证码图片填入。"""
        self._pick_into(self.test_img)

    def _pick_into(self, edit: QLineEdit):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "", "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)"
        )
        if path:
            edit.setText(path)

    def _ensure_running(self) -> bool:
        if not self._server.is_running():
            QMessageBox.warning(self, "提示", "请先启动服务再进行本地测试。")
            return False
        return True

    def _post_worker(self, url, files, on_ok, on_err):
        """在线程中发起 POST 请求，避免阻塞 UI。"""
        def run():
            try:
                import requests
                resp = requests.post(url, files=files, timeout=30)
                if resp.status_code == 200:
                    on_ok(resp.json())
                else:
                    on_err(f"HTTP {resp.status_code}: {resp.text}")
            except Exception as e:
                on_err(str(e))
        threading.Thread(target=run, daemon=True).start()

    def _test_ocr(self):
        if not self._ensure_running():
            return
        path = self.test_img.text().strip()
        if not path:
            QMessageBox.warning(self, "测试 OCR", "请先选择验证码图片。")
            return
        try:
            f = open(path, "rb")
        except Exception as e:
            QMessageBox.critical(self, "打开失败", str(e))
            return
        url = f"http://127.0.0.1:{self._server.port}/v1/ocr"
        self.test_out.setPlainText("识别中…")
        self.btn_test_ocr.setEnabled(False)

        def ok(data):
            f.close()
            self.test_out.setPlainText(f"识别结果：{data.get('result', '')}")
            self.btn_test_ocr.setEnabled(True)

        def err(msg):
            f.close()
            self.test_out.setPlainText(f"识别失败：{msg}")
            self.btn_test_ocr.setEnabled(True)

        self._post_worker(url, {"image": f}, ok, err)

    def _test_slide(self):
        if not self._ensure_running():
            return
        tp = self.test_target.text().strip()
        bp = self.test_bg.text().strip()
        if not tp or not bp:
            QMessageBox.warning(self, "测试滑块", "请先选择滑块小图(target)和背景图(background)。")
            return
        try:
            ft = open(tp, "rb")
            fb = open(bp, "rb")
        except Exception as e:
            QMessageBox.critical(self, "打开失败", str(e))
            return
        url = f"http://127.0.0.1:{self._server.port}/v1/slide"
        self.test_out.setPlainText("识别中…")
        self.btn_test_slide.setEnabled(False)

        def ok(data):
            ft.close()
            fb.close()
            tx = data.get("target_x", "未知")
            target = data.get("target", [])
            raw = data.get("raw", {})
            self.test_out.setPlainText(
                f"滑块目标 X（需移动距离）：{tx}\n"
                f"归一化 target：{target}\n"
                f"原始返回（用于不同版本兼容）：{raw}"
            )
            self.btn_test_slide.setEnabled(True)

        def err(msg):
            ft.close()
            fb.close()
            self.test_out.setPlainText(f"识别失败：{msg}")
            self.btn_test_slide.setEnabled(True)

        self._post_worker(url, {"target": ft, "background": fb}, ok, err)

    # ---------- 关闭 ----------
    def cleanup(self):
        """主窗口关闭时调用，停止 OCR 服务。"""
        if self._server.is_running():
            self._server.stop()
