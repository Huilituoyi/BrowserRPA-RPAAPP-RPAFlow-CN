# -*- coding: utf-8 -*-
"""
任务调度器：基于 APScheduler BackgroundScheduler。
负责注册/移除/启用/暂停定时任务，任务触发时调用 runner 执行，并通过信号上报状态。
"""
import time
import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from PySide6.QtCore import QObject, Signal

from core.logging.logger import get_logger
from . import runner
from .task_models import TaskStore, TaskDef

log = get_logger("scheduler")

# 全局最大并发浏览器实例数（每个任务冷启动一个 Chromium，限制并发防内存爆炸）
MAX_CONCURRENT_BROWSERS = 3


class TaskScheduler(QObject):
    # (task_id, status, info)  status ∈ running|success|failed
    status_changed = Signal(str, str, str)

    def __init__(self, store: TaskStore, config):
        super().__init__()
        self._store = store
        self._cfg = config
        # 全局并发信号量：限制同时运行的浏览器实例数
        self._browser_sem = threading.Semaphore(MAX_CONCURRENT_BROWSERS)
        # 任务级锁：同一任务不能并发执行（防止"立即运行"与定时触发重叠）
        self._task_locks: dict = {}
        self._task_locks_guard = threading.Lock()
        self._sched = BackgroundScheduler(daemon=True)
        self._sched.start()
        self.reload_all()

    def _get_task_lock(self, task_id: str) -> threading.Lock:
        """获取（或创建）指定任务的重入锁。"""
        with self._task_locks_guard:
            if task_id not in self._task_locks:
                self._task_locks[task_id] = threading.Lock()
            return self._task_locks[task_id]

    # ---------- 触发器 ----------
    def _make_trigger(self, task: TaskDef):
        st = task.schedule_type
        s = task.schedule or {}
        if st == "cron":
            return CronTrigger(**s)
        if st == "date":
            return DateTrigger(**s)
        return IntervalTrigger(**s)

    # ---------- 注册 ----------
    def reload_all(self):
        for t in self._store.all():
            if t.enabled:
                self._register(t)

    def _register(self, task: TaskDef):
        self._unregister(task.id)
        try:
            job = self._sched.add_job(
                self._run, trigger=self._make_trigger(task),
                args=[task.id], id=task.id, replace_existing=True,
                max_instances=1,  # 同一任务最多 1 个实例在跑
            )
            t = self._store.get(task.id)
            if t and getattr(job, "next_run_time", None):
                t.next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
                self._store.upsert(t)
            log.info("已注册任务 %s（%s / %s）", task.name, task.schedule_type, task.schedule)
        except Exception as e:
            log.error("注册任务失败 %s：%s", task.id, e)

    def _unregister(self, task_id: str):
        try:
            self._sched.remove_job(task_id)
        except Exception:
            pass

    # ---------- 增删改 ----------
    def add(self, task: TaskDef):
        self._store.upsert(task)
        if task.enabled:
            self._register(task)

    def update(self, task: TaskDef):
        self._store.upsert(task)
        if task.enabled:
            self._register(task)
        else:
            self._unregister(task.id)

    def remove(self, task_id: str):
        self._unregister(task_id)
        self._store.remove(task_id)

    def set_enabled(self, task_id: str, enabled: bool):
        t = self._store.get(task_id)
        if not t:
            return
        t.enabled = enabled
        self.update(t)

    def run_now(self, task_id: str):
        """立即执行一次：通过 APScheduler 的 modify_next_run 触发，
        走调度器的 max_instances 限制，而非裸线程直接调 _run。"""
        try:
            self._sched.modify_job(task_id, next_run_time=datetime.now())
        except Exception:
            # 任务可能未注册（如禁用状态），退回为直接调度
            self._sched.add_job(
                self._run, trigger="date", run_date=datetime.now(),
                args=[task_id], id=task_id + "_once", replace_existing=True,
            )

    # ---------- 执行 ----------
    def _run(self, task_id: str):
        """任务执行入口：任务级锁防止同任务并发，全局信号量限制浏览器总数。"""
        lock = self._get_task_lock(task_id)
        if not lock.acquire(blocking=False):
            # 该任务正在运行，跳过本次触发
            log.warning("任务[%s] 上一次执行尚未结束，跳过本次触发", task_id)
            return
        try:
            # 全局信号量：限制同时启动的浏览器实例数
            if not self._browser_sem.acquire(blocking=False):
                log.warning("并发浏览器实例已达上限(%d)，任务[%s] 排队等待…",
                            MAX_CONCURRENT_BROWSERS, task_id)
                self._browser_sem.acquire()  # 阻塞等待空位
            try:
                self._run_inner(task_id)
            finally:
                self._browser_sem.release()
        finally:
            lock.release()

    def _run_inner(self, task_id: str):
        """实际执行逻辑（已被并发控制保护）。"""
        t = self._store.get(task_id)
        if not t:
            return
        name = t.name or task_id
        start_time = time.time()
        t.last_status = "running"
        t.last_run = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._store.upsert(t)
        self.status_changed.emit(task_id, "running", "")
        try:
            runner.run_task(t, self._cfg)
            t.last_status = "success"
            t.last_error = ""
        except Exception as e:
            import traceback as _tb
            log.error("任务[%s] 执行失败：%s: %s", name, type(e).__name__, e)
            log.error("完整错误栈：\n%s", _tb.format_exc())
            t.last_status = "failed"
            t.last_error = "%s: %s" % (type(e).__name__, e)
        duration = round(time.time() - start_time, 1)
        t.history.insert(0, {"time": t.last_run, "status": t.last_status,
                             "error": t.last_error, "duration_sec": duration})
        if len(t.history) > 20:
            t.history = t.history[:20]
        self._store.upsert(t)
        self.status_changed.emit(task_id, t.last_status, t.last_error)

    def shutdown(self):
        try:
            self._sched.shutdown(wait=False)
        except Exception:
            pass
