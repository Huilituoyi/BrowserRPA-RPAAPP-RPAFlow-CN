# -*- coding: utf-8 -*-
"""回放引擎 runner.py 测试（mock 掉 Playwright，不启动真实浏览器）。

覆盖重点：
  - 任务类型分发与参数校验
  - 动作回放：navigate/click/fill/select/check/scroll 各分支
  - 资源释放：正常完成与中途异常时 browser/pw 是否被关闭
  - 多页抓取：max_pages、翻页失败提前结束

注意：test_resource_leak_on_new_context_failure 当前会 FAIL，
它精确定位了 runner._new_page 的资源泄漏缺陷（见 TEST_REPORT.md R1）。
"""
import json
import pytest

from core.tasks.task_models import TaskDef
from core.tasks import runner


def _task(kind="play_actions", payload=None):
    return TaskDef(name="T", kind=kind, payload=payload or {})


class TestDispatch:
    def test_unknown_kind_raises(self, fake_config):
        with pytest.raises(ValueError, match="未知任务类型"):
            runner.run_task(_task(kind="nope"), fake_config)

    def test_play_actions_missing_actions_raises(self, fake_pw, fake_config):
        with pytest.raises(ValueError, match="缺少 actions"):
            runner.run_task(_task(kind="play_actions", payload={"actions": []}), fake_config)

    def test_scrape_rules_missing_url_raises(self, fake_pw, fake_config):
        with pytest.raises(ValueError, match="缺少 url"):
            runner.run_task(_task(kind="scrape_rules", payload={"rules": [{"name": "x", "selector": "a"}]}),
                            fake_config)


class TestPlayActions:
    def test_navigate_then_click(self, fake_pw, fake_config):
        """navigate→click 全程无异常，资源被释放。"""
        t = _task(kind="play_actions", payload={
            "actions": [{"type": "navigate", "url": "https://x.com"},
                        {"type": "click", "selector": {"css": "#btn"}}]})
        runner.run_task(t, fake_config)
        page = fake_pw.browsers[0].context.page
        assert page.goto_calls == ["https://x.com"]
        assert page.locators and page.locators[0].actions[0][0] == "click"
        assert fake_pw.stopped and fake_pw.browsers[0].closed

    def test_fill_and_select(self, fake_pw, fake_config):
        t = _task(kind="play_actions", payload={
            "actions": [{"type": "fill", "selector": {"css": "#kw"}, "value": "hi"},
                        {"type": "select_option", "selector": {"css": "select"}, "value": "a"}]})
        runner.run_task(t, fake_config)
        page = fake_pw.browsers[0].context.page
        assert page.locators[0].actions[0] == ("fill", "hi", 30000)
        assert page.locators[1].actions[0] == ("select_option", "a", 30000)

    def test_check_uncheck(self, fake_pw, fake_config):
        t = _task(kind="play_actions", payload={
            "actions": [{"type": "check", "selector": {"css": "#c1"}, "value": "checked"},
                        {"type": "check", "selector": {"css": "#c2"}, "value": "unchecked"}]})
        runner.run_task(t, fake_config)
        locs = fake_pw.browsers[0].context.page.locators
        assert locs[0].actions[0][0] == "check"
        assert locs[1].actions[0][0] == "uncheck"

    def test_scroll_parses_xy(self, fake_pw, fake_config):
        t = _task(kind="play_actions", payload={
            "actions": [{"type": "navigate", "url": "https://x.com"},
                        {"type": "scroll", "value": json.dumps({"x": 10, "y": 200})}]})
        runner.run_task(t, fake_config)
        evals = fake_pw.browsers[0].context.page.evaluate_calls
        assert any("window.scrollTo(10, 200)" in e for e in evals)

    def test_scroll_invalid_value_falls_back_to_zero(self, fake_pw, fake_config):
        """R3：损坏的 scroll value 静默回退到 (0,0) 而非崩溃。"""
        t = _task(kind="play_actions", payload={
            "actions": [{"type": "navigate", "url": "https://x.com"},
                        {"type": "scroll", "value": "not-json"}]})
        runner.run_task(t, fake_config)  # 不应抛异常
        evals = fake_pw.browsers[0].context.page.evaluate_calls
        assert any("scrollTo(0, 0)" in e for e in evals)


class TestResourceCleanup:
    def test_browser_cleaned_on_action_failure(self, fake_pw, fake_config, monkeypatch):
        """回放中途某步抛异常，finally 仍应关闭 browser 并 stop pw。"""
        original = runner._play_one
        state = {"n": 0}
        def boom(page, a, config):
            state["n"] += 1
            if state["n"] == 2:
                raise RuntimeError("step boom")
            return original(page, a, config)
        monkeypatch.setattr(runner, "_play_one", boom)
        t = _task(kind="play_actions", payload={
            "actions": [{"type": "navigate", "url": "https://x.com"},
                        {"type": "click", "selector": {"css": "#btn"}}]})
        with pytest.raises(RuntimeError, match="第 2/2 步失败"):
            runner.run_task(t, fake_config)
        assert fake_pw.browsers[0].closed is True
        assert fake_pw.stopped is True

    def test_resource_leak_on_new_context_failure(self, fake_pw, fake_config):
        """R1 BUG：_new_page 内 new_context 抛异常时，已启动的 pw/browser 不会被清理。

        当前实现 _new_page 无 try/except 保护，异常向上抛出时调用方的
        try/finally 尚未进入，导致 Playwright(node) 与 Chromium 进程泄漏。
        本测试断言"正确行为"，因此在 bug 修复前会 FAIL。
        """
        fake_pw.chromium.new_context_side_effect = RuntimeError("context boom")
        t = _task(kind="play_actions", payload={
            "actions": [{"type": "navigate", "url": "https://x.com"}]})
        with pytest.raises(RuntimeError):
            runner.run_task(t, fake_config)
        assert fake_pw.stopped is True, "R1: new_context 失败后 pw.stop 未被调用，进程泄漏"
        assert fake_pw.browsers[0].closed is True, "R1: new_context 失败后 browser 未关闭"


class TestScrape:
    def test_scrape_rules_collects_rows(self, fake_pw, fake_config):
        fake_pw.set_evaluate_result('[{"a":"1"},{"a":"2"}]')
        t = _task(kind="scrape_rules", payload={
            "url": "https://x.com", "rules": [{"name": "a", "selector": "li"}]})
        runner.run_task(t, fake_config)
        assert fake_pw.stopped and fake_pw.browsers[0].closed

    def test_scrape_rules_empty_result(self, fake_pw, fake_config):
        fake_pw.set_evaluate_result(None)
        t = _task(kind="scrape_rules", payload={
            "url": "https://x.com", "rules": [{"name": "a", "selector": "li"}]})
        runner.run_task(t, fake_config)  # None 不应崩溃

    def test_scrape_table_max_pages_one(self, fake_pw, fake_config):
        fake_pw.set_evaluate_result('{"headers":["h"],"rows":[{"h":"v"}]}')
        t = _task(kind="scrape_table", payload={
            "url": "https://x.com", "table_selector": "table", "max_pages": 1})
        runner.run_task(t, fake_config)
        # max_pages=1 不应尝试点击下一页
        assert fake_pw.stopped
