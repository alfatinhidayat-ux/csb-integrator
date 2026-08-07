"""Sync missing suppliers into csb_db.supplier (id == Brighter supplier_id).

Fetches /master/supplier from the API (one representative server — cabang 1 & 5
share the same base_url) and upserts into `supplier` via the same logic used by
sync_finance.py (map_csb_supplier + upsert_csb_supplier). Running it for all
records is idempotent and safe.

Usage:
    python sync_supplier_missing.py --dry-run
    python sync_supplier_missing.py --run
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys

import pymysql

sys.path.insert(0, os.getcwd())

from config import Config
from auth import AuthManager
from db import DatabaseManager
from sync_finance import (
    ENDPOINT_META,
    fetch_all_pages,
    map_csb_supplier,
    upsert_csb_supplier,
)

REPRESENTATIVE_CABANG = 1  # cabang 1 & 5 share the same API server


def main():
    parser = argparse.ArgumentParser(description="Sync missing supplier -> csb_db.supplier")
    parser.add_argument("--dry-run", action="store_true", help="Hitung tanpa menulis DB")
    parser.add_argument("--run", action="store_true", help="Langsung tulis ke DB")
    args = parser.parse_args()

    default_cfg = Config.from_env()
    db = DatabaseManager(default_cfg, target_db="csb")
    db.connect()

    auth = AuthManager(default_cfg)
    auth.ensure_token()
    print(f"API OK ({default_cfg.base_url})")

    rows = fetch_all_pages(
        default_cfg, auth,
        ENDPOINT_META["supplier"]["path"], {},
        dict(ENDPOINT_META["supplier"]["params"]),
        None, None, verbose=True,
    )
    mapped = [map_csb_supplier(r, REPRESENTATIVE_CABANG) for r in rows]
    mapped = [r for r in mapped if r.get("id") is not None]
    print(f"Supplier dari API: {len(mapped)}")

    with db.conn.cursor() as cur:
        cur.execute("SELECT id FROM supplier")
        existing = {r["id"] for r in cur.fetchall()}
        # hanya supplier yang dipakai data pembelian laporan (cabang 1 & 5,
        # status Tertutup, periode 2026-01-01..2026-08-31)
        cur.execute(
            """
            SELECT DISTINCT supplier_data_supplier_id AS sid
            FROM brighter_persediaan_pembelian
            WHERE status_dok = 'Tertutup'
              AND tanggal >= '2026-01-01' AND tanggal <= '2026-08-31'
              AND cabang_id IN (1, 5)
              AND supplier_data_supplier_id IS NOT NULL
            """
        )
        needed = {r["sid"] for r in cur.fetchall()}
    missing = [r for r in mapped if int(r["id"]) in needed and int(r["id"]) not in existing]
    print(f"Sudah ada: {len(mapped) - len(missing)}, Belum ada: {len(missing)}")
    for r in sorted(missing, key=lambda x: int(x["id"])):
        print(f"  akan tambah: id={r['id']} kode={r.get('kode')} nama={r.get('nama')}")

    if args.dry_run:
        print("DRY RUN — tidak ada data yang ditulis.")
        db.close()
        return
    if not args.run:
        ans = input("Tulis ke database? (yes/no): ")
        if ans.strip().lower() != "yes":
            print("Dibatalkan.")
            db.close()
            return

    if missing:
        n = upsert_csb_supplier(db, missing, REPRESENTATIVE_CABANG, [1, 5])
        print(f"Supplier di-upsert: {n}")
    else:
        print("Tidak ada supplier yang perlu ditambahkan.")
    db.close()


if __name__ == "__main__":
    main()
