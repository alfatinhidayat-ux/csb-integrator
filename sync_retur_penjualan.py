import argparse
import sys
import time
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import pymysql

# Setup search path for custom imports
sys.path.insert(0, os.getcwd())

from config import Config
from auth import AuthManager
from db import DatabaseManager


def ensure_tables(db: DatabaseManager):
    """Creates brighter_retur_penjualan and brighter_retur_penjualan_detail tables if they don't exist (no DROP)."""
    print("Ensuring tables exist (brighter_retur_penjualan, brighter_retur_penjualan_detail)...")
    with db.conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS brighter_retur_penjualan (
                id BIGINT NOT NULL,
                cabang_id INT NOT NULL,
                tanggal DATE NULL,
                no_bukti VARCHAR(100) NULL,
                faktur_id BIGINT NULL,
                faktur_nobukti VARCHAR(100) NULL,
                customer_id INT NULL,
                status_dokumen VARCHAR(50) NULL,
                total_rp DECIMAL(15,2) NULL,
                keterangan TEXT NULL,
                cara_bayar VARCHAR(50) NULL,
                created_by VARCHAR(100) NULL,
                created_at DATETIME NULL,
                updated_by VARCHAR(100) NULL,
                updated_at DATETIME NULL,
                deleted_by VARCHAR(100) NULL,
                deleted_at DATETIME NULL,
                revised INT NULL,
                cust_no VARCHAR(50) NULL,
                cust_nama VARCHAR(255) NULL,
                cust_kelamin VARCHAR(10) NULL,
                cust_alamat TEXT NULL,
                cust_hp VARCHAR(50) NULL,
                cust_email VARCHAR(150) NULL,
                cust_npwp VARCHAR(50) NULL,
                cust_tgllahir DATE NULL,
                jproduk_id BIGINT NULL,
                jproduk_nobukti VARCHAR(100) NULL,
                jproduk_tanggal DATE NULL,
                jproduk_totalbiaya DECIMAL(15,2) NULL,
                jproduk_stat_dok VARCHAR(50) NULL,
                synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id, cabang_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS brighter_retur_penjualan_detail (
                id BIGINT NOT NULL,
                cabang_id INT NOT NULL,
                retur_id BIGINT NOT NULL,
                dproduk_id BIGINT NULL,
                produk_id INT NULL,
                satuan_id INT NULL,
                qty DECIMAL(15,4) NULL,
                qty_retur DECIMAL(15,4) NULL,
                harga DECIMAL(15,2) NULL,
                diskon DECIMAL(15,2) NULL,
                diskon_rp DECIMAL(15,2) NULL,
                subtotal_rp DECIMAL(15,2) NULL,
                keterangan TEXT NULL,
                produk_kode VARCHAR(50) NULL,
                produk_nama VARCHAR(255) NULL,
                produk_sku VARCHAR(100) NULL,
                produk_group INT NULL,
                produk_group_sub INT NULL,
                produk_brand VARCHAR(100) NULL,
                produk_aktif VARCHAR(20) NULL,
                satuan_kode VARCHAR(50) NULL,
                satuan_nama VARCHAR(100) NULL,
                synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id, cabang_id),
                KEY idx_retur_id (retur_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    db.conn.commit()
    print("Tables ready.")


def map_header(rec: dict, cabang_id: int) -> dict:
    """Maps API record dictionary to standardized flat schema for brighter_retur_penjualan."""
    timestamp = rec.get("timestamp_data") or {}
    cust = rec.get("rjproduk_cust_data") or {}
    faktur = rec.get("rjproduk_faktur_data") or {}

    return {
        "id": rec.get("rjproduk_id"),
        "cabang_id": cabang_id,
        "tanggal": rec.get("rjproduk_tanggal") or None,
        "no_bukti": rec.get("rjproduk_nobukti"),
        "faktur_id": rec.get("rjproduk_faktur_id"),
        "faktur_nobukti": rec.get("rjproduk_faktur_nobukti"),
        "customer_id": rec.get("rjproduk_cust_id"),
        "status_dokumen": rec.get("rjproduk_stat_dok"),
        "total_rp": rec.get("rjproduk_total_rp"),
        "keterangan": rec.get("rjproduk_keterangan"),
        "cara_bayar": rec.get("rjproduk_cara_bayar"),

        # Flattened timestamp_data
        "created_by": timestamp.get("created_by"),
        "created_at": timestamp.get("created_at") or None,
        "updated_by": timestamp.get("updated_by"),
        "updated_at": timestamp.get("updated_at") or None,
        "deleted_by": timestamp.get("deleted_by"),
        "deleted_at": timestamp.get("deleted_at") or None,
        "revised": timestamp.get("revised"),

        # Flattened rjproduk_cust_data
        "cust_no": cust.get("cust_no"),
        "cust_nama": cust.get("cust_nama"),
        "cust_kelamin": cust.get("cust_kelamin"),
        "cust_alamat": cust.get("cust_alamat"),
        "cust_hp": cust.get("cust_hp"),
        "cust_email": cust.get("cust_email"),
        "cust_npwp": cust.get("cust_npwp"),
        "cust_tgllahir": cust.get("cust_tgllahir") or None,

        # Flattened rjproduk_faktur_data (original POS invoice)
        "jproduk_id": faktur.get("jproduk_id"),
        "jproduk_nobukti": faktur.get("jproduk_nobukti"),
        "jproduk_tanggal": faktur.get("jproduk_tanggal") or None,
        "jproduk_totalbiaya": faktur.get("jproduk_totalbiaya"),
        "jproduk_stat_dok": faktur.get("jproduk_stat_dok"),
    }


def map_detail(rec: dict, cabang_id: int) -> dict:
    """Maps API record dictionary to standardized flat schema for brighter_retur_penjualan_detail."""
    prod = rec.get("rjproduk_det_produk_data") or {}
    satuan = rec.get("rjproduk_det_satuan_data") or {}

    return {
        "id": rec.get("rjproduk_det_id"),
        "cabang_id": cabang_id,
        "retur_id": rec.get("rjproduk_det_master_id"),
        "dproduk_id": rec.get("rjproduk_det_dproduk_id"),
        "produk_id": rec.get("rjproduk_det_produk_id"),
        "satuan_id": rec.get("rjproduk_det_satuan_id"),
        "qty": rec.get("rjproduk_det_qty"),
        "qty_retur": rec.get("rjproduk_det_qty_retur"),
        "harga": rec.get("rjproduk_det_produk_harga"),
        "diskon": rec.get("rjproduk_det_diskon"),
        "diskon_rp": rec.get("rjproduk_det_diskon_rp"),
        "subtotal_rp": rec.get("rjproduk_det_subtotal_rp"),
        "keterangan": rec.get("rjproduk_det_keterangan"),

        # Flattened rjproduk_det_produk_data
        "produk_kode": prod.get("produk_kode"),
        "produk_nama": prod.get("produk_nama"),
        "produk_sku": prod.get("produk_sku"),
        "produk_group": prod.get("produk_group"),
        "produk_group_sub": prod.get("produk_group_sub"),
        "produk_brand": prod.get("produk_brand"),
        "produk_aktif": prod.get("produk_aktif"),

        # Flattened rjproduk_det_satuan_data
        "satuan_kode": satuan.get("satuan_kode"),
        "satuan_nama": satuan.get("satuan_nama"),
    }


def fetch_retur_headers(config: Config, auth: AuthManager, cabang_id: int, tanggal_awal: str, tanggal_akhir: str) -> list[dict]:
    """Fetches retur penjualan headers for a specific branch from /transaksi/retur_penjualan.

    The API does not support server-side date filtering, so we scan every page
    and filter client-side on `rjproduk_tanggal`.
    """
    client = httpx.Client(base_url=config.base_url, timeout=config.request_timeout)
    page = 1
    results = []
    seen_ids = set()
    skipped_no_name = 0

    while True:
        params = {
            "page": str(page),
            "results_per_page": str(config.results_per_page),
            "rjproduk_stat_dok": "Semua",
            "rjproduk_cust_data": "true",
            "timestamp_data": "true",
            "rjproduk_cabang_id": str(cabang_id)
        }

        auth.ensure_token()
        headers = auth.get_headers()

        time.sleep(config.request_delay)
        print(f" -> Fetching retur headers for Cabang {cabang_id}, page {page}...")
        resp = client.get("/transaksi/retur_penjualan", params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        batch = data.get("data", [])
        if not batch:
            break

        for rec in batch:
            rec_date_str = rec.get("rjproduk_tanggal")
            if rec_date_str:
                # Client-side date range filter (server ignores date params)
                if tanggal_awal and rec_date_str < tanggal_awal:
                    continue
                if tanggal_akhir and rec_date_str > tanggal_akhir:
                    continue
            # Skip retur to customers who are not "Aktif" (inactive/deleted) —
            # not shown in the app report and inflate the totals (e.g. no-name
            # CSB/RJ/2601-0004..0007, inactive customer 6204 / SB/RJ/2602-0003).
            if (rec.get("rjproduk_cust_data") or {}).get("cust_aktif") != "Aktif":
                skipped_no_name += 1
                continue
            # Dedupe by id: offset pagination can repeat records when new
            # transactions are inserted mid-scan.
            rid = rec.get("rjproduk_id")
            if rid is not None:
                if rid in seen_ids:
                    continue
                seen_ids.add(rid)
            results.append(rec)

        # Determine if there's a next page
        paging = data.get("paging")
        if paging:
            total_pages = paging.get("total_pages", 0)
            if page >= total_pages:
                break
        else:
            total = data.get("total_records", 0) or 0
            total_pages = (total + config.results_per_page - 1) // config.results_per_page
            if page >= total_pages:
                break
        page += 1

    client.close()
    if skipped_no_name:
        print(f" -> Skipped {skipped_no_name} retur with inactive/unknown customer (matches app report).")
    return results


def fetch_retur_detail(config: Config, auth: AuthManager, retur_id: int) -> list[dict]:
    """Fetches detail items for a specific retur penjualan ID.

    Unlike the POS detail endpoint, /rjproduk_det is paginated, so we loop
    through all pages.
    """
    client = httpx.Client(base_url=config.base_url, timeout=config.request_timeout)
    url = f"/transaksi/retur_penjualan/{retur_id}/rjproduk_det"
    page = 1
    results = []

    while True:
        params = {
            "page": str(page),
            "results_per_page": str(config.results_per_page),
            "rjproduk_det_produk_data": "true",
            "rjproduk_det_satuan_data": "true",
            "timestamp_data": "false",
        }

        auth.ensure_token()
        headers = auth.get_headers()

        time.sleep(config.request_delay)
        resp = client.get(url, params=params, headers=headers)
        if resp.status_code == 404:
            client.close()
            return []
        resp.raise_for_status()
        data = resp.json()

        batch = data.get("data", [])
        if not batch:
            break
        results.extend(batch)

        paging = data.get("paging")
        total_pages = paging.get("total_pages", 0) if paging else 0
        if page >= total_pages:
            break
        page += 1

    client.close()
    return results


def insert_batch_upsert(db: DatabaseManager, table: str, records: list[dict], chunk_size: int = 500):
    """Upsert records in chunks — insert new or update existing on PK (id, cabang_id).

    The DB connection can be closed by the server after sitting idle during a
    long detail-fetch phase ("MySQL server has gone away"). Each chunk pings
    the connection, and on a dead connection it reconnects and retries.
    Chunking also keeps each packet well under max_allowed_packet.
    """
    if not records:
        return
    cols = list(records[0].keys())
    col_names = ", ".join(f"`{c}`" for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))
    update_cols = [c for c in cols if c not in ("id", "cabang_id")]
    update_clause = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in update_cols)
    sql = f"INSERT INTO `{table}` ({col_names}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {update_clause}"

    total = len(records)
    for start in range(0, total, chunk_size):
        chunk = records[start:start + chunk_size]
        batch = [[rec.get(c) for c in cols] for rec in chunk]

        for attempt in range(3):
            try:
                db.reconnect()
                with db.conn.cursor() as cur:
                    cur.executemany(sql, batch)
                db.conn.commit()
                break
            except (pymysql.err.OperationalError, pymysql.err.InterfaceError) as e:
                try:
                    db.conn.rollback()
                except Exception:
                    pass
                if attempt == 2:
                    raise
                print(f"    DB connection lost on {table} chunk {start}, reconnecting ({attempt + 1}/3)...")
                db.close()
                db.connect()

        print(f"    Upsert {table}: {min(start + chunk_size, total)}/{total} rows")


def main():
    parser = argparse.ArgumentParser(
        description="Sync Retur Penjualan Header & Detail from Brighter API into csb_db"
    )
    parser.add_argument(
        "-e", "--env",
        action="store_true",
        help="Load configuration from environment variables (BRIGHTER_*)",
    )
    parser.add_argument(
        "--cabang-ids",
        help="Comma-separated cabang IDs to sync (e.g. 1,2,6). Defaults to all active cabangs.",
    )
    parser.add_argument(
        "--tanggal-awal",
        help="Start date filter (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--tanggal-akhir",
        help="End date filter (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--all-history",
        action="store_true",
        help="Forces syncing of all historical data (warning: generates a large number of API calls)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=3,
        help="ThreadPool workers for detail fetch (default: 3)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose debugging logs",
    )
    args = parser.parse_args()

    # Load configuration
    config = Config.from_env()

    # Determine date range
    if args.tanggal_awal and args.tanggal_akhir:
        tanggal_awal = args.tanggal_awal
        tanggal_akhir = args.tanggal_akhir
    elif args.all_history:
        tanggal_awal = "2026-01-01"
        tanggal_akhir = datetime.now().strftime("%Y-%m-%d")
    else:
        tanggal_awal = "2026-01-01"
        tanggal_akhir = datetime.now().strftime("%Y-%m-%d")
        print(f"Defaulting to full range: {tanggal_awal} to {tanggal_akhir}")

    print(f"Date range filter: {tanggal_awal} -> {tanggal_akhir}")

    # Initialize Auth & Database (once)
    auth = AuthManager(config)
    db = DatabaseManager(config, target_db="csb")
    db.connect()

    # 1. Ensure tables exist without dropping existing data
    ensure_tables(db)

    # 2. Discover Cabang IDs
    cabang_ids = []
    if args.cabang_ids:
        cabang_ids = [int(x.strip()) for x in args.cabang_ids.split(",") if x.strip()]
    else:
        print("Discovering active cabangs...")
        db_brighter = DatabaseManager(config, target_db="brighter")
        db_brighter.connect()
        try:
            cabangs = db_brighter.get_cabang_list()
            cabang_ids = [c["id"] for c in cabangs]
        except Exception as e:
            print(f"Error fetching active cabangs from DB: {e}. Defaulting to cabang [1]")
            cabang_ids = [1]
        finally:
            db_brighter.close()

    print(f"Cabangs to sync: {cabang_ids}")

    total_headers_synced = 0
    total_details_synced = 0

    # 3. Main Sync Loop: for each cabang, scan all pages and filter client-side
    for c_id in cabang_ids:
        print(f"\n--- Cabang {c_id} | {tanggal_awal} -> {tanggal_akhir} ---")

        # Ping DB before each cabang to avoid lost connection
        try:
            db.reconnect()
        except Exception:
            pass

        try:
            headers = fetch_retur_headers(config, auth, c_id, tanggal_awal, tanggal_akhir)
            print(f" -> Found {len(headers)} retur headers in range.")
            if not headers:
                continue

            headers_to_insert = [map_header(h, c_id) for h in headers]

            print(f" -> Fetching details for {len(headers)} transactions...")
            errors_count = 0
            details_to_insert = []
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                future_to_id = {
                    executor.submit(fetch_retur_detail, config, auth, h["rjproduk_id"]): h["rjproduk_id"]
                    for h in headers
                }

                for i, future in enumerate(as_completed(future_to_id)):
                    retur_id = future_to_id[future]
                    try:
                        items = future.result()
                        for item in items:
                            mapped_det = map_detail(item, c_id)
                            details_to_insert.append(mapped_det)
                    except Exception as e:
                        if args.verbose:
                            print(f"Error fetching details for Retur ID {retur_id}: {e}")
                        errors_count += 1

                    if (i + 1) % 50 == 0 or (i + 1) == len(headers):
                        print(f"    Detail fetch progress: {i + 1}/{len(headers)}")

                    # Keep the MySQL connection alive during the long fetch phase
                    # so the server doesn't close it (prevents "MySQL server has gone away").
                    if (i + 1) % 200 == 0:
                        try:
                            db.reconnect()
                        except Exception:
                            pass

            if errors_count > 0:
                print(f" -> Detail fetch warnings/errors count: {errors_count}")

            print(f" -> Upserting {len(headers_to_insert)} headers...")
            insert_batch_upsert(db, "brighter_retur_penjualan", headers_to_insert)

            print(f" -> Upserting {len(details_to_insert)} items...")
            insert_batch_upsert(db, "brighter_retur_penjualan_detail", details_to_insert)

            total_headers_synced += len(headers_to_insert)
            total_details_synced += len(details_to_insert)
            print(f"Done Cabang {c_id}")

        except Exception as e:
            print(f"Error syncing Cabang {c_id}: {e}")

    db.close()

    print("\n" + "=" * 50)
    print("ALL RETUR PENJUALAN SYNC COMPLETE")
    print(f"Total Retur Headers synced: {total_headers_synced}")
    print(f"Total Retur Details synced: {total_details_synced}")
    print("=" * 50)


if __name__ == "__main__":
    main()
