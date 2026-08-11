# -*- coding: utf-8 -*-
"""
定时任务面板：新建/编辑/删除/启用/立即运行定时任务。
任务类型：按规则抓取 / 抓取表格 / 回放录制动作。
调度方式：间隔执行 / Cron 表达式 / 定时一次。
立即运行在后台线程执行（避免阻塞界面）。
"""
import json
import os
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QDialog, QFormLayout, QLineEdit, QComboBox, QSpinBox,
    QDoubleSpinBox, QCheckBox, QLabel, QDateTimeEdit, QAbstractItemView,
    QSplitter, QTabWidget, QPlainTextEdit, QScrollArea, QGroupBox,
    QListWidget, QListWidgetItem, QMenu,
)

from config.settings import CONFIG_DIR
from core.tasks.task_models import TaskDef
from core.recorder.action_models import Action, Selector
from core.recorder.codegen.python_gen import PythonGenerator
from core.logging.logger import get_logger
from ui.panels.codegen_panel import ActionDetailDialog

log = get_logger("ui.tasks")

_RULES_FILE = os.path.join(CONFIG_DIR, "scrape_rules.json")

_KIND_LABEL = {"scrape_rules": "按规则抓取", "scrape_table": "抓取表格", "play_actions": "回放动作"}
_SCHED_LABEL = {"interval": "间隔", "cron": "Cron", "date": "定时一次"}


class _ReorderableList(QListWidget):
    """支持上下拖拽排序的动作列表。拖拽完成后发出 reordered 信号。"""
    reordered = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)

    def dropEvent(self, event):
        super().dropEvent(event)
        self.reordered.emit()


# ===================== 任务编辑对话框 =====================
class TaskEditDialog(QDialog):
    def __init__(self, ctx, task=None, nav=None):
        super().__init__()
        self._ctx = ctx
        self._nav = nav
        self.setWindowTitle("编辑任务" if task else "新建任务")
        self.resize(1100, 650)
        self._actions = []
        self._loading = False  # 防止编辑信号循环触发（已废弃，保留兼容）

        # ---- 左侧表单（放到 ScrollArea 里）----
        left_widget = QWidget()
        form = QFormLayout(left_widget)

        self.name = QLineEdit(task.name if task else "")
        form.addRow("任务名称", self.name)

        self.desc = QPlainTextEdit((task.payload.get("description") if task else "") or "")
        self.desc.setPlaceholderText("任务说明（可选）：描述此任务的用途、注意事项等")
        self.desc.setMaximumHeight(60)
        form.addRow("任务说明", self.desc)

        self.kind = QComboBox()
        self.kind.addItem("按规则抓取", "scrape_rules")
        self.kind.addItem("抓取表格", "scrape_table")
        self.kind.addItem("回放录制动作", "play_actions")
        if task:
            idx = self.kind.findData(task.kind)
            if idx >= 0:
                self.kind.setCurrentIndex(idx)
        self.kind.currentIndexChanged.connect(self._on_kind_changed)
        form.addRow("任务类型", self.kind)

        self.sched_type = QComboBox()
        self.sched_type.addItem("间隔执行", "interval")
        self.sched_type.addItem("Cron 表达式", "cron")
        self.sched_type.addItem("定时一次", "date")
        if task:
            idx = self.sched_type.findData(task.schedule_type)
            if idx >= 0:
                self.sched_type.setCurrentIndex(idx)
        self.sched_type.currentIndexChanged.connect(self._on_sched)
        form.addRow("调度方式", self.sched_type)

        self.interval_min = QSpinBox()
        self.interval_min.setRange(1, 100000)
        self.interval_min.setValue(int((task.schedule or {}).get("minutes", 10)) if task else 10)
        form.addRow("间隔(分钟)", self.interval_min)

        cron = (task.schedule if task and task.schedule_type == "cron" else {})
        self.cron_minute = QLineEdit(str(cron.get("minute", "*")))
        self.cron_hour = QLineEdit(str(cron.get("hour", "*")))
        self.cron_dom = QLineEdit(str(cron.get("day", "*")))
        self.cron_month = QLineEdit(str(cron.get("month", "*")))
        self.cron_dow = QLineEdit(str(cron.get("day_of_week", "*")))
        form.addRow("cron: 分", self.cron_minute)
        form.addRow("cron: 时", self.cron_hour)
        form.addRow("cron: 日(每月)", self.cron_dom)
        form.addRow("cron: 月", self.cron_month)
        form.addRow("cron: 周(0-6)", self.cron_dow)

        self.date = QDateTimeEdit()
        self.date.setCalendarPopup(True)
        self.date.setDateTime(datetime.now())
        form.addRow("执行时间(定时一次)", self.date)

        # ---- 公共/抓取参数 ----
        self.url = QLineEdit((task.payload.get("url") if task else "") or "")
        self.url.setPlaceholderText("https://...")
        form.addRow("目标 URL", self.url)

        self.table_sel = QLineEdit((task.payload.get("table_selector") if task else "") or "table")
        form.addRow("表格选择器(抓取表格用)", self.table_sel)

        # ---- 规则集预设（从数据抓取面板保存的规则集载入）----
        rs_row = QHBoxLayout()
        self.ruleset = QComboBox()
        self.ruleset.setMinimumWidth(200)
        self._refresh_ruleset_combo()
        rs_row.addWidget(self.ruleset, 1)
        self.btn_load_ruleset = QPushButton("载入此规则集")
        self.btn_load_ruleset.clicked.connect(self._load_ruleset)
        rs_row.addWidget(self.btn_load_ruleset)
        form.addRow("规则集预设", rs_row)

        self.rules = QTableWidget(0, 3)
        self.rules.setHorizontalHeaderLabels(["字段名", "CSS选择器", "属性(可空)"])
        self.rules.setMaximumHeight(140)
        if task and task.kind == "scrape_rules":
            for r in task.payload.get("rules", []):
                self._add_rule(r.get("name", ""), r.get("selector", ""), r.get("attr", ""))
        form.addRow("抓取规则", self.rules)
        self.btn_add_rule = QPushButton("增加规则行")
        self.btn_add_rule.clicked.connect(lambda: self._add_rule())
        form.addRow("", self.btn_add_rule)

        # ---- 翻页设置（抓取类任务支持多页）----
        self.next_page_sel = QLineEdit((task.payload.get("next_page_selector") if task else "") or "")
        self.next_page_sel.setPlaceholderText("如 .next-page-btn，留空=不翻页")
        form.addRow("翻页选择器(可空)", self.next_page_sel)
        self.max_pages = QSpinBox()
        self.max_pages.setRange(1, 100)
        self.max_pages.setValue(int(task.payload.get("max_pages", 1)) if task else 1)
        form.addRow("最大页数", self.max_pages)

        self.export_excel = QCheckBox("抓取完成后自动导出 Excel")
        self.export_excel.setChecked(task.payload.get("export_excel", False) if task else False)
        form.addRow("", self.export_excel)
        self.oracle_table = QLineEdit((task.payload.get("oracle_table") if task else "") or "")
        self.oracle_table.setPlaceholderText("抓取完成后写入此 Oracle 表(可空)")
        form.addRow("写入 Oracle 表", self.oracle_table)

        # ---- 回放执行节奏（每步间隔，防止操作过快被封）----
        delay_box = QHBoxLayout()
        self.delay_min = QDoubleSpinBox()
        self.delay_min.setRange(0.0, 300.0)
        self.delay_min.setSingleStep(0.5)
        self.delay_min.setSuffix(" 秒")
        # 任务自定义值优先，否则用全局默认
        g_min = ctx.config.get("runner", "step_delay_min", default=1.0)
        self.delay_min.setValue(float(task.payload.get("step_delay_min", g_min)) if task else float(g_min))
        self.delay_max = QDoubleSpinBox()
        self.delay_max.setRange(0.0, 300.0)
        self.delay_max.setSingleStep(0.5)
        self.delay_max.setSuffix(" 秒")
        g_max = ctx.config.get("runner", "step_delay_max", default=3.0)
        self.delay_max.setValue(float(task.payload.get("step_delay_max", g_max)) if task else float(g_max))
        delay_box.addWidget(self.delay_min)
        delay_box.addWidget(QLabel("～"))
        delay_box.addWidget(self.delay_max)
        form.addRow("每步间隔(回放)", delay_box)

        # 是否显示浏览器窗口（关闭=无头，开启=可见，用于调试）
        self.show_browser = QCheckBox("执行时显示浏览器窗口（调试用，取消勾选=后台无头运行）")
        if task and "headless" in task.payload:
            self.show_browser.setChecked(not task.payload["headless"])
        else:
            self.show_browser.setChecked(not ctx.config.get("runner", "headless", default=True))
        form.addRow("", self.show_browser)

        # ---- 回放动作 ----
        if task and task.kind == "play_actions":
            self._actions = task.payload.get("actions", []) or []
        self.btn_use_actions = QPushButton("使用浏览器面板当前录制动作")
        self.btn_use_actions.clicked.connect(self._use_actions)
        self.lbl_actions = QLabel("已载入动作：%d 步" % len(self._actions))
        form.addRow("回放动作", self.btn_use_actions)
        form.addRow("", self.lbl_actions)

        btns = QHBoxLayout()
        ok = QPushButton("保存")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        btns.addStretch(1)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        form.addRow(btns)

        left_scroll = QScrollArea()
        left_scroll.setWidget(left_widget)
        left_scroll.setWidgetResizable(True)

        # ---- 右侧：动作详情 + 代码预览 ----
        self.right_panel = self._build_right_panel()

        # ---- 分栏布局 ----
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_scroll)
        splitter.addWidget(self.right_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        outer = QVBoxLayout(self)
        outer.addWidget(splitter)
        self._on_sched()
        self._on_kind_changed()
        self._sync_actions_list()

    def _add_rule(self, name="", sel="", attr=""):
        r = self.rules.rowCount()
        self.rules.insertRow(r)
        self.rules.setItem(r, 0, QTableWidgetItem(name))
        self.rules.setItem(r, 1, QTableWidgetItem(sel))
        self.rules.setItem(r, 2, QTableWidgetItem(attr))

    # ---------- 规则集预设 ----------
    def _read_rule_sets(self) -> dict:
        """从 scrape_rules.json 读取已保存的规则集 {name: [rule, ...]}。"""
        if not os.path.exists(_RULES_FILE):
            return {}
        try:
            with open(_RULES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.warning("读取规则集文件失败：%s", e)
            return {}

    def _refresh_ruleset_combo(self):
        self.ruleset.clear()
        names = list(self._read_rule_sets().keys())
        if names:
            self.ruleset.addItems(names)
        else:
            self.ruleset.addItem("（暂无保存的规则集，请先在「数据抓取」面板保存）")
            self.ruleset.model().item(0).setEnabled(False)

    def _load_ruleset(self):
        """把选中的规则集填入规则表格。"""
        name = self.ruleset.currentText()
        data = self._read_rule_sets()
        rules = data.get(name)
        if not rules:
            QMessageBox.warning(self, "载入规则集", "没有可用的规则集，请先在「数据抓取」面板保存。")
            return
        self.rules.setRowCount(0)
        for r in rules:
            self._add_rule(r.get("name", ""), r.get("selector", ""), r.get("attr", ""))
        log.info("已载入规则集「%s」(%d 条)", name, len(rules))

    def _use_actions(self):
        if self._ctx.recorder is None or not self._ctx.recorder.actions():
            QMessageBox.warning(self, "回放动作", "当前没有录制动作，请先在浏览器面板录制。")
            return
        self._actions = self._ctx.recorder.action_dicts()
        self.lbl_actions.setText("已载入动作：%d 步" % len(self._actions))
        self._sync_actions_list()

    # ---------- 右侧面板构建 ----------

    def _build_right_panel(self):
        """构建右侧面板：动作摘要列表（右键编辑） + 生成代码预览。"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        tabs = QTabWidget()

        # ---- Tab1: 动作列表（右键编辑，与代码生成 Tab 一致）----
        actions_widget = QWidget()
        a_layout = QVBoxLayout(actions_widget)
        a_layout.setContentsMargins(2, 2, 2, 2)

        # 工具栏
        bar = QHBoxLayout()
        btn_new = QPushButton("新建动作…")
        btn_new.clicked.connect(lambda: self._edit_action_dialog(new_action=True))
        btn_wait = QPushButton("插入等待…")
        btn_wait.clicked.connect(self._quick_insert_wait)
        btn_mark = QPushButton("插入标记点…")
        btn_mark.clicked.connect(self._quick_insert_mark)
        btn_del = QPushButton("删除选中行")
        btn_del.clicked.connect(self._delete_action_row)
        btn_up = QPushButton("上移")
        btn_up.clicked.connect(lambda: self._move_action(-1))
        btn_down = QPushButton("下移")
        btn_down.clicked.connect(lambda: self._move_action(1))
        for b in (btn_new, btn_wait, btn_mark, btn_del, btn_up, btn_down):
            bar.addWidget(b)
        bar.addStretch(1)
        a_layout.addLayout(bar)

        self.actions_list = _ReorderableList()
        self.actions_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.actions_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.actions_list.customContextMenuRequested.connect(self._show_context_menu)
        self.actions_list.reordered.connect(self._on_list_reordered)
        a_layout.addWidget(self.actions_list, 1)

        tabs.addTab(actions_widget, "动作详情")

        # ---- Tab2: 生成代码预览（只读）----
        self.code_preview = QPlainTextEdit()
        self.code_preview.setReadOnly(True)
        font = self.code_preview.font()
        font.setFamily("Consolas, Courier New, monospace")
        font.setPointSize(9)
        self.code_preview.setFont(font)
        self.code_preview.setPlaceholderText("（无录制动作，选择回放录制动作并载入动作后将显示生成代码）")
        tabs.addTab(self.code_preview, "生成代码")

        layout.addWidget(tabs)
        return panel

    # ---------- 右侧动作列表操作 ----------
    @staticmethod
    def _sel_text(a: Action) -> str:
        s = a.selector
        if not s:
            return ""
        return s.text or s.id or s.css or s.tag or ""

    def _summary(self, a: Action) -> str:
        """生成单步动作的中文摘要（与代码生成 Tab 保持一致）。"""
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

    def _sync_actions_list(self):
        """将 self._actions 刷新到右侧摘要列表 + 代码预览。"""
        self.actions_list.clear()
        for i, d in enumerate(self._actions):
            a = Action.from_dict(d) if isinstance(d, dict) else d
            item = QListWidgetItem("☰  %d. %s" % (i + 1, self._summary(a)))
            item.setData(Qt.ItemDataRole.UserRole, i)  # 记录原始索引，供拖拽排序用
            self.actions_list.addItem(item)
        self._refresh_code_preview()

    def _on_list_reordered(self):
        """拖拽排序后：据新顺序重排 self._actions，更新序号与代码预览。"""
        new_order = []
        for i in range(self.actions_list.count()):
            item = self.actions_list.item(i)
            new_order.append(item.data(Qt.ItemDataRole.UserRole))
        self._actions = [self._actions[i] for i in new_order]
        # 更新每个 item 的序号文本与 UserRole 为当前新位置
        for i in range(self.actions_list.count()):
            item = self.actions_list.item(i)
            a = Action.from_dict(self._actions[i]) if isinstance(self._actions[i], dict) else self._actions[i]
            item.setText("☰  %d. %s" % (i + 1, self._summary(a)))
            item.setData(Qt.ItemDataRole.UserRole, i)
        self._refresh_code_preview()

    # ---------- 右键菜单 ----------
    def _show_context_menu(self, pos):
        """右键菜单：编辑/新建/插入等待/复制/删除（与代码生成 Tab 一致）。"""
        item = self.actions_list.itemAt(pos)
        row = self.actions_list.row(item) if item else -1
        menu = QMenu(self)

        has_selection = row >= 0 and row < len(self._actions)

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
            act_del.triggered.connect(lambda: self._delete_action_at(row))

        menu.exec(self.actions_list.mapToGlobal(pos))

    def _delete_action_row(self):
        """工具栏：删除当前选中行。"""
        row = self.actions_list.currentRow()
        self._delete_action_at(row)

    def _delete_action_at(self, row):
        if row < 0 or row >= len(self._actions):
            return
        del self._actions[row]
        self.lbl_actions.setText("已载入动作：%d 步" % len(self._actions))
        self._sync_actions_list()
        if self.actions_list.count() > 0:
            self.actions_list.setCurrentRow(min(row, self.actions_list.count() - 1))

    def _move_action(self, offset):
        row = self.actions_list.currentRow()
        new_row = row + offset
        if row < 0 or new_row < 0 or new_row >= len(self._actions):
            return
        self._actions[row], self._actions[new_row] = self._actions[new_row], self._actions[row]
        self._sync_actions_list()
        self.actions_list.setCurrentRow(new_row)

    def _duplicate_action(self, row):
        """深拷贝并插入到下一行。"""
        if 0 <= row < len(self._actions):
            original = self._actions[row]
            d = original if isinstance(original, dict) else (
                original.to_dict() if hasattr(original, "to_dict") else {"type": "unknown"})
            import copy
            self._actions.insert(row + 1, copy.deepcopy(d))
            self.lbl_actions.setText("已载入动作：%d 步" % len(self._actions))
            self._sync_actions_list()
            self.actions_list.setCurrentRow(row + 1)

    # ---------- 新建/编辑动作、插入等待 ----------
    def _edit_action_dialog(self, row=None, new_action=False):
        """打开与代码生成 Tab 相同的动作详情对话框，编辑或新建动作。
        - new_action=True：新建模式，插入到当前选中行之后
        - row=N：编辑指定行
        """
        existing = None
        edit_row = row
        if new_action:
            cur = self.actions_list.currentRow()
            edit_row = cur + 1 if cur >= 0 else len(self._actions)
        elif row is not None:
            if 0 <= row < len(self._actions):
                d = self._actions[row]
                existing = Action.from_dict(d) if isinstance(d, dict) else d

        dlg = ActionDetailDialog(self, action=existing, ctx=self._ctx,
                                 nav=self._nav, enable_pick=True)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

        def on_finished():
            action = dlg.result_action()
            if action is None:
                return
            if new_action or row is None:
                self._actions.insert(edit_row, action.to_dict())
            else:
                self._actions[edit_row] = action.to_dict()
            self.lbl_actions.setText("已载入动作：%d 步" % len(self._actions))
            self._sync_actions_list()
            self.actions_list.setCurrentRow(edit_row)

        dlg.finished.connect(lambda _: on_finished())

    def _quick_insert_wait(self, row=None):
        """快速插入等待步骤（默认 2 秒），位置在选中行之后。"""
        if row is None:
            cur = self.actions_list.currentRow()
            insert_at = cur + 1 if cur >= 0 else len(self._actions)
        else:
            insert_at = row + 1
        action = Action(type="wait", value="2.0")
        self._actions.insert(insert_at, action.to_dict())
        self.lbl_actions.setText("已载入动作：%d 步" % len(self._actions))
        self._sync_actions_list()
        self.actions_list.setCurrentRow(insert_at)

    def _quick_insert_mark(self, row=None):
        """快速插入标记点（作为检查重试的跳转起点），位置在选中行之后。"""
        if row is None:
            cur = self.actions_list.currentRow()
            insert_at = cur + 1 if cur >= 0 else len(self._actions)
        else:
            insert_at = row + 1
        action = Action(type="mark", value="")
        self._actions.insert(insert_at, action.to_dict())
        self.lbl_actions.setText("已载入动作：%d 步" % len(self._actions))
        self._sync_actions_list()
        self.actions_list.setCurrentRow(insert_at)

    def _refresh_code_preview(self):
        """根据 self._actions 重新生成 Python 代码并显示。"""
        if not self._actions:
            self.code_preview.setPlainText("（无录制动作）")
            return
        try:
            actions = [
                a if isinstance(a, Action) else Action.from_dict(a)
                for a in self._actions
            ]
            gen = PythonGenerator()
            code, _ = gen.generate_with_map(actions)
            self.code_preview.setPlainText(code)
        except Exception as e:
            self.code_preview.setPlainText("# 生成代码失败：%s" % e)

    # ---------- 任务类型切换 ----------
    def _on_kind_changed(self):
        is_play = self.kind.currentData() == "play_actions"
        self.right_panel.setVisible(is_play)

    def _on_sched(self):
        st = self.sched_type.currentData()
        self.interval_min.setEnabled(st == "interval")
        for w in (self.cron_minute, self.cron_hour, self.cron_dom, self.cron_month, self.cron_dow):
            w.setEnabled(st == "cron")
        self.date.setEnabled(st == "date")

    def build(self) -> TaskDef:
        kind = self.kind.currentData()
        st = self.sched_type.currentData()
        if st == "interval":
            schedule = {"minutes": self.interval_min.value()}
        elif st == "cron":
            schedule = {}
            mp = {"minute": self.cron_minute, "hour": self.cron_hour, "day": self.cron_dom,
                  "month": self.cron_month, "day_of_week": self.cron_dow}
            for k, w in mp.items():
                v = w.text().strip()
                if v:
                    schedule[k] = v
            if not schedule:
                schedule = {"minute": "*"}
        else:
            schedule = {"run_date": self.date.dateTime().toString(Qt.DateFormat.ISODate)}

        payload = {}
        # 每步间隔 + 是否显示浏览器窗口（所有任务类型都保存）
        step_delay = {"step_delay_min": self.delay_min.value(),
                      "step_delay_max": self.delay_max.value(),
                      "headless": not self.show_browser.isChecked()}
        if kind == "scrape_rules":
            rules = []
            for r in range(self.rules.rowCount()):
                n = self.rules.item(r, 0).text().strip() if self.rules.item(r, 0) else ""
                s = self.rules.item(r, 1).text().strip() if self.rules.item(r, 1) else ""
                a = self.rules.item(r, 2).text().strip() if self.rules.item(r, 2) else ""
                if n and s:
                    d = {"name": n, "selector": s}
                    if a:
                        d["attr"] = a
                    rules.append(d)
            payload = {"url": self.url.text().strip(), "rules": rules,
                       "export_excel": self.export_excel.isChecked(),
                       "oracle_table": self.oracle_table.text().strip(),
                       "next_page_selector": self.next_page_sel.text().strip(),
                       "max_pages": self.max_pages.value()}
            payload.update(step_delay)
        elif kind == "scrape_table":
            payload = {"url": self.url.text().strip(),
                       "table_selector": self.table_sel.text().strip() or "table",
                       "export_excel": self.export_excel.isChecked(),
                       "oracle_table": self.oracle_table.text().strip(),
                       "next_page_selector": self.next_page_sel.text().strip(),
                       "max_pages": self.max_pages.value()}
            payload.update(step_delay)
        else:
            payload = {"actions": self._actions}
            payload.update(step_delay)

        # 任务说明（所有类型通用）
        desc_text = self.desc.toPlainText().strip()
        if desc_text:
            payload["description"] = desc_text

        return TaskDef(name=self.name.text().strip() or "未命名任务",
                       kind=kind, enabled=True, schedule_type=st,
                       schedule=schedule, payload=payload)


# ===================== 任务列表面板 =====================
class TasksPanel(QWidget):
    HEADERS = ["名称", "类型", "调度", "启用", "上次状态", "上次运行", "下次运行"]

    def __init__(self, ctx):
        super().__init__()
        self._ctx = ctx
        self._edit_dialogs = []  # 持有非模态编辑对话框引用，防止被 GC

        root = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.btn_add = QPushButton("新建任务")
        self.btn_edit = QPushButton("编辑")
        self.btn_del = QPushButton("删除")
        self.btn_toggle = QPushButton("启用/暂停")
        self.btn_run = QPushButton("立即运行")
        self.btn_refresh = QPushButton("刷新")
        for b in (self.btn_add, self.btn_edit, self.btn_del, self.btn_toggle, self.btn_run, self.btn_refresh):
            bar.addWidget(b)
        bar.addStretch(1)
        root.addLayout(bar)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, 2)

        # ---- 执行历史区域（选中任务后自动刷新）----
        root.addWidget(QLabel("执行历史（选中任务后显示，最近 20 条）"))
        self.history_table = QTableWidget(0, 4)
        self.history_table.setHorizontalHeaderLabels(["时间", "状态", "耗时(秒)", "错误"])
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.history_table, 1)

        self.btn_add.clicked.connect(self._add)
        self.btn_edit.clicked.connect(self._edit)
        self.btn_del.clicked.connect(self._del)
        self.btn_toggle.clicked.connect(self._toggle)
        self.btn_run.clicked.connect(self._run_now)
        self.btn_refresh.clicked.connect(self._refresh)
        # 选中任务时自动刷新执行历史
        self.table.itemSelectionChanged.connect(self._refresh_history)

        if ctx.scheduler is not None:
            ctx.scheduler.status_changed.connect(lambda *_: self._refresh())

        self._check_scheduler()
        self._refresh()  # 启动时立即加载已保存的任务

    def _check_scheduler(self):
        enabled = self._ctx.scheduler is not None
        for b in (self.btn_add, self.btn_edit, self.btn_toggle, self.btn_run):
            b.setEnabled(enabled)
        if not enabled:
            log.warning("未启用任务调度（apscheduler 未安装），任务面板仅可查看/删除。")

    def _refresh(self):
        tasks = self._ctx.store.all()
        self.table.setRowCount(len(tasks))
        for i, t in enumerate(tasks):
            self.table.setItem(i, 0, QTableWidgetItem(t.name))
            self.table.setItem(i, 1, QTableWidgetItem(_KIND_LABEL.get(t.kind, t.kind)))
            self.table.setItem(i, 2, QTableWidgetItem(self._sched_text(t)))
            self.table.setItem(i, 3, QTableWidgetItem("是" if t.enabled else "否"))
            self.table.setItem(i, 4, QTableWidgetItem(t.last_status or "-"))
            self.table.setItem(i, 5, QTableWidgetItem(t.last_run or "-"))
            self.table.setItem(i, 6, QTableWidgetItem(t.next_run or "-"))
            self.table.item(i, 0).setData(Qt.ItemDataRole.UserRole, t.id)

    def _refresh_history(self):
        """刷新执行历史表格：显示当前选中任务的 history。"""
        self.history_table.setRowCount(0)
        tid = self._selected_id()
        if not tid:
            return
        t = self._ctx.store.get(tid)
        if not t:
            return
        for h in (t.history or []):
            r = self.history_table.rowCount()
            self.history_table.insertRow(r)
            self.history_table.setItem(r, 0, QTableWidgetItem(str(h.get("time", ""))))
            self.history_table.setItem(r, 1, QTableWidgetItem(str(h.get("status", ""))))
            self.history_table.setItem(r, 2, QTableWidgetItem(str(h.get("duration_sec", ""))))
            self.history_table.setItem(r, 3, QTableWidgetItem(str(h.get("error", ""))))

    @staticmethod
    def _sched_text(t: TaskDef) -> str:
        st = _SCHED_LABEL.get(t.schedule_type, t.schedule_type)
        return "%s %s" % (st, t.schedule)

    def _selected_id(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        return self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)

    def _add(self):
        dlg = TaskEditDialog(self._ctx, nav=self.window().nav)
        self._edit_dialogs.append(dlg)
        dlg.finished.connect(lambda _: self._on_add_finished(dlg))
        dlg.show()

    def _on_add_finished(self, dlg):
        if dlg.result() == QDialog.DialogCode.Accepted:
            self._ctx.scheduler.add(dlg.build())
            self._refresh()
        if dlg in self._edit_dialogs:
            self._edit_dialogs.remove(dlg)
        dlg.deleteLater()

    def _edit(self):
        tid = self._selected_id()
        if not tid:
            return
        t = self._ctx.store.get(tid)
        dlg = TaskEditDialog(self._ctx, t, nav=self.window().nav)
        self._edit_dialogs.append(dlg)
        dlg.finished.connect(lambda _: self._on_edit_finished(dlg, tid, t.enabled))
        dlg.show()

    def _on_edit_finished(self, dlg, tid, orig_enabled):
        if dlg.result() == QDialog.DialogCode.Accepted:
            new = dlg.build()
            new.id = tid
            new.enabled = orig_enabled
            self._ctx.scheduler.update(new)
            self._refresh()
        if dlg in self._edit_dialogs:
            self._edit_dialogs.remove(dlg)
        dlg.deleteLater()

    def _del(self):
        tid = self._selected_id()
        if not tid:
            return
        if QMessageBox.question(self, "删除任务", "确定删除该任务？") != QMessageBox.StandardButton.Yes:
            return
        if self._ctx.scheduler is not None:
            self._ctx.scheduler.remove(tid)
        else:
            self._ctx.store.remove(tid)
        self._refresh()

    def _toggle(self):
        tid = self._selected_id()
        if not tid or self._ctx.scheduler is None:
            return
        t = self._ctx.store.get(tid)
        self._ctx.scheduler.set_enabled(tid, not t.enabled)
        self._refresh()

    def _run_now(self):
        tid = self._selected_id()
        if not tid or self._ctx.scheduler is None:
            return
        # 通过调度器触发（走 max_instances + 任务级锁），不再用裸线程
        self._ctx.scheduler.run_now(tid)
        QMessageBox.information(self, "立即运行", "任务已触发，请在列表查看状态。")
