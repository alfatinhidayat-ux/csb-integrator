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
    def __init__(self, config: Config, target_db: str = "brighter"):
        self.config = config
        self.target_db = target_db
        self.conn: Optional[pymysql.Connection] = None

    def connect(self):
        if self.target_db == "csb":
            kwargs = self.config.csb_db_kwargs()
        else:
            kwargs = dict(
                host=self.config.db_host,
                port=self.config.db_port,
                user=self.config.db_user,
                password=self.config.db_password,
                database=self.config.db_name,
            )
        self.conn = pymysql.connect(
            **kwargs,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
        )

    def close(self):
        if self.conn:
            self.conn.close()

    def reconnect(self):
        try:
            self.conn.ping(reconnect=True)
        except Exception:
            self.close()
            self.connect()

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

    def ensure_table(self, table: str, sample: dict, date_cols: Optional[set] = None) -> set[str]:
        date_cols = date_cols or set()
        existing = self.get_table_columns(table)
        if not existing:
            cols = []
            has_id = "id" in sample
            for k, v in sample.items():
                if k in ("id",):
                    if isinstance(v, str) or v is None:
                        cols.append(f"{_safe_col(k)} VARCHAR(255) NOT NULL")
                    else:
                        cols.append(f"{_safe_col(k)} {_mysql_type(v)} NOT NULL")
                elif k == "cabang_id":
                    cols.append("cabang_id INT NOT NULL")
                elif k in date_cols:
                    cols.append(f"{_safe_col(k)} DATE NULL")
                else:
                    cols.append(f"{_safe_col(k)} {_mysql_type(v)} NULL")
            if "cabang_id" not in sample:
                cols.append("cabang_id INT NOT NULL")
            if "synced_at" not in sample:
                cols.append("synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP")
            if has_id:
                pk = ", PRIMARY KEY (`id`, `cabang_id`)"
            else:
                cols.insert(0, "id INT AUTO_INCREMENT")
                pk = ", PRIMARY KEY (`id`)"
            sql = f"CREATE TABLE {_safe_col(table)} ({', '.join(cols)}{pk}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
            self._execute(sql)
            self.conn.commit()
            return set(sample.keys()) | {"cabang_id", "synced_at"}

        added = set()
        # kolom tanggal: jadikan DATE (dan strip waktu dari data lama agar bersih)
        for dcol in date_cols:
            if dcol not in existing:
                try:
                    self._execute(
                        f"ALTER TABLE {_safe_col(table)} ADD COLUMN {_safe_col(dcol)} DATE NULL"
                    )
                    added.add(dcol)
                except (pymysql.err.OperationalError, pymysql.err.ProgrammingError):
                    pass
            else:
                self._ensure_date_col(table, dcol)
                added.add(dcol)
        # kolom lain dari sample yang belum ada
        for k in sample:
            if k in existing or k in date_cols or k in ("id", "cabang_id", "synced_at"):
                continue
            col = k.replace("`", "")
            try:
                self._execute(
                    f"ALTER TABLE {_safe_col(table)} ADD COLUMN {_safe_col(col)} TEXT NULL"
                )
                added.add(col)
            except (pymysql.err.OperationalError, pymysql.err.ProgrammingError):
                pass
        # jamin kolom wajib selalu ada (cabang_id, synced_at)
        if "cabang_id" not in existing and "cabang_id" not in added:
            try:
                self._execute(
                    f"ALTER TABLE {_safe_col(table)} ADD COLUMN `cabang_id` INT NULL"
                )
                added.add("cabang_id")
            except (pymysql.err.OperationalError, pymysql.err.ProgrammingError):
                pass
        if "synced_at" not in existing and "synced_at" not in added:
            try:
                self._execute(
                    f"ALTER TABLE {_safe_col(table)} ADD COLUMN `synced_at` "
                    "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
                )
                added.add("synced_at")
            except (pymysql.err.OperationalError, pymysql.err.ProgrammingError):
                pass
        self.conn.commit()
        return existing | set(sample.keys()) | {"cabang_id", "synced_at"}

    def _ensure_date_col(self, table: str, col: str):
        """Convert an existing column to DATE, stripping the trailing time from its
        values so tanggal fields stay date-clean (no time component)."""
        try:
            cur = self._execute(f"SHOW COLUMNS FROM {_safe_col(table)} LIKE %s", (col,))
            row = cur.fetchone()
            if row and str(row.get("Type") or "").upper().startswith("DATE"):
                return
            self._execute(
                f"UPDATE {_safe_col(table)} SET {_safe_col(col)} = "
                f"DATE(STR_TO_DATE({_safe_col(col)}, '%%Y-%%m-%%d %%H:%%i:%%s')) "
                f"WHERE {_safe_col(col)} LIKE '%%:%%:%%'"
            )
            self._execute(f"ALTER TABLE {_safe_col(table)} MODIFY {_safe_col(col)} DATE NULL")
        except (pymysql.err.OperationalError, pymysql.err.ProgrammingError, pymysql.err.DataError):
            pass

    def upsert_records(self, table: str, records: list[dict], cabang_id: int):
        if not records:
            return
        first = records[0]
        has_id = "id" in first
        has_cabang_id = "cabang_id" in first
        cols = list(first.keys()) + ([] if has_cabang_id else ["cabang_id"])
        placeholders = ", ".join(["%s"] * len(cols))
        col_names = ", ".join(_safe_col(c) for c in cols)
        sql = (
            f"INSERT INTO {_safe_col(table)} ({col_names}) VALUES ({placeholders})"
        )
        if has_id:
            updates = ", ".join(
                f"{_safe_col(c)} = VALUES({_safe_col(c)})"
                for c in first.keys()
                if c not in ("id", "cabang_id")
            )
            if updates:
                sql += f" ON DUPLICATE KEY UPDATE {updates}"
        batch = []
        for rec in records:
            row = [
                json.dumps(rec.get(k), ensure_ascii=False) if isinstance(rec.get(k), (dict, list)) else rec.get(k)
                for k in first.keys()
            ]
            if not has_cabang_id:
                row.append(cabang_id)
            batch.append(row)
        cur = self.conn.cursor()
        cur.executemany(sql, batch)
        self.conn.commit()

    def truncate_table(self, table: str):
        self._execute(f"TRUNCATE TABLE {_safe_col(table)}")
        self.conn.commit()

    def delete_cabang_data(self, table: str, cabang_id: int):
        self._execute(
            f"DELETE FROM {_safe_col(table)} WHERE cabang_id = %s", (cabang_id,)
        )
        self.conn.commit()

    def get_cabang_list(self) -> list[dict]:
        cur = self._execute(
            "SELECT DISTINCT cabang_id AS id, cabang_nama AS nama_cabang "
            "FROM master_cabang ORDER BY cabang_id"
        )
        return cur.fetchall()

    def get_distinct(self, table: str, column: str) -> list:
        cur = self._execute(
            f"SELECT DISTINCT {_safe_col(column)} FROM {_safe_col(table)}"
        )
        return [row[column] for row in cur.fetchall()]

    def get_distinct_with_cabang(self, table: str, column: str, filter_col: str = "") -> list[tuple]:
        try:
            where = ""
            if filter_col:
                where = f" WHERE CAST({_safe_col(filter_col)} AS DECIMAL(20,2)) > 0"
            cur = self._execute(
                f"SELECT DISTINCT {_safe_col(column)}, cabang_id FROM {_safe_col(table)}{where}"
            )
            return [(row[column], row["cabang_id"]) for row in cur.fetchall()]
        except Exception as e:
            import logging
            logging.getLogger("brighter-sync").debug("get_distinct_with_cabang fallback for %s.%s: %s", table, column, e)
            return [(pid, 1) for pid in self.get_distinct(table, column)]
