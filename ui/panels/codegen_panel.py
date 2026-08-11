# -*- coding: utf-8 -*-
"""
代码生成面板：
- 左侧显示录制动作清单（支持 Shift+滚轮水平滚动）；
- 点击动作条目 → 右侧对应代码行高亮；
- 右侧代码窗口可编辑；
- 右侧按所选语言（Python/JS/TS/C#/Java）生成 Playwright 脚本，可复制/保存。
"""
import os
import warnings

from PySide6.QtCore import Qt, QRegularExpression, QLocale, Signal
from PySide6.QtGui import (
    QTextCursor, QColor, QSyntaxHighlighter, QTextCharFormat,
)
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QListWidget, QListWidgetItem,
    QComboBox, QTextEdit, QPushButton, QFileDialog, QMessageBox, QLabel,
    QApplication, QDialog, QFormLayout, QLineEdit, QStackedWidget,
    QDoubleSpinBox, QDialogButtonBox, QMenu, QCheckBox, QSpinBox,
    QAbstractItemView,
)

from core.recorder.codegen import generate_actions_only, LANGUAGES, ext_of, label_of
from core.recorder.action_models import Action, Selector
from config.settings import SCRIPT_DIR
from core.logging.logger import get_logger

log = get_logger("ui.codegen")


def _safe_disconnect(signal):
    """安全断开 Qt 信号连接，忽略未连接时的 RuntimeError/RuntimeWarning。"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            signal.disconnect()
        except (RuntimeError, Exception):
            pass


# ===================== 通用代码语法高亮 =====================
class _CodeHighlighter(QSyntaxHighlighter):
    """支持 Python / JS / TS / C# / Java 的基本语法高亮，颜色从配置读取。"""

    def __init__(self, document):
        super().__init__(document)
        self._rules = []

    def set_config(self, lang: str, colors: dict):
        """更新语言和颜色规则并重新高亮。"""
        self._colors = colors
        self._build_rules(lang)
        self.rehighlight()

    def _fmt(self, key: str) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self._colors.get(key, "#000000")))
        return fmt

    def _build_rules(self, lang: str):
        self._rules = []
        kw_fmt = self._fmt("code_keyword")
        str_fmt = self._fmt("code_string")
        cmt_fmt = self._fmt("code_comment")
        num_fmt = self._fmt("code_number")
        fn_fmt = self._fmt("code_func")

        # 按语言选择关键字集与注释符
        if lang == "python":
            kw = (r"\b(def|class|if|elif|else|for|while|try|except|finally|with|"
                   r"return|import|from|as|pass|break|continue|lambda|yield|global|"
                   r"nonlocal|assert|del|raise|in|is|not|and|or|None|True|False|"
                   r"async|await)\b")
            comment = r"#[^\n]*"
        elif lang in ("javascript", "typescript"):
            kw = (r"\b(var|let|const|function|class|if|else|for|while|switch|case|"
                   r"break|continue|return|try|catch|finally|throw|new|typeof|"
                   r"instanceof|in|of|delete|void|this|super|extends|yield|async|"
                   r"await|true|false|null|undefined)\b")
            comment = r"//[^\n]*"
        elif lang == "csharp":
            kw = (r"\b(public|private|protected|internal|static|void|class|interface|"
                   r"struct|enum|if|else|switch|case|for|foreach|while|do|break|"
                   r"continue|return|try|catch|finally|throw|new|using|namespace|"
                   r"get|set|true|false|null|async|await|var|string|int|bool|double|"
                   r"float)\b")
            comment = r"//[^\n]*"
        elif lang == "java":
            kw = (r"\b(public|private|protected|static|void|class|interface|enum|if|"
                   r"else|switch|case|for|while|do|break|continue|return|try|catch|"
                   r"finally|throw|new|import|package|extends|implements|this|super|"
                   r"true|false|null|abstract|final|synchronized|volatile|native|int|"
                   r"String|boolean|double|float|long|char|byte)\b")
            comment = r"//[^\n]*"
        else:
            kw = (r"\b(if|else|for|while|return|class|function|def|var|let|const|new|"
                   r"try|catch|public|private|void)\b")
            comment = r"(#|//)[^\n]*"

        self._rules.append((QRegularExpression(kw), kw_fmt))
        # 字符串（双引号 / 单引号）
        self._rules.append((QRegularExpression(r'"(?:[^"\\]|\\.)*"'), str_fmt))
        self._rules.append((QRegularExpression(r"'(?:[^'\\]|\\.)*'"), str_fmt))
        # 注释
        self._rules.append((QRegularExpression(comment), cmt_fmt))
        # 数字
        self._rules.append((QRegularExpression(r'\b\d+(?:\.\d+)?\b'), num_fmt))
        # 函数调用（标识符后跟左括号）
        self._rules.append((QRegularExpression(r'\b[a-zA-Z_]\w*(?=\s*\()'), fn_fmt))

    def highlightBlock(self, text: str):
        for pattern, fmt in self._rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), fmt)


# ===================== 支持拖拽排序 + Shift+滚轮水平滚动的列表 =====================
class _HScrollList(QListWidget):
    """支持上下拖拽排序，按住 Shift 滚动时改为水平滚动。"""
    reordered = Signal()  # 拖拽排序完成后发出

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - event.angleDelta().y())
            event.accept()
        else:
            super().wheelEvent(event)

    def dropEvent(self, event):
        super().dropEvent(event)
        self.reordered.emit()


# ===================== 动作详情编辑对话框 =====================
# 类型 → 需要哪些字段（选择器/值/URL/等待/验证码专属）
_NEEDS_SELECTOR = {"click", "fill", "select_option", "check", "hover", "fill_captcha", "slide_captcha", "check_retry", "slide_right"}
_NEEDS_VALUE = {"fill", "select_option", "press"}
_NEEDS_URL = {"navigate"}
_NEEDS_WAIT = {"wait"}
_NEEDS_CHECK = {"check"}
_NEEDS_PRESS = {"press"}
_NEEDS_SCROLL = {"scroll"}
_NEEDS_CAPTCHA_TEXT = {"fill_captcha"}
_NEEDS_CAPTCHA_SLIDE = {"slide_captcha"}

_ACTION_TYPES = [
    ("navigate", "导航 (navigate)"),
    ("click", "点击 (click)"),
    ("fill", "输入 (fill)"),
    ("select_option", "选择 (select_option)"),
    ("check", "勾选/取消勾选 (check)"),
    ("press", "按键 (press)"),
    ("hover", "悬停 (hover)"),
    ("scroll", "滚动 (scroll)"),
    ("wait", "等待 (wait)"),
    ("fill_captcha", "验证码识别 (fill_captcha)"),
    ("slide_captcha", "滑块验证码 (slide_captcha)"),
    ("slide_right", "滑动到最右侧 (slide_right)"),
    ("mark", "标记点 (mark)"),
    ("check_retry", "检查重试 (check_retry)"),
]


class ActionDetailDialog(QDialog):
    """动作详情编辑/新建对话框。非模态，根据类型动态显示字段。"""

    def __init__(self, parent, action=None, ctx=None, nav=None, enable_pick=True):
        super().__init__(parent)
        self.setWindowTitle("编辑动作" if action else "新建动作")
        self.setMinimumWidth(480)
        self.setModal(False)
        self._ctx = ctx
        self._nav = nav
        self._enable_pick = enable_pick
        self._result_action = None

        layout = QVBoxLayout(self)

        # ---- 类型选择 ----
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("动作类型："))
        self.type_combo = QComboBox()
        for k, label in _ACTION_TYPES:
            self.type_combo.addItem(label, k)
        type_row.addWidget(self.type_combo, 1)
        layout.addLayout(type_row)

        # ---- 选择器区域（CSS/XPath/ID）----
        sel_group = QFormLayout()
        css_row = QHBoxLayout()
        self.css_edit = QLineEdit()
        self.css_edit.setPlaceholderText("如 #search-btn 或 .submit > input")
        css_row.addWidget(self.css_edit, 1)
        self.btn_pick_css = QPushButton("拾取")
        self.btn_pick_css.setFixedWidth(60)
        css_row.addWidget(self.btn_pick_css)
        sel_group.addRow("CSS 选择器：", css_row)

        self.xpath_edit = QLineEdit()
        self.xpath_edit.setPlaceholderText("//div[@id='main']//button")
        sel_group.addRow("XPath：", self.xpath_edit)

        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("#search-btn")
        sel_group.addRow("ID 选择器：", self.id_edit)
        layout.addLayout(sel_group)

        # ---- 类型专属字段（QStackedWidget）----
        self.stack = QStackedWidget()

        # page 0: 空（click/hover 等无额外字段）
        self.stack.addWidget(QWidget())

        # page 1: navigate（URL）
        page_nav = QWidget()
        nav_form = QFormLayout(page_nav)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://example.com")
        nav_form.addRow("目标 URL：", self.url_edit)
        self.stack.addWidget(page_nav)

        # page 2: fill/select_option（值）
        page_value = QWidget()
        val_form = QFormLayout(page_value)
        self.value_edit = QLineEdit()
        self.value_edit.setPlaceholderText("要输入的文本或选项值")
        val_form.addRow("值：", self.value_edit)
        self.stack.addWidget(page_value)

        # page 3: press（按键名）
        page_press = QWidget()
        press_form = QFormLayout(page_press)
        self.press_edit = QLineEdit()
        self.press_edit.setPlaceholderText("Enter / Tab / Escape / ArrowDown …")
        press_form.addRow("按键：", self.press_edit)
        self.stack.addWidget(page_press)

        # page 4: scroll（X/Y）
        page_scroll = QWidget()
        scroll_form = QFormLayout(page_scroll)
        scroll_box = QHBoxLayout()
        self.scroll_x = QSpinBox()
        self.scroll_x.setRange(0, 99999)
        self.scroll_y = QSpinBox()
        self.scroll_y.setRange(0, 99999)
        scroll_box.addWidget(QLabel("X:"))
        scroll_box.addWidget(self.scroll_x)
        scroll_box.addWidget(QLabel("Y:"))
        scroll_box.addWidget(self.scroll_y)
        scroll_form.addRow("坐标：", scroll_box)
        self.stack.addWidget(page_scroll)

        # page 5: check（勾选状态）
        page_check = QWidget()
        check_form = QFormLayout(page_check)
        self.check_combo = QComboBox()
        self.check_combo.addItem("勾选", "checked")
        self.check_combo.addItem("取消勾选", "unchecked")
        check_form.addRow("状态：", self.check_combo)
        self.stack.addWidget(page_check)

        # page 6: wait（等待秒数）
        page_wait = QWidget()
        wait_form = QFormLayout(page_wait)
        self.wait_spin = QDoubleSpinBox()
        self.wait_spin.setRange(0.1, 300.0)
        self.wait_spin.setSingleStep(0.5)
        self.wait_spin.setValue(2.0)
        self.wait_spin.setSuffix(" 秒")
        self.wait_spin.setLocale(QLocale.c())  # 强制点号，避免中文逗号
        wait_form.addRow("等待时间：", self.wait_spin)
        self.stack.addWidget(page_wait)

        # page 7: fill_captcha（验证码图片选择器 + OCR 地址）
        page_cap_text = QWidget()
        ct_form = QFormLayout(page_cap_text)
        cap_img_row = QHBoxLayout()
        self.captcha_img_edit = QLineEdit()
        self.captcha_img_edit.setPlaceholderText("验证码图片 CSS 选择器，如 #captcha-img")
        cap_img_row.addWidget(self.captcha_img_edit, 1)
        self.btn_pick_cap_img = QPushButton("拾取")
        self.btn_pick_cap_img.setFixedWidth(60)
        cap_img_row.addWidget(self.btn_pick_cap_img)
        ct_form.addRow("图片选择器：", cap_img_row)
        self.captcha_ocr_edit = QLineEdit("http://127.0.0.1:8848")
        ct_form.addRow("OCR 地址：", self.captcha_ocr_edit)
        self.stack.addWidget(page_cap_text)

        # page 8: slide_captcha（小图+背景图+拖拽按钮+OCR）
        page_cap_slide = QWidget()
        cs_form = QFormLayout(page_cap_slide)
        slide_target_row = QHBoxLayout()
        self.slide_target_edit = QLineEdit()
        self.slide_target_edit.setPlaceholderText("滑块小图 CSS 选择器")
        slide_target_row.addWidget(self.slide_target_edit, 1)
        self.btn_pick_slide_target = QPushButton("拾取")
        self.btn_pick_slide_target.setFixedWidth(60)
        slide_target_row.addWidget(self.btn_pick_slide_target)
        cs_form.addRow("小图选择器：", slide_target_row)
        slide_bg_row = QHBoxLayout()
        self.slide_bg_edit = QLineEdit()
        self.slide_bg_edit.setPlaceholderText("背景图 CSS 选择器")
        slide_bg_row.addWidget(self.slide_bg_edit, 1)
        self.btn_pick_slide_bg = QPushButton("拾取")
        self.btn_pick_slide_bg.setFixedWidth(60)
        slide_bg_row.addWidget(self.btn_pick_slide_bg)
        cs_form.addRow("背景图选择器：", slide_bg_row)
        self.slide_ocr_edit = QLineEdit("http://127.0.0.1:8848")
        cs_form.addRow("OCR 地址：", self.slide_ocr_edit)
        self.stack.addWidget(page_cap_slide)

        # page 9: mark（标记名称，可选）
        page_mark = QWidget()
        mark_form = QFormLayout(page_mark)
        self.mark_name_edit = QLineEdit()
        self.mark_name_edit.setPlaceholderText("可选，仅用于日志标识")
        mark_form.addRow("标记名称：", self.mark_name_edit)
        mark_hint = QLabel("此动作作为「检查重试」的跳转起点，回放时不执行任何操作。")
        mark_hint.setWordWrap(True)
        mark_form.addRow(mark_hint)
        self.stack.addWidget(page_mark)

        # page 10: check_retry（最大重试次数 + 预期文本，成功标志选择器用通用 CSS 区域）
        page_retry = QWidget()
        retry_form = QFormLayout(page_retry)
        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(1, 99)
        self.retry_spin.setValue(3)
        self.retry_spin.setSuffix(" 次")
        retry_form.addRow("最大重试：", self.retry_spin)
        self.expected_text_edit = QLineEdit()
        self.expected_text_edit.setPlaceholderText("留空只检查元素是否存在；填写则额外校验文本")
        retry_form.addRow("预期文本：", self.expected_text_edit)
        retry_hint = QLabel("检查上方填写的 CSS 选择器元素是否存在。若填写了「预期文本」，"
                            "还需元素文本包含该值才算通过。未通过则跳回上一个「标记点」"
                            "重新执行，超过次数则报错中止。")
        retry_hint.setWordWrap(True)
        retry_form.addRow(retry_hint)
        self.stack.addWidget(page_retry)

        layout.addWidget(self.stack)

        # ---- 按钮 ----
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.close)
        layout.addWidget(btns)

        # 初始化数据
        if action:
            self._load_action(action)
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        self._on_type_changed()

    def _make_pick_handler(self, on_fill):
        """返回拾取按钮的点击回调。on_fill(selector) 负责把结果填入输入框（可填多处）。"""
        def handler():
            browser = getattr(self._ctx, "browser", None) if self._ctx else None
            if browser is None:
                QMessageBox.warning(self, "提示", "内置浏览器尚未初始化。")
                return
            main_win = self.window()
            nav = getattr(main_win, "nav", None)
            if nav is None:
                nav = getattr(self, "_nav", None)
            recorder = getattr(self._ctx, "recorder", None) if self._ctx else None
            was_recording = recorder.is_recording() if recorder else False
            if was_recording:
                recorder.stop()
            # 记录原始导航行，拾取完成后恢复
            orig_row = nav.currentRow() if nav is not None else -1
            if nav is not None:
                nav.setCurrentRow(0)  # 切到浏览器
            # 隐藏本对话框及所有父级对话框（露出浏览器）
            self.hide()
            self._hide_ancestor_dialogs()

            def on_picked(selector):
                if selector:
                    on_fill(selector)
                if was_recording and recorder is not None:
                    recorder.start()
                # 恢复导航行与对话框显示
                if nav is not None and orig_row >= 0:
                    nav.setCurrentRow(orig_row)
                self._show_ancestor_dialogs()
                self.show()
                self.raise_()
                self.activateWindow()

            browser.start_pick_mode(on_picked)
        return handler

    def _hide_ancestor_dialogs(self):
        """隐藏所有父级 QDialog（拾取时露出浏览器）。"""
        p = self.parent()
        while p is not None:
            if isinstance(p, QDialog):
                p.hide()
            p = p.parent()

    def _show_ancestor_dialogs(self):
        """恢复所有父级 QDialog 的显示。"""
        p = self.parent()
        while p is not None:
            if isinstance(p, QDialog):
                p.show()
            p = p.parent()

    def _fill_css_and_id(self, selector):
        """拾取结果填入 CSS 框；若为 #id 形式，同时填入 ID 框。"""
        self.css_edit.setText(selector)
        if selector.startswith("#"):
            id_part = selector[1:]
            if id_part and " " not in selector and "." not in selector:
                self.id_edit.setText(id_part)

    def _on_type_changed(self):
        """类型切换 → 显示/隐藏选择器和类型专属字段。"""
        t = self.type_combo.currentData()

        # 选择器区域可见性
        show_sel = t in _NEEDS_SELECTOR
        self.css_edit.setEnabled(show_sel)
        self.xpath_edit.setEnabled(show_sel)
        self.id_edit.setEnabled(show_sel)
        self.btn_pick_css.setEnabled(show_sel and self._enable_pick)

        # 拾取按钮连接
        _safe_disconnect(self.btn_pick_css)
        if show_sel:
            self.btn_pick_css.clicked.connect(
                self._make_pick_handler(lambda s: self._fill_css_and_id(s)))

        # 验证码类型拾取按钮
        for btn, edit in [
            (self.btn_pick_cap_img, self.captcha_img_edit),
            (self.btn_pick_slide_target, self.slide_target_edit),
            (self.btn_pick_slide_bg, self.slide_bg_edit),
        ]:
            btn.setEnabled(self._enable_pick)
            _safe_disconnect(btn)
            btn.clicked.connect(self._make_pick_handler(lambda s, e=edit: e.setText(s)))

        # 类型专属页面切换
        page_map = {
            "navigate": 1, "fill": 2, "select_option": 2,
            "press": 3, "scroll": 4, "check": 5,
            "wait": 6, "fill_captcha": 7, "slide_captcha": 8,
            "mark": 9, "check_retry": 10, "slide_right": 0,
        }
        self.stack.setCurrentIndex(page_map.get(t, 0))

    def _load_action(self, action):
        """从 Action 对象加载到 UI 控件。"""
        a = action
        idx = self.type_combo.findData(a.type)
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        if a.selector:
            self.css_edit.setText(a.selector.css or "")
            self.xpath_edit.setText(a.selector.xpath or "")
            self.id_edit.setText(a.selector.id or "")
        self.url_edit.setText(a.url or "")
        self.value_edit.setText(a.value or "")
        self.press_edit.setText(a.value or "")
        self.captcha_img_edit.setText(a.image_selector or "")
        self.captcha_ocr_edit.setText(a.value or "http://127.0.0.1:8848")
        self.slide_target_edit.setText(a.image_selector or "")
        self.slide_bg_edit.setText(a.background_selector or "")
        self.slide_ocr_edit.setText(a.value or "http://127.0.0.1:8848")
        if a.type == "check" and a.value:
            ci = self.check_combo.findData(a.value)
            if ci >= 0:
                self.check_combo.setCurrentIndex(ci)
        if a.type == "wait" and a.value:
            try:
                self.wait_spin.setValue(float(a.value))
            except (ValueError, TypeError):
                pass
        if a.type == "scroll" and a.value:
            try:
                import json as _json
                xy = _json.loads(a.value or "{}")
                self.scroll_x.setValue(int(xy.get("x", 0)))
                self.scroll_y.setValue(int(xy.get("y", 0)))
            except Exception:
                pass
        if a.type == "mark" and a.value:
            self.mark_name_edit.setText(a.value)
        if a.type == "check_retry" and a.value:
            try:
                self.retry_spin.setValue(int(a.value))
            except (ValueError, TypeError):
                pass
        if a.expected_text:
            self.expected_text_edit.setText(a.expected_text)

    def _build_selector(self):
        """从输入框构建 Selector 对象。"""
        css = self.css_edit.text().strip() or None
        xpath = self.xpath_edit.text().strip() or None
        id_sel = self.id_edit.text().strip() or None
        if not css and not xpath and not id_sel:
            return None
        return Selector(css=css, xpath=xpath, id=id_sel)

    def _on_ok(self):
        """构建 Action 并关闭。"""
        t = self.type_combo.currentData()

        # 校验
        if t in _NEEDS_SELECTOR and not self._build_selector():
            QMessageBox.warning(self, "提示", "请填写至少一个选择器（CSS / XPath / ID）。")
            return
        if t in _NEEDS_URL and not self.url_edit.text().strip():
            QMessageBox.warning(self, "提示", "请填写目标 URL。")
            return
        if t in _NEEDS_VALUE and not self.value_edit.text().strip():
            QMessageBox.warning(self, "提示", "请填写输入值。")
            return
        if t in _NEEDS_PRESS and not self.press_edit.text().strip():
            QMessageBox.warning(self, "提示", "请填写按键名称。")
            return

        # 构建
        kwargs = {"type": t}
        if t in _NEEDS_SELECTOR:
            kwargs["selector"] = self._build_selector()
        if t == "navigate":
            kwargs["url"] = self.url_edit.text().strip()
        elif t in _NEEDS_VALUE:
            kwargs["value"] = self.value_edit.text()
        elif t in _NEEDS_PRESS:
            kwargs["value"] = self.press_edit.text()
        elif t == "check":
            kwargs["value"] = self.check_combo.currentData()
        elif t == "wait":
            kwargs["value"] = str(self.wait_spin.value())
        elif t == "scroll":
            import json as _json
            kwargs["value"] = _json.dumps({
                "x": self.scroll_x.value(),
                "y": self.scroll_y.value(),
            })
        elif t == "fill_captcha":
            kwargs["value"] = self.captcha_ocr_edit.text().strip() or "http://127.0.0.1:8848"
            kwargs["image_selector"] = self.captcha_img_edit.text().strip()
        elif t == "slide_captcha":
            kwargs["value"] = self.slide_ocr_edit.text().strip() or "http://127.0.0.1:8848"
            kwargs["image_selector"] = self.slide_target_edit.text().strip()
            kwargs["background_selector"] = self.slide_bg_edit.text().strip()
        elif t == "slide_right":
            pass  # 只需要 selector（滑块按钮），用通用 CSS 区域
        elif t == "mark":
            kwargs["value"] = self.mark_name_edit.text().strip()
        elif t == "check_retry":
            kwargs["value"] = str(self.retry_spin.value())
            et = self.expected_text_edit.text().strip()
            if et:
                kwargs["expected_text"] = et

        self._result_action = Action(**kwargs)
        self.close()

    def result_action(self):
        """获取构建的 Action 对象，取消时返回 None。"""
        return self._result_action


# ===================== 代码生成面板 =====================
class CodegenPanel(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self._ctx = ctx
        self._line_map = []          # 动作 → 代码起始行号映射
        self._highlighted_line = -1  # 当前高亮的行

        root = QHBoxLayout(self)

        # ---- 左：动作列表 ----
        left = QVBoxLayout()
        hint = QLabel("录制动作（Shift + 鼠标滚轮可左右滚动，点击条目高亮对应代码）")
        hint.setWordWrap(True)
        left.addWidget(hint)
        self.list_widget = _HScrollList()
        self.list_widget.currentRowChanged.connect(self._on_action_selected)
        self.list_widget.reordered.connect(self._on_list_reordered)
        # 右键菜单
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        left.addWidget(self.list_widget, 1)

        # 动作编辑按钮
        edit_row = QHBoxLayout()
        self.btn_delete = QPushButton("删除选中步骤")
        self.btn_delete.clicked.connect(self._delete_step)
        self.btn_up = QPushButton("上移")
        self.btn_up.clicked.connect(self._move_up)
        self.btn_down = QPushButton("下移")
        self.btn_down.clicked.connect(self._move_down)
        edit_row.addWidget(self.btn_delete)
        edit_row.addWidget(self.btn_up)
        edit_row.addWidget(self.btn_down)
        left.addLayout(edit_row)

        # 特殊步骤插入
        special_row = QHBoxLayout()
        self.btn_add_action = QPushButton("新建动作…")
        self.btn_add_action.clicked.connect(lambda: self._edit_action_dialog(new_action=True))
        special_row.addWidget(self.btn_add_action)
        self.btn_add_wait = QPushButton("插入等待…")
        self.btn_add_wait.clicked.connect(self._quick_insert_wait)
        special_row.addWidget(self.btn_add_wait)
        self.btn_add_captcha = QPushButton("插入验证码识别…")
        self.btn_add_captcha.clicked.connect(self._add_captcha_step)
        special_row.addWidget(self.btn_add_captcha)
        self.btn_add_mark = QPushButton("插入标记点…")
        self.btn_add_mark.clicked.connect(self._quick_insert_mark)
        special_row.addWidget(self.btn_add_mark)
        special_row.addStretch(1)
        left.addLayout(special_row)

        self.btn_loadfile = QPushButton("从文件载入动作…")
        self.btn_loadfile.clicked.connect(self._load_file)
        left.addWidget(self.btn_loadfile)
        root.addLayout(left, 1)

        # ---- 右：代码生成 ----
        right = QVBoxLayout()
        row = QHBoxLayout()
        row.addWidget(QLabel("目标语言："))
        self.lang = QComboBox()
        for k in LANGUAGES:
            self.lang.addItem(label_of(k), k)
        self.lang.currentIndexChanged.connect(self._generate)
        row.addWidget(self.lang)
        row.addStretch(1)
        self.btn_copy = QPushButton("复制到剪贴板")
        self.btn_copy.clicked.connect(self._copy)
        self.btn_save = QPushButton("保存为脚本")
        self.btn_save.clicked.connect(self._save)
        row.addWidget(self.btn_copy)
        row.addWidget(self.btn_save)
        right.addLayout(row)

        self.code_view = QTextEdit()
        self.code_view.setReadOnly(False)   # 允许编辑
        self.code_view.setPlaceholderText("录制动作后将在此生成脚本…")
        self.code_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._apply_code_bg()
        right.addWidget(self.code_view, 1)

        # 语法高亮器
        self._highlighter = _CodeHighlighter(self.code_view.document())

        root.addLayout(right, 2)

        if self._ctx.recorder is not None:
            self._ctx.recorder.actions_changed.connect(self._refresh)
        self._refresh()

    # ---------- 动作摘要 ----------
    @staticmethod
    def _sel_text(a: Action) -> str:
        s = a.selector
        if not s:
            return ""
        return s.text or s.id or s.css or s.tag or ""

    def _summary(self, a: Action) -> str:
        t = a.type
        if t == "navigate":
            return "[导航] " + str(a.url or "")
        if t == "click":
            return "[点击] " + self._sel_text(a)
        if t == "fill":
            return "[输入] " + self._sel_text(a) + " = " + str(a.value or "")
        if t == "select_option":
            return "[选择] " + self._sel_text(a) + " = " + str(a.value or "")
        if t == "check":
            return "[勾选] " + self._sel_text(a) + " " + str(a.value or "")
        if t == "press":
            return "[按键] " + str(a.value or "")
        if t == "hover":
            return "[悬停] " + self._sel_text(a)
        if t == "scroll":
            return "[滚动] " + str(a.value or "")
        if t == "wait":
            return "[等待] " + str(a.value or "1") + " 秒"
        if t == "fill_captcha":
            return "[验证码识别] 图片(" + str(a.image_selector or "") + ") → 填入 " + self._sel_text(a)
        if t == "slide_captcha":
            return "[滑块验证码] 小图(" + str(a.image_selector or "") + ") 背景(" + \
                   str(a.background_selector or "") + ") → 拖拽 " + self._sel_text(a)
        if t == "slide_right":
            return "[滑动到最右侧] 拖拽 " + self._sel_text(a)
        if t == "mark":
            return "[标记点] " + str(a.value or "重试起点")
        if t == "check_retry":
            css = ""
            if a.selector:
                css = a.selector.css or a.selector.id or ""
            return "[检查重试] 成功标志(%s) 最多 %s 次" % (css, a.value or "3")
        return "[" + str(t) + "]"

    # ---------- 刷新 ----------
    def _refresh(self):
        self.list_widget.clear()
        for i, a in enumerate(self._ctx.recorder.actions()):
            item = QListWidgetItem("☰  %d. %s" % (i + 1, self._summary(a)))
            item.setData(Qt.ItemDataRole.UserRole, i)  # 记录原始索引，供拖拽排序用
            self.list_widget.addItem(item)
        self._generate()

    def _on_list_reordered(self):
        """拖拽排序后：据新顺序静默重排 recorder 数据，更新序号，重新生成代码。"""
        new_order = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            new_order.append(item.data(Qt.ItemDataRole.UserRole))
        # 静默重排（不发信号，避免重建列表打断拖拽结果）
        self._ctx.recorder.reorder(new_order, emit=False)
        # 更新每个 item 的序号文本与 UserRole 为当前新位置
        actions = self._ctx.recorder.actions()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setText("☰  %d. %s" % (i + 1, self._summary(actions[i])))
            item.setData(Qt.ItemDataRole.UserRole, i)
        self._generate()

    def _generate(self):
        actions = self._ctx.recorder.actions()
        if not actions:
            self.code_view.setPlainText("# 暂无录制动作。请先在浏览器面板点击「开始录制」并操作网页。")
            self._line_map = []
            return
        lang = self.lang.currentData()
        # 更新语法高亮规则（在设置文本之前，确保首次渲染即生效）
        self._highlighter.set_config(lang, self._get_colors())
        try:
            # 只生成每一步动作对应的代码，不带 import/启动/关闭等模板
            code, line_map = generate_actions_only(actions, lang)
            self.code_view.setPlainText(code)
            self._line_map = line_map
            self._highlighted_line = -1
        except Exception as e:
            log.exception("代码生成失败")
            self.code_view.setPlainText("代码生成失败：" + str(e))
            self._line_map = []

    # ---------- 点击动作 → 高亮对应代码行 ----------
    def _on_action_selected(self, row: int):
        if row < 0 or row >= len(self._line_map):
            return
        line = self._line_map[row]

        # 先清除上次高亮（把整行背景设回默认）
        if self._highlighted_line >= 0:
            self._set_line_bg(self._highlighted_line, None)

        # 高亮当前行
        self._set_line_bg(line, self._highlight_color())
        self._highlighted_line = line

        # 滚动到该行并选中
        cursor = QTextCursor(self.code_view.document().findBlockByNumber(line))
        self.code_view.setTextCursor(cursor)
        self.code_view.ensureCursorVisible()

    def _set_line_bg(self, line: int, color):
        """设置某一行的背景色；color=None 表示清除高亮。"""
        from PySide6.QtGui import QTextBlockFormat
        block = self.code_view.document().findBlockByNumber(line)
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        fmt = QTextBlockFormat()
        if color is not None:
            fmt.setBackground(color)
        cursor.setBlockFormat(fmt)

    # ---------- 从配置读取颜色 ----------
    def _get_colors(self) -> dict:
        return self._ctx.config.get("colors", default={}) or {}

    def _apply_code_bg(self):
        bg = self._get_colors().get("code_bg", "#FFFFFF")
        self.code_view.setStyleSheet(f"QTextEdit {{ background-color: {bg}; }}")

    def _highlight_color(self) -> QColor:
        return QColor(self._get_colors().get("highlight_bg", "#FFEB8C"))

    # ---------- 动作编辑 ----------
    def _delete_step(self):
        row = self.list_widget.currentRow()
        if row < 0:
            return
        self._ctx.recorder.remove_action(row)
        # actions_changed 信号会自动触发 _refresh
        count = self.list_widget.count()
        if count > 0:
            self.list_widget.setCurrentRow(min(row, count - 1))

    def _move_up(self):
        row = self.list_widget.currentRow()
        if row < 0:
            return
        self._ctx.recorder.move_action(row, -1)
        if row - 1 >= 0:
            self.list_widget.setCurrentRow(row - 1)

    def _move_down(self):
        row = self.list_widget.currentRow()
        if row < 0:
            return
        self._ctx.recorder.move_action(row, 1)
        if row + 1 < self.list_widget.count():
            self.list_widget.setCurrentRow(row + 1)

    # ---------- 右键菜单 ----------
    def _show_context_menu(self, pos):
        """右键菜单：编辑/新建/插入等待/复制/删除。"""
        item = self.list_widget.itemAt(pos)
        row = self.list_widget.row(item) if item else -1
        menu = QMenu(self)

        has_selection = row >= 0 and row < self.list_widget.count()

        if has_selection:
            act_edit = menu.addAction("编辑动作…")
            act_edit.triggered.connect(lambda: self._edit_action_dialog(row=row))
        act_new = menu.addAction("新建动作…")
        act_new.triggered.connect(lambda: self._edit_action_dialog(new_action=True))
        act_wait = menu.addAction("插入等待…")
        act_wait.triggered.connect(lambda: self._quick_insert_wait(row))
        act_mark = menu.addAction("插入标记点…")
        act_mark.triggered.connect(lambda: self._quick_insert_mark(row))
        if has_selection:
            menu.addSeparator()
            act_copy = menu.addAction("复制此动作")
            act_copy.triggered.connect(lambda: self._duplicate_action(row))
            act_del = menu.addAction("删除此动作")
            act_del.triggered.connect(lambda: self._ctx.recorder.remove_action(row))

        menu.exec(self.list_widget.mapToGlobal(pos))

    # ---------- 动作详情编辑/新建 ----------
    def _edit_action_dialog(self, row=None, new_action=False):
        """打开动作详情对话框进行编辑或新建。
        - new_action=True：新建模式，插入到当前选中行之后
        - row=N：编辑指定行
        """
        existing = None
        edit_row = row
        if new_action:
            cur = self.list_widget.currentRow()
            edit_row = cur + 1 if cur >= 0 else self._ctx.recorder.actions_count()
        elif row is not None:
            actions = self._ctx.recorder.actions()
            if 0 <= row < len(actions):
                existing = actions[row]

        dlg = ActionDetailDialog(self, action=existing, ctx=self._ctx)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

        def on_finished():
            action = dlg.result_action()
            if action is None:
                return
            if new_action or row is None:
                self._ctx.recorder.insert_action(edit_row, action)
                log.info("新建动作到位置 %d，类型=%s", edit_row, action.type)
                self.list_widget.setCurrentRow(edit_row)
            else:
                self._ctx.recorder.update_action(edit_row, action)
                log.info("更新动作 %d，类型=%s", edit_row, action.type)
                self.list_widget.setCurrentRow(edit_row)

        # 对话框关闭后回调
        dlg.finished.connect(lambda _: on_finished())

    def _quick_insert_wait(self, row=None):
        """快速插入等待步骤（默认 2 秒）。"""
        if row is None:
            cur = self.list_widget.currentRow()
            insert_at = cur + 1 if cur >= 0 else self._ctx.recorder.actions_count()
        else:
            insert_at = row + 1 if row >= 0 else self._ctx.recorder.actions_count()
        action = Action(type="wait", value="2.0")
        self._ctx.recorder.insert_action(insert_at, action)
        log.info("插入等待步骤到位置 %d", insert_at)
        self.list_widget.setCurrentRow(insert_at)

    def _quick_insert_mark(self, row=None):
        """快速插入标记点（作为检查重试的跳转起点）。"""
        if row is None:
            cur = self.list_widget.currentRow()
            insert_at = cur + 1 if cur >= 0 else self._ctx.recorder.actions_count()
        else:
            insert_at = row + 1 if row >= 0 else self._ctx.recorder.actions_count()
        action = Action(type="mark", value="")
        self._ctx.recorder.insert_action(insert_at, action)
        log.info("插入标记点到位置 %d", insert_at)
        self.list_widget.setCurrentRow(insert_at)

    def _duplicate_action(self, row):
        """深拷贝并插入到下一行。"""
        actions = self._ctx.recorder.actions()
        if 0 <= row < len(actions):
            original = actions[row]
            copy = Action.from_dict(original.to_dict())
            self._ctx.recorder.insert_action(row + 1, copy)
            log.info("复制动作 %d → %d", row, row + 1)
            self.list_widget.setCurrentRow(row + 1)

    # ---------- 复制 / 保存 ----------
    def _copy(self):
        QApplication.clipboard().setText(self.code_view.toPlainText())
        self.btn_copy.setText("已复制")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1200, lambda: self.btn_copy.setText("复制到剪贴板"))

    def _save(self):
        lang = self.lang.currentData()
        code = self.code_view.toPlainText()
        if not code.strip():
            return
        default = os.path.join(SCRIPT_DIR, "rpa_script." + ext_of(lang))
        path, _ = QFileDialog.getSaveFileName(
            self, "保存脚本", default, f"{label_of(lang)} 脚本 (*.{ext_of(lang)})"
        )
        if path:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(code)
                QMessageBox.information(self, "已保存", "脚本已保存到：\n" + path)
            except Exception as e:
                QMessageBox.critical(self, "保存失败", str(e))

    def _load_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "载入录制动作", "", "JSON (*.json)")
        if path:
            try:
                self._ctx.recorder.load(path)
            except Exception as e:
                QMessageBox.critical(self, "载入失败", str(e))

    # ---------- 插入验证码识别步骤 ----------
    def _make_pick_btn(self, target_edit, dlg):
        """为选择器输入框创建"拾取"按钮：点击后转到浏览器拾取元素。"""
        btn = QPushButton("拾取")
        btn.setFixedWidth(60)
        btn.setToolTip("点击后转到内置浏览器，左键单击页面元素来获取 CSS 选择器")
        btn.clicked.connect(lambda: self._start_pick(target_edit, dlg))
        return btn

    def _start_pick(self, target_edit, dlg):
        """隐藏对话框→暂停录制→切到浏览器→拾取元素→恢复录制→回来填入。"""
        browser = getattr(self._ctx, "browser", None)
        if browser is None:
            QMessageBox.warning(self, "提示", "内置浏览器尚未初始化。")
            return
        main_win = self.window()
        nav = getattr(main_win, "nav", None)
        # 临时暂停录制，避免拾取时的点击被录进去
        recorder = getattr(self._ctx, "recorder", None)
        was_recording = recorder.is_recording() if recorder else False
        if was_recording:
            recorder.stop()
        # 切到浏览器 Tab（第一个）
        if nav is not None:
            nav.setCurrentRow(0)
        # 隐藏对话框，露出浏览器
        dlg.hide()
        # 启动拾取
        def on_picked(selector):
            if selector:
                target_edit.setText(selector)
            # 恢复之前的录制状态
            if was_recording and recorder is not None:
                recorder.start()
            # 切回代码生成 Tab（第二个）
            if nav is not None:
                nav.setCurrentRow(1)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
        browser.start_pick_mode(on_picked)

    def _add_captcha_step(self):
        """弹出对话框配置验证码/滑块识别步骤，插入到当前选中位置之后。
        使用非模态对话框，避免拾取时 hide/show 导致 exec() 提前返回。"""
        from PySide6.QtWidgets import QDialogButtonBox, QStackedWidget, QRadioButton

        default_ocr = "http://127.0.0.1:%d" % int(self._ctx.config.get("ocr", "port", default=8848))

        dlg = QDialog(self)
        dlg.setWindowTitle("插入验证码识别步骤")
        dlg.setMinimumWidth(460)
        dlg.setModal(False)
        layout = QVBoxLayout(dlg)

        # ---- 模式切换 ----
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("识别类型："))
        rb_text = QRadioButton("文字验证码")
        rb_slide = QRadioButton("滑块验证码")
        rb_text.setChecked(True)
        mode_row.addWidget(rb_text)
        mode_row.addWidget(rb_slide)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        # ---- 堆叠页面：文字 vs 滑块 ----
        stack = QStackedWidget()
        layout.addWidget(stack)

        # -- 文字验证码页 --
        page_text = QWidget()
        ft = QFormLayout(page_text)
        img_edit = QLineEdit()
        img_edit.setPlaceholderText("如 #captcha-img 或 .captcha-image")
        img_row = QHBoxLayout(); img_row.addWidget(img_edit, 1)
        img_row.addWidget(self._make_pick_btn(img_edit, dlg))
        ft.addRow("验证码图片选择器（CSS）：", img_row)
        input_edit = QLineEdit()
        input_edit.setPlaceholderText("如 #captcha-input 或 input[name='code']")
        input_row = QHBoxLayout(); input_row.addWidget(input_edit, 1)
        input_row.addWidget(self._make_pick_btn(input_edit, dlg))
        ft.addRow("验证码输入框选择器（CSS）：", input_row)
        stack.addWidget(page_text)

        # -- 滑块验证码页 --
        page_slide = QWidget()
        fs = QFormLayout(page_slide)
        target_edit = QLineEdit()
        target_edit.setPlaceholderText("滑块小图选择器，如 .nc_iconfont.btn_slide")
        target_row = QHBoxLayout(); target_row.addWidget(target_edit, 1)
        target_row.addWidget(self._make_pick_btn(target_edit, dlg))
        fs.addRow("滑块小图选择器（CSS）：", target_row)
        bg_edit = QLineEdit()
        bg_edit.setPlaceholderText("背景大图选择器，如 #bg-canvas 或 .bg-img")
        bg_row = QHBoxLayout(); bg_row.addWidget(bg_edit, 1)
        bg_row.addWidget(self._make_pick_btn(bg_edit, dlg))
        fs.addRow("背景图选择器（CSS）：", bg_row)
        slider_edit = QLineEdit()
        slider_edit.setPlaceholderText("要拖拽的滑块按钮选择器，如 .slider-btn")
        slider_row = QHBoxLayout(); slider_row.addWidget(slider_edit, 1)
        slider_row.addWidget(self._make_pick_btn(slider_edit, dlg))
        fs.addRow("滑块拖拽按钮选择器（CSS）：", slider_row)
        stack.addWidget(page_slide)

        # 模式切换 → 切换堆叠页面
        rb_text.toggled.connect(lambda checked: stack.setCurrentIndex(0 if checked else 1))

        # ---- OCR 地址（两种模式共用）----
        form_common = QFormLayout()
        ocr_edit = QLineEdit(default_ocr)
        ocr_row = QHBoxLayout()
        ocr_row.addWidget(ocr_edit, 1)
        btn_local_ocr = QPushButton("设为本地 OCR 地址")
        btn_local_ocr.setToolTip("一键填入本机 OCR 服务地址（127.0.0.1:端口）")
        btn_local_ocr.clicked.connect(
            lambda: ocr_edit.setText(
                "http://127.0.0.1:%d" % int(self._ctx.config.get("ocr", "port", default=8848))
            )
        )
        ocr_row.addWidget(btn_local_ocr)
        form_common.addRow("OCR 服务地址：", ocr_row)
        layout.addLayout(form_common)

        # ---- 提示 ----
        hint = QLabel(
            "提示：点击「拾取」按钮可转到浏览器直接选取元素。\n"
            "文字验证码：截图验证码图片 → OCR识别 → 填入输入框\n"
            "滑块验证码：截取小图+背景图 → OCR识别缺口位置 → 自动拖拽滑块\n"
            "请确保 OCR 服务已启动（OCR 服务 Tab）。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6B7280;font-size:12px;")
        layout.addWidget(hint)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )

        def _on_ok():
            ocr_url = ocr_edit.text().strip() or default_ocr
            if rb_text.isChecked():
                img_sel = img_edit.text().strip()
                input_sel = input_edit.text().strip()
                if not img_sel or not input_sel:
                    QMessageBox.warning(self, "提示", "验证码图片选择器和输入框选择器都不能为空。")
                    return
                action = Action(
                    type="fill_captcha",
                    selector=Selector(css=input_sel),
                    value=ocr_url,
                    image_selector=img_sel,
                )
            else:
                target_sel = target_edit.text().strip()
                bg_sel = bg_edit.text().strip()
                slider_sel = slider_edit.text().strip()
                if not target_sel or not bg_sel or not slider_sel:
                    QMessageBox.warning(self, "提示", "滑块小图、背景图、拖拽按钮选择器都不能为空。")
                    return
                action = Action(
                    type="slide_captcha",
                    selector=Selector(css=slider_sel),
                    value=ocr_url,
                    image_selector=target_sel,
                    background_selector=bg_sel,
                )
            # 插入到当前选中行之后；没选中则追加到末尾
            row = self.list_widget.currentRow()
            insert_at = row + 1 if row >= 0 else self._ctx.recorder.actions_count()
            self._ctx.recorder.insert_action(insert_at, action)
            log.info("已插入验证码步骤到位置 %d，类型=%s", insert_at, action.type)
            dlg.close()

        btns.accepted.connect(_on_ok)
        btns.rejected.connect(dlg.close)
        layout.addWidget(btns)

        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
