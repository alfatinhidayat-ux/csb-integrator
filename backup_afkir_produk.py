"""Backup produk AFKIR manual (kode AFK-* / is_afkir=1) dari csb_db.produk
ke tabel `produk_backup_afkir` sebelum re-sync produk dari API.

Usage:
    python backup_afkir_produk.py --dry-run
    python backup_afkir_produk.py
"""

import argparse
import os
import sys
from datetime import datetime

import pymysql

sys.path.insert(0, os.getcwd())

from config import Config
from db import DatabaseManager

BACKUP_TABLE = "produk_backup_afkir"


def connect_csb() -> pymysql.connections.Connection:
    cfg = Config.from_env()
    kw = cfg.csb_db_kwargs()
    return pymysql.connect(
        host=kw["host"],
        port=kw["port"],
        user=kw["user"],
        password=kw["password"],
        database=kw["database"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def get_columns(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SHOW COLUMNS FROM produk")
        return [r["Field"] for r in cur.fetchall()]


def main():
    parser = argparse.ArgumentParser(description="Backup produk AFKIR manual sebelum re-sync")
    parser.add_argument("--dry-run", action="store_true", help="Hitung saja, tanpa menulis")
    args = parser.parse_args()

    conn = connect_csb()
    try:
        cols = get_columns(conn)
        cols_sql = ",".join(f"`{c}`" for c in cols)

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) c FROM produk
                WHERE produk_kode LIKE 'AFK-%%' OR is_afkir=1
                """
            )
            n = cur.fetchall()[0]["c"]

            cur.execute(
                f"""
                SELECT {cols_sql} FROM produk
                WHERE produk_kode LIKE 'AFK-%%' OR is_afkir=1
                """
            )
            rows = cur.fetchall()

        print(f"Produk AFKIR manual ditemukan: {len(rows)}")
        for r in rows:
            print(f"   id={r['produk_id']} kode='{r['produk_kode']}' nama='{r['produk_nama']}'")

        if args.dry_run:
            print("DRY RUN - tidak ada yang ditulis.")
            return

        if not rows:
            print("Tidak ada produk AFKIR manual. Tidak ada yang di-backup.")
            return

        now = datetime.now()
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {BACKUP_TABLE}")
            cur.execute(
                f"CREATE TABLE {BACKUP_TABLE} LIKE produk"
            )
            cur.execute(
                f"ALTER TABLE {BACKUP_TABLE} ADD COLUMN backup_at DATETIME"
            )
            insert_cols = cols + ["backup_at"]
            placeholders = ",".join(["%s"] * len(insert_cols))
            insert_cols_sql = ",".join(f"`{c}`" for c in insert_cols)
            for r in rows:
                cur.execute(
                    f"INSERT INTO {BACKUP_TABLE} ({insert_cols_sql}) VALUES ({placeholders})",
                    tuple(r.get(c) for c in cols) + (now,),
                )
        conn.commit()
        print(f"Backup selesai -> {BACKUP_TABLE}: {len(rows)} baris (backup_at={now})")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
