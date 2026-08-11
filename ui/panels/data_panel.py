# -*- coding: utf-8 -*-
"""
数据抓取面板：按字段规则或表格抓取当前浏览器页面数据。
支持保存/载入规则集（scrape_rules.json），结果可导出 Excel 或写入 Oracle。
"""
import json
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QFileDialog,
    QSpinBox, QComboBox, QAbstractItemView, QInputDialog,
)

from config.settings import CONFIG_DIR
from core.data.scraper import extract_by_rules, extract_first_table
from core.data.excel_exporter import export as export_excel
from core.logging.logger import get_logger

log = get_logger("ui.data")

_RULES_FILE = os.path.join(CONFIG_DIR, "scrape_rules.json")
_RULE_HEADERS = ["字段名", "CSS 选择器", "取值属性(可空)"]


class DataPanel(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self._ctx = ctx

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(8, 8, 8, 8)

        # ---- 目标页 ----
        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("目标页（在浏览器中打开后抓取，或在此输入并前往）："))
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://... 留空则抓取浏览器当前页")
        target_row.addWidget(self.url, 1)
        self.btn_go = QPushButton("前往")
        self.btn_go.clicked.connect(self._navigate_to_url)
        target_row.addWidget(self.btn_go)
        root.addLayout(target_row)

        # ---- 规则集 ----
        rs_row = QHBoxLayout()
        rs_row.addWidget(QLabel("规则集："))
        self.ruleset_combo = QComboBox()
        self.ruleset_combo.setMinimumWidth(200)
        self._refresh_ruleset_combo()
        rs_row.addWidget(self.ruleset_combo, 1)
        self.btn_load_ruleset = QPushButton("载入")
        self.btn_load_ruleset.clicked.connect(self._load_ruleset)
        self.btn_save_as = QPushButton("另存为...")
        self.btn_save_as.clicked.connect(self._save_ruleset_as)
        self.btn_overwrite = QPushButton("覆盖保存")
        self.btn_overwrite.clicked.connect(self._overwrite_ruleset)
        self.btn_del_ruleset = QPushButton("删除规则集")
        self.btn_del_ruleset.clicked.connect(self._delete_ruleset)
        for w in (self.btn_load_ruleset, self.btn_save_as, self.btn_overwrite, self.btn_del_ruleset):
            rs_row.addWidget(w)
        root.addLayout(rs_row)

        # ---- 抓取规则 ----
        root.addWidget(QLabel("抓取规则（字段名 / CSS 选择器 / 取值属性，属性可空=取文本）："))
        self.rules = QTableWidget(0, 3)
        self.rules.setHorizontalHeaderLabels(_RULE_HEADERS)
        self.rules.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.rules.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.rules)

        rule_bar = QHBoxLayout()
        self.btn_add_rule = QPushButton("增加规则行")
        self.btn_add_rule.clicked.connect(self._add_rule)
        self.btn_del_rule = QPushButton("删除选中行")
        self.btn_del_rule.clicked.connect(self._del_rule)
        rule_bar.addWidget(self.btn_add_rule)
        rule_bar.addWidget(self.btn_del_rule)
        rule_bar.addStretch(1)
        root.addLayout(rule_bar)

        # ---- 翻页与操作 ----
        opt_row = QHBoxLayout()
        opt_row.addWidget(QLabel("翻页选择器(可空)："))
        self.next_page_sel = QLineEdit()
        self.next_page_sel.setPlaceholderText("如 .next-page-btn，留空=不翻页")
        self.next_page_sel.setFixedWidth(220)
        opt_row.addWidget(self.next_page_sel)
        opt_row.addWidget(QLabel("最大页数："))
        self.max_pages = QSpinBox()
        self.max_pages.setRange(1, 1000)
        self.max_pages.setValue(1)
        opt_row.addWidget(self.max_pages)
        opt_row.addStretch(1)
        self.btn_scrape_rules = QPushButton("按规则抓取当前页")
        self.btn_scrape_rules.clicked.connect(self._scrape_rules)
        self.btn_scrape_table = QPushButton("一键抓取首个表格")
        self.btn_scrape_table.clicked.connect(self._scrape_first_table)
        opt_row.addWidget(self.btn_scrape_rules)
        opt_row.addWidget(self.btn_scrape_table)
        root.addLayout(opt_row)

        # ---- 抓取结果 ----
        root.addWidget(QLabel("抓取结果（按住 Shift + 鼠标滚轮可左右滚动）："))
        self.result = QTableWidget(0, 0)
        self.result.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.result.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.result, 1)

        # ---- 底部：Oracle + 导出 ----
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("写入 Oracle 表名(可空)："))
        self.oracle_table = QLineEdit()
        self.oracle_table.setPlaceholderText("留空使用设置中的默认表名")
        bottom.addWidget(self.oracle_table, 1)
        self.btn_export = QPushButton("导出 Excel")
        self.btn_export.clicked.connect(self._export_excel)
        self.btn_write_oracle = QPushButton("写入 Oracle")
        self.btn_write_oracle.clicked.connect(self._write_oracle)
        bottom.addWidget(self.btn_export)
        bottom.addWidget(self.btn_write_oracle)
        root.addLayout(bottom)

    # ---------- 目标页 ----------
    def _navigate_to_url(self):
        url = self.url.text().strip()
        if not url:
            QMessageBox.information(self, "前往", "URL 为空，将抓取浏览器当前页。")
            return
        if self._ctx.browser is None:
            QMessageBox.warning(self, "提示", "内置浏览器尚未初始化，请先打开【内置浏览器】面板。")
            return
        try:
            self._ctx.browser.load(url)
        except Exception as e:
            log.error("导航失败：%s", e, exc_info=True)
            QMessageBox.critical(self, "导航失败", str(e))

    # ---------- 规则编辑 ----------
    def _add_rule(self):
        row = self.rules.rowCount()
        self.rules.insertRow(row)
        for col, txt in enumerate(("", "", "")):
            self.rules.setItem(row, col, QTableWidgetItem(txt))

    def _del_rule(self):
        row = self.rules.currentRow()
        if row >= 0:
            self.rules.removeRow(row)

    def _read_rules(self):
        """从规则表格读取字段规则列表。"""
        rules = []
        for i in range(self.rules.rowCount()):
            name = self.rules.item(i, 0)
            sel = self.rules.item(i, 1)
            if not name or not sel:
                continue
            name, sel = name.text().strip(), sel.text().strip()
            if not name or not sel:
                continue
            rule = {"name": name, "selector": sel}
            attr_item = self.rules.item(i, 2)
            if attr_item and attr_item.text().strip():
                rule["attr"] = attr_item.text().strip()
            rules.append(rule)
        return rules

    def _set_rules(self, rules):
        """把规则列表填入表格。"""
        self.rules.setRowCount(0)
        for r in rules or []:
            row = self.rules.rowCount()
            self.rules.insertRow(row)
            self.rules.setItem(row, 0, QTableWidgetItem(r.get("name", "")))
            self.rules.setItem(row, 1, QTableWidgetItem(r.get("selector", "")))
            self.rules.setItem(row, 2, QTableWidgetItem(r.get("attr", "")))

    # ---------- 规则集管理 ----------
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
        self.ruleset_combo.clear()
        names = list(self._read_rule_sets().keys())
        if names:
            self.ruleset_combo.addItems(names)
        else:
            self.ruleset_combo.addItem("（暂无保存的规则集）")
            self.ruleset_combo.model().item(0).setEnabled(False)

    def _write_rule_sets(self, data: dict):
        os.makedirs(os.path.dirname(_RULES_FILE), exist_ok=True)
        with open(_RULES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _current_ruleset_name(self) -> str:
        name = self.ruleset_combo.currentText().strip()
        if name.startswith("（"):
            return ""
        return name

    def _save_ruleset_as(self):
        """另存为：弹窗输入新名称后保存当前规则。"""
        rules = self._read_rules()
        if not rules:
            QMessageBox.warning(self, "保存规则集", "当前没有可保存的字段规则。")
            return
        name, ok = QInputDialog.getText(self, "保存规则集", "请输入规则集名称：")
        if not ok or not name.strip():
            return
        name = name.strip()
        data = self._read_rule_sets()
        if name in data:
            if QMessageBox.question(
                self, "保存规则集", "规则集「%s」已存在，是否覆盖？" % name
            ) != QMessageBox.StandardButton.Yes:
                return
            log.info("已覆盖保存规则集「%s」(%d 条)", name, len(rules))
        else:
            log.info("已保存规则集「%s」(%d 条)", name, len(rules))
        data[name] = rules
        self._write_rule_sets(data)
        self._refresh_ruleset_combo()
        self.ruleset_combo.setCurrentText(name)

    def _overwrite_ruleset(self):
        """覆盖保存：把当前规则覆盖写入当前选中的规则集。"""
        name = self._current_ruleset_name()
        if not name:
            QMessageBox.warning(self, "覆盖保存", "请先选择一个要覆盖的规则集。")
            return
        rules = self._read_rules()
        if not rules:
            if QMessageBox.question(
                self, "覆盖保存", "当前规则为空，确定要清空规则集「%s」吗？" % name
            ) != QMessageBox.StandardButton.Yes:
                return
        data = self._read_rule_sets()
        data[name] = rules
        self._write_rule_sets(data)
        self._refresh_ruleset_combo()
        self.ruleset_combo.setCurrentText(name)
        log.info("已覆盖保存规则集「%s」(%d 条)", name, len(rules))

    def _load_ruleset(self):
        """把选中的规则集填入规则表格。"""
        name = self._current_ruleset_name()
        if not name:
            QMessageBox.warning(self, "载入规则集", "没有可用的规则集，请先保存。")
            return
        data = self._read_rule_sets()
        rules = data.get(name)
        if not rules:
            QMessageBox.warning(self, "载入规则集", "规则集「%s」为空。" % name)
            return
        self._set_rules(rules)
        log.info("已载入规则集「%s」(%d 条)", name, len(rules))

    def _delete_ruleset(self):
        name = self._current_ruleset_name()
        if not name:
            QMessageBox.warning(self, "删除规则集", "没有可删除的规则集。")
            return
        if QMessageBox.question(
            self, "删除规则集", "确定删除规则集「%s」？" % name
        ) != QMessageBox.StandardButton.Yes:
            return
        data = self._read_rule_sets()
        data.pop(name, None)
        self._write_rule_sets(data)
        self._refresh_ruleset_combo()
        log.info("已删除规则集「%s」", name)

    # ---------- 抓取 ----------
    def _check_browser(self):
        if self._ctx.browser is None:
            QMessageBox.warning(self, "提示", "内置浏览器尚未初始化，请先打开【内置浏览器】面板。")
            return False
        return True

    def _scrape_rules(self):
        if not self._check_browser():
            return
        rules = self._read_rules()
        if not rules:
            QMessageBox.warning(self, "按规则抓取", "请先添加至少一条字段规则（字段名 + CSS 选择器）。")
            return
        self.btn_scrape_rules.setEnabled(False)
        try:
            rows = extract_by_rules(self._ctx.browser, rules)
            self._show_result(rows)
            QMessageBox.information(self, "抓取完成", f"共抓取 {len(rows)} 行数据。")
        except Exception as e:
            log.error("按规则抓取失败：%s", e, exc_info=True)
            QMessageBox.critical(self, "抓取失败", str(e))
        finally:
            self.btn_scrape_rules.setEnabled(True)

    def _scrape_first_table(self):
        if not self._check_browser():
            return
        self.btn_scrape_table.setEnabled(False)
        try:
            rows = extract_first_table(self._ctx.browser)
            self._show_result(rows)
            QMessageBox.information(self, "抓取完成", f"共抓取 {len(rows)} 行数据。")
        except Exception as e:
            log.error("抓取首个表格失败：%s", e, exc_info=True)
            QMessageBox.critical(self, "抓取失败", str(e))
        finally:
            self.btn_scrape_table.setEnabled(True)

    # ---------- 结果展示 ----------
    def _show_result(self, rows):
        self._ctx.last_scraped = rows
        self.result.clear()
        if not rows:
            self.result.setColumnCount(0)
            self.result.setRowCount(0)
            return
        headers = list(rows[0].keys())
        self.result.setColumnCount(len(headers))
        self.result.setHorizontalHeaderLabels(headers)
        self.result.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for j, h in enumerate(headers):
                item = QTableWidgetItem(str(r.get(h, "")))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.result.setItem(i, j, item)

    # ---------- 输出 ----------
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
                log.error("导出 Excel 失败：%s", e, exc_info=True)
                QMessageBox.critical(self, "导出失败", str(e))

    def _write_oracle(self):
        if not self._ctx.last_scraped:
            QMessageBox.warning(self, "写入 Oracle", "没有可写入的数据，请先抓取。")
            return
        table = self.oracle_table.text().strip()
        try:
            from core.data.oracle_client import OracleClient
            oc = OracleClient(self._ctx.config)
            oc.insert_many(table, self._ctx.last_scraped)
            oc.disconnect()
            QMessageBox.information(self, "写入成功", f"已写入 Oracle 表 {table}（{len(self._ctx.last_scraped)} 行）")
        except Exception as e:
            log.error("写入 Oracle 失败：%s", e, exc_info=True)
            QMessageBox.critical(self, "写入失败", str(e))
