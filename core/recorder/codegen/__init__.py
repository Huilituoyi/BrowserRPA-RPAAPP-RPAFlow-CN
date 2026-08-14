# -*- coding: utf-8 -*-
"""代码生成工厂：把录制动作一键转为 Python/JS/TS/C#/Java 的 Playwright 脚本。"""
from typing import List
from .base import BaseGenerator
from .python_gen import PythonGenerator
from .js_gen import JavaScriptGenerator
from .ts_gen import TypeScriptGenerator
from .csharp_gen import CSharpGenerator
from .java_gen import JavaGenerator
from ..action_models import Action

_GENERATORS = {
    "python": PythonGenerator,
    "javascript": JavaScriptGenerator,
    "typescript": TypeScriptGenerator,
    "csharp": CSharpGenerator,
    "java": JavaGenerator,
}
LANGUAGES = list(_GENERATORS.keys())

_EXT = {"python": "py", "javascript": "js", "typescript": "ts", "csharp": "cs", "java": "java"}
_LABEL = {"python": "Python", "javascript": "JavaScript", "typescript": "TypeScript",
          "csharp": "C#", "java": "Java"}


def generate(actions: List[Action], language: str = "python") -> str:
    """生成指定语言的脚本字符串。"""
    cls = _GENERATORS.get(language.lower(), PythonGenerator)
    return cls().generate(actions)


def generate_with_map(actions: List[Action], language: str = "python"):
    """生成完整代码并返回 (代码字符串, 行号映射 list[int])。"""
    cls = _GENERATORS.get(language.lower(), PythonGenerator)
    return cls().generate_with_map(actions)


def generate_actions_only(actions: List[Action], language: str = "python"):
    """只生成每一步动作对应的代码（不带 import/启动/关闭等模板）。"""
    cls = _GENERATORS.get(language.lower(), PythonGenerator)
    return cls().generate_actions_only(actions)


def ext_of(language: str) -> str:
    return _EXT.get(language.lower(), "txt")


def label_of(language: str) -> str:
    return _LABEL.get(language.lower(), language)
