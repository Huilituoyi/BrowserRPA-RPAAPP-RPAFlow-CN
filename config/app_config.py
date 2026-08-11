# -*- coding: utf-8 -*-
"""
可读写运行时配置：浏览器设置、Oracle 连接、录制选项等。
持久化到 data/config/app_config.json，UI 修改后自动保存。
"""
import json
import os
from PySide6.QtCore import QObject, Signal

from config.settings import (
    CONFIG_FILE, CONFIG_DIR, DEFAULT_USER_AGENT,
    DEFAULT_VIEWPORT_WIDTH, DEFAULT_VIEWPORT_HEIGHT, DEFAULT_HOME_URL,
    DEFAULT_TIMEOUT_MS, LOG_LEVEL,
)


# ---------- 默认配置模板 ----------
DEFAULT_CONFIG = {
    "browser": {
        "home_url": DEFAULT_HOME_URL,
        "user_agent": DEFAULT_USER_AGENT,
        "viewport_width": DEFAULT_VIEWPORT_WIDTH,
        "viewport_height": DEFAULT_VIEWPORT_HEIGHT,
        "javascript_enabled": True,
        "load_images": True,
        "proxy": "",                 # 形如 http://host:port，留空则不使用
        "ignore_ssl_errors": False,
        "timeout_ms": DEFAULT_TIMEOUT_MS,
        "incognito": False,          # 无痕模式：True=每次全新不留痕，False=保留登录态
    },
    "recorder": {
        "auto_select_id": True,      # 优先用 id 作为定位
        "auto_select_text": True,    # 按钮等可用可见文本定位
        "record_scroll": False,      # 默认不录制滚动（噪声大）
        "record_hover": False,
    },
    "oracle": {
        # 占位配置：瘦模式连接，无需 Oracle 客户端
        "host": "localhost",
        "port": 1521,
        "service_name": "ORCL",
        "username": "",
        "password": "",
        "table": "",                 # 默认操作的表名
    },
    "runner": {
        "step_delay_min": 1.0,       # 回放时每步之间最小停顿(秒)
        "step_delay_max": 3.0,       # 最大停顿(秒)，实际在 min~max 间随机，更拟人
        "headless": True,            # 定时任务执行时是否隐藏浏览器窗口(True=隐藏)
    },
    "ocr": {
        "port": 8848,                # ddddocr 验证码识别服务端口
        "host": "127.0.0.1",         # 监听地址：127.0.0.1=仅本机，0.0.0.0=局域网可访问
        "autostart": False,          # 应用启动时是否自动开启 OCR 服务
    },
    "log": {
        "level": LOG_LEVEL,
    },
    "colors": {
        # 代码语法高亮颜色
        "code_keyword": "#0000FF",    # 关键字（蓝）
        "code_string": "#A31515",     # 字符串（红）
        "code_comment": "#008000",    # 注释（绿）
        "code_number": "#098658",     # 数字（青绿）
        "code_func": "#795E26",       # 函数名（棕）
        "code_default": "#000000",    # 默认文字
        "code_bg": "#FFFFFF",         # 代码背景
        # 日志颜色
        "log_debug": "#6B7280",
        "log_info": "#1F2937",
        "log_warning": "#B45309",
        "log_error": "#DC2626",
        "log_critical": "#7F1D1D",
        "log_bg_light": "#FFFFFF",
        "log_bg_dark": "#1E1E1E",
        "log_fg_light": "#1F2937",
        "log_fg_dark": "#D1D5DB",
        # 高亮颜色（代码生成面板中点击动作时高亮行）
        "highlight_bg": "#FFEB8C",
        # 主题
        "log_theme": "system",       # system / light / dark
    },
}


class AppConfig(QObject):
    """全局配置单例，UI 与各模块共享。修改后发信号、自动落盘。"""

    config_changed = Signal(str, object)  # (key_path, new_value)

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        super().__init__()
        self._initialized = True
        self._data = self._load()

    # ---------- 读写 ----------
    def _load(self) -> dict:
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return _deep_merge(DEFAULT_CONFIG, json.load(f))
            except Exception:
                # 配置损坏则回退默认
                pass
        return json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝默认

    def save(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def reload(self):
        self._data = self._load()

    # ---------- 取值 ----------
    def get(self, *keys, default=None):
        """get('browser', 'user_agent') 多级取值。"""
        cur = self._data
        for k in keys:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                return default
        return cur

    def set(self, *keys_and_value):
        """set('browser', 'user_agent', 'xxx') 多级赋值并保存发信号。"""
        if len(keys_and_value) < 2:
            raise ValueError("至少提供一个 key 和一个 value")
        *keys, value = keys_and_value
        cur = self._data
        for k in keys[:-1]:
            cur = cur.setdefault(k, {})
        cur[keys[-1]] = value
        self.save()
        self.config_changed.emit(".".join(keys), value)

    def all(self) -> dict:
        return self._data


def _deep_merge(base: dict, override: dict) -> dict:
    """用 override 覆盖 base（递归一层），保证新增字段有默认值。"""
    result = json.loads(json.dumps(base))
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k].update(v)
        else:
            result[k] = v
    return result
