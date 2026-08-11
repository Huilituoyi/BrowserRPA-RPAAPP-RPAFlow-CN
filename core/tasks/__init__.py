# -*- coding: utf-8 -*-
"""
任务子系统：任务定义/存储 + 调度 + 执行。
注意：scheduler / runner 依赖 apscheduler / playwright，故不在包级导入，
由调用方按需 `from core.tasks.scheduler import TaskScheduler`，避免未装依赖时影响主程序启动。
"""
from .task_models import TaskDef, TaskStore

__all__ = ["TaskDef", "TaskStore"]
