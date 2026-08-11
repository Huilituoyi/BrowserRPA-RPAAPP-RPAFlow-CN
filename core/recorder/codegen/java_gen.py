# -*- coding: utf-8 -*-
"""Java + Playwright 脚本生成器。"""
from .base import BaseGenerator
from ..action_models import Action

_ROLE = {
    "button": "BUTTON", "link": "LINK", "checkbox": "CHECKBOX", "radio": "RADIO",
    "combobox": "COMBOBOX", "textbox": "TEXTBOX", "menuitem": "MENU_ITEM",
    "tab": "TAB", "navigation": "NAVIGATION", "search": "SEARCH",
}


def _q(v) -> str:
    s = "" if v is None else str(v)
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return '"' + s + '"'


class JavaGenerator(BaseGenerator):
    LANG = "java"
    INDENT = "        "   # 类内 main 内 = 8 空格

    def header(self) -> str:
        return (
            "// 由 RPAAPP 录制生成（Java / Playwright）\n"
            "import com.microsoft.playwright.*;\n\n"
            "public class RpaScript {\n"
            "    public static void main(String[] args) {\n"
            "        try (Playwright playwright = Playwright.create()) {\n"
            "            Browser browser = playwright.chromium().launch(new BrowserType.LaunchOptions().setHeadless(false));\n"
            "            Page page = browser.newPage();\n"
        )

    def footer(self) -> str:
        return (
            "\n            browser.close();\n        }\n    }\n}\n"
        )

    def empty(self) -> str:
        return "// 暂无录制动作"

    def _role(self, r: str) -> str:
        return "AriaRole." + _ROLE.get(r.lower(), r.upper())

    def _loc(self, sel) -> str:
        kind, primary, secondary = self.pick(sel)
        if kind == "role":
            return f"page.getByRole({self._role(primary)}, new Page.GetByRoleOptions().setName({_q(secondary)}))"
        if kind == "text":
            return f"page.getByText({_q(primary)})"
        if kind == "xpath":
            return f"page.locator({_q('xpath=' + primary)})"
        return f"page.locator({_q(primary)})"

    def emit(self, a: Action) -> str:
        t = a.type
        if t == "navigate":
            return f"page.goto({_q(a.url)});"
        if t == "click":
            return f"{self._loc(a.selector)}.click();"
        if t == "fill":
            return f"{self._loc(a.selector)}.fill({_q(a.value)});"
        if t == "select_option":
            return f"{self._loc(a.selector)}.selectOption({_q(a.value)});"
        if t == "check":
            if a.value == "checked":
                return f"{self._loc(a.selector)}.check();"
            return f"{self._loc(a.selector)}.uncheck();"
        if t == "press":
            return f'page.locator("body").press({_q(a.value)});'
        if t == "hover":
            return f"{self._loc(a.selector)}.hover();"
        if t == "scroll":
            x, y = self.parse_scroll(a.value)
            return f'page.evaluate("window.scrollTo({x}, {y})");'
        if t == "wait":
            return f"Thread.sleep((long)(float({_q(a.value or '1')}) * 1000));"
        if t == "mark":
            return "// ── 标记点（重试起点）──"
        if t == "slide_right":
            return "// 滑动到最右侧：拖拽滑块按钮到轨道尽头"
        if t == "check_retry":
            css = ""
            if a.selector:
                css = a.selector.css or a.selector.id or ""
            return ("// ── 检查重试：若元素(%s)不存在则跳回上一个标记点（最多 %s 次）──\n"
                    "// 注意：判断/重试逻辑仅在定时任务回放中生效，导出脚本不包含此逻辑"
                    % (css, a.value or "3"))
        return f"// 未知动作: {t}"
