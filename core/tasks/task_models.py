# -*- coding: utf-8 -*-
"""
任务定义模型与持久化存储。
任务类型 kind：
  - scrape_rules   定时按字段规则抓取页面
  - scrape_table   定时抓取页面表格
  - play_actions   定时回放录制的动作
schedule_type: interval（间隔）/ cron（cron 表达式字段）/ date（一次性）
payload 依据 kind 不同而不同，详见 TaskDef 注释。
"""
import json
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Dict, Optional

from config.settings import TASK_DIR
from core.logging.logger import get_logger

log = get_logger("tasks")

_TASKS_FILE = os.path.join(TASK_DIR, "tasks.json")


@dataclass
class TaskDef:
    id: str = ""
    name: str = ""
    kind: str = "scrape_rules"        # scrape_rules | scrape_table | play_actions
    enabled: bool = True
    schedule_type: str = "interval"   # interval | cron | date
    schedule: Dict = field(default_factory=lambda: {"minutes": 10})
    payload: Dict = field(default_factory=dict)
    last_run: str = ""
    next_run: str = ""
    last_status: str = ""             # running | success | failed
    last_error: str = ""
    # 执行历史，每项 {"time","status","error","duration_sec"}，保留最近 20 条
    history: list = field(default_factory=list)

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:8]

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "TaskDef":
        """从字典构造 TaskDef。缺失字段或显式 null 时使用 dataclass 默认值，
        避免旧数据/不完整数据把 name、history 等覆盖为 None。"""
        kwargs = {}
        for k in cls.__dataclass_fields__:
            if k in d and d[k] is not None:
                kwargs[k] = d[k]
        return cls(**kwargs)


class TaskStore:
    """任务持久化（JSON 文件），线程安全。"""
    def __init__(self, path: str = _TASKS_FILE):
        self.path = path
        self._tasks: Dict[str, TaskDef] = {}
        self._lock = threading.Lock()
        self.load()

    def load(self):
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            if os.path.exists(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._tasks = {d["id"]: TaskDef.from_dict(d) for d in data}
                except Exception as e:
                    log.warning("任务文件读取失败，已重置：%s", e)
                    self._tasks = {}

    def save(self):
        """原子写入：先写临时文件，再 os.replace 替换，避免并发覆盖损坏。"""
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            data = json.dumps(
                [t.to_dict() for t in self._tasks.values()],
                ensure_ascii=False, indent=2,
            )
            # 写临时文件后原子替换，防止写一半崩溃导致文件损坏
            fd, tmp_path = tempfile.mkstemp(
                dir=os.path.dirname(self.path), suffix=".tmp", prefix="tasks_",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(data)
                os.replace(tmp_path, self.path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

    def all(self) -> List[TaskDef]:
        with self._lock:
            return list(self._tasks.values())

    def get(self, task_id: str) -> Optional[TaskDef]:
        with self._lock:
            return self._tasks.get(task_id)

    def upsert(self, task: TaskDef):
        with self._lock:
            self._tasks[task.id] = task
        self.save()

    def remove(self, task_id: str):
        with self._lock:
            if task_id in self._tasks:
                del self._tasks[task_id]
        self.save()
