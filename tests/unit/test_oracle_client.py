# -*- coding: utf-8 -*-
"""Oracle 客户端 oracle_client.py 测试（mock oracledb，不连真实数据库）。

覆盖：连接校验、查询行数上限、批量写入的字段一致性（含 O1 缺陷验证）。
"""
import pytest

from core.data import oracle_client
from core.data.oracle_client import OracleClient


def _cfg(username="u"):
    class _C:
        def get(self, *keys, default=None):
            d = {"oracle": {"host": "h", "port": 1521, "service_name": "s",
                            "username": username, "password": "p"}}
            cur = d
            for k in keys:
                if isinstance(cur, dict) and k in cur:
                    cur = cur[k]
                else:
                    return default
            return cur
    return _C()


class FakeCursor:
    def __init__(self, rows, description=None):
        self._rows = rows
        self.description = description or [("C",)]
        self.rowcount = len(rows)
        self.many_calls = []
    def execute(self, sql, params=None): pass
    def executemany(self, sql, data):
        data = list(data)
        self.many_calls.append((sql, data))
        self.rowcount = len(data)
    def fetchmany(self, limit):
        return self._rows[:limit]
    def __enter__(self): return self
    def __exit__(self, *a): pass


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.version = "19c"
        self.commits = 0
        self.closed = False
    def cursor(self): return self._cursor
    def commit(self): self.commits += 1
    def close(self): self.closed = True


class FakeOracledb:
    def __init__(self, cursor):
        self._cursor = cursor
        self.connect_calls = []
    def connect(self, user=None, password=None, dsn=None):
        self.connect_calls.append((user, dsn))
        return FakeConn(self._cursor)


class TestConnect:
    def test_missing_username_raises(self, monkeypatch):
        monkeypatch.setattr(oracle_client, "oracledb", FakeOracledb(FakeCursor([])))
        oc = OracleClient(_cfg(username=""))
        with pytest.raises(ValueError, match="未配置"):
            oc.connect()

    def test_dsn_built_from_config(self, monkeypatch):
        fake = FakeOracledb(FakeCursor([]))
        monkeypatch.setattr(oracle_client, "oracledb", fake)
        oc = OracleClient(_cfg())
        oc.connect()
        assert fake.connect_calls[0][1] == "h:1521/s"


class TestQuery:
    def test_limit_caps_rows(self, monkeypatch):
        rows = [("r%d" % i,) for i in range(100)]
        monkeypatch.setattr(oracle_client, "oracledb", FakeOracledb(FakeCursor(rows)))
        oc = OracleClient(_cfg())
        result = oc.query("SELECT * FROM t", limit=5)
        assert len(result) == 5
        assert result[0] == {"C": "r0"}


class TestInsertMany:
    def test_uses_first_row_columns(self, monkeypatch):
        """insert_many 以第一行字段构造 SQL。"""
        cur = FakeCursor([])
        monkeypatch.setattr(oracle_client, "oracledb", FakeOracledb(cur))
        oc = OracleClient(_cfg())
        oc.insert_many("T", [{"a": 1, "b": 2}, {"a": 3, "b": 4}])
        sql, data = cur.many_calls[0]
        assert '"a"' in sql and '"b"' in sql

    def test_extra_columns_in_later_rows_silently_dropped(self, monkeypatch):
        """O1 BUG：后续行多出的字段被静默丢弃，可能导致数据丢失。

        insert_many 仅以 rows[0] 的 key 构造列，r.get(k) 对缺失 key 填 None，
        但多出的 key 完全不会出现在 SQL 与绑定参数中。本测试断言"应保留"，
        因此在缺陷修复前会 FAIL。
        """
        cur = FakeCursor([])
        monkeypatch.setattr(oracle_client, "oracledb", FakeOracledb(cur))
        oc = OracleClient(_cfg())
        oc.insert_many("T", [{"a": 1, "b": 2}, {"a": 3, "b": 4, "c": 99}])
        sql, data = cur.many_calls[0]
        # 期望：所有出现过的列都应被写入；当前实现丢弃 c
        all_keys = set()
        for r in [{"a": 1, "b": 2}, {"a": 3, "b": 4, "c": 99}]:
            all_keys.update(r.keys())
        for k in all_keys:
            assert ('"%s"' % k) in sql, "O1: 列 %s 未出现在 SQL 中，数据被丢弃" % k
