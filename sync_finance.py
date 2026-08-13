import argparse
import atexit
import dataclasses
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import httpx
import pymysql

# Setup search path for custom imports
sys.path.insert(0, os.getcwd())

from config import Config
from auth import AuthManager
from db import DatabaseManager

# Nama tabel target di csb_db (bukan darker_mirror)
SUPPLIER_TABLE = "supplier"          # tabel supplier CSB yang sudah ada (relasi FK)

# Hash bcrypt valid (placeholder) untuk akun user supplier baru.
DUMMY_PASSWORD_HASH = "$2y$12$Uz8LHQTFtjsBliHJUy8hYOaou5BPGKz2O/6YYzzD0V8ppx3JHm3RS"

TABLES = {
    "pembelian": "brighter_persediaan_pembelian",
    "pelunasan_hutang": "brighter_transaksi_pelunasan_hutang",
    "pelunasan_hutang_detail": "brighter_transaksi_pelunasan_hutang_detail",
    "pelunasan_hutang_foto": "brighter_transaksi_pelunasan_hutang_foto",
    "pelunasan_piutang": "brighter_transaksi_pelunasan_piutang",
    "piutang_customer_detail": "brighter_transaksi_piutang_customer_detail",
}

CLARIFY_HUTANG_TABLES = {
    "header": "supplier_hutang_pelunasan",
    "item": "supplier_hutang_pelunasan_items",
    "media": "supplier_hutang_pelunasan_media",
}

# Kolom bertipe tanggal (dengan nama setelah strip prefix) yang cukup disimpan
# sebagai DATE tanpa komponen waktu agar data tabel pelunasan bersih.
DATE_COLS_BY_TABLE = {
    "brighter_transaksi_pelunasan_hutang": {"tanggal", "date_post"},
    "brighter_transaksi_pelunasan_piutang": {
        "tanggal", "date_post", "cust_data_cust_tgllahir",
        "cust_data_cust_preward_exp_date",
    },
}

ENDPOINT_META = {
    "supplier": {
        "path": "/master/supplier",
        "prefix": "supplier_",
        "cabang_param": None,
        "params": {
            "supplier_aktif": "Aktif",
            "timestamp_data": "true",
        },
    },
    "pembelian": {
        "path": "/persediaan/pembelian",
        "prefix": "pembelian_",
        "cabang_param": "pembelian_cabang_id",
        "params": {
            "pembelian_status_dok": "Semua",
            "pembelian_status_lunas": "Semua",
            "pembelian_supplier_data": "true",
            "timestamp_data": "true",
        },
    },
    "pelunasan_hutang": {
        "path": "/transaksi/pelunasan_hutang",
        "prefix": "fhutang_",
        "cabang_param": "fhutang_cabang_id",
        "params": {
            "fhutang_stat_dok": "Semua",
            "fhutang_supp_data": "true",
            "timestamp_data": "true",
        },
    },
    "pelunasan_hutang_detail": {
        "path": "/transaksi/pelunasan_hutang/{id}/detail_pelunasan_hutang",
        "prefix": "dhutang_",
        "cabang_param": None,
        "params": {
            "dhutang_master_hutang_data": "true",
            "timestamp_data": "true",
        },
        "parent_key": "id",
        "join_id": "fhutang_id",           # injected column naming from parent header
    },
    "pelunasan_hutang_foto": {
        "path": "/transaksi/pelunasan_hutang/{id}/dfhutang_foto",
        "prefix": "dfhutang_",
        "cabang_param": None,
        "params": {
            "timestamp_data": "true",
        },
        "parent_key": "id",
        "join_id": "fhutang_id",
    },
    "pelunasan_piutang": {
        "path": "/transaksi/pelunasan_piutang",
        "prefix": "fpiutang_",
        "cabang_param": "fpiutang_cabang_id",
        "params": {
            "fpiutang_stat_dok": "Semua",
            "fpiutang_cust_data": "true",
            "timestamp_data": "true",
        },
    },
    "piutang_customer_detail": {
        "path": "/transaksi/piutang_penjualan",
        "prefix": "lpiutang_",
        "cabang_param": "lpiutang_cabang_id",
        "params": {
            "lpiutang_stat_dok": "Tertutup",
            "piutang_cust_data": "true",
        },
        # Tidak ada filter min_faktur_tanggal — semua faktur dari semua periode
        # diambil agar angka cocok dengan laporan Brighter mode "semua periode".
    },
}


def flatten(value, _prefix="", _seen=None):
    """Recursively flatten dict/list into a flat {key: scalar} dict.

    Nested dicts are expanded into underscore-joined keys; arrays of dicts are
    kept as JSON strings so no data is ever dropped.
    """
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


def map_record(endpoint_name, rec, cabang_id, extra=None):
    """Flatten an API record, strip the endpoint field prefix, and standardize
    columns for csb_db. `extra` injects parent FK / cabang columns for children.

    Jika record tidak punya `id` nyata, dibuat id deterministik dari isi record —
    supaya tabel tetap punya PK (id, cabang_id) dan TIDAK menduplikat bila script
    dijalankan berulang-ulang (upsert bukan insert polos).
    """
    meta = ENDPOINT_META[endpoint_name]
    flat = flatten(rec)
    mapped = {strip_prefix(meta["prefix"], k): v for k, v in flat.items()}
    mapped["cabang_id"] = cabang_id
    if extra:
        mapped.update(extra)

    # kolom tanggal pada pelunasan hutang/piutang: cukup DATE (tanpa waktu)
    table = TABLES.get(endpoint_name)
    for dc in DATE_COLS_BY_TABLE.get(table, set()):
        v = mapped.get(dc)
        if isinstance(v, str) and len(v) >= 10 and v[:4].isdigit() and v[4:5] == "-":
            mapped[dc] = v[:10]

    rid = mapped.get("id")
    if rid is None:
        # id deterministik INTEGER (maks 60 bit -> muat di kolom BIGINT dan VARCHAR),
        # agar konsisten dengan record lain yang punya id nyata bertipe integer.
        seed = json.dumps(
            {k: v for k, v in mapped.items() if k not in ("id", "synced_at")},
            sort_keys=True, default=str,
        )
        mapped["id"] = int(hashlib.md5(f"{cabang_id}|{seed}".encode()).hexdigest()[:15], 16)
    return mapped


def fetch_all_pages(config, auth, path, path_kwargs, query_params=None, cabang_id=None, cabang_param=None, verbose=False):
    """Fetch every page of an endpoint using paging.total_pages (and a fallback
    on short pages) so no record is ever skipped.

    `path_kwargs` replace placeholders like {id}/{cust_id} in the URL. `query_params`
    are sent as-is per request. `cabang_id` is injected under `cabang_param` when given.
    Pages advance until paging.total_pages is reached.
    """
    client = httpx.Client(base_url=config.base_url, timeout=config.request_timeout)
    url = path.format(**(path_kwargs or {}))
    query_params = dict(query_params or {})
    all_results = []
    page = 1
    while True:
        p = dict(query_params)
        p["page"] = str(page)
        p["results_per_page"] = str(config.results_per_page)
        if cabang_id:
            p[cabang_param or "lpiutang_cabang_id"] = str(cabang_id)

        auth.ensure_token()
        headers = auth.get_headers()
        time.sleep(config.request_delay)
        if verbose:
            print(f"      -> GET {url} page {page}")

        # Retry backoff seimbang untuk error sementara (5xx mis. 502 / network):
        # cukup lama agar server pulih, tapi tidak terlalu lama sampai terasa hang.
        max_tries = max(config.max_retries + 2, 5)
        resp = None
        for attempt in range(max_tries):
            try:
                resp = client.get(url, params=p, headers=headers)
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
        if resp is None:
            raise httpx.TransportError(f"network failure fetching {url}")
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        data = resp.json()

        batch = data.get("data") or []
        all_results.extend(batch)

        total_pages = 1
        paging = data.get("paging") or {}
        if paging.get("total_pages"):
            total_pages = int(paging["total_pages"])
        elif len(batch) < int(config.results_per_page):
            break
        if page >= total_pages:
            break
        page += 1

    client.close()
    return all_results


def sync_headers(config, auth, db, endpoint_name, cabang_id, verbose=False):
    """Fetch all pages of a cabang-scoped header endpoint for a branch."""
    meta = ENDPOINT_META[endpoint_name]
    return fetch_all_pages(
        config, auth,
        meta["path"], {},
        dict(meta["params"]),
        cabang_id if meta["cabang_param"] else None,
        meta["cabang_param"],
        verbose,
    )


def upsert_batch(db, table, records, cabang_id):
    if not records:
        return 0
    first = records[0]
    db.ensure_table(table, first, DATE_COLS_BY_TABLE.get(table, set()))
    db.upsert_records(table, records, cabang_id)
    return len(records)


def ensure_clarify_hutang_tables(db):
    """Ensure Clarify-owned legacy supplier debt payment tables exist.

    These tables mirror Brighter pelunasan hutang into Clarify shape without
    posting anything to kas_bank. Upserts are keyed by legacy ids so this can be
    run repeatedly by the integrator without duplicating rows.
    """
    cur = db.conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS supplier_hutang_pelunasan (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            legacy_fhutang_id BIGINT NOT NULL,
            cabang_id BIGINT UNSIGNED NOT NULL,
            supplier_id BIGINT UNSIGNED NULL,
            supplier_kode VARCHAR(50) NULL,
            supplier_nama VARCHAR(200) NULL,
            pelunasan_number VARCHAR(100) NOT NULL,
            tanggal DATE NULL,
            payment_method VARCHAR(50) NULL,
            akun_id BIGINT NULL,
            total_pelunasan DECIMAL(15,2) NOT NULL DEFAULT 0,
            keterangan TEXT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'posted',
            source_system VARCHAR(30) NOT NULL DEFAULT 'brighter_legacy',
            is_legacy TINYINT(1) NOT NULL DEFAULT 1,
            legacy_stat_dok VARCHAR(50) NULL,
            legacy_post VARCHAR(50) NULL,
            legacy_date_post DATE NULL,
            legacy_created_by VARCHAR(100) NULL,
            legacy_created_at DATETIME NULL,
            legacy_updated_by VARCHAR(100) NULL,
            legacy_updated_at DATETIME NULL,
            synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uq_supplier_hutang_pelunasan_legacy (legacy_fhutang_id, cabang_id),
            KEY idx_supplier_hutang_pelunasan_cabang_tanggal (cabang_id, tanggal),
            KEY idx_supplier_hutang_pelunasan_supplier (supplier_id),
            KEY idx_supplier_hutang_pelunasan_status (status)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS supplier_hutang_pelunasan_items (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            supplier_hutang_pelunasan_id BIGINT UNSIGNED NOT NULL,
            legacy_detail_id BIGINT NOT NULL,
            legacy_fhutang_id BIGINT NOT NULL,
            cabang_id BIGINT UNSIGNED NOT NULL,
            legacy_master_hutang_id BIGINT NULL,
            legacy_pembelian_id BIGINT NULL,
            pembelian_nobukti VARCHAR(100) NULL,
            no_tagihan VARCHAR(100) NULL,
            tanggal_hutang DATE NULL,
            hutang_awal DECIMAL(15,2) NOT NULL DEFAULT 0,
            terbayar_sebelumnya DECIMAL(15,2) NOT NULL DEFAULT 0,
            nilai_pelunasan DECIMAL(15,2) NOT NULL DEFAULT 0,
            sisa_hutang DECIMAL(15,2) NOT NULL DEFAULT 0,
            keterangan TEXT NULL,
            source_system VARCHAR(30) NOT NULL DEFAULT 'brighter_legacy',
            is_legacy TINYINT(1) NOT NULL DEFAULT 1,
            synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uq_supplier_hutang_pelunasan_item_legacy (legacy_detail_id, cabang_id),
            KEY idx_supplier_hutang_pelunasan_items_header (supplier_hutang_pelunasan_id),
            KEY idx_supplier_hutang_pelunasan_items_legacy_header (legacy_fhutang_id, cabang_id),
            KEY idx_supplier_hutang_pelunasan_items_pembelian (legacy_pembelian_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS supplier_hutang_pelunasan_media (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            supplier_hutang_pelunasan_id BIGINT UNSIGNED NOT NULL,
            legacy_foto_id BIGINT NOT NULL,
            legacy_fhutang_id BIGINT NOT NULL,
            cabang_id BIGINT UNSIGNED NOT NULL,
            file_path TEXT NULL,
            file_url TEXT NULL,
            file_url_medium TEXT NULL,
            file_url_thumbnail TEXT NULL,
            file_size BIGINT NULL,
            file_size_medium BIGINT NULL,
            file_size_thumbnail BIGINT NULL,
            keterangan TEXT NULL,
            source_system VARCHAR(30) NOT NULL DEFAULT 'brighter_legacy',
            is_legacy TINYINT(1) NOT NULL DEFAULT 1,
            synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uq_supplier_hutang_pelunasan_media_legacy (legacy_foto_id, cabang_id),
            KEY idx_supplier_hutang_pelunasan_media_header (supplier_hutang_pelunasan_id),
            KEY idx_supplier_hutang_pelunasan_media_legacy_header (legacy_fhutang_id, cabang_id)
        )
    """)
    db.conn.commit()


def _stat_to_clarify_status(stat_dok):
    s = (stat_dok or "").strip().lower()
    if s in ("batal", "dibatalkan", "cancelled", "canceled"):
        return "void"
    if s in ("tertutup", "posted", "selesai"):
        return "posted"
    return "draft"


def sync_clarify_hutang_pelunasan(db, cabang_id):
    """Reconcile Brighter pelunasan hutang staging into Clarify legacy tables."""
    ensure_clarify_hutang_tables(db)
    cur = db.conn.cursor()

    cur.execute("""
        INSERT INTO supplier_hutang_pelunasan (
            legacy_fhutang_id, cabang_id, supplier_id, supplier_kode, supplier_nama,
            pelunasan_number, tanggal, payment_method, akun_id, total_pelunasan,
            keterangan, status, source_system, is_legacy, legacy_stat_dok,
            legacy_post, legacy_date_post, legacy_created_by, legacy_created_at,
            legacy_updated_by, legacy_updated_at, synced_at
        )
        SELECT
            h.id, h.cabang_id, h.supp, h.supp_data_supplier_kode, h.supp_data_supplier_nama,
            h.nobukti, h.tanggal, h.cara, h.akun, COALESCE(h.bayar, 0),
            h.keterangan,
            CASE
                WHEN LOWER(COALESCE(h.stat_dok, '')) IN ('batal','dibatalkan','cancelled','canceled') THEN 'void'
                WHEN LOWER(COALESCE(h.stat_dok, '')) IN ('tertutup','posted','selesai') THEN 'posted'
                ELSE 'draft'
            END,
            'brighter_legacy', 1, h.stat_dok, h.post, h.date_post,
            h.timestamp_data_created_by,
            CASE
                WHEN h.timestamp_data_created_at REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}' THEN h.timestamp_data_created_at
                ELSE NULL
            END,
            h.timestamp_data_updated_by,
            CASE
                WHEN h.timestamp_data_updated_at REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}' THEN h.timestamp_data_updated_at
                ELSE NULL
            END,
            NOW()
        FROM brighter_transaksi_pelunasan_hutang h
        WHERE h.cabang_id = %s
        ON DUPLICATE KEY UPDATE
            supplier_id = VALUES(supplier_id),
            supplier_kode = VALUES(supplier_kode),
            supplier_nama = VALUES(supplier_nama),
            pelunasan_number = VALUES(pelunasan_number),
            tanggal = VALUES(tanggal),
            payment_method = VALUES(payment_method),
            akun_id = VALUES(akun_id),
            total_pelunasan = VALUES(total_pelunasan),
            keterangan = VALUES(keterangan),
            status = VALUES(status),
            legacy_stat_dok = VALUES(legacy_stat_dok),
            legacy_post = VALUES(legacy_post),
            legacy_date_post = VALUES(legacy_date_post),
            legacy_created_by = VALUES(legacy_created_by),
            legacy_created_at = VALUES(legacy_created_at),
            legacy_updated_by = VALUES(legacy_updated_by),
            legacy_updated_at = VALUES(legacy_updated_at),
            synced_at = VALUES(synced_at)
    """, (cabang_id,))
    header_rows = cur.rowcount

    cur.execute("""
        INSERT INTO supplier_hutang_pelunasan_items (
            supplier_hutang_pelunasan_id, legacy_detail_id, legacy_fhutang_id,
            cabang_id, legacy_master_hutang_id, legacy_pembelian_id,
            pembelian_nobukti, no_tagihan, tanggal_hutang, hutang_awal,
            terbayar_sebelumnya, nilai_pelunasan, sisa_hutang, keterangan,
            source_system, is_legacy, synced_at
        )
        SELECT
            p.id, d.id, d.fhutang_id, d.cabang_id, d.master_hutang_id,
            d.master_hutang_data_pembelian_id,
            d.master_hutang_data_pembelian_nobukti,
            d.master_hutang_data_pembelian_no_tagihan,
            CASE
                WHEN d.master_hutang_data_pembelian_tanggal REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                    THEN DATE(d.master_hutang_data_pembelian_tanggal)
                WHEN d.tanggal REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                    THEN DATE(d.tanggal)
                ELSE NULL
            END,
            COALESCE(d.hutang_awal, 0), COALESCE(d.terbayar, 0),
            COALESCE(d.nilai_bayar, 0), COALESCE(d.sisa_bayar, 0),
            d.keterangan, 'brighter_legacy', 1, NOW()
        FROM brighter_transaksi_pelunasan_hutang_detail d
        JOIN supplier_hutang_pelunasan p
          ON p.legacy_fhutang_id = d.fhutang_id
         AND p.cabang_id = d.cabang_id
        WHERE d.cabang_id = %s
        ON DUPLICATE KEY UPDATE
            supplier_hutang_pelunasan_id = VALUES(supplier_hutang_pelunasan_id),
            legacy_fhutang_id = VALUES(legacy_fhutang_id),
            legacy_master_hutang_id = VALUES(legacy_master_hutang_id),
            legacy_pembelian_id = VALUES(legacy_pembelian_id),
            pembelian_nobukti = VALUES(pembelian_nobukti),
            no_tagihan = VALUES(no_tagihan),
            tanggal_hutang = VALUES(tanggal_hutang),
            hutang_awal = VALUES(hutang_awal),
            terbayar_sebelumnya = VALUES(terbayar_sebelumnya),
            nilai_pelunasan = VALUES(nilai_pelunasan),
            sisa_hutang = VALUES(sisa_hutang),
            keterangan = VALUES(keterangan),
            synced_at = VALUES(synced_at)
    """, (cabang_id,))
    item_rows = cur.rowcount

    cur.execute("""
        INSERT INTO supplier_hutang_pelunasan_media (
            supplier_hutang_pelunasan_id, legacy_foto_id, legacy_fhutang_id,
            cabang_id, file_path, file_url, file_url_medium, file_url_thumbnail,
            file_size, file_size_medium, file_size_thumbnail, keterangan,
            source_system, is_legacy, synced_at
        )
        SELECT
            p.id, f.id, f.fhutang_id, f.cabang_id, f.foto_path, f.foto_url,
            f.foto_url_medium, f.foto_url_thumbnail, f.foto_size,
            f.foto_size_medium, f.foto_size_thumbnail, f.foto_keterangan,
            'brighter_legacy', 1, NOW()
        FROM brighter_transaksi_pelunasan_hutang_foto f
        JOIN supplier_hutang_pelunasan p
          ON p.legacy_fhutang_id = f.fhutang_id
         AND p.cabang_id = f.cabang_id
        WHERE f.cabang_id = %s
        ON DUPLICATE KEY UPDATE
            supplier_hutang_pelunasan_id = VALUES(supplier_hutang_pelunasan_id),
            legacy_fhutang_id = VALUES(legacy_fhutang_id),
            file_path = VALUES(file_path),
            file_url = VALUES(file_url),
            file_url_medium = VALUES(file_url_medium),
            file_url_thumbnail = VALUES(file_url_thumbnail),
            file_size = VALUES(file_size),
            file_size_medium = VALUES(file_size_medium),
            file_size_thumbnail = VALUES(file_size_thumbnail),
            keterangan = VALUES(keterangan),
            synced_at = VALUES(synced_at)
    """, (cabang_id,))
    media_rows = cur.rowcount

    db.conn.commit()
    return {"header": header_rows, "item": item_rows, "media": media_rows}


def _count_piutang_tanpa_nama(db, cabang_id) -> int:
    """Hitung record piutang pelanggan milik cabang yang nama cust-nya masih
    NULL/kosong. Digunakan setelah sync piutang untuk memicu backfill nama."""
    try:
        cur = db.conn.cursor()
        cur.execute(
            f"SELECT COUNT(*) AS n FROM {TABLES['piutang_customer_detail']} "
            f"WHERE cabang_id = %s "
            f"AND (cust_data_cust_nama IS NULL OR cust_data_cust_nama = '')",
            (cabang_id,),
        )
        return cur.fetchone()["n"]
    except Exception:
        return 0


def _delete_orphan_piutang(db, cabang_id, api_ids, verbose=False):
    """Hapus record piutang_customer_detail milik cabang yang `id`-nya (lpiutang_id)
    tidak lagi ada di respons API. Mengembalikan jumlah record yang dihapus."""
    if not api_ids:
        return 0
    try:
        cur = db.conn.cursor()
        api_ids = [int(i) for i in api_ids]
        marks = ",".join(["%s"] * len(api_ids))
        cur.execute(
            f"DELETE FROM {TABLES['piutang_customer_detail']} "
            f"WHERE cabang_id = %s AND id NOT IN ({marks})",
            (cabang_id, *api_ids),
        )
        db.conn.commit()
        return cur.rowcount
    except Exception as e:
        if verbose:
            print(f"       -> delete orphan piutang skipped: {e}")
        return 0


def backfill_piutang_customer_data(db, cabang_id, verbose=False):
    """Lengkapi kolom cust_data_cust_* pada brighter_transaksi_piutang_customer_detail
    dari kolom `customer` berdasar customer id (cust). Sumber API /piutang_penjualan
    tidak menyertakan detail customer (field cust_data), jadi di-join manual.
    Hanya kolom yang relevan diisi; sisanya dibiarkan NULL."""
    cols = {
        "cust_data_cust_id": "id",
        "cust_data_cust_cabang_id": "cabang_id",
        "cust_data_cust_no": "kode",
        "cust_data_cust_kategori_id": "kategori_id",
        "cust_data_cust_jns_identitas": "jns_identitas",
        "cust_data_cust_no_identitas": "no_identitas",
        "cust_data_cust_nama": "nama",
        "cust_data_cust_kelamin": "kelamin",
        "cust_data_cust_alamat": "alamat",
        "cust_data_cust_hp": "notelp",
        "cust_data_cust_email": "email",
        "cust_data_cust_tgllahir": "tanggal_lahir",
        "cust_data_cust_keterangan": "keterangan",
        "cust_data_cust_npwp": "npwp",
        "cust_data_cust_aktif": "aktif",
    }
    sets = ", ".join(f"`{k}` = COALESCE(`{k}`, c.`{kk}`)" for k, kk in cols.items())
    sql = (
        f"UPDATE brighter_transaksi_piutang_customer_detail d "
        f"JOIN customer c ON c.id = d.cust "
        f"SET {sets} "
        f"WHERE d.cabang_id = %s AND d.cust_data_cust_nama IS NULL"
    )
    try:
        cur = db.conn.cursor()
        n = cur.execute(sql, (cabang_id,))
        db.conn.commit()
        if verbose:
            print(f"       -> backfill customer data: {n} rows")
        return n
    except Exception as e:
        if verbose:
            print(f"       -> backfill customer data skipped: {e}")
        return 0


def _pick(rec, *keys):
    """Return the first non-None value among candidate keys."""
    for k in keys:
        v = rec.get(k)
        if v is not None:
            return v
    return None


def map_csb_supplier(rec: dict, cabang_id: int) -> dict:
    """Map a Brighter /master/supplier record into the EXISTING csb_db `supplier`
    table schema so the finance tables can relate to it (id == supplier_id).
    Only columns that Brighter actually provides are included — untouched CSB
    columns are left alone on existing rows (pure upsert, no truncate)."""
    ts = rec.get("timestamp_data") or {}
    sup_id = _pick(rec, "supplier_id", "id", "supp_id")
    nama = _pick(rec, "supplier_nama", "supp_nama") or ""
    kode = _pick(rec, "supplier_kode", "supp_kode") or ""
    aktif_raw = _pick(rec, "supplier_aktif", "supp_aktif", "aktif")
    aktif = 1 if str(aktif_raw).lower() in ("aktif", "1", "true", "yes") else 0
    # uuid wajib NOT NULL — dibuat deterministik dari supplier id
    uuid_val = str(hashlib.md5(f"supplier-{sup_id}".encode()).hexdigest())
    uuid_val = f"{uuid_val[:8]}-{uuid_val[8:12]}-{uuid_val[12:16]}-{uuid_val[16:20]}-{uuid_val[20:32]}"

    mapped = {
        "id": sup_id,
        "uuid": uuid_val,
        "kode": kode or str(sup_id),
        "nama": nama or f"Supplier {sup_id}",
        "aktif": aktif,
        "cabang_id": _pick(rec, "supplier_cabang_id", "cabang_id") or cabang_id,
        "keterangan": _pick(rec, "supplier_keterangan", "supp_keterangan"),
        "alamat": _pick(rec, "supplier_alamat", "supp_alamat"),
        "notelp": _pick(rec, "supplier_notelp", "supp_notelp", "supplier_telp", "supplier_no_telp"),
        "email": _pick(rec, "supplier_email", "supp_email"),
        "npwp": _pick(rec, "supplier_npwp", "supp_npwp"),
        "nama_kontak": _pick(rec, "supplier_kontak", "supp_nama_kontak", "supplier_nama_kontak"),
        "notelp_kontak": _pick(rec, "supplier_kontak_notelp", "supp_notelp_kontak"),
        "foto_path": _pick(rec, "supplier_foto", "supp_foto", "foto_path"),
        "created_by": ts.get("created_by"),
        "created_at": ts.get("created_at"),
        "updated_by": ts.get("updated_by"),
        "updated_at": ts.get("updated_at"),
        "revised": ts.get("revised"),
    }
    return {k: v for k, v in mapped.items() if v is not None}


def _ensure_supplier_col(db, col: str, coltype: str):
    """Add a column to `supplier` if it doesn't exist yet."""
    cur = db.conn.cursor()
    cur.execute("SHOW COLUMNS FROM `supplier` LIKE %s", (col,))
    if not cur.fetchone():
        cur.execute(f"ALTER TABLE `supplier` ADD COLUMN `{col}` {coltype} NULL")


def upsert_csb_supplier(db, records: list[dict], rep_cabang_id: int, mapping_cabang_ids: list[int]) -> int:
    """Upsert master supplier ke tabel `supplier` csb_db (id PK, TANPA truncate).

    Desain (sesuai kebutuhan):
    - Satu supplier = SATU record; kolom `cabang_id` selalu `1`.
    - Kolom tambahan `cabang_id_ref_bright` = cabang referensi asal dari Brighter
      (cabang server tempat supplier ini ditarik).
    - Cabang-cabang lain tempat supplier ini berlaku dimasukkan ke tabel
      `authenticated_user_cabang` (mapping user supplier -> cabang), sehingga tidak
      ada tumpang tindih: 1 supplier saja, akses cabang diatur lewat mapping itu.
    - `authenticated_user_id` dipertahankan untuk supplier yang sudah punya; dibuat
      baru (account_type='supplier') untuk yang belum.
    """
    if not records:
        return 0

    _ensure_supplier_col(db, "cabang_id_ref_bright", "INT")

    auth_cols = ["id", "uuid", "kode", "nama", "aktif", "cabang_id", "cabang_id_ref_bright",
                 "authenticated_user_id", "keterangan", "alamat", "notelp", "email", "npwp",
                 "nama_kontak", "notelp_kontak", "foto_path", "created_by", "created_at",
                 "updated_by", "updated_at", "revised"]
    upd_cols = [c for c in auth_cols if c not in ("id", "authenticated_user_id")]
    col_names = ", ".join(f"`{c}`" for c in auth_cols)
    placeholders = ", ".join(["%s"] * len(auth_cols))
    update_clause = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in upd_cols)
    supplier_sql = (
        f"INSERT INTO `{SUPPLIER_TABLE}` ({col_names}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {update_clause}"
    )
    aub_sql = (
        "INSERT INTO `authenticated_user_cabang` "
        "(`authenticated_user_id`, `cabang_id`, `is_default`, `assigned_at`) "
        "VALUES (%s, %s, 0, CURRENT_TIMESTAMP) "
        "ON DUPLICATE KEY UPDATE `updated_at` = CURRENT_TIMESTAMP"
    )

    cur = db.conn.cursor()
    cur.execute("SELECT `id`, `authenticated_user_id` FROM `supplier`")
    existing = {r["id"]: r["authenticated_user_id"] for r in cur.fetchall()}
    # username dikunci lowercase karena index unik MySQL case-insensitive
    cur.execute("SELECT LOWER(`username`) AS u, `id` FROM `authenticated_users`")
    uname_to_id = {r["u"]: r["id"] for r in cur.fetchall() if r["u"]}

    def _user_uuid(seed):
        h = hashlib.md5(seed.encode()).hexdigest()
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    def _reserve_supplier_user(cur, uname_to_id, base_username, nama):
        """Create a SUPPLIER-authenticated user whose username is GUARANTEED unique,
        so no two suppliers ever share one user (protects `supplier.authenticated_user_id`
        UNIQUE and prevents duplicate account_rows)."""
        suffix = 0
        while True:
            username = base_username if suffix == 0 else f"{base_username}{suffix}"
            key = username.lower()
            if key in uname_to_id:
                # username sudah dipakai supplier/user lain → jangan di-reuse,
                # coba varian berikutnya agar tiap supplier punya user sendiri
                suffix += 1
                continue
            try:
                cur.execute(
                    "INSERT INTO `authenticated_users` "
                    "(`uuid`, `username`, `name`, `email`, `password`, `account_type`, `status`) "
                    "VALUES (%s, %s, %s, %s, %s, 'supplier', 'active')",
                    (
                        _user_uuid(f"user-{username}"),
                        username,
                        nama or username,
                        f"{key}@brighter.supplier",
                        DUMMY_PASSWORD_HASH,
                    ),
                )
                uname_to_id[key] = cur.lastrowid
                return cur.lastrowid
            except pymysql.err.IntegrityError:
                # kalah race: username sempat diambil thread/txn lain → catat lalu
                # putar ke varian berikutnya (TANPA di-reuse/dipakai bersama supplier lain)
                cur.execute(
                    "SELECT `id` FROM `authenticated_users` WHERE LOWER(`username`)=%s LIMIT 1",
                    (key,),
                )
                row = cur.fetchone()
                if row:
                    uname_to_id[key] = row["id"]
                suffix += 1

    batch = []
    cnt = 0
    COMMIT_EVERY = 200
    for rec in records:
        try:
            sid_key = int(rec["id"])
        except (TypeError, ValueError):
            sid_key = rec["id"]
        auth_id = existing.get(sid_key)
        if auth_id is None:
            base_username = (rec.get("kode") or str(rec["id"])).strip()
            auth_id = _reserve_supplier_user(cur, uname_to_id, base_username, rec.get("nama"))
        rec = dict(rec)
        rec["authenticated_user_id"] = auth_id
        # 1 record per supplier: cabang_id selalu 1, referensi asal dari server ini
        rec["cabang_id"] = 1
        rec["cabang_id_ref_bright"] = rep_cabang_id
        # update memori supaya id/api yang sama di batch yang sama tidak buat user dobel
        existing[sid_key] = auth_id
        batch.append([rec.get(c) for c in auth_cols])

        # mapping: user supplier berlaku di cabang-cabang dalam server yang sama
        for cid in mapping_cabang_ids:
            cur.execute(aub_sql, (auth_id, cid))
        cnt += 1

        # commit bertahap supaya transaksi tidak terlalu panjang (hindari lock timeout)
        if len(batch) >= COMMIT_EVERY:
            cur.executemany(supplier_sql, batch)
            db.conn.commit()
            batch = []

    if batch:
        cur.executemany(supplier_sql, batch)
    db.conn.commit()
    return cnt


def discover_cabangs(config, auth, verbose=False):
    """Discover all active cabangs directly from the API (/master/cabang).
    Returns a list of cabang_id ints — no DB mirror required."""
    client = httpx.Client(base_url=config.base_url, timeout=config.request_timeout)
    cabang_ids = []
    page = 1
    while True:
        auth.ensure_token()
        time.sleep(config.request_delay)
        resp = client.get(
            "/master/cabang",
            params={"page": str(page), "results_per_page": "100", "cabang_aktif": "Aktif"},
            headers=auth.get_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("data") or []
        if not batch:
            break
        for rec in batch:
            cid = rec.get("cabang_id")
            if cid is not None:
                cabang_ids.append(int(cid))
        total_pages = (data.get("paging") or {}).get("total_pages")
        if total_pages and page >= int(total_pages):
            break
        if page * 100 >= (data.get("total") or 0):
            break
        page += 1
    client.close()
    return sorted(set(cabang_ids))


def load_cabang_urls(db):
    """Load {cabang_id: url_api} for active cabangs from csb_db `cabang` table.
    Setiap cabang punya server API (Brighter) yang berbeda, jadi base_url harus
    diarahkan per cabang melalui kolom url_api."""
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
        description="Sync Sync Master Supplier & Transaksi Keuangan (Hutang & Piutang)"
    )
    parser.add_argument(
        "-e", "--env", action="store_true",
        help="Load configuration from environment variables (BRIGHTER_*)",
    )
    parser.add_argument(
        "--cabang-ids",
        help="Comma-separated cabang IDs to sync (e.g. 1,6). Defaults to ALL active cabangs discovered from the API.",
    )
    parser.add_argument(
        "--workers", type=int, default=5,
        help="ThreadPool workers for child (detail/foto) fetch (default: 5)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable verbose debugging logs",
    )
    args = parser.parse_args()

    default_cfg = Config.from_env()

    # Initialize Auth & Database (csb_db)
    db = DatabaseManager(default_cfg, target_db="csb")
    db.connect()
    # tutup koneksi pada akhir run (termasuk saat Ctrl+C / crash) supaya transaksi
    # yang belum commit di-rollback MySQL dan TIDAK meninggalkan lock di server
    def _close_safe():
        try:
            db.close()
        except Exception:
            pass
    atexit.register(_close_safe)

    # Peta url_api tiap cabang (server API Brighter per cabang)
    cabang_urls = load_cabang_urls(db)

    # Discover cabang IDs (from API by default = 1 command runs all cabangs)
    auth_default = AuthManager(default_cfg)

    # Pre-flight: pastikan API server terjangkau SEBELUM sync panjang, supaya
    # gagal cepat dengan pesan jelas (bukan menunggu retry berulang sia-sia).
    try:
        auth_default.ensure_token()
        print(f"API OK ({default_cfg.base_url})")
    except Exception as e:
        print(f"\n[Pre-flight] Tidak dapat menghubungi API Brighter ({default_cfg.base_url}).")
        print(f"  Detail: {e}")
        print("  Cek koneksi internet / VPN / status server, lalu jalankan ulang.")
        db.close()
        sys.exit(1)

    if args.cabang_ids:
        cabang_ids = [int(x.strip()) for x in args.cabang_ids.split(",") if x.strip()]
    else:
        print("Discovering active cabangs from API...")
        try:
            cabang_ids = discover_cabangs(default_cfg, auth_default, args.verbose)
        except Exception as e:
            print(f"Error discovering cabangs from API: {e}. Defaulting to [1]")
            cabang_ids = [1]
        if not cabang_ids:
            cabang_ids = [1]

    print(f"Cabangs to sync: {cabang_ids}")
    for cid in cabang_ids:
        url = cabang_urls.get(cid)
        print(f"   cabang {cid} -> base_url: {url or default_cfg.base_url}")

    # Konfigurasi + auth PER SERVER (base_url diarahkan ke url_api cabang).
    # Karena token tidak bisa lintas domain, semua cabang yang memakai URL server
    # yang sama berbagi SATU AuthManager (satu login per server, tidak spam /login).
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

    totals = {name: 0 for name in TABLES}
    supplier_total = 0

    # 0. Master Supplier -> di-upsert ke tabel `supplier` yang sudah ada di csb_db
    #    (id == supplier_id, relasi ke pelunasan hutang). Karena tiap server punya
    #    master supplier sendiri, tarik per URL server (dedup) memakai server
    #    pertama yang memakai URL tersebut.
    print("\n[0/7] Master Supplier (upsert per server, ke tabel `supplier`)...")
    server_repr = {}   # base_url -> representative cabang_id
    for cid in cabang_ids:
        url = cabang_cfg[cid].base_url
        server_repr.setdefault(url, cid)
    server_synced = 0
    for url, rep_cid in server_repr.items():
        c_cfg = cabang_cfg[rep_cid]
        c_auth = cabang_auth[rep_cid]
        # cabang-cabang yang berbagi URL server yang sama → semua jadi mapping access
        # supplier ini di `authenticated_user_cabang`
        mapping_cabang_ids = [cid for cid, u in cabang_cfg.items() if u.base_url == url]
        try:
            supp_rows = fetch_all_pages(
                c_cfg, c_auth,
                ENDPOINT_META["supplier"]["path"], {},
                dict(ENDPOINT_META["supplier"]["params"]),
                None, None, args.verbose,
            )
            supp_mapped = [map_csb_supplier(r, rep_cid) for r in supp_rows]
            supp_mapped = [r for r in supp_mapped if r.get("id") is not None]
            n = upsert_csb_supplier(db, supp_mapped, rep_cid, mapping_cabang_ids)
            server_synced += n
            supplier_total += len(supp_mapped)
            print(f"       -> {url} : {len(supp_rows)} supplier (upsert {n})")
        except Exception as e:
            print(f"       -> ERROR syncing master supplier @ {url}: {e}")
    if not server_repr:
        print("       (tidak ada cabang yang di-sync)")

    for c_id in cabang_ids:
        print(f"\n--- Cabang {c_id} ---")
        try:
            db.reconnect()
        except Exception:
            pass

        # Base URL & auth khusus cabang ini (mengikuti kolom url_api di tabel cabang)
        cfg_c = cabang_cfg[c_id]
        auth_c = cabang_auth[c_id]

        # 1. Faktur Pembelian (dokumen lawan/asal dari pelunasan hutang)
        print("  [1/7] Faktur Pembelian (header)...")
        try:
            pembelian_rows = sync_headers(cfg_c, auth_c, db, "pembelian", c_id, args.verbose)
        except Exception as e:
            print(f"       -> ERROR header faktur pembelian: {e}")
            pembelian_rows = []
        pembelian_mapped = [map_record("pembelian", r, c_id) for r in pembelian_rows]
        totals["pembelian"] += upsert_batch(db, TABLES["pembelian"], pembelian_mapped, c_id)
        print(f"       -> {len(pembelian_rows)} records")

        # 2. Header: Pelunasan Hutang
        print("  [2/7] Pelunasan Hutang (header)...")
        try:
            hutang_rows = sync_headers(cfg_c, auth_c, db, "pelunasan_hutang", c_id, args.verbose)
        except Exception as e:
            print(f"       -> ERROR header pelunasan hutang: {e}")
            hutang_rows = []
        hutang_mapped = [map_record("pelunasan_hutang", r, c_id) for r in hutang_rows]
        totals["pelunasan_hutang"] += upsert_batch(db, TABLES["pelunasan_hutang"], hutang_mapped, c_id)
        print(f"       -> {len(hutang_rows)} records")

        # 3 & 4. Child: Detail + Foto per pelunasan hutang (concurrent)
        detail_rows = []
        foto_rows = []
        if hutang_rows:
            print("  [3/7] Detail Pelunasan Hutang (concurrent)...")
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futs = {
                    ex.submit(
                        fetch_all_pages, cfg_c, auth_c,
                        ENDPOINT_META["pelunasan_hutang_detail"]["path"],
                        {"id": h["fhutang_id"]},
                        ENDPOINT_META["pelunasan_hutang_detail"]["params"],
                        None, None, args.verbose
                    ): h
                    for h in hutang_rows
                }
                for fut in as_completed(futs):
                    h = futs[fut]
                    try:
                        for r in fut.result():
                            detail_rows.append(map_record(
                                "pelunasan_hutang_detail", r, c_id,
                                extra={"fhutang_id": h["fhutang_id"]},
                            ))
                    except Exception as e:
                        print(f"    error detail for fhutang {h.get('fhutang_id')}: {e}")
            print(f"       -> {len(detail_rows)} detail records")
            totals["pelunasan_hutang_detail"] += upsert_batch(
                db, TABLES["pelunasan_hutang_detail"], detail_rows, c_id)

            print("  [4/7] Foto Pelunasan Hutang (concurrent)...")
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futs = {
                    ex.submit(
                        fetch_all_pages, cfg_c, auth_c,
                        ENDPOINT_META["pelunasan_hutang_foto"]["path"],
                        {"id": h["fhutang_id"]},
                        ENDPOINT_META["pelunasan_hutang_foto"]["params"],
                        None, None, args.verbose
                    ): h
                    for h in hutang_rows
                }
                for fut in as_completed(futs):
                    h = futs[fut]
                    try:
                        for r in fut.result():
                            foto_rows.append(map_record(
                                "pelunasan_hutang_foto", r, c_id,
                                extra={"fhutang_id": h["fhutang_id"]},
                            ))
                    except Exception as e:
                        print(f"    error foto for fhutang {h.get('fhutang_id')}: {e}")
            print(f"       -> {len(foto_rows)} foto records")
            totals["pelunasan_hutang_foto"] += upsert_batch(
                db, TABLES["pelunasan_hutang_foto"], foto_rows, c_id)

        print("  [4b/7] Reconcile Pelunasan Hutang -> Clarify legacy tables...")
        try:
            clarify_counts = sync_clarify_hutang_pelunasan(db, c_id)
            print(
                "       -> Clarify upsert "
                f"header={clarify_counts['header']}, "
                f"items={clarify_counts['item']}, "
                f"media={clarify_counts['media']} "
                "(tanpa posting kas_bank)"
            )
        except Exception as e:
            print(f"       -> ERROR reconcile Clarify pelunasan hutang: {e}")

        # 5. Header: Pelunasan Piutang
        print("  [5/7] Pelunasan Piutang (header)...")
        try:
            piutang_rows = sync_headers(cfg_c, auth_c, db, "pelunasan_piutang", c_id, args.verbose)
        except Exception as e:
            print(f"       -> ERROR header pelunasan piutang: {e}")
            piutang_rows = []
        piutang_mapped = [map_record("pelunasan_piutang", r, c_id) for r in piutang_rows]
        totals["pelunasan_piutang"] += upsert_batch(db, TABLES["pelunasan_piutang"], piutang_mapped, c_id)
        print(f"       -> {len(piutang_rows)} records")

        # 6/7. Piutang Customer: tarik langsung seluruh faktur piutang per cabang
        #      dari /transaksi/piutang_penjualan (filter stat_dok=Tertutup). Semua
        #      periode faktur diambil agar angka cocok dgn laporan Brighter
        #      mode "semua periode" (tanpa filter tanggal faktur).
        print("  [6/7] Piutang Customer Detail (per cabang, stat_dok=Tertutup, semua periode)...")
        try:
            piutang_detail_rows = sync_headers(cfg_c, auth_c, db, "piutang_customer_detail", c_id, args.verbose)
        except Exception as e:
            print(f"       -> ERROR piutang customer detail: {e}")
            piutang_detail_rows = []
        piutang_detail_mapped = [map_record("piutang_customer_detail", r, c_id) for r in piutang_detail_rows]
        totals["piutang_customer_detail"] += len(piutang_detail_mapped)
        upsert_batch(db, TABLES["piutang_customer_detail"], piutang_detail_mapped, c_id)
        # Hapus record yang TIDAK ADA di API lagi (faktur dibatalkan/dihapus di
        # aplikasi) agar tabel selalu sejalan dgn sumber. Identifikasi via lpiutang_id.
        api_ids = [r.get("lpiutang_id") for r in piutang_detail_rows if r.get("lpiutang_id") is not None]
        _del = _delete_orphan_piutang(db, c_id, api_ids, args.verbose)
        if _del:
            print(f"       -> deleted {_del} orphan piutang (tidak ada di API)")
        print(f"       -> {len(piutang_detail_rows)} records")
        backfill_piutang_customer_data(db, c_id, args.verbose)

        # Backfill nama pelanggan dari API Brighter bila masih ada yang tanpa nama
        # (fallback lokal `customer` tak mencakup pelanggan baru belum tersync).
        _missing = _count_piutang_tanpa_nama(db, c_id)
        if _missing:
            print(f"       -> {_missing} record tanpa nama - jalankan backfill nama...")
            try:
                subprocess.run(
                    [
                        sys.executable,
                        "backfill_piutang_cust_nama.py",
                        "--env", "--cabang-ids", str(c_id),
                    ],
                    check=True,
                )
            except Exception as e:
                print(f"       -> backfill nama gagal: {e}")
        else:
            print("       -> semua piutang sudah punya nama")

        print(f"Done Cabang {c_id}")

    db.close()

    print("\n" + "=" * 50)
    print("FINANCE SYNC COMPLETE")
    print(f"  {'supplier':30s} -> {supplier_total} records (upsert per server)")
    for name, table in TABLES.items():
        print(f"  {name:30s} -> {totals[name]} records")
    print("=" * 50)

if __name__ == "__main__":
    main()
