"""Tarik data dashboard Brighter (7 endpoint laporan) per bulan ke tabel baru di csb_db.

Rentang default: 2026-01-01 s.d. hari ini. Untuk tiap (cabang, bulan) endpoint
di-fetch dengan tanggal_awal = awal bulan dan tanggal_akhir = akhir bulan.

Endpoint yang diolah (semua diverifikasi punya data):
  /laporan/dashboard/rekap_dashboard                  -> dash_rekap_dashboard
  /laporan/dashboard/penerimaan_per_user              -> dash_penerimaan_per_user
  /laporan/dashboard/detail_penjualan                 -> dash_detail_penjualan
  /laporan/dashboard/detail_piutang                   -> dash_detail_piutang
  /laporan/dashboard/detail_hutang                    -> dash_detail_hutang
  /laporan/dashboard/detail_deposit_pelanggan         -> dash_detail_deposit_pelanggan
  /laporan/dashboard/detail_retur_penjualan           -> dash_detail_retur_penjualan

detail_kasbank dan detail_retur_pembelian tidak pernah mengembalikan data pada
semua cabang yang dicoba (404) -> tidak diikutkan.

Pemakaian:
    python sync_dash_api.py --env
    python sync_dash_api.py --env --cabang-ids 1,5
    python sync_dash_api.py --env --tanggal-awal 2026-01-01 --tanggal-akhir 2026-03-31
    python sync_dash_api.py --env --bulan 2026-03 2026-05
"""

import argparse
import atexit
import dataclasses
import json
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
from sync_saldo_kas_harian import load_cabang_urls

DASH_PATH = "/laporan/dashboard/"


def _to_date(value):
    if value is None:
        return None
    s = str(value).strip()
    return (s.split("T")[0] or None) if s else None


# ── Spesifikasi endpoint per tabel ────────────────────────────────────────────

SPEC = [
    {
        "name": "rekap_dashboard",
        "path": DASH_PATH + "rekap_dashboard",
        "table": "dash_rekap_dashboard",
        "unique": ["cabang_id", "bulan", "jenis_dashboard"],
        "fields": ["cabang_id", "bulan", "jenis_dashboard", "jenis_dashboard_str", "rekap_json"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS `dash_rekap_dashboard` (
                `cabang_id` INT NOT NULL,
                `bulan` DATE NOT NULL,
                `jenis_dashboard` VARCHAR(100) NOT NULL,
                `jenis_dashboard_str` VARCHAR(100) NULL,
                `rekap_json` TEXT NULL,
                `synced_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (`cabang_id`, `bulan`, `jenis_dashboard`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        "flatten": lambda cabang_id, bulan, item: {
            "cabang_id": cabang_id,
            "bulan": bulan,
            "jenis_dashboard": item.get("jenis_dashboard"),
            "jenis_dashboard_str": item.get("jenis_dashboard_str"),
            "rekap_json": json.dumps(item.get("rekap") or {}, ensure_ascii=False),
        },
    },
    {
        "name": "penerimaan_per_user",
        "path": DASH_PATH + "penerimaan_per_user",
        "table": "dash_penerimaan_per_user",
        "unique": ["cabang_id", "bulan", "username", "jenis_dashboard"],
        "fields": ["cabang_id", "bulan", "username", "jenis_dashboard",
                   "jenis_dashboard_str", "rekap_json"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS `dash_penerimaan_per_user` (
                `cabang_id` INT NOT NULL,
                `bulan` DATE NOT NULL,
                `username` VARCHAR(100) NOT NULL,
                `jenis_dashboard` VARCHAR(100) NOT NULL,
                `jenis_dashboard_str` VARCHAR(100) NULL,
                `rekap_json` TEXT NULL,
                `synced_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (`cabang_id`, `bulan`, `username`, `jenis_dashboard`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        "flatten": lambda cabang_id, bulan, item: _flatten_pu(cabang_id, bulan, item),
    },
    {
        "name": "detail_penjualan",
        "path": DASH_PATH + "detail_penjualan",
        "table": "dash_detail_penjualan",
        "unique": ["cabang_id", "bulan", "jproduk_nobukti"],
        "fields": ["cabang_id", "bulan", "jproduk_nobukti", "jproduk_tanggal", "cust_id",
                   "cust_no", "cust_nama", "qty_item_produk", "total_biaya", "tunai_rp",
                   "transfer_rp", "card_rp", "qris_barcode_rp", "qris_scan_rp",
                   "wallet_rp", "piutang_rp"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS `dash_detail_penjualan` (
                `cabang_id` INT NOT NULL,
                `bulan` DATE NOT NULL,
                `jproduk_nobukti` VARCHAR(100) NOT NULL,
                `jproduk_tanggal` DATE NULL,
                `cust_id` BIGINT NULL,
                `cust_no` VARCHAR(50) NULL,
                `cust_nama` VARCHAR(255) NULL,
                `qty_item_produk` DOUBLE NULL,
                `total_biaya` DOUBLE NULL,
                `tunai_rp` DOUBLE NULL,
                `transfer_rp` DOUBLE NULL,
                `card_rp` DOUBLE NULL,
                `qris_barcode_rp` DOUBLE NULL,
                `qris_scan_rp` DOUBLE NULL,
                `wallet_rp` DOUBLE NULL,
                `piutang_rp` DOUBLE NULL,
                `synced_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (`cabang_id`, `bulan`, `jproduk_nobukti`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        "flatten": lambda cabang_id, bulan, item: {
            "cabang_id": cabang_id,
            "bulan": bulan,
            "jproduk_nobukti": item.get("jproduk_nobukti"),
            "jproduk_tanggal": _to_date(item.get("jproduk_tanggal")),
            "cust_id": item.get("cust_id"),
            "cust_no": item.get("cust_no"),
            "cust_nama": item.get("cust_nama"),
            "qty_item_produk": item.get("qty_item_produk"),
            "total_biaya": item.get("total_biaya"),
            "tunai_rp": item.get("tunai_rp"),
            "transfer_rp": item.get("transfer_rp"),
            "card_rp": item.get("card_rp"),
            "qris_barcode_rp": item.get("qris_barcode_rp"),
            "qris_scan_rp": item.get("qris_scan_rp"),
            "wallet_rp": item.get("wallet_rp"),
            "piutang_rp": item.get("piutang_rp"),
        },
    },
    {
        "name": "detail_piutang",
        "path": DASH_PATH + "detail_piutang",
        "table": "dash_detail_piutang",
        "unique": ["cabang_id", "bulan", "fpiutang_nobukti"],
        "fields": ["cabang_id", "bulan", "fpiutang_nobukti", "fpiutang_tanggal",
                   "fpiutang_cust", "cust_nama", "fpiutang_cara", "bayar"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS `dash_detail_piutang` (
                `cabang_id` INT NOT NULL,
                `bulan` DATE NOT NULL,
                `fpiutang_nobukti` VARCHAR(100) NOT NULL,
                `fpiutang_tanggal` DATE NULL,
                `fpiutang_cust` BIGINT NULL,
                `cust_nama` VARCHAR(255) NULL,
                `fpiutang_cara` VARCHAR(50) NULL,
                `bayar` DOUBLE NULL,
                `synced_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (`cabang_id`, `bulan`, `fpiutang_nobukti`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        "flatten": lambda cabang_id, bulan, item: {
            "cabang_id": cabang_id,
            "bulan": bulan,
            "fpiutang_nobukti": item.get("fpiutang_nobukti"),
            "fpiutang_tanggal": _to_date(item.get("fpiutang_tanggal")),
            "fpiutang_cust": item.get("fpiutang_cust"),
            "cust_nama": item.get("cust_nama"),
            "fpiutang_cara": item.get("fpiutang_cara"),
            "bayar": item.get("bayar"),
        },
    },
    {
        "name": "detail_hutang",
        "path": DASH_PATH + "detail_hutang",
        "table": "dash_detail_hutang",
        "unique": ["cabang_id", "bulan", "fhutang_nobukti"],
        "fields": ["cabang_id", "bulan", "fhutang_nobukti", "fhutang_tanggal",
                   "fhutang_supp", "supplier_nama", "fhutang_cara", "bayar"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS `dash_detail_hutang` (
                `cabang_id` INT NOT NULL,
                `bulan` DATE NOT NULL,
                `fhutang_nobukti` VARCHAR(100) NOT NULL,
                `fhutang_tanggal` DATE NULL,
                `fhutang_supp` BIGINT NULL,
                `supplier_nama` VARCHAR(255) NULL,
                `fhutang_cara` VARCHAR(50) NULL,
                `bayar` DOUBLE NULL,
                `synced_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (`cabang_id`, `bulan`, `fhutang_nobukti`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        "flatten": lambda cabang_id, bulan, item: {
            "cabang_id": cabang_id,
            "bulan": bulan,
            "fhutang_nobukti": item.get("fhutang_nobukti"),
            "fhutang_tanggal": _to_date(item.get("fhutang_tanggal")),
            "fhutang_supp": item.get("fhutang_supp"),
            "supplier_nama": item.get("supplier_nama"),
            "fhutang_cara": item.get("fhutang_cara"),
            "bayar": item.get("bayar"),
        },
    },
    {
        "name": "detail_deposit_pelanggan",
        "path": DASH_PATH + "detail_deposit_pelanggan",
        "table": "dash_detail_deposit_pelanggan",
        "unique": ["cabang_id", "bulan", "no_faktur"],
        "fields": ["cabang_id", "bulan", "no_faktur", "tanggal", "customer_id",
                   "cust_no", "cust_nama", "cara_bayar", "jumlah_rp"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS `dash_detail_deposit_pelanggan` (
                `cabang_id` INT NOT NULL,
                `bulan` DATE NOT NULL,
                `no_faktur` VARCHAR(100) NOT NULL,
                `tanggal` DATE NULL,
                `customer_id` BIGINT NULL,
                `cust_no` VARCHAR(50) NULL,
                `cust_nama` VARCHAR(255) NULL,
                `cara_bayar` VARCHAR(50) NULL,
                `jumlah_rp` DOUBLE NULL,
                `synced_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (`cabang_id`, `bulan`, `no_faktur`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        "flatten": lambda cabang_id, bulan, item: {
            "cabang_id": cabang_id,
            "bulan": bulan,
            "no_faktur": item.get("no_faktur"),
            "tanggal": _to_date(item.get("tanggal")),
            "customer_id": item.get("customer_id"),
            "cust_no": item.get("cust_no"),
            "cust_nama": item.get("cust_nama"),
            "cara_bayar": item.get("cara_bayar"),
            "jumlah_rp": item.get("jumlah_rp"),
        },
    },
    {
        "name": "detail_retur_penjualan",
        "path": DASH_PATH + "detail_retur_penjualan",
        "table": "dash_detail_retur_penjualan",
        "unique": ["cabang_id", "bulan", "no_bukti"],
        "fields": ["cabang_id", "bulan", "no_bukti", "tanggal", "customer_id",
                   "cust_no", "cust_nama", "cara_bayar", "total_rp", "cabang_kode",
                   "cabang_nama"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS `dash_detail_retur_penjualan` (
                `cabang_id` INT NOT NULL,
                `bulan` DATE NOT NULL,
                `no_bukti` VARCHAR(100) NOT NULL,
                `tanggal` DATE NULL,
                `customer_id` BIGINT NULL,
                `cust_no` VARCHAR(50) NULL,
                `cust_nama` VARCHAR(255) NULL,
                `cara_bayar` VARCHAR(50) NULL,
                `total_rp` DOUBLE NULL,
                `cabang_kode` VARCHAR(50) NULL,
                `cabang_nama` VARCHAR(255) NULL,
                `synced_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (`cabang_id`, `bulan`, `no_bukti`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        "flatten": lambda cabang_id, bulan, item: {
            "cabang_id": cabang_id,
            "bulan": bulan,
            "no_bukti": item.get("no_bukti"),
            "tanggal": _to_date(item.get("tanggal")),
            "customer_id": item.get("customer_id"),
            "cust_no": item.get("cust_no"),
            "cust_nama": item.get("cust_nama"),
            "cara_bayar": item.get("cara_bayar"),
            "total_rp": item.get("total_rp"),
            "cabang_kode": item.get("cabang_kode"),
            "cabang_nama": item.get("cabang_nama"),
        },
    },
]


def _halve_penjualan_kasir1(rekap):
    """Koreksi anomali sumber Brighter: endpoint penerimaan_per_user double-count
    penjualan user `Kasir1` (nilai total_rp & total_piutang_rp persis 2x nilai
    benar di pos_transactions / brighter_pos status Tertutup, lihat AGENTS.md).
    Berlaku konsisten untuk cb2 & cb7 pada semua bulan final. Seluruh komponen
    total_* ikut dibagi 2 agar JSON tetap konsisten."""
    if not isinstance(rekap, dict):
        return rekap
    out = dict(rekap)
    for key in list(out.keys()):
        if key.startswith("total_") and isinstance(out[key], (int, float)):
            out[key] = out[key] / 2.0
    return out


def _flatten_pu(cabang_id, bulan, item):
    """penerimaan_per_user: data berbentuk [{username, detail:[{jenis, rekap}]}]."""
    rows = []
    username = item.get("username")
    for det in item.get("detail") or []:
        rekap = det.get("rekap") or {}
        if username == "Kasir1" and det.get("jenis_dashboard") == "penjualan":
            rekap = _halve_penjualan_kasir1(rekap)
        rows.append({
            "cabang_id": cabang_id,
            "bulan": bulan,
            "username": username,
            "jenis_dashboard": det.get("jenis_dashboard"),
            "jenis_dashboard_str": det.get("jenis_dashboard_str"),
            "rekap_json": json.dumps(rekap, ensure_ascii=False),
        })
    return rows


# ── Helpers DB ───────────────────────────────────────────────────────────────

def ensure_tables(db, specs):
    for spec in specs:
        cur = db.conn.cursor()
        cur.execute(spec["ddl"])
    db.conn.commit()


def upsert_rows(db, table, fields, unique, rows, chunk=500):
    if not rows:
        return 0
    col_names = ", ".join(f"`{c}`" for c in fields)
    placeholders = ", ".join(["%s"] * len(fields))
    updates = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in fields if c not in unique)
    updates += ", `synced_at` = CURRENT_TIMESTAMP"
    sql = (
        f"INSERT INTO `{table}` ({col_names}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {updates}"
    )
    batch = [[r.get(c) for c in fields] for r in rows]
    cur = db.conn.cursor()
    n = 0
    for start in range(0, len(batch), chunk):
        cur.executemany(sql, batch[start:start + chunk])
        n += len(batch[start:start + chunk])
    db.conn.commit()
    return n


def delete_month_rows(db, table, cabang_id, bulan):
    cur = db.conn.cursor()
    cur.execute(f"DELETE FROM `{table}` WHERE cabang_id = %s AND bulan = %s", (cabang_id, bulan))
    db.conn.commit()


# ── Fetch API ────────────────────────────────────────────────────────────────

def fetch_dash(config, auth, path, cabang_id, tanggal_awal, tanggal_akhir, verbose=False):
    """Fetch satu endpoint dashboard untuk satu rentang. Return (rows_available, data_list).

    rows_available=True bila endpoint menjawab 200 dengan list data non-kosong.
    On 404 / data kosong -> (False, []).
    """
    client = httpx.Client(base_url=config.base_url, timeout=config.request_timeout)
    params = {
        "tanggal_awal": tanggal_awal,
        "tanggal_akhir": tanggal_akhir,
        "cabang_id": str(cabang_id),
    }
    max_tries = max(config.max_retries + 2, 5)
    resp = None
    for attempt in range(max_tries):
        auth.ensure_token()
        headers = auth.get_headers()
        if verbose:
            print(f"  -> GET {path} cb={cabang_id} {tanggal_awal}..{tanggal_akhir}")
        try:
            resp = client.get(path, params=params, headers=headers)
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
        raise httpx.TransportError(f"network failure fetching {path} cabang {cabang_id}")
    if resp.status_code == 404:
        return False, []
    resp.raise_for_status()
    data = resp.json().get("data") or []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        data = []
    return True, data


# ── Iterator bulan ───────────────────────────────────────────────────────────

def iterate_months(tanggal_awal, tanggal_akhir):
    """Yields (awal, akhir, label) per bulan antara dua tanggal (inklusif)."""
    cur = datetime.strptime(tanggal_awal, "%Y-%m-%d").date().replace(day=1)
    end = datetime.strptime(tanggal_akhir, "%Y-%m-%d").date()
    while cur <= end:
        last = min(end, (cur + timedelta(days=32)).replace(day=1) - timedelta(days=1))
        yield cur.isoformat(), last.isoformat(), cur.strftime("%Y-%m")
        cur = (cur + timedelta(days=32)).replace(day=1)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Tarik data dashboard Brighter (7 endpoint) per bulan ke csb_db"
    )
    parser.add_argument("-e", "--env", action="store_true")
    parser.add_argument("--cabang-ids", default=None,
                        help="Comma-separated cabang IDs; default semua cabang aktif di csb_db")
    parser.add_argument("--tanggal-awal", default="2026-01-01", help="YYYY-MM-DD")
    parser.add_argument("--tanggal-akhir", default=date.today().isoformat(), help="YYYY-MM-DD")
    parser.add_argument("--bulan", nargs="+", default=None,
                        help="Tarik bulan spesifik (format YYYY-MM), mis. 2026-01 2026-03")
    parser.add_argument("--endpoints", default=None,
                        help="Comma-separated nama endpoint (rekap_dashboard,detail_penjualan,...)")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="Tidak menulis ke DB, hanya tampilkan rencana")
    args = parser.parse_args()

    default_cfg = Config.from_env()

    if args.bulan:
        bulan_list = sorted(args.bulan)
        tanggal_awal = bulan_list[0] + "-01"
        tgl_akhir = datetime.strptime(bulan_list[-1] + "-01", "%Y-%m-%d").date()
        next_month = (tgl_akhir.replace(day=1) + timedelta(days=32)).replace(day=1)
        tanggal_akhir = (next_month - timedelta(days=1)).isoformat()
    else:
        tanggal_awal = args.tanggal_awal
        tanggal_akhir = args.tanggal_akhir
        bulan_list = None

    db = DatabaseManager(default_cfg, target_db="csb")
    db.connect()

    def _close_safe():
        try:
            db.close()
        except Exception:
            pass
    atexit.register(_close_safe)

    specs = SPEC
    if args.endpoints:
        names = {x.strip() for x in args.endpoints.split(",") if x.strip()}
        specs = [s for s in specs if s["name"] in names]

    ensure_tables(db, specs)

    cabang_urls = load_cabang_urls(db)
    if args.cabang_ids:
        cabang_ids = [int(x.strip()) for x in args.cabang_ids.split(",") if x.strip()]
    else:
        cabang_ids = sorted(cabang_urls.keys())
    if not cabang_ids:
        print("[error] tidak ada cabang aktif di csb_db (`cabang`). Gunakan --cabang-ids.")
        sys.exit(1)

    server_cfg, server_auth = {}, {}
    cabang_cfg, cabang_auth = {}, {}
    for cid in cabang_ids:
        url = cabang_urls.get(cid)
        key = url or default_cfg.base_url
        if key not in server_cfg:
            cfg = dataclasses.replace(default_cfg, base_url=url) if url else default_cfg
            server_cfg[key] = cfg
            server_auth[key] = AuthManager(cfg)
        cabang_cfg[cid] = server_cfg[key]
        cabang_auth[cid] = server_auth[key]

    print(f"Sinkron ke: csb_db")
    print(f"Endpoint: {[s['name'] for s in specs]}")
    print(f"Rentang: {tanggal_awal} .. {tanggal_akhir}")
    print(f"Cabang: {cabang_ids}")
    if args.dry_run:
        print("\n[dry-run] Tidak ada API/DB yang dipanggil.")
        return 0

    # Pre-flight token
    try:
        cabang_auth[cabang_ids[0]].ensure_token()
        print(f"API OK ({cabang_cfg[cabang_ids[0]].base_url})")
    except Exception as e:
        print(f"[Pre-flight] Tidak dapat menghubungi API Brighter: {e}")
        sys.exit(1)

    months = list(iterate_months(tanggal_awal, tanggal_akhir))
    if bulan_list:
        months = [m for m in months if m[2] in bulan_list]

    total_rows = {s["table"]: 0 for s in specs}
    for m_awal, m_akhir, m_label in months:
        for cid in cabang_ids:
            for spec in specs:
                try:
                    available, data = fetch_dash(
                        cabang_cfg[cid], cabang_auth[cid], spec["path"], cid,
                        m_awal, m_akhir, args.verbose,
                    )
                except Exception as e:
                    print(f"  [err] {spec['name']} cb{cid} {m_label}: {e}")
                    continue
                if not available:
                    continue
                rows = []
                for item in data:
                    flat = spec["flatten"](cid, m_awal, item)
                    if isinstance(flat, list):
                        rows.extend(filter(None, flat))
                    elif flat:
                        rows.append(flat)
                if not rows:
                    continue
                delete_month_rows(db, spec["table"], cid, m_awal)
                n = upsert_rows(db, spec["table"], spec["fields"], spec["unique"], rows)
                total_rows[spec["table"]] += n
                print(f"  cb{cid} {m_label} {spec['name']:<24} {n} baris -> {spec['table']}")

    db.close()
    print("\n" + "=" * 55)
    print("COMPLETE - total baris per tabel:")
    for s in specs:
        print(f"  {s['table']:<32} {total_rows[s['table']]:>10,}")
    print("=" * 55)


if __name__ == "__main__":
    main()