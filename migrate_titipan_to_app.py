"""Migrate Brighter barang titipan (internal) into the app's `barang_titipan_supplier`
and `barang_titipan_supplier_detail` tables (csb_db), preserving original
`btitipan_id` / `btitipan_det_id` so pembelian references and the detail->header FK
keep working.

Source: GET /persediaan/barang_titipan_internal (header, all cabang) and
        GET /persediaan/barang_titipan_internal/{id}/detail_barang_titipan_internal
        (per-header detail).

Notes:
- Eksternal endpoint returns 404; only `internal` type exists in the API. All rows
  store btitipan_jenis from the API (currently 'internal').
- App convention: endpoint has no cabang_param -> cabang_id=1 for every row; the
  real cabang stays in btitipan_cabang_id (matches the existing 701-row import).
- Field names map 1:1 from the API response (including timestamp_data.* for
  created/updated/deleted by/at).
- Full replace: both tables are emptied first, then re-populated from the API
  (API is the source of truth). Original ids are preserved on insert.

Usage:
    python migrate_titipan_to_app.py --no-write
    python migrate_titipan_to_app.py --limit 5 --no-write
    python migrate_titipan_to_app.py --run
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import httpx
import pymysql

sys.path.insert(0, os.getcwd())

from config import Config
from auth import AuthManager

HEADER_PATH = "/persediaan/barang_titipan_internal"
DETAIL_PATH = "/persediaan/barang_titipan_internal/{btitipan_id}/detail_barang_titipan_internal"

# Header tanpa supplier_id di-skip (kolom btitipan_supplier_id app NOT NULL).
SKIP_NULL_SUPPLIER = True

HEADER_FIELDS = [
    "btitipan_id", "btitipan_nobukti", "btitipan_supplier_id",
    "btitipan_non_konosemen", "btitipan_kapal_id", "btitipan_pelabuhan_asal_id",
    "btitipan_pelabuhan_tujuan_id", "btitipan_tanggal", "btitipan_jenis",
    "btitipan_total_kubikasi", "btitipan_no_container", "btitipan_total_tonase",
    "btitipan_total_kubikasi_rp", "btitipan_total_tonase_rp",
    "btitipan_grand_total_rp", "btitipan_status_dok", "btitipan_cabang_id",
    "btitipan_status_diambil_konosemen", "btitipan_keterangan",
]

HEADER_COLS = HEADER_FIELDS + ["cabang_id"] + [
    "created_by", "created_at", "updated_by", "updated_at", "deleted_by", "deleted_at",
]

DETAIL_FIELDS = [
    "btitipan_det_id", "btitipan_det_master_id", "btitipan_det_dorder_id",
    "btitipan_det_dorder_master_id", "btitipan_det_tanggal",
    "btitipan_det_no_container", "btitipan_det_qty_produk", "btitipan_det_produk_id",
    "btitipan_det_satuan_id", "btitipan_det_panjang", "btitipan_det_lebar",
    "btitipan_det_tinggi", "btitipan_det_berat", "btitipan_det_total_kubikasi",
    "btitipan_det_total_kubikasi_rp", "btitipan_det_total_tonase",
    "btitipan_det_total_tonase_rp", "btitipan_det_satuan_kemasan_id",
    "btitipan_det_qty_kemasan", "btitipan_det_dorder_harga",
    "btitipan_det_dorder_diskon", "btitipan_det_dorder_diskon_rp",
    "btitipan_det_dorder_subtotal_rp",
]

TIMESTAMP_FIELDS = [
    "created_by", "created_at", "updated_by", "updated_at", "deleted_by", "deleted_at",
]


def connect_csb() -> pymysql.connections.Connection:
    cfg = Config.from_env()
    kw = cfg.csb_db_kwargs()
    return pymysql.connect(
        host=kw["host"], port=kw["port"], user=kw["user"], password=kw["password"],
        database=kw["database"], charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, autocommit=False,
        connect_timeout=30, read_timeout=300, write_timeout=300,
    )


def fetch_paginated(client, path, params, headers, cfg):
    page = 1
    rows = []
    while True:
        p = dict(params)
        p["page"] = str(page)
        p["results_per_page"] = str(cfg.results_per_page)
        r = client.get(path, params=p, headers=headers)
        r.raise_for_status()
        d = r.json()
        batch = d.get("data") or []
        rows.extend(batch)
        paging = d.get("paging") or {}
        total_pages = int(paging.get("total_pages", 0) or 0)
        if page >= total_pages:
            break
        page += 1
    return rows


def extract_header(row: dict) -> list:
    ts = row.get("timestamp_data") or {}
    vals = [row.get(f) for f in HEADER_FIELDS]
    vals.append(1)  # cabang_id convention
    for f in TIMESTAMP_FIELDS:
        vals.append(ts.get(f))
    return vals


def extract_detail(row: dict) -> list:
    vals = []
    for f in DETAIL_FIELDS:
        v = row.get(f)
        if f == "btitipan_det_dorder_diskon" and v is not None:
            v = float(v)
            if v > 999.99:
                v = v / 1000.0
        vals.append(v)
    return vals


def main():
    parser = argparse.ArgumentParser(
        description="Migrate brighter barang titipan -> csb_db app tables"
    )
    parser.add_argument("--limit", type=int, default=0, help="Batasi jumlah header (0 = semua)")
    parser.add_argument("--no-write", action="store_true", help="Hanya hitung rencana, tanpa tulis DB")
    parser.add_argument("--dry-run", action="store_true", help="Alias untuk --no-write")
    parser.add_argument("--run", action="store_true", help="Langsung tulis ke DB tanpa konfirmasi")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    cfg = Config.from_env()
    auth = AuthManager(cfg)
    auth.ensure_token()
    headers_auth = auth.get_headers()

    client = httpx.Client(base_url=cfg.base_url, timeout=cfg.request_timeout)
    try:
        print("Fetching header titipan dari API ...")
        hdrs = fetch_paginated(
            client, HEADER_PATH, {"timestamp_data": "true"}, headers_auth, cfg
        )
        skipped_null_supplier = sum(
            1 for h in hdrs if h.get("btitipan_supplier_id") in (None, "", 0)
        )
        if SKIP_NULL_SUPPLIER:
            hdrs = [
                h for h in hdrs if h.get("btitipan_supplier_id") not in (None, "", 0)
            ]
        print(f"  header di API: {len(hdrs) + skipped_null_supplier} (skip supplier kosong: {skipped_null_supplier})")

        if args.limit:
            hdrs = hdrs[: args.limit]

        total_details = 0
        detail_errors = 0
        details_cache: dict[int, list] = {}
        for i, h in enumerate(hdrs, 1):
            btitipan_id = int(h["btitipan_id"])
            time.sleep(cfg.request_delay)
            auth.ensure_token()
            try:
                dets = fetch_paginated(
                    client, DETAIL_PATH.format(btitipan_id=btitipan_id), {},
                    headers_auth, cfg,
                )
            except Exception as e:
                detail_errors += 1
                print(f"  ERROR detail {btitipan_id} {h.get('btitipan_nobukti')} -> {e}")
                dets = []
            details_cache[btitipan_id] = dets
            total_details += len(dets)
            if args.verbose and i % 200 == 0:
                print(f"  [{i}/{len(hdrs)}] header diproses, detail {total_details}")

        print("=" * 62)
        print("Rencana migrasi barang titipan -> csb_db")
        print(f"  Header : {len(hdrs)}")
        print(f"  Detail : {total_details}")
        print(f"  Error  : {detail_errors}")
        print(f"  Skip (supplier kosong): {skipped_null_supplier}")
        print("=" * 62)

        if args.no_write or args.dry_run:
            print("NO WRITE — tidak ada data yang ditulis.")
            return

        if not args.run:
            ans = input("Tulis ke database? (yes/no): ")
            if ans.strip().lower() != "yes":
                print("Dibatalkan.")
                return

        conn = connect_csb()

        # Full replace: kosongkan kedua tabel lalu isi ulang.
        with conn.cursor() as cur:
            cur.execute("DELETE FROM barang_titipan_supplier_detail")
            cur.execute("DELETE FROM barang_titipan_supplier")
        conn.commit()

        inserted_h = 0
        with conn.cursor() as cur:
            cols_h = ",".join(HEADER_COLS)
            ph = ",".join(["%s"] * len(HEADER_COLS))
            sql_h = f"INSERT INTO barang_titipan_supplier ({cols_h}) VALUES ({ph})"
            for h in hdrs:
                cur.execute(sql_h, extract_header(h))
                inserted_h += 1
        conn.commit()
        print(f"  Header tersimpan: {inserted_h}")

        inserted_d = 0
        with conn.cursor() as cur:
            cols_d = ",".join(DETAIL_FIELDS)
            ph = ",".join(["%s"] * len(DETAIL_FIELDS))
            sql_d = f"INSERT INTO barang_titipan_supplier_detail ({cols_d}) VALUES ({ph})"
            for h in hdrs:
                btitipan_id = int(h["btitipan_id"])
                for d in details_cache.get(btitipan_id, []):
                    cur.execute(sql_d, extract_detail(d))
                    inserted_d += 1
        conn.commit()
        print(f"  Detail tersimpan: {inserted_d}")

        # Verification
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) c FROM barang_titipan_supplier")
            n_h = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) c FROM barang_titipan_supplier_detail")
            n_d = cur.fetchone()["c"]
            cur.execute(
                """
                SELECT COUNT(DISTINCT d.pembelian_det_btitipan_id) c
                FROM brighter_persediaan_pembelian_detail d
                WHERE d.pembelian_det_btitipan_id IS NOT NULL
                  AND EXISTS (SELECT 1 FROM barang_titipan_supplier b
                              WHERE b.btitipan_id=d.pembelian_det_btitipan_id)
                """
            )
            covered = cur.fetchone()["c"]
        print("=" * 62)
        print(f"  DB header : {n_h}")
        print(f"  DB detail : {n_d}")
        print(f"  Referensi pembelian yang ter-cover: {covered}")
        print("=" * 62)
    finally:
        client.close()
        if "conn" in locals():
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
