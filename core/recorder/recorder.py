# -*- coding: utf-8 -*-
"""
录制管理器：
- 用 QWebChannel 把网页事件桥接回 Python；
- 解析为 Action 并记录；
- 每次 load_finished 自动注入监听脚本并记录 navigate；
- start/stop/clear，供 UI 与 codegen 复用动作列表。
"""
import json
import os
from typing import List

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWebChannel import QWebChannel

from core.browser.browser_widget import BrowserWidget
from core.logging.logger import get_logger
from .action_models import Action, actions_to_jsonable
from .inject_js import build_inject_js, build_deactivate_js

log = get_logger("recorder")


class _RecorderBridge(QObject):
    """暴露给 JS 的桥接对象（注册名 rpaBridge）。"""
    action_raw = Signal(str)

    @Slot(str)
    def recordAction(self, raw: str):
        self.action_raw.emit(raw)


class Recorder(QObject):
    actions_changed = Signal()       # 动作列表有变化
    state_changed = Signal(bool)     # 录制开关

    def __init__(self, browser: BrowserWidget, config):
        super().__init__()
        self._browser = browser
        self._cfg = config
        self._actions: List[Action] = []
        self._recording = False
        self._last_nav_url: str = ""

        # 建立 WebChannel 双向通道
        self._bridge = _RecorderBridge()
        self._channel = QWebChannel(browser.page)
        self._channel.registerObject("rpaBridge", self._bridge)
        browser.page.setWebChannel(self._channel)

        self._bridge.action_raw.connect(self._on_raw)
        browser.load_finished.connect(self._on_load_finished)

    # ---------- 开关 ----------
    def is_recording(self) -> bool:
        return self._recording

    def start(self):
        if self._recording:
            return
        self._recording = True
        self._last_nav_url = ""
        self.state_changed.emit(True)
        log.info("录制已开始")

        cur = self._browser.url()
        if cur and not cur.startswith("data:"):
            self._record_navigate(cur)
        self._inject()

    def stop(self):
        if not self._recording:
            return
        self._recording = False
        self.state_changed.emit(False)
        self._browser.run_js(build_deactivate_js())
        log.info("录制已停止，共记录 %d 个动作", len(self._actions))

    def clear(self):
        self._actions.clear()
        self._last_nav_url = ""
        self.actions_changed.emit()
        log.info("已清空录制动作")

    def remove_action(self, index: int):
        """删除指定索引的录制动作。"""
        if 0 <= index < len(self._actions):
            removed = self._actions.pop(index)
            self.actions_changed.emit()
            log.info("已删除动作 %d：%s", index, removed.type)

    def insert_action(self, index: int, action: Action):
        """在指定位置插入一个动作（用于手动添加验证码识别等特殊步骤）。"""
        if 0 <= index <= len(self._actions):
            self._actions.insert(index, action)
            self.actions_changed.emit()
            log.info("已在位置 %d 插入动作：%s", index, action.type)

    def update_action(self, index: int, action: Action):
        """替换指定位置的动作。"""
        if 0 <= index < len(self._actions):
            self._actions[index] = action
            self.actions_changed.emit()
            log.info("已更新动作 %d：%s", index, action.type)

    def move_action(self, index: int, direction: int):
        """移动指定索引的动作，direction=1 下移，-1 上移。"""
        new_index = index + direction
        if 0 <= index < len(self._actions) and 0 <= new_index < len(self._actions):
            self._actions[index], self._actions[new_index] = (
                self._actions[new_index], self._actions[index]
            )
            self.actions_changed.emit()
            log.info("已移动动作 %d → %d", index, new_index)

    def reorder(self, new_order, emit=True):
        """按 new_order 重排动作。new_order[i] = 原第 new_order[i] 个动作放到位置 i。
        emit=False 时不发信号（拖拽排序后由调用方自行刷新代码，避免重建列表）。"""
        if len(new_order) != len(self._actions):
            return
        self._actions = [self._actions[i] for i in new_order]
        if emit:
            self.actions_changed.emit()
        log.info("动作已重新排序")

    # ---------- 注入 ----------
    def _inject(self):
        rs = self._cfg.get("recorder", "record_scroll", default=False)
        rh = self._cfg.get("recorder", "record_hover", default=False)
        self._browser.run_js(build_inject_js(True, rs, rh))

    def _on_load_finished(self, ok: bool, url: str):
        if not self._recording:
            return
        if ok:
            self._inject()
            if url and not url.startswith("data:"):
                self._record_navigate(url)

    def _record_navigate(self, url: str):
        if url == self._last_nav_url:
            return
        self._last_nav_url = url
        self._append(Action(type="navigate", url=url))

    # ---------- 接收 JS 动作 ----------
    def _on_raw(self, raw: str):
        try:
            d = json.loads(raw)
        except Exception as e:
            log.warning("无法解析录制事件：%s", e)
            return
        if d.get("type") == "ready":
            return  # 绑定心跳，不入列表
        self._append(Action.from_dict(d))

    def _append(self, action: Action):
        self._actions.append(action)
        log.info("记录动作：%s%s%s",
                 action.type,
                 (" value=" + str(action.value)) if action.value else "",
                 (" url=" + str(action.url)) if (action.type == "navigate") else "")
        self.actions_changed.emit()

    # ---------- 读取/持久化 ----------
    def actions(self) -> List[Action]:
        return list(self._actions)

    def actions_count(self) -> int:
        return len(self._actions)

    def action_dicts(self):
        return actions_to_jsonable(self._actions)

    def save(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.action_dicts(), f, ensure_ascii=False, indent=2)
        log.info("录制动作已保存：%s（共 %d 步）", path, len(self._actions))

    def load(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._actions = [Action.from_dict(d) for d in data]
        self.actions_changed.emit()
        log.info("已载入录制动作：%s（共 %d 步）", path, len(self._actions))
