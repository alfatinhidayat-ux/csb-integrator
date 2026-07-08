import json
import logging
from typing import Any, Optional

import pymysql
from pymysql.cursors import DictCursor

from config import Config

logger = logging.getLogger("csb-produk-sync")


def _safe_col(name: str) -> str:
    return f"`{name}`"


class CsbSafeWriter:
    """Upsert-only writer for existing csb_db production tables.

    Deliberately has no create/alter/truncate/drop/delete method of any kind -
    the only way to change data is upsert(), and upsert() only ever touches
    columns that already exist in the target table and are explicitly
    whitelisted for update.
    """

    def __init__(self, config: Config):
        self.config = config
        self.conn: Optional[pymysql.Connection] = None
        self._column_cache: dict[str, set[str]] = {}
        self._dropped_fields_warned: set[tuple[str, str]] = set()

    def connect(self):
        self.conn = pymysql.connect(
            **self.config.csb_db_kwargs(),
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
        )

    def close(self):
        if self.conn:
            self.conn.close()

    def ping(self):
        """Reconnect if the connection has gone stale (e.g. idle-timed-out by
        the server during a long API fetch phase before any query was sent)."""
        self.conn.ping(reconnect=True)

    def get_columns(self, table: str) -> set[str]:
        if table not in self._column_cache:
            cur = self.conn.cursor()
            cur.execute(f"SHOW COLUMNS FROM {_safe_col(table)}")
            self._column_cache[table] = {row["Field"] for row in cur.fetchall()}
        return self._column_cache[table]

    def _serialize(self, value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return value

    def upsert(
        self,
        table: str,
        pk_col: str,
        record: dict,
        update_cols: list[str],
        dry_run: bool = False,
    ) -> str:
        """Insert or update a single record. Returns 'insert', 'update', or 'skip'.

        Only fields whose key matches an existing column are written. On INSERT
        all matching fields are included; on UPDATE only fields in update_cols
        (intersected with matching fields) are written - everything else in the
        existing row is left untouched.
        """
        existing_cols = self.get_columns(table)
        if pk_col not in record:
            raise ValueError(f"record missing pk field '{pk_col}' for table {table}")

        matched = {k: v for k, v in record.items() if k in existing_cols}
        dropped = set(record.keys()) - existing_cols
        for field in dropped:
            key = (table, field)
            if key not in self._dropped_fields_warned:
                logger.info("  [%s] field '%s' has no matching column, dropped", table, field)
                self._dropped_fields_warned.add(key)

        if not matched:
            return "skip"

        cur = self.conn.cursor()
        cur.execute(
            f"SELECT 1 FROM {_safe_col(table)} WHERE {_safe_col(pk_col)} = %s",
            (matched[pk_col],),
        )
        exists = cur.fetchone() is not None

        if dry_run:
            return "update" if exists else "insert"

        if exists:
            update_fields = [c for c in update_cols if c in matched and c != pk_col]
            if not update_fields:
                return "skip"
            set_clause = ", ".join(f"{_safe_col(c)} = %s" for c in update_fields)
            values = [self._serialize(matched[c]) for c in update_fields] + [matched[pk_col]]
            cur.execute(
                f"UPDATE {_safe_col(table)} SET {set_clause} WHERE {_safe_col(pk_col)} = %s",
                values,
            )
            return "update"
        else:
            cols = list(matched.keys())
            placeholders = ", ".join(["%s"] * len(cols))
            col_names = ", ".join(_safe_col(c) for c in cols)
            values = [self._serialize(matched[c]) for c in cols]
            cur.execute(
                f"INSERT INTO {_safe_col(table)} ({col_names}) VALUES ({placeholders})",
                values,
            )
            return "insert"

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()
