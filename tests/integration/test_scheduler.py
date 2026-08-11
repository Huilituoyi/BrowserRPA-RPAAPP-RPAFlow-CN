# -*- coding: utf-8 -*-
"""调度器 scheduler.py 测试：并发控制、任务级锁、历史截断。

直接多线程调用 TaskScheduler._run 来验证并发语义，绕过 APScheduler 的
触发时序，使断言稳定。runner.run_task 被 mock 为可控的占位函数。
"""
import os
import threading
import time

import pytest
from PySide6.QtWidgets import QApplication

from core.tasks.task_models import TaskDef, TaskStore
from core.tasks.scheduler import TaskScheduler, MAX_CONCURRENT_BROWSERS
from core.tasks import runner


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _store(tmp_data_dir):
    return TaskStore(os.path.join(tmp_data_dir, "tasks.json"))


def _play_task():
    return TaskDef(name="t", kind="play_actions",
                   payload={"actions": [{"type": "navigate", "url": "https://x.com"}]})


class TestTaskLock:
    def test_same_task_skipped_while_running(self, tmp_data_dir, fake_config, monkeypatch):
        """同一任务正在执行时，第二次触发应被任务级锁跳过。"""
        store = _store(tmp_data_dir)
        sched = TaskScheduler(store, fake_config)
        started = threading.Event()
        block = threading.Event()
        count = {"n": 0}
        def fake_run(task, config):
            count["n"] += 1
            started.set()
            block.wait(timeout=3)
        monkeypatch.setattr(runner, "run_task", fake_run)
        t = _play_task(); store.upsert(t)

        th1 = threading.Thread(target=sched._run, args=(t.id,))
        th1.start()
        assert started.wait(timeout=3)
        th2 = threading.Thread(target=sched._run, args=(t.id,))
        th2.start(); th2.join(timeout=3)
        assert not th2.is_alive(), "第二次触发应被立即跳过"
        block.set(); th1.join(timeout=3)
        assert count["n"] == 1, "同一任务不应并发执行两次"
        sched.shutdown()


class TestGlobalConcurrency:
    def test_concurrency_capped_at_max(self, tmp_data_dir, fake_config, monkeypatch):
        """多个不同任务同时触发，并发执行数不超过 MAX_CONCURRENT_BROWSERS。"""
        store = _store(tmp_data_dir)
        sched = TaskScheduler(store, fake_config)
        state = {"n": 0, "peak": 0}
        guard = threading.Lock()
        def fake_run(task, config):
            with guard:
                state["n"] += 1
                state["peak"] = max(state["peak"], state["n"])
            time.sleep(0.25)
            with guard:
                state["n"] -= 1
        monkeypatch.setattr(runner, "run_task", fake_run)

        tids = []
        for i in range(6):
            t = _play_task(); t.name = f"t{i}"; store.upsert(t); tids.append(t.id)

        threads = [threading.Thread(target=sched._run, args=(tid,)) for tid in tids]
        for x in threads: x.start()
        for x in threads: x.join(timeout=10)
        assert state["peak"] <= MAX_CONCURRENT_BROWSERS
        assert state["peak"] == MAX_CONCURRENT_BROWSERS, "6 任务应打满并发上限"
        sched.shutdown()


class TestHistory:
    def test_history_truncated_to_20(self, tmp_data_dir, fake_config, monkeypatch):
        """执行历史超过 20 条时被截断。"""
        store = _store(tmp_data_dir)
        sched = TaskScheduler(store, fake_config)
        monkeypatch.setattr(runner, "run_task", lambda t, c: None)
        t = _play_task(); store.upsert(t)
        for _ in range(25):
            sched._run(t.id)
        assert len(store.get(t.id).history) == 20
        sched.shutdown()

    def test_failure_records_error(self, tmp_data_dir, fake_config, monkeypatch):
        """任务失败时 history 记录 failed 状态与错误信息。"""
        store = _store(tmp_data_dir)
        sched = TaskScheduler(store, fake_config)
        def boom(t, c): raise RuntimeError("boom")
        monkeypatch.setattr(runner, "run_task", boom)
        t = _play_task(); store.upsert(t)
        sched._run(t.id)
        rec = store.get(t.id)
        assert rec.last_status == "failed"
        assert "boom" in rec.last_error
        assert rec.history[0]["status"] == "failed"
        sched.shutdown()
