# -*- coding: utf-8 -*-
"""
全局静态配置：应用路径常量与默认值。
运行时可变的配置（浏览器设置、Oracle 连接等）见 app_config.py。
"""
import os

# ---------- 应用根目录 ----------
APP_NAME = "RPAAPP"
APP_VERSION = "1.0.0"

# 项目根目录（config 的上一级）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 运行时数据目录（与代码分离，便于打包/清理）
DATA_DIR = os.path.join(BASE_DIR, "data")
LOG_DIR = os.path.join(DATA_DIR, "logs")
TASK_DIR = os.path.join(DATA_DIR, "tasks")          # 定时任务定义
SCRAPE_DIR = os.path.join(DATA_DIR, "scraped")       # 抓取的数据文件
SCRIPT_DIR = os.path.join(DATA_DIR, "scripts")       # 生成的脚本
CONFIG_DIR = os.path.join(DATA_DIR, "config")        # 用户配置
CACHE_DIR = os.path.join(DATA_DIR, "webengine_cache")  # 浏览器缓存

# 用户可编辑配置文件
CONFIG_FILE = os.path.join(CONFIG_DIR, "app_config.json")

# ---------- 日志默认值 ----------
LOG_LEVEL = "INFO"          # DEBUG / INFO / WARNING / ERROR / CRITICAL
LOG_FILE_MAX_BYTES = 5 * 1024 * 1024   # 单个日志文件最大 5MB
LOG_FILE_BACKUP_COUNT = 7              # 保留 7 个历史日志

# ---------- 浏览器默认值 ----------
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_VIEWPORT_WIDTH = 1280
DEFAULT_VIEWPORT_HEIGHT = 800
DEFAULT_HOME_URL = "https://www.bing.com"

# ---------- 默认超时（毫秒）----------
DEFAULT_TIMEOUT_MS = 30000

# ---------- 内置浏览器录制用的注入脚本标识 ----------
RECORDER_JS_TOKEN = "__RPAAPP_RECORDER__"


def ensure_dirs():
    """启动时确保所有运行时目录存在。"""
    for d in (DATA_DIR, LOG_DIR, TASK_DIR, SCRAPE_DIR, SCRIPT_DIR, CONFIG_DIR, CACHE_DIR):
        os.makedirs(d, exist_ok=True)
