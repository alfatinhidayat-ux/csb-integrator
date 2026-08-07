import argparse
import sys
import time
import os
import json
import dataclasses
from datetime import datetime

import httpx
import pymysql

# Setup search path for custom imports
sys.path.insert(0, os.getcwd())

from config import Config
from auth import AuthManager
from db import DatabaseManager


def flatten(value, _prefix="", _seen=None):
    """Recursively flatten dict/list into a flat {key: scalar} dict."""
    if _seen is None:
        _seen = set()
    out = {}
    if isinstance(value, dict):
        for k, v in value.items():
            key = f"{_prefix}{k}"
            if key in _seen:
                continue
            _seen.add(key)
            if isinstance(v, dict):
                out.update(flatten(v, f"{key}_", _seen))
            elif isinstance(v, list):
                out[key] = v if (not v or any(not isinstance(i, dict) for i in v)) else json.dumps(v, ensure_ascii=False)
            else:
                out[key] = v
    return out


def strip_prefix(prefix, key):
    if prefix and key.startswith(prefix):
        return key[len(prefix):]
    return key


def map_record(rec: dict, cabang_id: int) -> dict:
    """Maps API record dictionary to standardized flat schema for brighter_persediaan_pembelian."""
    flat = flatten(rec)
    mapped = {strip_prefix("pembelian_", k): v for k, v in flat.items()}
    mapped["cabang_id"] = cabang_id

    # format tanggal
    v = mapped.get("tanggal")
    if isinstance(v, str) and len(v) >= 10 and v[:4].isdigit() and v[4:5] == "-":
        mapped["tanggal"] = v[:10]

    return mapped


def align_record_keys(records: list[dict]) -> list[dict]:
    if not records:
        return records
    all_keys = set()
    for r in records:
        all_keys.update(r.keys())
    
    aligned = []
    for r in records:
        aligned.append({k: r.get(k, None) for k in all_keys})
    return aligned


def fetch_pembelian_headers(config: Config, auth: AuthManager, cabang_id: int, tanggal_awal: str, tanggal_akhir: str) -> list[dict]:
    """Fetches purchase headers from /persediaan/pembelian."""
    client = httpx.Client(base_url=config.base_url, timeout=config.request_timeout)
    page = 1
    results = []
    seen_ids = set()

    while True:
        params = {
            "page": str(page),
            "results_per_page": str(config.results_per_page),
            "pembelian_status_dok": "Semua",
            "pembelian_status_lunas": "Semua",
            "pembelian_supplier_data": "true",
            "timestamp_data": "true",
            "pembelian_cabang_id": str(cabang_id)
        }

        auth.ensure_token()
        headers = auth.get_headers()

        time.sleep(config.request_delay)
        print(f" -> Fetching pembelian headers for Cabang {cabang_id}, page {page}...")
        resp = client.get("/persediaan/pembelian", params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        batch = data.get("data", [])
        if not batch:
            break

        for rec in batch:
            rec_date_str = rec.get("pembelian_tanggal")
            if rec_date_str:
                if tanggal_awal and rec_date_str < tanggal_awal:
                    continue
                if tanggal_akhir and rec_date_str > tanggal_akhir:
                    continue

            pid = rec.get("pembelian_id")
            if pid is not None:
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
            results.append(rec)

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


def load_cabang_urls(db):
    cur = db.conn.cursor()
    cur.execute("SELECT `id`, `url_api` FROM `cabang` WHERE `aktif` = 1")
    out = {}
    for r in cur.fetchall():
        url = (r["url_api"] or "").strip().rstrip("/")
        if url:
            out[r["id"]] = url
    return out


def main():
    parser = argparse.ArgumentParser(
        description="Sync Pembelian from Brighter API into csb_db"
    )
    parser.add_argument(
        "-e", "--env",
        action="store_true",
        help="Load configuration from environment variables (BRIGHTER_*)",
    )
    parser.add_argument(
        "--cabang-ids",
        help="Comma-separated cabang IDs to sync (e.g. 1,2,6). Defaults to active cabangs in database.",
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
        help="Forces syncing of all historical data",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose debugging logs",
    )
    args = parser.parse_args()

    config = Config.from_env()

    if args.tanggal_awal and args.tanggal_akhir:
        tanggal_awal = args.tanggal_awal
        tanggal_akhir = args.tanggal_akhir
    elif args.all_history:
        tanggal_awal = "2026-01-01"
        tanggal_akhir = datetime.now().strftime("%Y-%m-%d")
    else:
        tanggal_awal = "2026-01-01"
        tanggal_akhir = datetime.now().strftime("%Y-%m-%d")
        print(f"Defaulting to date range: {tanggal_awal} to {tanggal_akhir}")

    print(f"Date range filter: {tanggal_awal} -> {tanggal_akhir}")

    db = DatabaseManager(config, target_db="csb")
    db.connect()

    cabang_urls = load_cabang_urls(db)

    cabang_ids = []
    if args.cabang_ids:
        cabang_ids = [int(x.strip()) for x in args.cabang_ids.split(",") if x.strip()]
    else:
        cabang_ids = sorted(list(cabang_urls.keys()))

    print(f"Cabangs to sync: {cabang_ids}")

    # Set up config and auth per server url
    server_cfg = {}
    server_auth = {}
    cabang_cfg = {}
    cabang_auth = {}
    for cid in cabang_ids:
        url = cabang_urls.get(cid)
        key = url or config.base_url
        if key not in server_cfg:
            cfg = dataclasses.replace(config, base_url=url) if url else config
            server_cfg[key] = cfg
            server_auth[key] = AuthManager(cfg)
        cabang_cfg[cid] = server_cfg[key]
        cabang_auth[cid] = server_auth[key]

    total_headers_synced = 0

    for c_id in cabang_ids:
        print(f"\n--- Cabang {c_id} | {tanggal_awal} -> {tanggal_akhir} ---")
        try:
            db.reconnect()
        except Exception:
            pass

        cfg_c = cabang_cfg[c_id]
        auth_c = cabang_auth[c_id]

        try:
            headers = fetch_pembelian_headers(cfg_c, auth_c, c_id, tanggal_awal, tanggal_akhir)
            print(f" -> Found {len(headers)} pembelian headers in range.")
            if not headers:
                continue

            headers_to_insert = [map_record(h, c_id) for h in headers]
            headers_to_insert = align_record_keys(headers_to_insert)

            print(f" -> Checking and updating table schema...")
            db.ensure_table("brighter_persediaan_pembelian", headers_to_insert[0], {"tanggal"})

            print(f" -> Upserting {len(headers_to_insert)} headers...")
            db.upsert_records("brighter_persediaan_pembelian", headers_to_insert, c_id)

            total_headers_synced += len(headers_to_insert)
            print(f"Done Cabang {c_id}")

        except Exception as e:
            print(f"Error syncing Cabang {c_id}: {e}")

    db.close()

    print("\n" + "=" * 50)
    print("ALL PEMBELIAN SYNC COMPLETE")
    print(f"Total Pembelian Headers synced: {total_headers_synced}")
    print("=" * 50)


if __name__ == "__main__":
    main()
