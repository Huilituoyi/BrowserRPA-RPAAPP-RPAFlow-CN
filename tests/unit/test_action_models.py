# -*- coding: utf-8 -*-
"""Action / Selector 数据模型单元测试。"""
from core.recorder.action_models import Action, Selector, actions_to_jsonable


class TestSelector:
    def test_defaults(self):
        s = Selector()
        assert s.css is None
        assert s.id is None

    def test_with_values(self):
        s = Selector(css="#btn", role="button", name="提交")
        assert s.css == "#btn"
        assert s.role == "button"


class TestAction:
    def test_from_dict_full(self):
        d = {
            "type": "click",
            "selector": {"css": "#submit", "role": "button", "text": "确定"},
            "value": None,
        }
        a = Action.from_dict(d)
        assert a.type == "click"
        assert a.selector.css == "#submit"
        assert a.selector.role == "button"
        assert a.selector.text == "确定"

    def test_from_dict_no_selector(self):
        d = {"type": "navigate", "url": "https://x.com"}
        a = Action.from_dict(d)
        assert a.type == "navigate"
        assert a.selector is None
        assert a.url == "https://x.com"

    def test_to_dict_roundtrip(self):
        a = Action(type="fill", selector=Selector(css="#kw"), value="hello")
        d = a.to_dict()
        a2 = Action.from_dict(d)
        assert a2.type == "fill"
        assert a2.selector.css == "#kw"
        assert a2.value == "hello"

    def test_actions_to_jsonable(self):
        actions = [
            Action(type="navigate", url="https://a.com"),
            Action(type="click", selector=Selector(css="#btn")),
        ]
        result = actions_to_jsonable(actions)
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["type"] == "navigate"
        assert result[1]["selector"]["css"] == "#btn"
