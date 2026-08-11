# -*- coding: utf-8 -*-
"""
代码生成基类。
选择器择优策略（与 Playwright 官方 codegen 一致）：
  1) role + name      → getByRole（最稳定）
  2) text             → getByText
  3) css（含 id）     → locator
  4) xpath            → locator('xpath=...')
"""
import json
from typing import Tuple
from ..action_models import Action, Selector


class BaseGenerator:
    LANG = "base"
    INDENT = "    "

    def header(self) -> str:
        raise NotImplementedError

    def footer(self) -> str:
        raise NotImplementedError

    def empty(self) -> str:
        return "// no recorded actions"

    def emit(self, action: Action) -> str:
        raise NotImplementedError

    def generate(self, actions) -> str:
        return self.generate_with_map(actions)[0]

    def generate_with_map(self, actions) -> tuple:
        """
        生成完整代码，并返回 (完整代码, 行号映射)。
        行号映射: list[int]，第 i 个动作对应代码中的起始行号(0-based)。
        """
        header = self.header()
        header_lines = header.count("\n") + (0 if header.endswith("\n") else 1)
        first_line = header_lines if header.endswith("\n") else header_lines

        code_lines = []
        line_map = []
        cur = first_line
        for a in actions:
            line_map.append(cur)
            emitted = self.emit(a)
            n = emitted.count("\n") + 1
            code_lines.append(self.INDENT + emitted)
            cur += n

        if not code_lines:
            code_lines = [self.INDENT + self.empty()]
            line_map = []

        body = "\n".join(code_lines)
        full = header + body + self.footer()
        return (full, line_map)

    def generate_actions_only(self, actions) -> tuple:
        """
        只生成每一步动作对应的代码（不带 import/启动/关闭等模板），
        返回 (代码字符串, 行号映射)。
        """
        code_lines = []
        line_map = []
        cur = 0
        for a in actions:
            line_map.append(cur)
            emitted = self.emit(a)
            code_lines.append(emitted)
            cur += emitted.count("\n") + 1

        if not code_lines:
            code_lines = [self.empty()]
            line_map = []

        return ("\n".join(code_lines), line_map)

    # ---------- 选择器择优 ----------
    @staticmethod
    def pick(sel: Selector) -> Tuple[str, str, str]:
        """返回 (kind, primary, secondary)。kind ∈ role|text|css|xpath"""
        if not sel:
            return ("css", "body", "")
        if sel.role and (sel.name or sel.text):
            return ("role", sel.role, sel.name or sel.text)
        if sel.text:
            return ("text", sel.text, "")
        if sel.css:
            return ("css", sel.css, "")
        if sel.id:
            return ("css", sel.id, "")
        if sel.xpath:
            return ("xpath", sel.xpath, "")
        return ("css", "body", "")

    @staticmethod
    def parse_scroll(value):
        try:
            xy = json.loads(value or "{}")
            return int(xy.get("x", 0)), int(xy.get("y", 0))
        except Exception:
            return 0, 0
