# -*- coding: utf-8 -*-
"""
Oracle 数据库客户端（oracledb 瘦模式，无需安装 Oracle 客户端）。
连接信息来自 AppConfig('oracle', ...)，默认为占位值，由用户在设置面板填入真实信息。

注意：
- 列名/表名用双引号包裹，兼容中文列名；
- 绑定参数名使用 ASCII（c0,c1…），where 条件的参数也需用 ASCII 名（见 update 注释）。
"""
from typing import List, Dict, Optional, Tuple, Any

import oracledb

from core.logging.logger import get_logger

log = get_logger("oracle")


class OracleClient:
    def __init__(self, config):
        self._cfg = config
        self._conn = None

    # ---------- 连接 ----------
    def _dsn(self) -> str:
        h = self._cfg.get("oracle", "host", default="localhost")
        p = self._cfg.get("oracle", "port", default=1521)
        s = self._cfg.get("oracle", "service_name", default="ORCL")
        return f"{h}:{p}/{s}"

    def connect(self):
        u = self._cfg.get("oracle", "username", default="")
        pw = self._cfg.get("oracle", "password", default="")
        if not u:
            raise ValueError("未配置 Oracle 用户名，请先在设置中填写连接信息")
        self._conn = oracledb.connect(user=u, password=pw, dsn=self._dsn())
        ver = self._conn.version
        log.info("Oracle 已连接：%s（版本 %s）", self._dsn(), ver)
        return self._conn

    def disconnect(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
            log.info("Oracle 连接已关闭")

    @property
    def conn(self):
        return self._conn

    def is_connected(self) -> bool:
        return self._conn is not None

    def ensure(self):
        if not self.is_connected():
            self.connect()
        return self._conn

    def test_connection(self) -> Tuple[bool, str]:
        """测试连接，不影响当前长连接。"""
        try:
            u = self._cfg.get("oracle", "username", default="")
            pw = self._cfg.get("oracle", "password", default="")
            if not u:
                return False, "未配置用户名"
            c = oracledb.connect(user=u, password=pw, dsn=self._dsn())
            ver = c.version
            c.close()
            return True, f"连接成功（Oracle {ver}）"
        except Exception as e:
            return False, str(e)

    # ---------- 查询 ----------
    def query(self, sql: str, params: Any = None, limit: int = 10000) -> List[Dict]:
        """查询，返回 list[dict]。params 可为 dict（命名）或 sequence（位置）。
        limit 限制最大取数行数，防止 fetchall 大数据量撑爆内存。"""
        conn = self.ensure()
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            cols = [d[0] for d in (cur.description or [])]
            rows = [dict(zip(cols, r)) for r in cur.fetchmany(limit)]
        log.info("查询完成，返回 %d 行（上限 %d）", len(rows), limit)
        return rows

    def list_tables(self) -> List[str]:
        rows = self.query("SELECT table_name FROM user_tables ORDER BY table_name")
        return [r["TABLE_NAME"] for r in rows]

    def table_columns(self, table: str) -> List[str]:
        rows = self.query(
            "SELECT column_name FROM user_tab_columns WHERE table_name = UPPER(:t) ORDER BY column_id",
            {"t": table.strip('"')},
        )
        return [r["COLUMN_NAME"] for r in rows]

    # ---------- 写入 ----------
    def execute(self, sql: str, params: Any = None) -> int:
        conn = self.ensure()
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            conn.commit()
            return cur.rowcount

    def insert(self, table: str, row: Dict) -> int:
        items = list(row.items())
        cols = ",".join([f'"{k}"' for k, _ in items])
        binds = ",".join([f":c{i}" for i in range(len(items))])
        params = {f"c{i}": v for i, (_, v) in enumerate(items)}
        sql = f'INSERT INTO "{table}" ({cols}) VALUES ({binds})'
        n = self.execute(sql, params)
        log.info("插入 %s：%d 行受影响", table, n)
        return n

    def insert_many(self, table: str, rows: List[Dict]) -> int:
        if not rows:
            return 0
        # 用所有行出现过的列构造 SQL，避免后续行多出的字段被静默丢弃（O1）
        keys: List[str] = []
        seen: set = set()
        for r in rows:
            for k in r.keys():
                if k not in seen:
                    seen.add(k)
                    keys.append(k)
        cols = ",".join([f'"{k}"' for k in keys])
        binds = ",".join([f":c{i}" for i in range(len(keys))])
        sql = f'INSERT INTO "{table}" ({cols}) VALUES ({binds})'
        data = [{f"c{i}": r.get(k) for i, k in enumerate(keys)} for r in rows]
        conn = self.ensure()
        with conn.cursor() as cur:
            cur.executemany(sql, data)
            conn.commit()
            n = cur.rowcount
        log.info("批量插入 %s：%d 行受影响", table, n)
        return n

    def update(self, table: str, data: Dict, where: str = "",
               where_params: Optional[Dict] = None) -> int:
        """
        更新。where 中的绑定参数需用 ASCII 名（如 :w0），并通过 where_params 传入。
        例：update('t', {'price':10}, 'id=:w0', {'w0':7})
        """
        items = list(data.items())
        sets = ",".join([f'"{k}"=:c{i}' for i, (k, _) in enumerate(items)])
        params = {f"c{i}": v for i, (_, v) in enumerate(items)}
        sql = f'UPDATE "{table}" SET {sets}'
        if where:
            sql += " WHERE " + where
            if where_params:
                params.update(where_params)
        n = self.execute(sql, params)
        log.info("更新 %s：%d 行受影响", table, n)
        return n

    def delete(self, table: str, where: str = "", params: Any = None) -> int:
        sql = f'DELETE FROM "{table}"'
        if where:
            sql += " WHERE " + where
        n = self.execute(sql, params)
        log.info("删除 %s：%d 行受影响", table, n)
        return n
