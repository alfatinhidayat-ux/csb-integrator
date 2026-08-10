"""Fetch pembelian detail per dokumen dari Brighter API, simpan ke tabel staging
`brighter_persediaan_pembelian_detail`, dan hasilkan laporan matching produk
terhadap csb_db.produk.

Endpoint: GET /persediaan/pembelian/{id}/pembelian_detail_produk
           (pembelian_det_produk_data=true untuk mendapatkan produk_kode/nama)

Cabang 1 (Kobisonta) & 5 (Kairatu), status Tertutup, tanggal 2026-01-01..2026-08-31.

Usage:
    python cek_produk_pembelian.py            # fetch semua + simpan + laporan
    python cek_produk_pembelian.py --limit 5  # uji coba
"""

import argparse
import csv
import os
import random
import sys
import time
from datetime import datetime

import httpx
import pymysql

sys.path.insert(0, os.getcwd())

from config import Config
from auth import AuthManager
from db import DatabaseManager

TANGGAL_AWAL = "2026-01-01"
TANGGAL_AKHIR = datetime.now().strftime("%Y-%m-%d")  # dinamis: sampai hari ini
STATUS_DOK = "Tertutup"

DETAIL_PATH = "/persediaan/pembelian/{pid}/pembelian_detail_produk"
DETAIL_TABLE = "brighter_persediaan_pembelian_detail"
REPORT_CSV = "laporan_cek_produk_pembelian.csv"
FAILED_CSV = "laporan_cek_produk_pembelian_failed.csv"

# kolom yang dipertahankan dari respons detail API
DETAIL_KEEP = [
    "pembelian_det_id", "pembelian_det_master_id", "pembelian_det_btitipan_id",
    "pembelian_det_btitipan_det_id", "pembelian_det_btitipan_nobukti",
    "pembelian_det_no_container", "pembelian_det_produk_id", "pembelian_det_satuan_id",
    "pembelian_det_produk_harga", "pembelian_det_diskon", "pembelian_det_diskon_rp",
    "pembelian_det_qty_beli", "pembelian_det_qty_diterima",
    "pembelian_det_subtotal_rp", "pembelian_det_subtotal_net_rp",
    "pembelian_det_keterangan",
]


def _request_with_retry(client, path, params, headers, cfg, verbose=False):
    """GET dengan retry + backoff untuk error sementara (5xx / 429 / network).

    Tanpa retry ini, satu 502 sesaat dari server membuat seluruh dokumen
    dilewati dan datanya hilang dari staging. Sekarang tiap error sementara
    dicoba ulang sampai `max_tries` dengan jeda membesar agar server pulih.
    """
    max_tries = max(cfg.max_retries + 2, 5)
    resp = None
    for attempt in range(max_tries):
        try:
            resp = client.get(path, params=params, headers=headers)
        except (httpx.TransportError, httpx.TimeoutException):
            resp = None
        if resp is not None and resp.status_code < 400:
            return resp.json()
        if resp is None or resp.status_code in (429,) or resp.status_code >= 500:
            if attempt + 1 < max_tries:
                sleep_s = min(2.0 ** attempt * 3, 20.0) + random.uniform(0, 0.5)
                code = resp.status_code if resp is not None else "network"
                if verbose:
                    print(f"      -> retry {attempt + 1}/{max_tries} ({code}) {path} in {sleep_s:.1f}s")
                time.sleep(sleep_s)
                continue
        break
    if resp is None:
        raise httpx.TransportError(f"network failure fetching {path}")
    resp.raise_for_status()
    return resp.json()


def fetch_paginated(client, path, params, headers, cfg, verbose=False):
    page = 1
    rows = []
    while True:
        p = dict(params)
        p["page"] = str(page)
        p["results_per_page"] = str(cfg.results_per_page)
        d = _request_with_retry(client, path, p, headers, cfg, verbose)
        batch = d.get("data") or []
        rows.extend(batch)
        paging = d.get("paging") or {}
        total_pages = int(paging.get("total_pages", 0) or 0)
        if page >= total_pages:
            break
        page += 1
    return rows


def fetch_dokumen_det(client, cfg, headers_auth, h, verbose=False):
    """Fetch semua halaman detail produk untuk satu dokumen pembelian."""
    pid = int(h["id"])
    dets = fetch_paginated(
        client,
        DETAIL_PATH.format(pid=pid),
        {"pembelian_det_produk_data": "true", "pembelian_cabang_id": str(h["cabang_id"])},
        headers_auth,
        cfg,
        verbose,
    )
    return pid, dets


def process_dokumen(h, dets, by_id, mismatch_id, mismatch_name, rows_csv, rows_db, no_write):
    """Proses baris detail satu dokumen -> rows_csv/rows_db + hitungan qty.
    Return (qty_detail, missing_produk_data)."""
    qty = 0.0
    missing = 0
    for d in dets:
        qty += float(d.get("pembelian_det_qty_beli") or 0)
        prod = d.get("pembelian_det_produk_data") or {}
        p_id = prod.get("produk_id")
        p_kode = (prod.get("produk_kode") or "").strip()
        p_nama = (prod.get("produk_nama") or "").strip()
        p_sku = (prod.get("produk_sku") or "").strip()

        if not p_id and not p_kode and not p_nama:
            missing += 1

        row = by_id.get(int(p_id)) if p_id is not None else None
        status = "OK"
        detail_note = ""
        db_kode, db_nama = p_kode, p_nama
        db_afkir = ""
        if row is None:
            status = "MISSING_ID"
            detail_note = "produk_id tidak ada di csb_db.produk"
            mismatch_id.setdefault((p_id, p_kode, p_nama), 0)
            mismatch_id[(p_id, p_kode, p_nama)] += 1
        else:
            db_kode = row["produk_kode"] or ""
            db_nama = row["produk_nama"] or ""
            db_afkir = "AFKIR" if (row["is_afkir"] or row["produk_jenis_afkir"]) else ""
            if (db_kode or "").strip() != p_kode or (db_nama or "").strip() != p_nama:
                status = "DIFF_NAME"
                detail_note = "kode/nama produk berbeda dengan csb_db"
                mismatch_name.setdefault(
                    (p_id, p_kode, p_nama, db_kode, db_nama, db_afkir), []
                ).append((h["nobukti"], h["cabang_id"]))

        rows_csv.append({
            "cabang": h["cabang_id"],
            "nobukti": h["nobukti"],
            "det_id": d.get("pembelian_det_id"),
            "produk_id": p_id,
            "produk_kode_api": p_kode,
            "produk_nama_api": p_nama,
            "produk_kode_db": db_kode,
            "produk_nama_db": db_nama,
            "afkir": db_afkir,
            "qty_beli": d.get("pembelian_det_qty_beli"),
            "qty_diterima": d.get("pembelian_det_qty_diterima"),
            "subtotal_rp": d.get("pembelian_det_subtotal_rp"),
            "subtotal_net_rp": d.get("pembelian_det_subtotal_net_rp"),
            "status": status,
            "catatan": detail_note,
        })

        if not no_write:
            row_db = {k: d.get(k) for k in DETAIL_KEEP}
            row_db["produk_kode"] = p_kode
            row_db["produk_nama"] = p_nama
            row_db["produk_sku"] = p_sku
            row_db["cabang_id"] = h["cabang_id"]
            row_db["nobukti"] = h["nobukti"]
            rows_db.append(row_db)

    return qty, missing


def load_headers(conn, cabang_ids: tuple[int, ...]):
    with conn.cursor() as cur:
        ph = ",".join(["%s"] * len(cabang_ids))
        cur.execute(
            f"""
            SELECT id, cabang_id, nobukti, total_qty_produk, total_biaya_rp,
                   total_net_rp, total_bayar_rp, total_sisa_rp,
                   supplier_data_supplier_id, supplier_data_supplier_kode,
                   supplier_data_supplier_nama
            FROM brighter_persediaan_pembelian
            WHERE cabang_id IN ({ph}) AND status_dok=%s
              AND tanggal>=%s AND tanggal<=%s
            ORDER BY cabang_id, id
            """,
            (*cabang_ids, STATUS_DOK, TANGGAL_AWAL, TANGGAL_AKHIR),
        )
        return cur.fetchall()


def load_produk(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT produk_id, produk_kode, produk_nama, is_afkir, produk_jenis_afkir FROM produk")
        by_id = {}
        by_kode = {}
        by_nama = {}
        for r in cur.fetchall():
            by_id[int(r["produk_id"])] = r
            k = (r["produk_kode"] or "").strip().lower()
            if k:
                by_kode[k] = r
            n = (r["produk_nama"] or "").strip().lower()
            if n:
                by_nama.setdefault(n, r)
        return by_id, by_kode, by_nama


def ensure_detail_table(conn):
    with conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {DETAIL_TABLE} (
                pembelian_det_id BIGINT,
                pembelian_det_master_id BIGINT,
                pembelian_det_btitipan_id BIGINT,
                pembelian_det_btitipan_det_id BIGINT,
                pembelian_det_btitipan_nobukti VARCHAR(100),
                pembelian_det_no_container VARCHAR(50),
                pembelian_det_produk_id BIGINT,
                pembelian_det_satuan_id BIGINT,
                pembelian_det_produk_harga DECIMAL(18,4),
                pembelian_det_diskon DECIMAL(12,2),
                pembelian_det_diskon_rp DECIMAL(18,2),
                pembelian_det_qty_beli DECIMAL(16,4),
                pembelian_det_qty_diterima DECIMAL(16,4),
                pembelian_det_subtotal_rp DECIMAL(18,2),
                pembelian_det_subtotal_net_rp DECIMAL(18,2),
                pembelian_det_keterangan TEXT,
                produk_kode VARCHAR(100),
                produk_nama VARCHAR(255),
                produk_sku VARCHAR(100),
                cabang_id INT,
                nobukti VARCHAR(100),
                PRIMARY KEY (pembelian_det_id)
            ) ENGINE=InnoDB
            """
        )
        conn.commit()


def main():
    parser = argparse.ArgumentParser(description="Fetch detail pembelian + cek matching produk")
    parser.add_argument("--limit", type=int, default=0, help="Batasi jumlah dokumen (0 = semua)")
    parser.add_argument("--no-write", action="store_true", help="Jangan simpan ke DB (hanya laporan)")
    parser.add_argument("--cabang-ids", default="1,5",
                        help="Comma-separated cabang IDs; default 1,5")
    parser.add_argument("--retry-failed", metavar="CSV", default=None,
                        help="Proses ulang HANYA dokumen yang gagal pada run sebelumnya "
                             "(file laporan_cek_produk_pembelian_failed.csv)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    cabang_ids = tuple(int(x.strip()) for x in args.cabang_ids.split(",") if x.strip())

    cfg = Config.from_env()
    auth = AuthManager(cfg)
    auth.ensure_token()

    db = DatabaseManager(cfg, target_db="csb")
    db.connect()
    conn = db.conn

    headers = load_headers(conn, cabang_ids)
    by_id, by_kode, by_nama = load_produk(conn)
    if not args.no_write or args.retry_failed:
        ensure_detail_table(conn)
    print(f"Dokumen staging (cab {','.join(str(c) for c in cabang_ids)}, {STATUS_DOK}, {TANGGAL_AWAL}..{TANGGAL_AKHIR}): {len(headers)}")
    print(f"Produk di csb_db.produk: {len(by_id)}")

    client = httpx.Client(base_url=cfg.base_url, timeout=cfg.request_timeout)

    if args.retry_failed:
        retry_set = set()
        with open(args.retry_failed, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    retry_set.add((int(row["cabang_id"]), int(row["id"])))
                except (KeyError, ValueError):
                    continue
        headers = [h for h in headers if (int(h["cabang_id"]), int(h["id"])) in retry_set]
        print(f"Mode retry-failed: {len(headers)} dokumen gagal akan diproses ulang")

    processed = 0
    qty_detail = 0.0
    qty_header = 0.0
    rows_csv = []
    rows_db = []
    mismatch_id = {}
    mismatch_name = {}
    missing_produk_data = 0
    still_failed = []
    retried_ok = 0

    def fetch_and_process(h, delay):
        """Fetch detail satu dokumen + proses barisnya. Return (ok, error_msg)."""
        nonlocal qty_detail, qty_header, missing_produk_data
        time.sleep(delay)
        auth.ensure_token()
        headers_auth = auth.get_headers()
        try:
            _, dets = fetch_dokumen_det(client, cfg, headers_auth, h, args.verbose)
        except Exception as e:
            print(f"  ERROR {h['cabang_id']}:{h['id']} {h['nobukti']} -> {e}")
            return False, str(e)
        qty_header += float(h["total_qty_produk"] or 0)
        q, m = process_dokumen(
            h, dets, by_id, mismatch_id, mismatch_name,
            rows_csv, rows_db, args.no_write and not args.retry_failed,
        )
        qty_detail += q
        missing_produk_data += m
        return True, None

    for h in headers:
        if args.limit and processed >= args.limit:
            break
        processed += 1
        ok, err = fetch_and_process(h, cfg.request_delay)
        if not ok:
            still_failed.append((h, err))
        elif args.verbose and processed % 100 == 0:
            print(f"  [{processed}] cab {h['cabang_id']} {h['nobukti']} -> diproses")

    # Pass 2: coba ulang dokumen yang gagal sekali lagi setelah jeda, server
    # biasanya sudah pulih dari 502 sehingga data bisa dilengkapi di run yang sama.
    if still_failed:
        print(f"\n--- Retry {len(still_failed)} dokumen gagal (pass 2, jeda + backoff) ---")
        time.sleep(5)
        remaining = []
        for h, _ in still_failed:
            ok, err = fetch_and_process(h, cfg.request_delay * 5)
            if ok:
                retried_ok += 1
                print(f"  OK retry cab {h['cabang_id']}:{h['id']} {h['nobukti']} -> pulih")
            else:
                remaining.append((h, err))
        still_failed = remaining

    client.close()

    # tulis ke DB bila diminta. Normal: replace per cabang (delete semua lalu insert).
    # Mode retry-failed: delete HANYA nobukti yang diproses ulang, lalu insert —
    # supaya baris hasil run sebelumnya (yang sudah sukses) tidak ikut terhapus.
    if rows_db:
        with conn.cursor() as cur:
            if args.retry_failed:
                pairs = [(h["cabang_id"], h["nobukti"]) for h in headers]
                conditions = ",".join(["(%s, %s)"] * len(pairs))
                cur.execute(
                    f"DELETE FROM {DETAIL_TABLE} WHERE (cabang_id, nobukti) IN ({conditions})",
                    [v for p in pairs for v in p],
                )
            else:
                ph = ",".join(["%s"] * len(cabang_ids))
                cur.execute(f"DELETE FROM {DETAIL_TABLE} WHERE cabang_id IN ({ph})", cabang_ids)
        cols = list(rows_db[0].keys())
        placeholders = ",".join(["%s"] * len(cols))
        colnames = ",".join(f"`{c}`" for c in cols)
        sql = f"INSERT INTO {DETAIL_TABLE} ({colnames}) VALUES ({placeholders})"
        with conn.cursor() as cur:
            for r in rows_db:
                cur.execute(sql, tuple(r.get(c) for c in cols))
        conn.commit()
        print(f"[DB] {DETAIL_TABLE}: {len(rows_db)} baris detail tersimpan")

    # laporan CSV (mode retry-failed tidak menimpa laporan lengkap sebelumnya)
    if not args.retry_failed:
        with open(REPORT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows_csv[0].keys()) if rows_csv else [])
            if rows_csv:
                writer.writeheader()
                writer.writerows(rows_csv)
        print(f"[CSV] laporan ditulis: {REPORT_CSV} ({len(rows_csv)} baris)")

    # dokumen yang masih gagal disimpan untuk run berikutnya via --retry-failed
    if still_failed:
        with open(FAILED_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["cabang_id", "id", "nobukti", "error"])
            for h, err in still_failed:
                writer.writerow([h["cabang_id"], h["id"], h["nobukti"], err])
        print(f"[CSV] dokumen gagal ditulis: {FAILED_CSV} ({len(still_failed)})")
    elif os.path.exists(FAILED_CSV):
        os.remove(FAILED_CSV)

    # ringkasan
    print("\n" + "=" * 60)
    print(f"Dokumen diproses : {processed} (+ {retried_ok} pulih di pass 2)")
    print(f"Baris detail     : {len(rows_csv)}")
    print(f"Qty detail       : {qty_detail:,.4f}")
    print(f"Qty header       : {qty_header:,.4f}")
    print(f"Selisih qty      : {qty_detail - qty_header:,.4f}")
    print(f"Produk unik      : {len(set((r['produk_id'], r['produk_kode_api'], r['produk_nama_api']) for r in rows_csv))}")
    print(f"Detail tanpa produk_data : {missing_produk_data}")
    print(f"MISSING_ID (tidak di csb_db.produk) : {len(mismatch_id)} produk unik")
    for (p_id, k, n), c in sorted(mismatch_id.items(), key=lambda x: -x[1])[:25]:
        print(f"   id={p_id} kode='{k}' nama='{n}' -> {c} baris")
    print(f"DIFF_NAME (kode/nama beda) : {len(mismatch_name)} produk unik")
    for (p_id, k, n, dk, dn, af), docs in sorted(mismatch_name.items(), key=lambda x: x[0]):
        contoh = ", ".join(f"{nb}({cab})" for nb, cab in docs[:3])
        print(f"   id={p_id} api:'{k}'/'{n}' db:'{dk}'/'{dn}' {af} -> {len(docs)} baris [{contoh}]")
    if still_failed:
        print(f"ERROR fetch: {len(still_failed)} (tertulis di {FAILED_CSV})")
        for h, err in still_failed[:10]:
            print(f"   {h['nobukti']} ({h['id']}) -> {err}")

    db.conn.close()


if __name__ == "__main__":
    main()
