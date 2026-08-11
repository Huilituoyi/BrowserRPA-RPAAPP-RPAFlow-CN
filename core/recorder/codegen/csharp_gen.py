# -*- coding: utf-8 -*-
"""C# (.NET) + Playwright 脚本生成器。"""
from .base import BaseGenerator
from ..action_models import Action

_ROLE = {
    "button": "Button", "link": "Link", "checkbox": "Checkbox", "radio": "Radio",
    "combobox": "ComboBox", "textbox": "TextBox", "menuitem": "MenuItem",
    "tab": "Tab", "navigation": "Navigation", "search": "Search",
}


def _q(v) -> str:
    s = "" if v is None else str(v)
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return '"' + s + '"'


class CSharpGenerator(BaseGenerator):
    LANG = "csharp"
    INDENT = "        "   # 类内方法体内 = 8 空格

    def header(self) -> str:
        return (
            "// 由 RPAAPP 录制生成（.NET / Playwright）\n"
            "using System.Threading.Tasks;\n"
            "using Microsoft.Playwright;\n\n"
            "class RpaScript\n{\n"
            "    public static async Task Run()\n    {\n"
            "        using var playwright = await Playwright.CreateAsync();\n"
            "        var browser = await playwright.Chromium.LaunchAsync(new() { Headless = false });\n"
            "        var page = await browser.NewPageAsync();\n"
        )

    def footer(self) -> str:
        return (
            "\n        await browser.CloseAsync();\n    }\n}\n"
        )

    def empty(self) -> str:
        return "// 暂无录制动作"

    def _role(self, r: str) -> str:
        return "AriaRole." + (_ROLE.get(r.lower(), r[:1].upper() + r[1:]))

    def _loc(self, sel) -> str:
        kind, primary, secondary = self.pick(sel)
        if kind == "role":
            return f"page.GetByRole({self._role(primary)}, new Page.GetByRoleOptions {{ Name = {_q(secondary)} }})"
        if kind == "text":
            return f"page.GetByText({_q(primary)})"
        if kind == "xpath":
            return f"page.Locator({_q('xpath=' + primary)})"
        return f"page.Locator({_q(primary)})"

    def emit(self, a: Action) -> str:
        t = a.type
        if t == "navigate":
            return f"await page.GotoAsync({_q(a.url)});"
        if t == "click":
            return f"await {self._loc(a.selector)}.ClickAsync();"
        if t == "fill":
            return f"await {self._loc(a.selector)}.FillAsync({_q(a.value)});"
        if t == "select_option":
            return f"await {self._loc(a.selector)}.SelectOptionAsync(new[] {{ {_q(a.value)} }});"
        if t == "check":
            if a.value == "checked":
                return f"await {self._loc(a.selector)}.CheckAsync();"
            return f"await {self._loc(a.selector)}.UncheckAsync();"
        if t == "press":
            return f'await page.Locator("body").PressAsync({_q(a.value)});'
        if t == "hover":
            return f"await {self._loc(a.selector)}.HoverAsync();"
        if t == "scroll":
            x, y = self.parse_scroll(a.value)
            return f'await page.EvaluateAsync("window.scrollTo({x}, {y})");'
        if t == "wait":
            return f"await Task.Delay((int)(float({_q(a.value or '1')}) * 1000));"
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
