import json
from datetime import datetime
from typing import Any, Optional

import pymysql
from pymysql.cursors import DictCursor

from config import Config


def _mysql_type(value: Any) -> str:
    if isinstance(value, bool):
        return "TINYINT(1)"
    if isinstance(value, int):
        return "BIGINT"
    if isinstance(value, float):
        return "DOUBLE"
    if value is None:
        return "TEXT"
    return "TEXT"


def _safe_col(name: str) -> str:
    return f"`{name}`"


class DatabaseManager:
    def __init__(self, config: Config):
        self.config = config
        self.conn: Optional[pymysql.Connection] = None

    def connect(self):
        self.conn = pymysql.connect(
            host=self.config.db_host,
            port=self.config.db_port,
            user=self.config.db_user,
            password=self.config.db_password,
            database=self.config.db_name,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
        )

    def close(self):
        if self.conn:
            self.conn.close()

    def _execute(self, sql: str, params: tuple = ()):
        cur = self.conn.cursor()
        cur.execute(sql, params)
        return cur

    def ensure_sync_meta(self):
        self._execute("""
            CREATE TABLE IF NOT EXISTS sync_meta (
                endpoint VARCHAR(255) NOT NULL,
                cabang_id INT NOT NULL,
                last_synced_at DATETIME,
                synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (endpoint, cabang_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        self.conn.commit()

    def get_last_synced(self, endpoint: str, cabang_id: int) -> Optional[datetime]:
        cur = self._execute(
            "SELECT last_synced_at FROM sync_meta WHERE endpoint = %s AND cabang_id = %s",
            (endpoint, cabang_id),
        )
        row = cur.fetchone()
        return row["last_synced_at"] if row else None

    def update_sync_meta(self, endpoint: str, cabang_id: int, last_synced_at: datetime):
        self._execute(
            "INSERT INTO sync_meta (endpoint, cabang_id, last_synced_at) "
            "VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE last_synced_at = %s, synced_at = CURRENT_TIMESTAMP",
            (endpoint, cabang_id, last_synced_at, last_synced_at),
        )
        self.conn.commit()

    def get_table_columns(self, table: str) -> set[str]:
        try:
            cur = self._execute(f"SHOW COLUMNS FROM {_safe_col(table)}")
            return {row["Field"] for row in cur.fetchall()}
        except (pymysql.err.OperationalError, pymysql.err.ProgrammingError):
            return set()

    def ensure_table(self, table: str, sample: dict) -> set[str]:
        existing = self.get_table_columns(table)
        if not existing:
            cols = []
            for k, v in sample.items():
                if k in ("id",):
                    cols.append(f"{_safe_col(k)} {_mysql_type(v)} NOT NULL")
                else:
                    cols.append(f"{_safe_col(k)} {_mysql_type(v)} NULL")
            cols.append("cabang_id INT NOT NULL")
            cols.append("synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP")
            pk = ", PRIMARY KEY (`id`, `cabang_id`)" if "id" in sample else ""
            sql = f"CREATE TABLE {_safe_col(table)} ({', '.join(cols)}{pk}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
            self._execute(sql)
            self.conn.commit()
            return set(sample.keys()) | {"cabang_id", "synced_at"}
        needed = {"cabang_id", "synced_at"}
        for k in sample:
            if k not in existing:
                col = k.replace("`", "")
                try:
                    self._execute(
                        f"ALTER TABLE {_safe_col(table)} ADD COLUMN {_safe_col(col)} TEXT NULL"
                    )
                except (pymysql.err.OperationalError, pymysql.err.ProgrammingError):
                    pass
                needed.add(col)
        return existing | needed

    def upsert_records(self, table: str, records: list[dict], cabang_id: int):
        if not records:
            return
        first = records[0]
        cols = list(first.keys()) + ["cabang_id"]
        placeholders = ", ".join(["%s"] * len(cols))
        col_names = ", ".join(_safe_col(c) for c in cols)
        updates = ", ".join(
            f"{_safe_col(c)} = VALUES({_safe_col(c)})"
            for c in first.keys()
            if c != "id"
        )
        sql = (
            f"INSERT INTO {_safe_col(table)} ({col_names}) VALUES ({placeholders})"
        )
        if updates:
            sql += f" ON DUPLICATE KEY UPDATE {updates}"
        batch = []
        for rec in records:
            row = [
                json.dumps(rec.get(k), ensure_ascii=False) if isinstance(rec.get(k), (dict, list)) else rec.get(k)
                for k in first.keys()
            ] + [cabang_id]
            batch.append(row)
        cur = self.conn.cursor()
        cur.executemany(sql, batch)
        self.conn.commit()

    def delete_cabang_data(self, table: str, cabang_id: int):
        self._execute(
            f"DELETE FROM {_safe_col(table)} WHERE cabang_id = %s", (cabang_id,)
        )
        self.conn.commit()

    def get_cabang_list(self) -> list[dict]:
        cur = self._execute("SELECT DISTINCT id, nama_cabang FROM master_cabang ORDER BY id")
        return cur.fetchall()
