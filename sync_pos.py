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
    """Creates brighter_pos and brighter_pos_detail tables if they don't exist (no DROP)."""
    print("Ensuring tables exist (brighter_pos, brighter_pos_detail)...")
    with db.conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS brighter_pos (
                id BIGINT NOT NULL,
                cabang_id INT NOT NULL,
                tanggal DATE NULL,
                no_bukti VARCHAR(100) NULL,
                customer_id INT NULL,
                keterangan TEXT NULL,
                status_dokumen VARCHAR(50) NULL,
                bayar DECIMAL(15,2) NULL,
                cara VARCHAR(100) NULL,
                card_jenis VARCHAR(100) NULL,
                card_edc VARCHAR(100) NULL,
                card_no VARCHAR(100) NULL,
                total_biaya DECIMAL(15,2) NULL,
                request_stat_dok_batal VARCHAR(50) NULL,
                request_batal_keterangan TEXT NULL,
                request_batal_at DATETIME NULL,
                request_batal_by VARCHAR(100) NULL,
                approval_batal_at DATETIME NULL,
                approval_batal_by VARCHAR(100) NULL,
                created_by VARCHAR(100) NULL,
                created_at DATETIME NULL,
                updated_by VARCHAR(100) NULL,
                updated_at DATETIME NULL,
                deleted_by VARCHAR(100) NULL,
                deleted_at DATETIME NULL,
                revised INT NULL,
                cust_no VARCHAR(50) NULL,
                cust_jns_identitas VARCHAR(50) NULL,
                cust_no_identitas VARCHAR(100) NULL,
                cust_nama VARCHAR(255) NULL,
                cbayar_id BIGINT NULL,
                cbayar_nama VARCHAR(50) NULL,
                cbayar_nilai_bayar_rp DECIMAL(15,2) NULL,
                cbayar_card_jenis VARCHAR(50) NULL,
                cbayar_card_edc VARCHAR(50) NULL,
                cbayar_card_no VARCHAR(100) NULL,
                cbayar_card_tarik_tunai DECIMAL(15,2) NULL,
                cbayar_transfer_bank_id BIGINT NULL,
                cbayar_transfer_nama VARCHAR(150) NULL,
                cbayar_all_methods VARCHAR(255) NULL,
                synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id, cabang_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS brighter_pos_detail (
                id BIGINT NOT NULL,
                cabang_id INT NOT NULL,
                pos_id BIGINT NOT NULL,
                produk_id INT NULL,
                satuan_id INT NULL,
                jumlah DECIMAL(15,4) NULL,
                jumlah_retur DECIMAL(15,4) NULL,
                harga DECIMAL(15,2) NULL,
                diskon DECIMAL(5,2) NULL,
                diskon_rp DECIMAL(15,2) NULL,
                produk_kode VARCHAR(50) NULL,
                produk_nama VARCHAR(255) NULL,
                produk_sku VARCHAR(100) NULL,
                produk_group INT NULL,
                produk_group_sub INT NULL,
                produk_brand VARCHAR(100) NULL,
                produk_aktif VARCHAR(20) NULL,
                satuan_code VARCHAR(50) NULL,
                satuan_nama VARCHAR(100) NULL,
                synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id, cabang_id),
                KEY idx_pos_id (pos_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    db.conn.commit()
    print("Tables ready.")


def map_header(rec: dict, cabang_id: int) -> dict:
    """Maps API record dictionary to standardized flat schema for brighter_pos."""
    timestamp = rec.get("timestamp_data") or {}
    cust = rec.get("jproduk_cust_data") or {}
    
    cb_list = rec.get("jproduk_cara_bayar_data") or []
    if isinstance(cb_list, dict):
        cb_list = [cb_list]
    elif not isinstance(cb_list, list):
        cb_list = []
        
    cb_first = cb_list[0] if cb_list else {}
    
    # Concatenate all payment methods
    methods = []
    for cb in cb_list:
        name = cb.get("djual_cbayar_nama") or "unknown"
        val = cb.get("djual_nilai_bayar_rp") or 0.0
        methods.append(f"{name}: {val}")
    cbayar_all_methods = ", ".join(methods) if methods else None
    
    return {
        "id": rec.get("jproduk_id"),
        "cabang_id": cabang_id,
        "tanggal": rec.get("jproduk_tanggal") or None,
        "no_bukti": rec.get("jproduk_nobukti"),
        "customer_id": rec.get("jproduk_cust"),
        "keterangan": rec.get("jproduk_keterangan"),
        "status_dokumen": rec.get("jproduk_stat_dok"),
        "bayar": rec.get("jproduk_bayar"),
        "cara": rec.get("jproduk_cara"),
        "card_jenis": rec.get("jproduk_card_jenis"),
        "card_edc": rec.get("jproduk_card_edc"),
        "card_no": rec.get("jproduk_card_no"),
        "total_biaya": rec.get("jproduk_totalbiaya"),
        "request_stat_dok_batal": rec.get("jproduk_request_stat_dok_batal"),
        "request_batal_keterangan": rec.get("jproduk_request_batal_keterangan"),
        "request_batal_at": rec.get("jproduk_request_batal_at") or None,
        "request_batal_by": rec.get("jproduk_request_batal_by"),
        "approval_batal_at": rec.get("jproduk_approval_batal_at") or None,
        "approval_batal_by": rec.get("jproduk_approval_batal_by"),
        
        # Flattened timestamp_data
        "created_by": timestamp.get("created_by"),
        "created_at": timestamp.get("created_at") or None,
        "updated_by": timestamp.get("updated_by"),
        "updated_at": timestamp.get("updated_at") or None,
        "deleted_by": timestamp.get("deleted_by"),
        "deleted_at": timestamp.get("deleted_at") or None,
        "revised": timestamp.get("revised"),
        
        # Flattened jproduk_cust_data
        "cust_no": cust.get("cust_no"),
        "cust_jns_identitas": cust.get("cust_jns_identitas"),
        "cust_no_identitas": cust.get("cust_no_identitas"),
        "cust_nama": cust.get("cust_nama"),
        
        # Flattened jproduk_cara_bayar_data (first payment method)
        "cbayar_id": cb_first.get("djual_cbayar_id"),
        "cbayar_nama": cb_first.get("djual_cbayar_nama"),
        "cbayar_nilai_bayar_rp": cb_first.get("djual_nilai_bayar_rp"),
        "cbayar_card_jenis": cb_first.get("djual_card_jenis"),
        "cbayar_card_edc": cb_first.get("djual_card_edc"),
        "cbayar_card_no": cb_first.get("djual_card_no"),
        "cbayar_card_tarik_tunai": cb_first.get("djual_card_tarik_tunai_rp"),
        "cbayar_transfer_bank_id": cb_first.get("djual_transfer_bank_id"),
        "cbayar_transfer_nama": cb_first.get("djual_transfer_nama"),
        "cbayar_all_methods": cbayar_all_methods,
    }


def map_detail(rec: dict, cabang_id: int) -> dict:
    """Maps API record dictionary to standardized flat schema for brighter_pos_detail."""
    prod = rec.get("dproduk_produk_data") or {}
    satuan = rec.get("dproduk_satuan_data") or {}
    
    return {
        "id": rec.get("dproduk_id"),
        "cabang_id": cabang_id,
        "pos_id": rec.get("dproduk_master"),
        "produk_id": rec.get("dproduk_produk"),
        "satuan_id": rec.get("dproduk_satuan"),
        "jumlah": rec.get("dproduk_jumlah"),
        "jumlah_retur": rec.get("dproduk_retur_jml"),
        "harga": rec.get("dproduk_harga"),
        "diskon": rec.get("dproduk_diskon"),
        "diskon_rp": rec.get("dproduk_diskon_rp"),
        
        # Flattened dproduk_produk_data
        "produk_kode": prod.get("produk_kode"),
        "produk_nama": prod.get("produk_nama"),
        "produk_sku": prod.get("produk_sku"),
        "produk_group": prod.get("produk_group"),
        "produk_group_sub": prod.get("produk_group_sub"),
        "produk_brand": prod.get("produk_brand"),
        "produk_aktif": prod.get("produk_aktif"),
        
        # Flattened dproduk_satuan_data
        "satuan_code": satuan.get("satuan_code") or satuan.get("satuan_kode"),
        "satuan_nama": satuan.get("satuan_nama"),
    }


def fetch_pos_headers(config: Config, auth: AuthManager, cabang_id: int, tanggal_awal: str, tanggal_akhir: str) -> list[dict]:
    """Fetches POS headers for a specific branch from /transaksi/pos.

    The API does not support server-side date filtering and does NOT return
    records sorted by `jproduk_tanggal` (ordering is by id, and the date can be
    edited independently, so it is NOT monotonic with id). Therefore we scan
    every page and filter client-side. Early stopping on date is unsafe and
    caused whole ranges of dates to be skipped.
    """
    client = httpx.Client(base_url=config.base_url, timeout=config.request_timeout)
    page = 1
    results = []
    seen_ids = set()
    
    while True:
        params = {
            "page": str(page),
            "results_per_page": str(config.results_per_page),  # Max allowed page size
            "jproduk_stat_dok": "Semua",
            "jproduk_cust_data": "true",
            "timestamp_data": "true",
            "jproduk_cabang_id": str(cabang_id)
        }
        
        auth.ensure_token()
        headers = auth.get_headers()
        
        time.sleep(config.request_delay)
        print(f" -> Fetching headers for Cabang {cabang_id}, page {page}...")
        resp = client.get("/transaksi/pos", params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        
        batch = data.get("data", [])
        if not batch:
            break
            
        for rec in batch:
            rec_date_str = rec.get("jproduk_tanggal")
            if rec_date_str:
                # Client-side date range filter (server ignores date params)
                if tanggal_awal and rec_date_str < tanggal_awal:
                    continue
                if tanggal_akhir and rec_date_str > tanggal_akhir:
                    continue
            # Dedupe by id: offset pagination can repeat records when new
            # transactions are inserted mid-scan.
            rid = rec.get("jproduk_id")
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
    return results


def fetch_pos_detail(config: Config, auth: AuthManager, pos_id: int) -> list[dict]:
    """Fetches detailed items for a specific POS ID."""
    client = httpx.Client(base_url=config.base_url, timeout=config.request_timeout)
    url = f"/transaksi/pos/{pos_id}/detail_pos"
    params = {
        "dproduk_produk_data": "true",
        "dproduk_satuan_data": "true"
    }
    
    auth.ensure_token()
    headers = auth.get_headers()
    
    resp = client.get(url, params=params, headers=headers)
    client.close()
    
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    return resp.json().get("data", []) or []


def insert_batch_upsert(db: DatabaseManager, table: str, records: list[dict]):
    """Upsert records — insert new or update existing on PK (id, cabang_id)."""
    if not records:
        return
    cols = list(records[0].keys())
    col_names = ", ".join(f"`{c}`" for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))
    update_cols = [c for c in cols if c not in ("id", "cabang_id")]
    update_clause = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in update_cols)
    sql = f"INSERT INTO `{table}` ({col_names}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {update_clause}"
    
    batch = []
    for rec in records:
        row = [rec.get(c) for c in cols]
        batch.append(row)
        
    with db.conn.cursor() as cur:
        cur.executemany(sql, batch)
    db.conn.commit()


def main():
    parser = argparse.ArgumentParser(
        description="Sync POS Header & Detail from Brighter API into csb_db"
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
    if args.env:
        config = Config.from_env()
    else:
        # Load from .env file or default
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
            headers = fetch_pos_headers(config, auth, c_id, tanggal_awal, tanggal_akhir)
            print(f" -> Found {len(headers)} POS headers in range.")
            if not headers:
                continue
                
            headers_to_insert = [map_header(h, c_id) for h in headers]
                
            print(f" -> Fetching details for {len(headers)} transactions...")
            errors_count = 0
            details_to_insert = []
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                future_to_id = {
                    executor.submit(fetch_pos_detail, config, auth, h["jproduk_id"]): h["jproduk_id"]
                    for h in headers
                }
                
                for i, future in enumerate(as_completed(future_to_id)):
                    pos_id = future_to_id[future]
                    try:
                        items = future.result()
                        for item in items:
                            mapped_det = map_detail(item, c_id)
                            details_to_insert.append(mapped_det)
                    except Exception as e:
                        if args.verbose:
                            print(f"Error fetching details for POS ID {pos_id}: {e}")
                        errors_count += 1
                        
                    if (i + 1) % 50 == 0 or (i + 1) == len(headers):
                        print(f"    Detail fetch progress: {i + 1}/{len(headers)}")
            
            if errors_count > 0:
                print(f" -> Detail fetch warnings/errors count: {errors_count}")
                
            print(f" -> Upserting {len(headers_to_insert)} headers...")
            insert_batch_upsert(db, "brighter_pos", headers_to_insert)
            
            print(f" -> Upserting {len(details_to_insert)} items...")
            insert_batch_upsert(db, "brighter_pos_detail", details_to_insert)
            
            total_headers_synced += len(headers_to_insert)
            total_details_synced += len(details_to_insert)
            print(f"Done Cabang {c_id}")
            
        except Exception as e:
            print(f"Error syncing Cabang {c_id}: {e}")

    db.close()
    
    print("\n" + "=" * 50)
    print("ALL POS SYNC COMPLETE")
    print(f"Total POS Headers synced: {total_headers_synced}")
    print(f"Total POS Details synced: {total_details_synced}")
    print("=" * 50)


if __name__ == "__main__":
    main()
