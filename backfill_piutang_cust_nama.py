"""Backfill cust_data_cust_nama (dan kolom cust_data_cust_* lainnya) pada tabel
brighter_transaksi_piutang_customer_detail untuk record yang nama pelanggannya
masih NULL/kosong.

Urutan sumber data:
  1. API Brighter  /master/customer/:id   (paling akurat, data terkini)
  2. master_customer (csb_db)             (fallback, data hasil sync terakhir)
  3. brighter_pos.cust_nama               (fallback terakhir, dari transaksi POS)

Cara pakai:
  python backfill_piutang_cust_nama.py --env
  python backfill_piutang_cust_nama.py --env --dry-run      # preview tanpa update
  python backfill_piutang_cust_nama.py --env --cabang-ids 1 # hanya cabang tertentu
"""

import argparse
import sys
import time

import httpx
import pymysql
import pymysql.cursors

from config import Config
from auth import AuthManager


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_missing_cust_ids(cur, cabang_ids=None):
    """Ambil semua cust_id yang cust_data_cust_nama-nya masih NULL/kosong."""
    where_cabang = ""
    params = []
    if cabang_ids:
        marks = ",".join(["%s"] * len(cabang_ids))
        where_cabang = f"AND cabang_id IN ({marks})"
        params = list(cabang_ids)

    cur.execute(
        f"""
        SELECT DISTINCT cust AS cust_id, cabang_id
        FROM brighter_transaksi_piutang_customer_detail
        WHERE (cust_data_cust_nama IS NULL OR cust_data_cust_nama = '')
          AND cust IS NOT NULL
          {where_cabang}
        ORDER BY cust
        """,
        params,
    )
    return cur.fetchall()


def fetch_customer_from_api(config, auth, cust_id, verbose=False):
    """Fetch data customer dari /master/customer/:id.
    Return dict data customer atau None jika tidak ditemukan / error."""
    url = f"{config.base_url}/master/customer/{cust_id}"
    auth.ensure_token()
    headers = auth.get_headers()
    try:
        resp = httpx.get(url, headers=headers, timeout=config.request_timeout)
        if resp.status_code == 404:
            if verbose:
                print(f"    [API] cust_id {cust_id} -> 404 tidak ditemukan")
            return None
        resp.raise_for_status()
        data = resp.json()
        # Response bisa berupa list atau dict tergantung endpoint
        if isinstance(data, list):
            return data[0] if data else None
        if isinstance(data, dict):
            # Beberapa endpoint membungkus dalam {"data": {...}}
            return data.get("data") or data
    except Exception as e:
        if verbose:
            print(f"    [API] cust_id {cust_id} -> error: {e}")
    return None


def map_api_to_cols(rec):
    """Petakan field API /master/customer ke kolom cust_data_cust_* di DB."""
    if not rec:
        return {}
    return {
        "cust_data_cust_nama":          rec.get("cust_nama"),
        "cust_data_cust_no":            rec.get("cust_no"),
        "cust_data_cust_aktif":         rec.get("cust_aktif"),
        "cust_data_cust_cabang_id":     rec.get("cust_cabang_id"),
        "cust_data_cust_kategori_id":   rec.get("cust_kategori_id"),
        "cust_data_cust_jns_identitas": rec.get("cust_jns_identitas"),
        "cust_data_cust_no_identitas":  rec.get("cust_no_identitas"),
        "cust_data_cust_kelamin":       rec.get("cust_kelamin"),
        "cust_data_cust_alamat":        rec.get("cust_alamat"),
        "cust_data_cust_hp":            rec.get("cust_notelp") or rec.get("cust_hp"),
        "cust_data_cust_email":         rec.get("cust_email"),
        "cust_data_cust_npwp":          rec.get("cust_npwp"),
        "cust_data_cust_keterangan":    rec.get("cust_keterangan"),
        "cust_data_cust_tgllahir":      rec.get("cust_tgllahir") or rec.get("cust_tanggal_lahir"),
    }


def fallback_from_master_customer(cur, cust_id):
    """Fallback: ambil dari tabel master_customer (csb_db)."""
    cur.execute(
        """
        SELECT cust_nama, cust_no, cust_aktif, cust_cabang_id,
               cust_kategori_id, cust_jns_identitas, cust_no_identitas,
               cust_kelamin, cust_alamat, cust_notelp, cust_email,
               cust_npwp, cust_keterangan, cust_tgllahir
        FROM master_customer
        WHERE id = %s
        LIMIT 1
        """,
        (cust_id,),
    )
    row = cur.fetchone()
    if not row:
        return {}
    return {
        "cust_data_cust_nama":          row.get("cust_nama"),
        "cust_data_cust_no":            row.get("cust_no"),
        "cust_data_cust_aktif":         row.get("cust_aktif"),
        "cust_data_cust_cabang_id":     row.get("cust_cabang_id"),
        "cust_data_cust_kategori_id":   row.get("cust_kategori_id"),
        "cust_data_cust_jns_identitas": row.get("cust_jns_identitas"),
        "cust_data_cust_no_identitas":  row.get("cust_no_identitas"),
        "cust_data_cust_kelamin":       row.get("cust_kelamin"),
        "cust_data_cust_alamat":        row.get("cust_alamat"),
        "cust_data_cust_hp":            row.get("cust_notelp"),
        "cust_data_cust_email":         row.get("cust_email"),
        "cust_data_cust_npwp":          row.get("cust_npwp"),
        "cust_data_cust_keterangan":    row.get("cust_keterangan"),
        "cust_data_cust_tgllahir":      row.get("cust_tgllahir"),
    }


def fallback_from_brighter_pos(cur, cust_id):
    """Fallback: ambil cust_nama dari brighter_pos (nama terpanjang/non-empty)."""
    cur.execute(
        """
        SELECT cust_nama
        FROM brighter_pos
        WHERE customer_id = %s
          AND cust_nama IS NOT NULL AND cust_nama <> ''
        ORDER BY LENGTH(cust_nama) DESC
        LIMIT 1
        """,
        (cust_id,),
    )
    row = cur.fetchone()
    if not row:
        return {}
    return {"cust_data_cust_nama": row["cust_nama"]}


def apply_update(cur, conn, cust_id, cols, dry_run=False):
    """Update semua record piutang milik cust_id dengan data baru."""
    updates = {k: v for k, v in cols.items() if v is not None}
    if not updates:
        return 0

    set_clause = ", ".join(
        f"`{k}` = COALESCE(`{k}`, %s)" for k in updates
    )
    values = list(updates.values()) + [cust_id]

    sql = (
        f"UPDATE brighter_transaksi_piutang_customer_detail "
        f"SET {set_clause} "
        f"WHERE cust = %s "
        f"  AND (cust_data_cust_nama IS NULL OR cust_data_cust_nama = '')"
    )

    if dry_run:
        return 1  # simulasi

    cur.execute(sql, values)
    conn.commit()
    return cur.rowcount


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Backfill cust_data_cust_nama dari API Brighter untuk piutang tanpa nama."
    )
    parser.add_argument("--env", action="store_true", help="Load config dari .env")
    parser.add_argument("--cabang-ids", help="Comma-separated cabang IDs (default: semua)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview saja, tidak ada perubahan ke DB")
    parser.add_argument("-v", "--verbose", action="store_true", help="Output detail per request")
    args = parser.parse_args()

    config = Config.from_env() if args.env else Config()
    cabang_ids = (
        [int(x.strip()) for x in args.cabang_ids.split(",") if x.strip()]
        if args.cabang_ids else None
    )

    if args.dry_run:
        print("  DRY-RUN mode -- tidak ada perubahan ke DB")

    # Koneksi DB (csb_db)
    conn = pymysql.connect(
        **config.csb_db_kwargs(),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
    cur = conn.cursor()

    # Auth API
    auth = AuthManager(config)
    auth.ensure_token()
    print("Login API berhasil")

    # Ambil semua cust_id yang belum punya nama
    rows = get_missing_cust_ids(cur, cabang_ids)
    if not rows:
        print("Tidak ada record tanpa nama -- sudah bersih!")
        conn.close()
        return

    unique_cust_ids = list({r["cust_id"] for r in rows})
    print(f"\nDitemukan {len(rows)} record tanpa nama "
          f"({len(unique_cust_ids)} cust_id unik)\n")

    stats = {"api": 0, "master_customer": 0, "brighter_pos": 0, "not_found": 0}

    for cust_id in sorted(unique_cust_ids):
        print(f"  cust_id {cust_id} ...", end=" ", flush=True)

        # Sumber 1: API Brighter
        time.sleep(config.request_delay)
        api_rec = fetch_customer_from_api(config, auth, cust_id, args.verbose)
        cols = map_api_to_cols(api_rec)

        if cols.get("cust_data_cust_nama"):
            source = "API"
            stats["api"] += 1
        else:
            # Sumber 2: master_customer (csb_db)
            cols = fallback_from_master_customer(cur, cust_id)
            if cols.get("cust_data_cust_nama"):
                source = "master_customer"
                stats["master_customer"] += 1
            else:
                # Sumber 3: brighter_pos
                cols = fallback_from_brighter_pos(cur, cust_id)
                if cols.get("cust_data_cust_nama"):
                    source = "brighter_pos"
                    stats["brighter_pos"] += 1
                else:
                    source = "NOT FOUND"
                    stats["not_found"] += 1

        nama = cols.get("cust_data_cust_nama") or "-"
        n_updated = apply_update(cur, conn, cust_id, cols, dry_run=args.dry_run)
        dr_tag = " [DRY-RUN]" if args.dry_run else ""
        print(f"[{source}] '{nama}' -> {n_updated} row(s) updated{dr_tag}")

    # Ringkasan
    print("\n" + "=" * 55)
    print("BACKFILL SELESAI")
    print(f"  Dari API Brighter   : {stats['api']} cust_id")
    print(f"  Dari master_customer: {stats['master_customer']} cust_id")
    print(f"  Dari brighter_pos   : {stats['brighter_pos']} cust_id")
    print(f"  Tidak ditemukan     : {stats['not_found']} cust_id")
    print("=" * 55)

    if stats["not_found"] > 0:
        print("\n  cust_id yang tidak ditemukan di semua sumber kemungkinan")
        print("  adalah pelanggan walk-in tanpa data terdaftar di sistem.")

    conn.close()


if __name__ == "__main__":
    main()
