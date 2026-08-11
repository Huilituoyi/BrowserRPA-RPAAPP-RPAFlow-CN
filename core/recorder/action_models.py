# -*- coding: utf-8 -*-
"""
录制动作的数据模型。
JS 端捕获事件 → JSON → Python 解析为 Action（含 Selector）。
codegen 再据 Action 生成各语言脚本。
"""
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any


@dataclass
class Selector:
    """元素定位信息：尽量记录多种，供 codegen 按语言择优使用。"""
    css: Optional[str] = None
    xpath: Optional[str] = None
    id: Optional[str] = None          # 形如 #search
    role: Optional[str] = None        # aria role，如 button / link
    name: Optional[str] = None        # accessible name（aria-label/title/alt）
    text: Optional[str] = None        # 可见文本（按钮/链接）
    tag: Optional[str] = None         # 标签名


@dataclass
class Action:
    """单个用户操作。"""
    type: str                         # navigate|...|fill_captcha|slide_captcha
    selector: Optional[Selector] = None
    value: Optional[str] = None       # fill_captcha/slide_captcha 时存 OCR 服务地址
    url: Optional[str] = None
    timestamp: Optional[str] = None
    image_selector: Optional[str] = None       # 验证码图片 CSS 选择器（fill_captcha）/ 滑块小图（slide_captcha）
    background_selector: Optional[str] = None  # 滑块背景图 CSS 选择器（slide_captcha 专用）
    expected_text: Optional[str] = None        # check_retry：元素出现后还需匹配的预期文本（留空只检查存在）

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Action":
        sel = None
        if isinstance(d.get("selector"), dict):
            sd = d["selector"]
            sel = Selector(
                css=sd.get("css"), xpath=sd.get("xpath"), id=sd.get("id"),
                role=sd.get("role"), name=sd.get("name"),
                text=sd.get("text"), tag=sd.get("tag"),
            )
        return cls(
            type=d.get("type", "unknown"),
            selector=sel,
            value=d.get("value"),
            url=d.get("url"),
            timestamp=d.get("timestamp"),
            image_selector=d.get("image_selector"),
            background_selector=d.get("background_selector"),
            expected_text=d.get("expected_text"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def actions_to_jsonable(actions: List[Action]) -> List[Dict[str, Any]]:
    return [a.to_dict() for a in actions]
