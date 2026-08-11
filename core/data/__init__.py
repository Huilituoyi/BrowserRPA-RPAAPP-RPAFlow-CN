# -*- coding: utf-8 -*-
"""数据子系统：网页抓取 + Excel 导出 + Oracle 客户端。"""
from .scraper import extract_by_rules, extract_table, extract_first_table, run_js_sync
from .excel_exporter import export as export_excel

__all__ = [
    "extract_by_rules", "extract_table", "extract_first_table",
    "run_js_sync", "export_excel",
]
