"""Sync master pinjaman karyawan dari API Brighter ke tabel `pinjaman_karyawan`.

Program ini mengisi tabel yang SUDAH ADA tanpa mengubah strukturnya:
- TIDAK drop / create ulang
- TIDAK truncate
- TIDAK ALTER TABLE
- Hanya INSERT ... ON DUPLICATE KEY UPDATE (upsert kunci PK `ppinjaman_id`),
  memakai kolom yang memang ada di tabel (intersection dengan SHOW COLUMNS),
  jadi aman bila struktur tabel berubah / ada kolom typo / kolom dirubah tipe.

Sumber data: /personalia/pengajuan_pinjaman_karyawan
"""
import argparse
import atexit
import os
import random
import sys
import time

import httpx

sys.path.insert(0, os.getcwd())

from config import Config
from auth import AuthManager
from db import DatabaseManager

TABLE = "pinjaman_karyawan"
PATH = "/personalia/pengajuan_pinjaman_karyawan"

# Kolom yang diambil langsung dari payload API (field ppinjaman_*)
API_COLUMNS = [
    "ppinjaman_id", "ppinjaman_cabang_id", "ppinjaman_no", "ppinjaman_tanggal",
    "ppinjaman_karyawan_id", "ppinjaman_karyawan_lain",
    "ppinjaman_karyawan_atasan_id", "ppinjaman_departemen_id",
    "ppinjaman_jabatan_id", "ppinjaman_golongan_id", "ppinjaman_level_id",
    "ppinjaman_jenis", "ppinjaman_status", "ppinjaman_aktif",
    "ppinjaman_keterangan", "ppinjaman_nilai", "ppinjaman_pelunasan",
    "ppinjaman_sisa", "ppinjaman_tgl_awal_pelunasan", "ppinjaman_termin_waktu",
    "ppinjaman_setuju_1", "ppinjaman_setuju_1_status",
    "ppinjaman_setuju_2", "ppinjaman_setuju_2_status",
    "ppinjaman_setuju_3", "ppinjaman_setuju_3_status",
    "ppinjaman_dkasbank_pinjaman_karyawan_id",
]

# Kolom audit dari timestamp_data (sesuai yang ADA di tabel — `revised` di-skip
# karena tidak ada di struktur saat ini)
AUDIT_COLUMNS = [
    "created_by", "updated_by", "deleted_by",
    "created_at", "updated_at", "deleted_at",
]


def clip(value, max_len):
    """Potong string agar muat kolom varchar (created_by csb_db varchar(20))."""
    if value is None:
        return None
    if isinstance(value, str) and max_len and len(value) > max_len:
        return value[:max_len]
    return value


def fetch_all_pages(config, auth, verbose=False):
    client = httpx.Client(base_url=config.base_url, timeout=config.request_timeout)
    params = {"timestamp_data": "true"}
    all_results = []
    page = 1
    while True:
        p = dict(params)
        p["page"] = str(page)
        p["results_per_page"] = str(config.results_per_page)
        auth.ensure_token()
        headers = auth.get_headers()
        time.sleep(config.request_delay)
        if verbose:
            print(f"  -> GET {PATH} page {page}")

        resp = None
        max_tries = max(config.max_retries + 2, 5)
        for attempt in range(max_tries):
            try:
                resp = client.get(PATH, params=p, headers=headers)
            except (httpx.TransportError, httpx.TimeoutException):
                resp = None
            if resp is not None and resp.status_code < 500:
                break
            if attempt + 1 < max_tries:
                sleep_s = min(2.0 ** attempt, 8.0) + random.uniform(0, 0.5)
                if verbose:
                    print(f"      -> retry {attempt + 1}/{max_tries} "
                          f"({resp.status_code if resp else 'net'}) in {sleep_s:.1f}s")
                time.sleep(sleep_s)
        if resp is None:
            raise httpx.TransportError(f"network failure fetching {PATH}")
        if resp.status_code == 404:
            break
        resp.raise_for_status()

        data = resp.json()
        batch = data.get("data") or []
        all_results.extend(batch)
        paging = data.get("paging") or {}
        total_pages = paging.get("total_pages")
        if total_pages:
            if page >= int(total_pages):
                break
        elif len(batch) < int(config.results_per_page):
            break
        page += 1
    client.close()
    return all_results


def map_record(rec, cabang_default):
    ts = rec.get("timestamp_data") or {}
    row = {col: rec.get(col) for col in API_COLUMNS}
    # nilai audit dari timestamp_data
    for col in AUDIT_COLUMNS:
        row[col] = ts.get(col)
    # varchar(20) pada created_by / updated_by / deleted_by
    for col in ("created_by", "updated_by", "deleted_by"):
        row[col] = clip(row.get(col), 20)
    # sisa pinjaman tidak boleh negatif (maks. 0 = sudah lunas); data sumber
    # kadang salah (pelunasan > nilai) sehingga perlu di-clamp.
    sisa = row.get("ppinjaman_sisa")
    if sisa is not None:
        try:
            if float(sisa) < 0:
                row["ppinjaman_sisa"] = 0
        except (TypeError, ValueError):
            pass
    # cabang_id wajib NOT NULL -> ambil dari data pinjaman, fallback 1
    row["cabang_id"] = row.get("ppinjaman_cabang_id") or cabang_default
    return row


def main():
    parser = argparse.ArgumentParser(
        description="Sync master pinjaman karyawan ke tabel pinjaman_karyawan (upsert, struktur dipertahankan)"
    )
    parser.add_argument("-e", "--env", action="store_true",
                        help="Load configuration from environment (BRIGHTER_*)")
    parser.add_argument("--cabang-ids", default=None,
                        help="Comma-separated cabang IDs; default semua yang aktif di DB")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    config = Config.from_env()
    auth = AuthManager(config)
    db = DatabaseManager(config, target_db="csb")

    def _close_safe():
        try:
            db.close()
        except Exception:
            pass
    atexit.register(_close_safe)

    db.connect()

    # Debug: pastikan API terjangkau dulu (fail fast)
    try:
        auth.ensure_token()
    except Exception as e:
        print(f"[error] tidak dapat menghubungi API Brighter ({config.base_url}): {e}")
        sys.exit(1)

    # --- baca kolom yang ADA di tabel (jangan pernah menyentuh struktur) ---
    cur = db.conn.cursor()
    cur.execute(f"SHOW COLUMNS FROM `{TABLE}`")
    real_cols = {r["Field"] for r in cur.fetchall()}
    if "ppinjaman_id" not in real_cols:
        print(f"[error] tabel `{TABLE}` tidak punya kolom pk `ppinjaman_id`.")
        sys.exit(1)

    records = fetch_all_pages(config, auth, args.verbose)
    print(f"fetch: {len(records)} record dari API")

    # dedup by ppinjaman_id
    rows_by_id = {}
    for rec in records:
        rid = rec.get("ppinjaman_id")
        if rid is None:
            continue
        rid = int(rid) if str(rid).isdigit() else rid
        if rid not in rows_by_id:
            rows_by_id[rid] = rec
    print(f"unik: {len(rows_by_id)} ppinjaman_id")

    # peta hanya kolom yang benar-benar ada di tabel
    candidate = map_record(rows_by_id[next(iter(rows_by_id))], 1) if rows_by_id else {}
    cols = [c for c in candidate if c in real_cols]
    if "ppinjaman_id" not in cols:
        print("[error] tidak ada kolom ppinjaman_id yang bisa di-upsert.")
        sys.exit(1)

    # existing pk untuk melaporkan inserted vs updated
    cur.execute(f"SELECT `ppinjaman_id` FROM `{TABLE}`")
    existing_pk = {r["ppinjaman_id"] for r in cur.fetchall()}

    insert_cols = [c for c in cols if c != "synced_at"]
    upd_cols = [c for c in insert_cols if c != "ppinjaman_id"]
    col_names = ", ".join(f"`{c}`" for c in insert_cols)
    placeholders = ", ".join(["%s"] * len(insert_cols))
    update_clause = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in upd_cols)
    if update_clause:
        update_clause += ", `synced_at` = CURRENT_TIMESTAMP"
    else:
        update_clause = "`synced_at` = CURRENT_TIMESTAMP"
    sql = (
        f"INSERT INTO `{TABLE}` ({col_names}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {update_clause}"
    )

    batch = [ [map_record(rec, 1)[c] for c in insert_cols] for rec in rows_by_id.values() ]

    inserted = 0
    updated = 0
    totals = {"inserted": 0, "updated": 0}
    for rid in rows_by_id:
        if rid in existing_pk:
            totals["updated"] += 1
        else:
            totals["inserted"] += 1

    CHUNK = 500
    n = 0
    for start in range(0, len(batch), CHUNK):
        chunk = batch[start:start + CHUNK]
        cur.executemany(sql, chunk)
        n += len(chunk)
    db.conn.commit()

    print(f"done: {n} record di-upsert ke `{TABLE}`")
    print(f"  - baru (INSERT): {totals['inserted']}")
    print(f"  - diperbarui (UPDATE): {totals['updated']}")

if __name__ == "__main__":
    main()