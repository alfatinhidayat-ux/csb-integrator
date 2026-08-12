"""Sync saldo kas harian dari API Brighter ke tabel baru `brighter_saldo_kas_harian` di csb_db.

Sumber data: GET /transaksi/kas_harian/rekapitulasi/saldo
Mengembalikan satu baris per tanggal per cabang: kas_awal, kas_masuk, kas_keluar,
kas_akhir, selisih, dan `saldo` (total kumulatif kas sejak awal rentang yang diminta).

Catatan penting:
- `saldo` adalah nilai kumulatif dari `tanggal_awal`, jadi WAJIB satu panggilan
  rentang penuh per cabang (jangan dipecah per bulan atau nilainya nyeleneh).
- Endpoint mengembalikan 404 bila tidak ada data sama sekali dalam rentang
  (mis. Januari/Februari 2026 untuk cabang 1, sebelum aplikasi dipakai) -> dilewati.
- Tabel ini tabel BARU yang terpisah (milik script ini, aman di-create).
"""
import argparse
import atexit
import dataclasses
import os
import random
import sys
import time
from datetime import date, datetime, timedelta

import httpx

sys.path.insert(0, os.getcwd())

from config import Config
from auth import AuthManager
from db import DatabaseManager

TABLE = "brighter_saldo_kas_harian"
PATH = "/transaksi/kas_harian/rekapitulasi/saldo"

TABLE_REKAP = "brighter_penerimaan_rekap"
PATH_REKAP = "/transaksi/kas_harian/penerimaan/rekap"
REKAP_METHODS = ["tunai", "transfer", "card", "qris", "wallet", "piutang"]

# Tabel penyesuaian rekap: DELTA yang ditambahkan di atas nilai API setiap
# kali sync rekap selesai, agar angka rekap == dashboard Brighter meskipun
# ada selisih yang tidak tersedia di API. Dikelola lewat file SQL
# `fin_dash_adjustment_table.sql` (cabang_id + tanggal, kolom metode, aktif).
TABLE_ADJUSTMENT = "brighter_penerimaan_rekap_adjustment"

# Kolom database (selain cabang_id + synced_at).
FIELDS = ["tanggal", "kas_awal", "kas_keluar", "kas_masuk", "kas_akhir", "selisih", "saldo"]

DDL = f"""
CREATE TABLE IF NOT EXISTS `{TABLE}` (
    `cabang_id` INT NOT NULL,
    `tanggal` DATE NOT NULL,
    `kas_awal` DOUBLE NULL,
    `kas_keluar` DOUBLE NULL,
    `kas_masuk` DOUBLE NULL,
    `kas_akhir` DOUBLE NULL,
    `selisih` DOUBLE NULL,
    `saldo` DOUBLE NULL,
    `synced_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`cabang_id`, `tanggal`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

DDL_REKAP = f"""
CREATE TABLE IF NOT EXISTS `{TABLE_REKAP}` (
    `cabang_id` INT NOT NULL,
    `tanggal` DATE NOT NULL,
    `cabang_nama` VARCHAR(255) NULL,
    `cabang_kode` VARCHAR(50) NULL,
    `tunai` DOUBLE NULL,
    `transfer` DOUBLE NULL,
    `card` DOUBLE NULL,
    `qris` DOUBLE NULL,
    `wallet` DOUBLE NULL,
    `piutang` DOUBLE NULL,
    `total` DOUBLE NULL,
    `synced_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`cabang_id`, `tanggal`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

DDL_ADJUSTMENT = f"""
CREATE TABLE IF NOT EXISTS `{TABLE_ADJUSTMENT}` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `cabang_id` INT NOT NULL,
    `tanggal` DATE NOT NULL,
    `tunai` DOUBLE NOT NULL DEFAULT 0,
    `transfer` DOUBLE NOT NULL DEFAULT 0,
    `card` DOUBLE NOT NULL DEFAULT 0,
    `qris` DOUBLE NOT NULL DEFAULT 0,
    `wallet` DOUBLE NOT NULL DEFAULT 0,
    `piutang` DOUBLE NOT NULL DEFAULT 0,
    `total` DOUBLE NOT NULL DEFAULT 0,
    `keterangan` VARCHAR(255) NULL,
    `aktif` TINYINT(1) NOT NULL DEFAULT 1,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_adj_cabang_tanggal` (`cabang_id`, `tanggal`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def load_cabang_urls(db):
    """Load {cabang_id: url_api} for ACTIVE cabangs from csb_db `cabang` table."""
    cur = db.conn.cursor()
    cur.execute("SELECT `id`, `url_api` FROM `cabang` WHERE `aktif` = 1")
    out = {}
    for r in cur.fetchall():
        url = (r["url_api"] or "").strip().rstrip("/")
        if url:
            out[r["id"]] = url
    return out


def fetch_saldo(config, auth, cabang_id, tanggal_awal, tanggal_akhir, verbose=False):
    """Fetch the daily saldo rows for one cabang over a full range.

    Returns a list of dict rows, or [] when the API has no data in the range (404).
    """
    client = httpx.Client(base_url=config.base_url, timeout=config.request_timeout)
    params = {
        "kh_cabang_id": str(cabang_id),
        "tanggal_awal": tanggal_awal,
        "tanggal_akhir": tanggal_akhir,
    }
    max_tries = max(config.max_retries + 2, 5)
    resp = None
    for attempt in range(max_tries):
        auth.ensure_token()
        headers = auth.get_headers()
        if verbose:
            print(f"  -> GET {PATH} cabang={cabang_id} {tanggal_awal}..{tanggal_akhir}")
        try:
            resp = client.get(PATH, params=params, headers=headers)
        except (httpx.TransportError, httpx.TimeoutException):
            resp = None
        if resp is not None and resp.status_code < 500:
            break
        if attempt + 1 < max_tries:
            sleep_s = min(2.0 ** attempt * 3, 20.0) + random.uniform(0, 0.5)
            if verbose:
                print(f"      -> retry {attempt + 1}/{max_tries} "
                      f"({resp.status_code if resp else 'net'}) in {sleep_s:.1f}s")
            time.sleep(sleep_s)
    client.close()
    if resp is None:
        raise httpx.TransportError(f"network failure fetching {PATH} cabang {cabang_id}")
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    return resp.json().get("data") or []


def fetch_rekap_day(config, auth, cabang_id, tanggal, verbose=False):
    """Fetch one day of penerimaan rekap (totals per payment method).

    Returns a dict (mapped to a DB row) or None on failure/empty.
    """
    client = httpx.Client(base_url=config.base_url, timeout=config.request_timeout)
    params = {
        "tanggal_awal": tanggal,
        "tanggal_akhir": tanggal,
        "cabang_id": str(cabang_id),
    }
    max_tries = max(config.max_retries + 2, 5)
    resp = None
    for attempt in range(max_tries):
        auth.ensure_token()
        headers = auth.get_headers()
        if verbose:
            print(f"  -> GET {PATH_REKAP} cabang={cabang_id} {tanggal}")
        try:
            resp = client.get(PATH_REKAP, params=params, headers=headers)
        except (httpx.TransportError, httpx.TimeoutException):
            resp = None
        if resp is not None and resp.status_code < 500:
            break
        if attempt + 1 < max_tries:
            sleep_s = min(2.0 ** attempt * 3, 20.0) + random.uniform(0, 0.5)
            if verbose:
                print(f"      -> retry {attempt + 1}/{max_tries} "
                      f"({resp.status_code if resp else 'net'}) in {sleep_s:.1f}s")
            time.sleep(sleep_s)
    client.close()
    if resp is None:
        raise httpx.TransportError(f"network failure fetching {PATH_REKAP} cabang {cabang_id}")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json().get("data") or {}


def map_rekap_row(cabang_id, tanggal, data):
    """Flatten one day's rekap response into a single DB row."""
    row = {"cabang_id": cabang_id, "tanggal": tanggal}
    for method in REKAP_METHODS:
        block = data.get(method) or {}
        row[method] = block.get("total_keseluruhan")
        detail = block.get("detail") or []
        if detail and not row.get("cabang_nama"):
            row["cabang_nama"] = detail[0].get("cabang_nama")
            row["cabang_kode"] = detail[0].get("cabang_kode")
    row["total"] = data.get("total")
    return row


def upsert_rekap_rows(db, rows):
    if not rows:
        return 0
    fields = ["tanggal", "cabang_nama", "cabang_kode"] + REKAP_METHODS + ["total"]
    col_names = ", ".join(f"`{c}`" for c in fields)
    placeholders = ", ".join(["%s"] * len(fields))
    updates = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in fields if c != "tanggal")
    updates += ", `synced_at` = CURRENT_TIMESTAMP"
    sql = (
        f"INSERT INTO `{TABLE_REKAP}` (`cabang_id`, {col_names}) VALUES (%s, {placeholders}) "
        f"ON DUPLICATE KEY UPDATE {updates}"
    )
    batch = [
        [r["cabang_id"]] + [r.get(c) for c in fields]
        for r in rows
    ]
    cur = db.conn.cursor()
    CHUNK = 500
    n = 0
    for start in range(0, len(batch), CHUNK):
        cur.executemany(sql, batch[start:start + CHUNK])
        n += len(batch[start:start + CHUNK])
    db.conn.commit()
    return n


def apply_rekap_adjustments(db):
    """Apply all ACTIVE deltas from the adjustment table on top of the rekap
    rows that just got upserted (values reset to API first, so this never
    double-applies on repeated sync runs)."""
    cur = db.conn.cursor()
    cur.execute(DDL_ADJUSTMENT)
    sql = (
        f"UPDATE `{TABLE_REKAP}` r "
        f"JOIN `{TABLE_ADJUSTMENT}` a "
        f"  ON r.cabang_id = a.cabang_id AND r.tanggal = a.tanggal "
        "SET r.tunai    = r.tunai + a.tunai, "
        "    r.transfer = r.transfer + a.transfer, "
        "    r.card     = r.card + a.card, "
        "    r.qris     = r.qris + a.qris, "
        "    r.wallet   = r.wallet + a.wallet, "
        "    r.piutang  = r.piutang + a.piutang, "
        "    r.total    = r.total + a.total, "
        "    r.synced_at = CURRENT_TIMESTAMP "
        "WHERE a.aktif = 1"
    )
    cur.execute(sql)
    db.conn.commit()
    return cur.rowcount


def upsert_rows(db, cabang_id, rows):
    if not rows:
        return 0
    col_names = ", ".join(f"`{c}`" for c in FIELDS)
    placeholders = ", ".join(["%s"] * len(FIELDS))
    updates = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in FIELDS if c != "tanggal")
    updates += ", `synced_at` = CURRENT_TIMESTAMP"
    sql = (
        f"INSERT INTO `{TABLE}` (`cabang_id`, {col_names}) VALUES (%s, {placeholders}) "
        f"ON DUPLICATE KEY UPDATE {updates}"
    )
    batch = [
        [cabang_id]
        + [row.get(c) for c in FIELDS]
        for row in rows
        if row.get("tanggal")
    ]
    cur = db.conn.cursor()
    CHUNK = 500
    n = 0
    for start in range(0, len(batch), CHUNK):
        cur.executemany(sql, batch[start:start + CHUNK])
        n += len(batch[start:start + CHUNK])
    db.conn.commit()
    return n


def main():
    parser = argparse.ArgumentParser(
        description="Sync saldo kas harian (per tanggal per cabang) ke "
                    f"tabel `{TABLE}` di csb_db"
    )
    parser.add_argument("-e", "--env", action="store_true",
                        help="Load configuration from environment variables (BRIGHTER_*)")
    parser.add_argument("--cabang-ids", default=None,
                        help="Comma-separated cabang IDs; default semua cabang aktif di csb_db")
    parser.add_argument("--tanggal-awal", default="2026-01-01",
                        help="Tanggal awal format YYYY-MM-DD (default: 2026-01-01)")
    parser.add_argument("--tanggal-akhir", default=date.today().isoformat(),
                        help="Tanggal akhir format YYYY-MM-DD (default: hari ini)")
    parser.add_argument("--verbose", action="store_true",
                        help="Enable verbose debugging logs")
    args = parser.parse_args()

    default_cfg = Config.from_env()

    db = DatabaseManager(default_cfg, target_db="csb")
    db.connect()

    def _close_safe():
        try:
            db.close()
        except Exception:
            pass
    atexit.register(_close_safe)

    cur = db.conn.cursor()
    cur.execute(DDL)
    cur.execute(DDL_REKAP)
    cur.execute(DDL_ADJUSTMENT)
    db.conn.commit()

    cabang_urls = load_cabang_urls(db)
    if args.cabang_ids:
        cabang_ids = [int(x.strip()) for x in args.cabang_ids.split(",") if x.strip()]
    else:
        cabang_ids = sorted(cabang_urls.keys())
    if not cabang_ids:
        print("[error] tidak ada cabang aktif di csb_db (`cabang`). Gunakan --cabang-ids.")
        sys.exit(1)

    server_cfg = {}
    server_auth = {}
    cabang_cfg = {}
    cabang_auth = {}
    for cid in cabang_ids:
        url = cabang_urls.get(cid)
        key = url or default_cfg.base_url
        if key not in server_cfg:
            cfg = dataclasses.replace(default_cfg, base_url=url) if url else default_cfg
            server_cfg[key] = cfg
            server_auth[key] = AuthManager(cfg)
        cabang_cfg[cid] = server_cfg[key]
        cabang_auth[cid] = server_auth[key]

    # Pre-flight token
    try:
        cabang_auth[cabang_ids[0]].ensure_token()
        print(f"API OK ({cabang_cfg[cabang_ids[0]].base_url})")
    except Exception as e:
        print(f"[Pre-flight] Tidak dapat menghubungi API Brighter: {e}")
        db.close()
        sys.exit(1)

    print(f"Rentang: {args.tanggal_awal} .. {args.tanggal_akhir}")
    print(f"Cabang: {cabang_ids}")

    total = 0
    total_rekap = 0
    for cid in cabang_ids:
        print(f"\n--- Cabang {cid} ---")
        try:
            db.reconnect()
        except Exception:
            pass
        cfg_c = cabang_cfg[cid]
        auth_c = cabang_auth[cid]
        try:
            rows = fetch_saldo(cfg_c, auth_c, cid, args.tanggal_awal, args.tanggal_akhir, args.verbose)
        except Exception as e:
            print(f"       -> ERROR: {e}")
            rows = []
        if not rows:
            print("       -> tidak ada data di rentang ini (API mengembalikan kosong/404)")
        n = upsert_rows(db, cid, rows)
        if rows:
            print(f"       -> {len(rows)} hari saldo di-fetch, {n} di-upsert ke `{TABLE}`")
        total += n

        # Rekap penerimaan: per hari (agar serasi dengan tabel saldo harian)
        awal = date.fromisoformat(args.tanggal_awal)
        akhir = date.fromisoformat(args.tanggal_akhir)
        rekap_rows = []
        d = awal
        while d <= akhir:
            try:
                data = fetch_rekap_day(cfg_c, auth_c, cid, d.isoformat(), args.verbose)
            except Exception as e:
                print(f"       -> ERROR rekap {d}: {e}")
                data = None
            if data:
                rekap_rows.append(map_rekap_row(cid, d.isoformat(), data))
            d += timedelta(days=1)
        n_rekap = upsert_rekap_rows(db, rekap_rows)
        if rekap_rows:
            print(f"       -> {len(rekap_rows)} hari rekap di-fetch, {n_rekap} di-upsert ke `{TABLE_REKAP}`")
        n_adj = apply_rekap_adjustments(db)
        if n_adj:
            print(f"       -> {n_adj} baris rekap disesuaikan dari `{TABLE_ADJUSTMENT}` "
                  f"(delta aktif diterapkan ulang)")
        total_rekap += n_rekap

    db.close()
    print("\n" + "=" * 50)
    print(f"COMPLETE: {total} baris saldo -> `{TABLE}`")
    print(f"          {total_rekap} baris rekap penerimaan -> `{TABLE_REKAP}`")
    print("=" * 50)


if __name__ == "__main__":
    main()