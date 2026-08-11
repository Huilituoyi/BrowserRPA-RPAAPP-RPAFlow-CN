# -*- coding: utf-8 -*-
"""Python + Playwright 脚本生成器。"""
from .base import BaseGenerator
from ..action_models import Action


def _q(v) -> str:
    return repr("" if v is None else str(v))


class PythonGenerator(BaseGenerator):
    LANG = "python"

    def header(self) -> str:
        return (
            "# 由 RPAAPP 录制生成（Python / Playwright）\n"
            "from playwright.sync_api import sync_playwright\n\n"
            "def run(playwright):\n"
            "    browser = playwright.chromium.launch(headless=False)\n"
            "    page = browser.new_page()\n"
        )

    def footer(self) -> str:
        return (
            "\n    browser.close()\n\n"
            "with sync_playwright() as p:\n    run(p)\n"
        )

    def empty(self) -> str:
        return "pass  # 暂无录制动作"

    def _loc(self, sel) -> str:
        kind, primary, secondary = self.pick(sel)
        if kind == "role":
            return f"page.get_by_role({_q(primary)}, name={_q(secondary)})"
        if kind == "text":
            return f"page.get_by_text({_q(primary)})"
        if kind == "xpath":
            return f"page.locator({_q('xpath=' + primary)})"
        return f"page.locator({_q(primary)})"

    def emit(self, a: Action) -> str:
        t = a.type
        if t == "navigate":
            return f"page.goto({_q(a.url)})"
        if t == "click":
            return f"{self._loc(a.selector)}.click()"
        if t == "fill":
            return f"{self._loc(a.selector)}.fill({_q(a.value)})"
        if t == "select_option":
            return f"{self._loc(a.selector)}.select_option({_q(a.value)})"
        if t == "check":
            if a.value == "checked":
                return f"{self._loc(a.selector)}.check()"
            return f"{self._loc(a.selector)}.uncheck()"
        if t == "press":
            return f'page.locator("body").press({_q(a.value)})'
        if t == "hover":
            return f"{self._loc(a.selector)}.hover()"
        if t == "scroll":
            x, y = self.parse_scroll(a.value)
            return f'page.evaluate("window.scrollTo({x}, {y})")'
        if t == "wait":
            return f"__import__('time').sleep(float({_q(a.value or '1')}))"
        if t == "fill_captcha":
            ocr_url = (a.value or "http://127.0.0.1:8848").rstrip("/")
            img_sel = a.image_selector or "img"
            lines = [
                "# 验证码识别：截图(%s) → OCR识别 → 填入输入框" % img_sel,
                "import requests as _r",
                "_captcha_bytes = page.locator(%s).screenshot()" % _q(img_sel),
                '_resp = _r.post(%s, files={"image": ("captcha.png", _captcha_bytes)})'
                    % _q(ocr_url + "/v1/ocr"),
                "_captcha_text = _resp.json().get('result', '')",
                "%s.fill(_captcha_text)" % self._loc(a.selector),
            ]
            return "\n".join(lines)
        if t == "slide_captcha":
            ocr_url = (a.value or "http://127.0.0.1:8848").rstrip("/")
            target_sel = a.image_selector or "img"
            bg_sel = a.background_selector or "img"
            lines = [
                "# 滑块验证码：截取小图+背景图 → OCR识别缺口 → 拖拽滑块",
                "import requests as _r",
                "_target_bytes = page.locator(%s).screenshot()" % _q(target_sel),
                "_bg_bytes = page.locator(%s).screenshot()" % _q(bg_sel),
                '_resp = _r.post(%s, files={"target": ("t.png", _target_bytes), '
                '"background": ("b.png", _bg_bytes)})' % _q(ocr_url + "/v1/slide"),
                "_distance = _resp.json().get('target_x', 0)",
                "# 拖拽滑块到目标位置",
                "_slider = %s" % self._loc(a.selector),
                '_box = _slider.bounding_box()',
                'page.mouse.move(_box["x"] + _box["width"] / 2, _box["y"] + _box["height"] / 2)',
                "page.mouse.down()",
                'page.mouse.move(_box["x"] + _box["width"] / 2 + _distance, '
                '_box["y"] + _box["height"] / 2, steps=20)',
                "page.mouse.up()",
            ]
            return "\n".join(lines)
        if t == "mark":
            return "# ── 标记点（重试起点）──"
        if t == "slide_right":
            return ("# 滑动到最右侧：拖拽滑块按钮 %s 到轨道尽头" %
                    (a.selector.css or a.selector.id if a.selector else ""))
        if t == "check_retry":
            css = ""
            if a.selector:
                css = a.selector.css or a.selector.id or ""
            return ("# ── 检查重试：若元素(%s)不存在则跳回上一个标记点（最多 %s 次）──\n"
                    "# 注意：判断/重试逻辑仅在定时任务回放中生效，导出脚本不包含此逻辑"
                    % (css, a.value or "3"))
        return f"# 未知动作: {t}"
