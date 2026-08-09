"""Sync POS detail (baris item) berdasarkan nomor nota dari Brighter API.

Program membaca daftar nomor nota dari tabel tampungan `pos_nota_staging`
(yang berada di csb_db, dibuat otomatis bila belum ada). Isi nomor nota ke tabel
itu lalu jalankan program; setiap nota diproses sekali dan statusnya diperbarui.

Pencarian nota TANPA scan ledger: endpoint GET /transaksi/pos ternyata punya
parameter `keywords` yang benar-benar menyaring (diverifikasi langsung ke API
produksi, Agustus 2026) — GET /transaksi/pos?keywords=SB/FR/2601-0513
mengembalikan tepat satu record. Jadi cukup 1 request per nota, bukan scan
ribuan halaman.

Mengisi nomor nota (status default 'pending'):

    INSERT INTO pos_nota_staging (nota) VALUES ('SB/FR/2601-0521');
    INSERT INTO pos_nota_staging (nota) VALUES ('SB/NI/2603-0794');

Atau langsung lewat CLI (otomatis dimasukkan ke tabel tampungan):

    python sync_pos_by_nota.py --env --nota "SB/FR/2601-0521"

Status nota: pending -> done | not_found | error

Urutan proses: tahun terbaru dulu (2026, lalu ke bawah). Proses per tahun juga
bisa dibatasi dengan --tahun.

Contoh:
    python sync_pos_by_nota.py --env
    python sync_pos_by_nota.py --env --tahun 2026
    python sync_pos_by_nota.py --env --tahun "2026,2025"
"""

import argparse
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import httpx

sys.path.insert(0, os.getcwd())

from config import Config
from auth import AuthManager
from db import DatabaseManager
from sync_pos import fetch_pos_detail, map_detail, insert_batch_upsert

STAGING_TABLE = "pos_nota_staging"

auth_lock = threading.Lock()


def ensure_staging_table(db: DatabaseManager):
    """Creates the pos_nota_staging table if it does not exist (no drop)."""
    with db.conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS `{STAGING_TABLE}` (
                id INT AUTO_INCREMENT PRIMARY KEY,
                nota VARCHAR(100) NOT NULL UNIQUE,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                pos_id BIGINT NULL,
                cabang_id INT NULL,
                tanggal DATE NULL,
                detail_count INT NULL,
                message TEXT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                processed_at DATETIME NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    db.conn.commit()


def seed_staging_notas(db: DatabaseManager, notas: list[str]) -> int:
    """Inserts CLI-provided notas into the staging table as 'pending' (skips duplicates)."""
    if not notas:
        return 0
    added = 0
    with db.conn.cursor() as cur:
        for n in notas:
            cur.execute(
                f"INSERT IGNORE INTO `{STAGING_TABLE}` (nota) VALUES (%s)", (n,)
            )
            if cur.rowcount:
                added += 1
    db.conn.commit()
    return added


def load_pending_notas(db: DatabaseManager) -> list[tuple[int, str]]:
    """Returns (row_id, nota) for all nota with status 'pending', oldest first."""
    with db.conn.cursor() as cur:
        cur.execute(
            f"SELECT id, nota FROM `{STAGING_TABLE}` WHERE status = 'pending' ORDER BY id"
        )
        return [(row["id"], row["nota"]) for row in cur.fetchall()]


def update_staging(db: DatabaseManager, row_id: int, **fields):
    """Updates a staging row by id with the given column=value fields."""
    if not fields or row_id is None:
        return
    sets = ", ".join(f"`{k}` = %s" for k in fields)
    vals = list(fields.values()) + [row_id]
    with db.conn.cursor() as cur:
        cur.execute(f"UPDATE `{STAGING_TABLE}` SET {sets} WHERE id = %s", vals)
    db.conn.commit()


def ensure_detail_table(db: DatabaseManager):
    """Creates brighter_pos_detail if it does not exist (no drop, no header table)."""
    print("Ensuring table brighter_pos_detail exists (no drop)...")
    with db.conn.cursor() as cur:
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
    print("Table ready.")


def normalize_nota(raw: str) -> str:
    return raw.strip().upper()


def parse_notas(raw: str) -> list[str]:
    notas = []
    for token in raw.split(","):
        token = normalize_nota(token)
        if token:
            notas.append(token)
    return notas


def nota_month(nota: str) -> tuple[int, int] | None:
    """Extracts (year, month) from the YYMM segment of a nota number.

    Format nota: BRANCH/USER/YYMM-SEQUENCE, e.g. SB/FR/2601-0521 -> (2026, 1).
    Returns None when the month cannot be parsed.
    """
    parts = nota.split("/")
    if len(parts) >= 3:
        m = re.match(r"^(\d{2})(\d{2})", parts[2])
        if m:
            yy, mm = int(m.group(1)), int(m.group(2))
            if 1 <= mm <= 12:
                return 2000 + yy, mm
    m = re.search(r"(?<![\d])(\d{2})(0[1-9]|1[0-2])(?![\d])", nota)
    if m:
        return 2000 + int(m.group(1)), int(m.group(2))
    return None


def api_get(client: httpx.Client, config: Config, auth: AuthManager, path: str, params: dict) -> dict:
    """GET with retry + token refresh on 401/403. Token refresh guarded by a lock
    so it is safe to call from multiple worker threads."""
    last_err = None
    for attempt in range(config.max_retries):
        try:
            with auth_lock:
                auth.ensure_token()
                headers = auth.get_headers()
            resp = client.get(path, params=params, headers=headers)
        except httpx.HTTPError as e:
            last_err = e
            time.sleep(1)
            continue
        if resp.status_code in (401, 403):
            with auth_lock:
                auth.token = None
            continue
        if resp.status_code == 404:
            return {"status": {"code": 404}, "data": None}
        if resp.status_code >= 500:
            resp.raise_for_status()
        if resp.status_code >= 400:
            resp.raise_for_status()
        return resp.json()
    raise last_err


def find_pos_by_nota(client: httpx.Client, config: Config, auth: AuthManager, nota: str) -> dict | None:
    """Searches the POS endpoint by no_bukti using the `keywords` filter.

    Returns the header record whose jproduk_nobukti matches exactly, or None.
    """
    params = {
        "page": "1",
        "results_per_page": str(config.results_per_page),
        "jproduk_stat_dok": "Semua",
        "jproduk_cust_data": "true",
        "timestamp_data": "true",
        "keywords": nota,
    }
    data = api_get(client, config, auth, "/transaksi/pos", params).get("data") or []
    for rec in data:
        if (rec.get("jproduk_nobukti") or "").strip().upper() == nota:
            return rec
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Sync POS detail (baris item) by no_bukti/nota into csb_db.brighter_pos_detail"
    )
    parser.add_argument(
        "-e", "--env",
        action="store_true",
        help="Load configuration from BRIGHTER_* environment variables",
    )
    parser.add_argument(
        "--nota",
        action="append",
        default=[],
        help="Nomor nota untuk dimasukkan ke tabel tampungan (opsional; bisa diulang atau dipisah koma)",
    )
    parser.add_argument(
        "--tahun",
        default=None,
        help="Hanya proses nota dari tahun ini (mis. 2026). Bisa dipisah koma, mis. 2026,2025.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset status semua nota di tabel tampungan ke 'pending' (bisa digabung --tahun)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=5,
        help="ThreadPool workers untuk pencarian nota + fetch detail (default: 5)",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=None,
        help="Override delay antar request (detik)",
    )
    parser.add_argument(
        "--results-per-page",
        type=int,
        default=None,
        help="Override ukuran halaman API (default: 100)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Log detail proses",
    )
    args = parser.parse_args()

    config = Config.from_env()
    if args.request_delay is not None:
        config.request_delay = args.request_delay
    if args.results_per_page is not None:
        config.results_per_page = args.results_per_page

    tahun_filter: set[int] = set()
    if args.tahun:
        for t in args.tahun.split(","):
            t = t.strip()
            if t.isdigit():
                tahun_filter.add(int(t))

    auth = AuthManager(config)
    db = DatabaseManager(config, target_db="csb")
    db.connect()

    ensure_staging_table(db)
    ensure_detail_table(db)

    if args.reset:
        with db.conn.cursor() as cur:
            cur.execute(f"SELECT id, nota FROM `{STAGING_TABLE}`")
            all_rows = [(row["id"], row["nota"]) for row in cur.fetchall()]
        ids_to_reset = []
        for rid, nota in all_rows:
            if tahun_filter:
                ym = nota_month(nota)
                if ym is None or ym[0] not in tahun_filter:
                    continue
            ids_to_reset.append(rid)
        if ids_to_reset:
            with db.conn.cursor() as cur:
                cur.executemany(
                    f"UPDATE `{STAGING_TABLE}` SET status = 'pending', message = NULL, "
                    "processed_at = NULL, pos_id = NULL, cabang_id = NULL, "
                    "tanggal = NULL, detail_count = NULL WHERE id = %s",
                    [(rid,) for rid in ids_to_reset],
                )
            db.conn.commit()
            print(f"Reset {len(ids_to_reset)} nota menjadi 'pending'.")

    seeded = 0
    for group in args.nota:
        seeded += seed_staging_notas(db, parse_notas(group))
    if seeded:
        print(f"Ditambahkan {seeded} nota baru ke tabel {STAGING_TABLE}.")

    rows = load_pending_notas(db)
    if not rows:
        print(
            f"Tidak ada nota dengan status 'pending' di tabel {STAGING_TABLE}.\n"
            "Isi dulu nomor nota-nya, misal:\n"
            f"  INSERT INTO {STAGING_TABLE} (nota) VALUES ('SB/FR/2601-0521');\n"
            "atau jalankan dengan --nota \"SB/FR/2601-0521\"."
        )
        db.close()
        sys.exit(0)

    rows_by_nota = {nota: row_id for row_id, nota in rows}
    notas = list(rows_by_nota.keys())

    if tahun_filter:
        notas = [n for n in notas if nota_month(n) is not None and nota_month(n)[0] in tahun_filter]
        if not notas:
            print(f"Tidak ada nota pending untuk tahun {tahun_filter}.")
            db.close()
            sys.exit(0)
        print(f"Filter tahun {sorted(tahun_filter)}: {len(notas)} nota.")

    # Urutan: tahun terbaru dulu (2026, lalu ke bawah), dalam tahun urut bulan naik.
    notas.sort(
        key=lambda n: (
            -(nota_month(n)[0] if nota_month(n) else 9999),
            nota_month(n)[1] if nota_month(n) else 0,
        )
    )
    print(f"Nota pending yang akan diproses ({len(notas)}): {notas}")

    start_time = time.time()
    now = datetime.now()

    client = httpx.Client(base_url=config.base_url, timeout=config.request_timeout, follow_redirects=True)
    found_all: dict[str, dict] = {}
    search_errors: list[str] = []
    not_found: list[str] = []

    try:
        print(f"Fase 1: mencari {len(notas)} nota via keyword (paralel {args.workers} thread)...")
        done = 0
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_to_nota = {
                executor.submit(find_pos_by_nota, client, config, auth, n): n
                for n in notas
            }
            for future in as_completed(future_to_nota):
                nota = future_to_nota[future]
                done += 1
                search_failed = False
                try:
                    rec = future.result()
                except Exception as e:
                    search_failed = True
                    search_errors.append(nota)
                    update_staging(
                        db, rows_by_nota[nota],
                        status="error", message=f"search: {e}", processed_at=now,
                    )
                if search_failed:
                    pass
                elif rec is not None:
                    found_all[nota] = rec
                else:
                    not_found.append(nota)
                    update_staging(
                        db, rows_by_nota[nota],
                        status="not_found", message="not found in ledger", processed_at=now,
                    )
                if done % 50 == 0 or done == len(notas):
                    print(
                        f"    [progress] {done}/{len(notas)} nota diproses, "
                        f"{len(found_all)} ketemu, {len(not_found)} tidak ketemu, {len(search_errors)} error"
                    )
    finally:
        client.close()

    print(f"Fase 1 selesai dalam {time.time() - start_time:.1f} detik.")
    if search_errors:
        print(f"  Error pencarian ({len(search_errors)}): {search_errors}")

    if not found_all:
        print("\nTidak ada nota ditemukan, tidak ada yang disinkronkan.")
        db.close()
        sys.exit(1)

    headers = list(found_all.values())
    print(f"\nFase 2: mengambil detail untuk {len(headers)} nota yang ketemu...")
    detail_counts: dict[str, int] = {}
    errored_notas: set[str] = set()
    items_by_cabang: dict[int, list[dict]] = {}
    detail_errors = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_header = {
            executor.submit(fetch_pos_detail, config, auth, h["jproduk_id"]): h
            for h in headers
        }
        for i, future in enumerate(as_completed(future_to_header)):
            h = future_to_header[future]
            nota = (h.get("jproduk_nobukti") or "").strip().upper()
            try:
                items = future.result()
                detail_counts[nota] = len(items)
                cabang = h.get("jproduk_cabang_id") or 1
                for item in items:
                    items_by_cabang.setdefault(cabang, []).append(map_detail(item, cabang))
            except Exception as e:
                detail_errors += 1
                errored_notas.add(nota)
                update_staging(
                    db, rows_by_nota.get(nota),
                    status="error", message=f"detail fetch: {e}", processed_at=now,
                )
                print(f"  Error detail nota {h.get('jproduk_nobukti')}: {e}")
            if (i + 1) % 50 == 0 or (i + 1) == len(headers):
                print(f"  Detail progress: {i + 1}/{len(headers)}")
            if (i + 1) % 200 == 0:
                try:
                    db.reconnect()
                except Exception:
                    pass

    if detail_errors:
        print(f"  Detail errors: {detail_errors}")

    total_items = 0
    for cabang, items in sorted(items_by_cabang.items()):
        print(f"  Upsert brighter_pos_detail (cabang {cabang}): {len(items)} baris")
        insert_batch_upsert(db, "brighter_pos_detail", items)
        total_items += len(items)

    for nota, rec in found_all.items():
        if nota in errored_notas:
            continue
        update_staging(
            db, rows_by_nota.get(nota),
            status="done",
            pos_id=rec.get("jproduk_id"),
            cabang_id=rec.get("jproduk_cabang_id"),
            tanggal=rec.get("jproduk_tanggal"),
            detail_count=detail_counts.get(nota, 0),
            processed_at=now,
        )

    db.close()

    print("\n" + "=" * 50)
    print("SELESAI - SYNC DETAIL BY NOTA")
    print(f"Nota ditemukan: {len(found_all)}/{len(notas)}")
    print(f"Nota tidak ditemukan: {len(not_found)}")
    print(f"Nota error: {len(search_errors) + len(errored_notas)}")
    print(f"Total baris detail di-upsert: {total_items}")
    print(f"Durasi total: {time.time() - start_time:.1f} detik")
    print("=" * 50)


if __name__ == "__main__":
    main()
