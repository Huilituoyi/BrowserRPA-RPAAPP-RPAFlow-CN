# -*- coding: utf-8 -*-
"""代码生成单元测试。"""
import pytest

from core.recorder.action_models import Action, Selector
from core.recorder.codegen import generate, generate_actions_only, LANGUAGES, ext_of, label_of


def make_actions():
    """构造标准测试动作序列。"""
    return [
        Action(type="navigate", url="https://example.com"),
        Action(type="click", selector=Selector(role="button", name="搜索")),
        Action(type="fill", selector=Selector(css="#kw"), value="测试关键词"),
        Action(type="press", selector=Selector(css="#kw"), value="Enter"),
    ]


class TestMultiLanguage:
    """多语言生成。"""

    def test_all_languages_produce_code(self):
        actions = make_actions()
        for lang in LANGUAGES:
            code, _ = generate_actions_only(actions, lang)
            assert isinstance(code, str)
            assert len(code) > 0, f"{lang} 生成了空代码"

    def test_python_contains_page_goto(self):
        actions = make_actions()
        code, _ = generate_actions_only(actions, "python")
        assert "page.goto" in code or "goto" in code.lower()

    def test_ext_of(self):
        assert ext_of("python") == "py"
        assert ext_of("javascript") == "js"
        assert ext_of("typescript") == "ts"
        assert ext_of("csharp") == "cs"
        assert ext_of("java") == "java"
        assert ext_of("unknown") == "txt"

    def test_label_of(self):
        assert label_of("python") == "Python"
        assert label_of("javascript") == "JavaScript"
        assert label_of("csharp") == "C#"


class TestActionsOnly:
    """generate_actions_only 不含模板。"""

    def test_no_import_statement(self):
        """actions_only 模式不含 import/from。"""
        actions = make_actions()
        code, _ = generate_actions_only(actions, "python")
        assert "import" not in code
        assert "from" not in code

    def test_line_map_count(self):
        """行号映射数量与动作数量一致。"""
        actions = make_actions()
        _, line_map = generate_actions_only(actions, "python")
        assert len(line_map) == len(actions)


class TestEdgeCases:
    """边界情况。"""

    def test_empty_actions(self):
        code, line_map = generate_actions_only([], "python")
        assert isinstance(code, str)
        assert len(line_map) == 0

    def test_action_without_selector(self):
        """没有 selector 的动作不报错。"""
        actions = [Action(type="navigate", url="https://x.com")]
        code, _ = generate_actions_only(actions, "python")
        assert isinstance(code, str)
        assert len(code) > 0
