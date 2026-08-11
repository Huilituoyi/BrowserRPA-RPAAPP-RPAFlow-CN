# -*- coding: utf-8 -*-
"""TaskDef / TaskStore 单元测试。"""
import os
import json
import threading

import pytest

from core.tasks.task_models import TaskDef, TaskStore


class TestTaskDef:
    """TaskDef 数据模型。"""

    def test_auto_id(self):
        """新建 TaskDef 自动生成 8 位 hex id。"""
        t = TaskDef(name="测试任务")
        assert t.id and len(t.id) == 8
        assert t.name == "测试任务"

    def test_to_dict_from_dict_roundtrip(self):
        """序列化→反序列化往返一致。"""
        t = TaskDef(name="抓取", kind="scrape_rules", schedule={"minutes": 5})
        t.history.append({"time": "2026-01-01", "status": "success", "error": "", "duration_sec": 1.2})
        d = t.to_dict()
        t2 = TaskDef.from_dict(d)
        assert t2.name == t.name
        assert t2.kind == t.kind
        assert t2.schedule == t.schedule
        assert t2.history == t.history

    def test_from_dict_missing_fields(self):
        """缺失字段使用 dataclass 默认值。"""
        t = TaskDef.from_dict({"id": "abc12345"})
        assert t.name == ""
        assert t.kind == "scrape_rules"
        assert t.enabled is True
        assert t.history == []

    def test_from_dict_empty_history(self):
        """旧数据没有 history 字段时兼容。"""
        t = TaskDef.from_dict({"id": "old1", "name": "旧任务"})
        assert t.history == []

    def test_history_truncation_manual(self):
        """history 超过 20 条时手动截断逻辑正确。"""
        t = TaskDef(name="历史测试")
        for i in range(25):
            t.history.append({"time": str(i), "status": "success", "error": "", "duration_sec": 0})
        # 模拟调度器截断
        t.history = t.history[:20]
        assert len(t.history) == 20


class TestTaskStore:
    """TaskStore 持久化 + 线程安全。"""

    def test_upsert_and_get(self, tmp_data_dir):
        """写入后能读取。"""
        path = os.path.join(tmp_data_dir, "tasks.json")
        store = TaskStore(path)
        t = TaskDef(name="任务A", kind="scrape_table")
        store.upsert(t)
        assert store.get(t.id) is not None
        assert store.get(t.id).name == "任务A"

    def test_remove(self, tmp_data_dir):
        """删除后不再存在。"""
        path = os.path.join(tmp_data_dir, "tasks.json")
        store = TaskStore(path)
        t = TaskDef(name="任务B")
        store.upsert(t)
        store.remove(t.id)
        assert store.get(t.id) is None

    def test_persistence_across_instances(self, tmp_data_dir):
        """写入文件后，新建 TaskStore 能读回。"""
        path = os.path.join(tmp_data_dir, "tasks.json")
        store1 = TaskStore(path)
        t = TaskDef(name="持久化测试")
        store1.upsert(t)

        store2 = TaskStore(path)
        assert store2.get(t.id) is not None
        assert store2.get(t.id).name == "持久化测试"

    def test_atomic_write(self, tmp_data_dir):
        """原子写入：保存后没有残留临时文件。"""
        path = os.path.join(tmp_data_dir, "tasks.json")
        store = TaskStore(path)
        store.upsert(TaskDef(name="原子写入"))
        # 目录下不应有 .tmp 文件
        files = [f for f in os.listdir(tmp_data_dir) if f.endswith(".tmp")]
        assert files == [], f"发现残留临时文件: {files}"

    def test_json_file_valid(self, tmp_data_dir):
        """保存的 JSON 文件格式正确。"""
        path = os.path.join(tmp_data_dir, "tasks.json")
        store = TaskStore(path)
        store.upsert(TaskDef(name="格式校验"))
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["name"] == "格式校验"

    def test_concurrent_upsert(self, tmp_data_dir):
        """10 个线程并发 upsert 不丢数据。"""
        path = os.path.join(tmp_data_dir, "tasks.json")
        store = TaskStore(path)

        def writer(i):
            store.upsert(TaskDef(name="并发%d" % i))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(store.all()) == 10

    def test_load_corrupt_file(self, tmp_data_dir):
        """损坏的 JSON 文件能优雅降级。"""
        path = os.path.join(tmp_data_dir, "tasks.json")
        os.makedirs(tmp_data_dir, exist_ok=True)
        with open(path, "w") as f:
            f.write("{{invalid json")
        store = TaskStore(path)
        # 不报错，返回空列表
        assert store.all() == []
