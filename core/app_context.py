# -*- coding: utf-8 -*-
"""
应用上下文：在各面板之间共享 config / browser / recorder / scheduler 等核心对象。
"""


class AppContext:
    def __init__(self):
        from config.app_config import AppConfig
        from core.tasks.task_models import TaskStore
        self.config = AppConfig()
        self.store = TaskStore()
        self.browser = None          # BrowserWidget（浏览器面板创建后赋值）
        self.recorder = None         # Recorder
        self.scheduler = None        # TaskScheduler（依赖 apscheduler，启动时创建）
        self.last_scraped = []       # 最近一次抓取的数据，供 Oracle 面板写入
