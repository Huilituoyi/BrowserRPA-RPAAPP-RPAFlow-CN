# -*- coding: utf-8 -*-
"""TypeScript + Playwright 脚本生成器。"""
from .base import BaseGenerator
from ..action_models import Action


def _q(v) -> str:
    s = "" if v is None else str(v)
    s = s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    return "'" + s + "'"


class TypeScriptGenerator(BaseGenerator):
    LANG = "typescript"

    def header(self) -> str:
        return (
            "// 由 RPAAPP 录制生成（TypeScript / Playwright）\n"
            "import { chromium, Page, Browser } from 'playwright';\n\n"
            "(async () => {\n"
            "  const browser: Browser = await chromium.launch({ headless: false });\n"
            "  const page: Page = await browser.newPage();\n"
        )

    def footer(self) -> str:
        return (
            "\n  await browser.close();\n})();\n"
        )

    def empty(self) -> str:
        return "// 暂无录制动作"

    def _loc(self, sel) -> str:
        kind, primary, secondary = self.pick(sel)
        if kind == "role":
            return f"page.getByRole({_q(primary)}, {{ name: {_q(secondary)} }})"
        if kind == "text":
            return f"page.getByText({_q(primary)})"
        if kind == "xpath":
            return f"page.locator({_q('xpath=' + primary)})"
        return f"page.locator({_q(primary)})"

    def emit(self, a: Action) -> str:
        t = a.type
        if t == "navigate":
            return f"await page.goto({_q(a.url)});"
        if t == "click":
            return f"await {self._loc(a.selector)}.click();"
        if t == "fill":
            return f"await {self._loc(a.selector)}.fill({_q(a.value)});"
        if t == "select_option":
            return f"await {self._loc(a.selector)}.selectOption({_q(a.value)});"
        if t == "check":
            if a.value == "checked":
                return f"await {self._loc(a.selector)}.check();"
            return f"await {self._loc(a.selector)}.uncheck();"
        if t == "press":
            return f"await page.locator('body').press({_q(a.value)});"
        if t == "hover":
            return f"await {self._loc(a.selector)}.hover();"
        if t == "scroll":
            x, y = self.parse_scroll(a.value)
            return f"await page.evaluate('window.scrollTo({x}, {y})');"
        if t == "wait":
            return f"await page.waitForTimeout(float({_q(a.value or '1')}) * 1000);"
        if t == "fill_captcha":
            ocr_url = (a.value or "http://127.0.0.1:8848").rstrip("/")
            img_sel = a.image_selector or "img"
            lines = [
                f"// 验证码识别：截图({_q(img_sel)}) → OCR识别 → 填入输入框",
                f"const _captchaBytes = await page.locator({_q(img_sel)}).screenshot();",
                f"const _resp = await fetch({_q(ocr_url + '/v1/ocr')}, {{",
                "  method: 'POST',",
                "  body: (() => { const fd = new FormData();"
                " fd.append('image', new Blob([_captchaBytes]), 'captcha.png'); return fd; })(),",
                "});",
                "const _data = await _resp.json();",
                "const _captchaText: string = (_data as any).result || '';",
                f"await {self._loc(a.selector)}.fill(_captchaText);",
            ]
            return "\n".join(lines)
        if t == "slide_captcha":
            ocr_url = (a.value or "http://127.0.0.1:8848").rstrip("/")
            target_sel = a.image_selector or "img"
            bg_sel = a.background_selector or "img"
            lines = [
                f"// 滑块验证码：截取小图({target_sel})+背景图({bg_sel}) → OCR识别缺口 → 拖拽滑块",
                f"const _targetBytes = await page.locator({_q(target_sel)}).screenshot();",
                f"const _bgBytes = await page.locator({_q(bg_sel)}).screenshot();",
                f"const _resp = await fetch({_q(ocr_url + '/v1/slide')}, {{",
                "  method: 'POST',",
                "  body: (() => { const fd = new FormData();"
                " fd.append('target', new Blob([_targetBytes]), 't.png');"
                " fd.append('background', new Blob([_bgBytes]), 'b.png'); return fd; })(),",
                "});",
                "const _data = await _resp.json();",
                "const _distance: number = (_data as any).target_x || 0;",
                "// 拖拽滑块到目标位置",
                f"const _slider = {self._loc(a.selector)};",
                "const _box = await _slider.boundingBox();",
                "if (_box) {",
                "  await page.mouse.move(_box.x + _box.width / 2, _box.y + _box.height / 2);",
                "  await page.mouse.down();",
                "  await page.mouse.move(_box.x + _box.width / 2 + _distance,"
                " _box.y + _box.height / 2, { steps: 20 });",
                "  await page.mouse.up();",
                "}",
            ]
            return "\n".join(lines)
        if t == "mark":
            return "// ── 标记点（重试起点）──"
        if t == "slide_right":
            sel_desc = ""
            if a.selector:
                sel_desc = a.selector.css or a.selector.id or ""
            return f"// 滑动到最右侧：拖拽滑块按钮({sel_desc})到轨道尽头"
        if t == "check_retry":
            css = ""
            if a.selector:
                css = a.selector.css or a.selector.id or ""
            return ("// ── 检查重试：若元素(%s)不存在则跳回上一个标记点（最多 %s 次）──\n"
                    "// 注意：判断/重试逻辑仅在定时任务回放中生效，导出脚本不包含此逻辑"
                    % (css, a.value or "3"))
        return f"// 未知动作: {t}"
