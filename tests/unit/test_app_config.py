# -*- coding: utf-8 -*-
"""AppConfig 配置管理单元测试。"""
import os
import json

from config.app_config import AppConfig, DEFAULT_CONFIG, _deep_merge


class TestDeepMerge:
    """_deep_merge 合并逻辑。"""

    def test_merge_new_keys(self):
        """override 中没有的 key 保留 base 默认。"""
        base = {"a": 1, "b": {"c": 2, "d": 3}}
        override = {"b": {"c": 99}}
        result = _deep_merge(base, override)
        assert result["a"] == 1
        assert result["b"]["c"] == 99
        assert result["b"]["d"] == 3  # 保留 base 的默认值

    def test_merge_nested_dict(self):
        """嵌套 dict 递归合并。"""
        base = {"browser": {"ua": "default", "timeout": 30000}}
        override = {"browser": {"ua": "custom"}}
        result = _deep_merge(base, override)
        assert result["browser"]["ua"] == "custom"
        assert result["browser"]["timeout"] == 30000


class TestDefaultConfig:
    """默认配置完整性。"""

    def test_browser_section(self):
        b = DEFAULT_CONFIG["browser"]
        for key in ("home_url", "user_agent", "viewport_width", "viewport_height",
                     "javascript_enabled", "load_images", "proxy", "ignore_ssl_errors",
                     "timeout_ms", "incognito"):
            assert key in b, f"browser.{key} 缺失"

    def test_colors_section(self):
        c = DEFAULT_CONFIG["colors"]
        assert "code_keyword" in c
        assert "log_error" in c
        assert "highlight_bg" in c
        assert len(c) >= 15  # 至少 15 个颜色项


class TestAppConfig:
    """AppConfig 读写。"""

    def test_get_nested(self):
        cfg = AppConfig()
        ua = cfg.get("browser", "user_agent", default="?")
        assert ua != "?" and len(ua) > 10

    def test_get_default(self):
        cfg = AppConfig()
        val = cfg.get("browser", "nonexistent_key", default="fallback")
        assert val == "fallback"

    def test_set_and_get(self):
        cfg = AppConfig()
        cfg.set("browser", "timeout_ms", 5000)
        assert cfg.get("browser", "timeout_ms") == 5000
        # 恢复默认，避免影响其他测试
        cfg.set("browser", "timeout_ms", 30000)

    def test_set_creates_nested_key(self):
        cfg = AppConfig()
        cfg.set("browser", "new_custom_key", "hello")
        assert cfg.get("browser", "new_custom_key") == "hello"
