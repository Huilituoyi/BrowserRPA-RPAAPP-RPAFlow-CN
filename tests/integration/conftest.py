# -*- coding: utf-8 -*-
"""集成测试共享 fixtures：可控的 Fake Playwright / 内存配置。

目的：把 runner / scheduler 对真实浏览器与外部进程的依赖隔离开，
使测试无需启动 Chromium 即可验证回放逻辑、资源释放与并发控制。
"""
import pytest


# ============ Fake Playwright 对象套件 ============
class FakeLocator:
    def __init__(self, page=None):
        self.page = page
        self.actions = []
        self.click_side_effect = None
    def click(self, timeout=None):
        if self.click_side_effect:
            raise self.click_side_effect
        self.actions.append(("click", timeout))
    def fill(self, value, timeout=None): self.actions.append(("fill", value, timeout))
    def select_option(self, value, timeout=None): self.actions.append(("select_option", value, timeout))
    def check(self, timeout=None): self.actions.append(("check", timeout))
    def uncheck(self, timeout=None): self.actions.append(("uncheck", timeout))
    def hover(self, timeout=None): self.actions.append(("hover", timeout))


class FakePage:
    def __init__(self, browser=None):
        self.browser = browser
        self.goto_calls = []
        self.evaluate_calls = []
        self.locators = []
    def goto(self, url, timeout=None): self.goto_calls.append(url)
    def wait_for_load_state(self, state="load", timeout=None): pass
    def evaluate(self, script):
        self.evaluate_calls.append(script)
        return self.browser._evaluate_result if self.browser else None
    def locator(self, sel):
        loc = FakeLocator(self); self.locators.append(loc); return loc
    def get_by_role(self, role, name=None):
        loc = FakeLocator(self); self.locators.append(loc); return loc
    def get_by_text(self, text):
        loc = FakeLocator(self); self.locators.append(loc); return loc
    def press(self, key, timeout=None): pass


class FakeContext:
    def __init__(self, browser):
        self.browser = browser
        self.closed = False
        self.page = FakePage(browser)
    def new_page(self): return self.page
    def close(self):
        self.closed = True
        self.browser.pw._record("context_close")


class FakeBrowserType:
    def __init__(self, pw):
        self.pw = pw
        self.new_context_side_effect = None  # 测试可设置，让 new_context 抛异常
    def launch(self, headless=True):
        self.pw.last_headless = headless
        b = FakeBrowser(self.pw)
        b.new_context_side_effect = self.new_context_side_effect
        self.pw.browsers.append(b)
        return b


class FakeBrowser:
    def __init__(self, pw):
        self.pw = pw
        self.closed = False
        self.context = None
        self.new_context_kwargs = None
        self.new_context_side_effect = None
        self._evaluate_result = pw._evaluate_result  # 继承全局 evaluate 结果
    def new_context(self, **kw):
        self.new_context_kwargs = kw
        if self.new_context_side_effect:
            raise self.new_context_side_effect
        self.context = FakeContext(self)
        self.pw._record("new_context")
        return self.context
    def close(self):
        self.closed = True
        self.pw._record("browser_close")


class FakePlaywright:
    """playwright.sync_api.sync_playwright().start() 的替身。"""
    def __init__(self):
        self.stopped = False
        self.browsers = []
        self.events = []
        self._evaluate_result = None
        self.chromium = FakeBrowserType(self)
    def _record(self, ev): self.events.append(ev)
    def stop(self):
        self.stopped = True
        self._record("pw_stop")
    def set_evaluate_result(self, value): self._evaluate_result = value


class _Starter:
    def __init__(self): self.instance = FakePlaywright()
    def start(self):
        self.instance._record("pw_start")
        return self.instance


@pytest.fixture
def fake_pw(monkeypatch):
    """替换 playwright.sync_api.sync_playwright，返回可控的 FakePlaywright。"""
    starter = _Starter()
    import playwright.sync_api as pwa
    monkeypatch.setattr(pwa, "sync_playwright", lambda: starter)
    return starter.instance


@pytest.fixture
def fake_config():
    """内存版配置，行为对齐 AppConfig.get 多级取值。step_delay 设为 0 加速测试。"""
    class _Cfg:
        def __init__(self):
            self._d = {
                "browser": {"user_agent": "UA", "viewport_width": 1280,
                            "viewport_height": 800, "timeout_ms": 30000},
                "runner": {"headless": True, "step_delay_min": 0, "step_delay_max": 0},
            }
        def get(self, *keys, default=None):
            cur = self._d
            for k in keys:
                if isinstance(cur, dict) and k in cur:
                    cur = cur[k]
                else:
                    return default
            return cur
    return _Cfg()
