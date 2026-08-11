# -*- coding: utf-8 -*-
"""数据抓取器 scraper.py 测试。

通过 monkeypatch run_js_sync 绕开 Qt 事件循环依赖，专注验证
解析逻辑、空值容错与多页抓取的边界控制。
"""
import pytest

from core.data import scraper


class TestExtractByRules:
    def test_normal_rows(self, monkeypatch):
        monkeypatch.setattr(scraper, "run_js_sync",
                            lambda b, js, timeout=10.0: '[{"a":"1"},{"a":"2"}]')
        rows = scraper.extract_by_rules(object(), [{"name": "a", "selector": "li"}])
        assert rows == [{"a": "1"}, {"a": "2"}]

    def test_none_result_returns_empty(self, monkeypatch):
        monkeypatch.setattr(scraper, "run_js_sync", lambda b, js, timeout=10.0: None)
        assert scraper.extract_by_rules(object(), [{"name": "a", "selector": "li"}]) == []

    def test_invalid_json_returns_empty(self, monkeypatch):
        monkeypatch.setattr(scraper, "run_js_sync", lambda b, js, timeout=10.0: "not json{")
        assert scraper.extract_by_rules(object(), [{"name": "a", "selector": "li"}]) == []


class TestExtractTable:
    def test_table_with_headers(self, monkeypatch):
        monkeypatch.setattr(scraper, "run_js_sync",
                            lambda b, js, timeout=10.0: '{"headers":["h1","h2"],"rows":[{"h1":"x","h2":"y"}]}')
        rows = scraper.extract_table(object(), "table")
        assert rows == [{"h1": "x", "h2": "y"}]

    def test_table_not_found(self, monkeypatch):
        monkeypatch.setattr(scraper, "run_js_sync",
                            lambda b, js, timeout=10.0: '{"error":"未找到表格","headers":[],"rows":[]}')
        assert scraper.extract_table(object(), "table") == []


class TestPagedScrape:
    def test_stops_when_no_next_page(self, monkeypatch):
        """找不到下一页元素时，抓完当前页即停止。"""
        monkeypatch.setattr(scraper, "extract_by_rules", lambda *a: [{"x": 1}])
        monkeypatch.setattr(scraper, "_click_next_page", lambda *a: False)
        rows = scraper.extract_by_rules_paged(object(), [], ".next", max_pages=5)
        assert rows == [{"x": 1}]

    def test_respects_max_pages(self, monkeypatch):
        """抓取页数不超过 max_pages。"""
        counter = {"p": 0}
        def fake_extract(*a):
            counter["p"] += 1
            return [{"p": counter["p"]}]
        monkeypatch.setattr(scraper, "extract_by_rules", fake_extract)
        monkeypatch.setattr(scraper, "_click_next_page", lambda *a: True)
        rows = scraper.extract_by_rules_paged(object(), [], ".next", max_pages=3)
        assert counter["p"] == 3
        assert [r["p"] for r in rows] == [1, 2, 3]
