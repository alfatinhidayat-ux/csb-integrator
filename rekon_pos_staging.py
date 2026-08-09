"""Rekonsiliasi nota staging (pos_nota_staging) terhadap ledger POS Brighter.

Program mengunduh SELURUH header transaksi POS dari API (GET /transaksi/pos)
sekali ke tabel cache lokal `pos_ledger_cache`, lalu mencocokkan setiap nomor
nota di pos_nota_staging dengan beberapa strategi:

  1. exact_nobukti        : staging nota == jproduk_nobukti (identik)
  2. nota_in_keterangan   : string staging nota muncul di jproduk_keterangan
                            (nota staging ternyata adalah REFERENSI yang tertulis
                             di kolom Keterangan dari record POS asli)
  3. seq_in_nobukti       : bagian YYMM-SEQ staging nota == segmen terakhir
                            jproduk_nobukti (fallback; BISA ambigu karena nomor
                            urut dipakai ulang di banyak cabang/user)
  4. seq_in_keterangan    : YYMM-SEQ staging nota muncul di keterangan

CATATAN: pencocokan paling andal adalah (1) dan (2) via keywords string LENGKAP
nota staging ΓÇö record yang keterangannya memuat string itu adalah record aslinya.
Pencocokan berbasis sequence (3/4) hanya fallback karena rawan ambigu.

Hasil ditulis ke tabel `pos_rekonsiliasi` dan CSV (default: rekon_pos_report.csv),
agar Anda tahu untuk setiap nota: apakah ada, nomor aslinya, cabang, dan tanggalnya.

Penggunaan:
    python rekon_pos_staging.py --env
    python rekon_pos_staging.py --env --fast      # tanpa unduh ledger penuh (~2-3 menit)
    python rekon_pos_staging.py --env --refresh   # paksa unduh ulang cache
    python rekon_pos_staging.py --env --csv my_report.csv

Mode default (unduh ledger penuh ~1.416 halaman) butuh ±24 menit sekali jalan.
Mode --fast memakai pencarian keywords per segment (1 request per nota staging),
jauh lebih cepat dan sudah cukup untuk menentukan nota asli tiap nomor staging.
"""

import argparse
import csv
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
from sync_pos_by_nota import STAGING_TABLE, api_get

CACHE_TABLE = "pos_ledger_cache"
REKON_TABLE = "pos_rekonsiliasi"
DEFAULT_CSV = "rekon_pos_report.csv"

CACHE_FIELDS = [
    "jproduk_id", "jproduk_tanggal", "jproduk_nobukti", "jproduk_cabang_id",
    "jproduk_cust", "jproduk_keterangan", "jproduk_stat_dok", "jproduk_bayar",
]

page_lock = threading.Lock()
insert_lock = threading.Lock()


def ensure_cache_table(db: DatabaseManager, drop: bool = False):
    if drop:
        with db.conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS `{CACHE_TABLE}`")
        db.conn.commit()
    with db.conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS `{CACHE_TABLE}` (
                jproduk_id BIGINT NOT NULL PRIMARY KEY,
                jproduk_tanggal DATE NULL,
                jproduk_nobukti VARCHAR(200) NULL,
                jproduk_cabang_id INT NULL,
                jproduk_cust BIGINT NULL,
                jproduk_keterangan TEXT NULL,
                jproduk_stat_dok VARCHAR(50) NULL,
                jproduk_bayar DECIMAL(15,2) NULL,
                synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    db.conn.commit()


def ensure_rekon_table(db: DatabaseManager):
    with db.conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS `{REKON_TABLE}` (
                id INT AUTO_INCREMENT PRIMARY KEY,
                nota VARCHAR(100) NOT NULL,
                seq VARCHAR(30) NULL,
                status VARCHAR(30) NOT NULL,
                kandidat TEXT NULL,
                catatan TEXT NULL,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_nota (nota)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    db.conn.commit()


def ledger_total(client: httpx.Client, config: Config, auth: AuthManager) -> tuple[int, int]:
    data = api_get(client, config, auth, "/transaksi/pos",
                   {"page": "1", "results_per_page": "1", "jproduk_stat_dok": "Semua"})
    paging = data.get("paging") or {}
    return int(paging.get("total_records") or 0), int(paging.get("total_pages") or 0)


def cache_count(db: DatabaseManager) -> int:
    with db.conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS c FROM `{CACHE_TABLE}`")
        return int(cur.fetchone()["c"])


def download_ledger(db: DatabaseManager, config: Config, auth: AuthManager,
                    workers: int, total: int, total_pages: int, verbose: bool) -> int:
    client = httpx.Client(base_url=config.base_url, timeout=config.request_timeout,
                          follow_redirects=True)
    page_counter = {"next": 1}
    all_rows: list[tuple] = []
    errors = []

    def worker():
        local_rows = []
        while True:
            with page_lock:
                p = page_counter["next"]
                page_counter["next"] += 1
            if p > total_pages:
                break
            try:
                data = api_get(client, config, auth, "/transaksi/pos", {
                    "page": str(p),
                    "results_per_page": str(config.results_per_page),
                    "jproduk_stat_dok": "Semua",
                })
                for rec in data.get("data") or []:
                    local_rows.append(tuple(rec.get(f) for f in CACHE_FIELDS))
                if verbose and p % 100 == 0:
                    with insert_lock:
                        print(f"    fetched page {p}/{total_pages}")
            except Exception as e:
                errors.append((p, str(e)))
        return local_rows

    try:
        done_pages = 0
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(worker) for _ in range(workers)]
            for fut in as_completed(futures):
                chunk = fut.result()
                all_rows.extend(chunk)
                done_pages += 1
                if done_pages % workers == 0:
                    print(f"    [progress] {done_pages * workers} worker done, {len(all_rows)} rows")
    finally:
        client.close()

    if errors:
        print(f"  Page errors ({len(errors)}): {errors[:20]}")

    print(f"  Downloaded {len(all_rows)} rows, inserting into {CACHE_TABLE}...")
    inserted = 0
    cols = ", ".join(f"`{f}`" for f in CACHE_FIELDS)
    ph = ", ".join(["%s"] * len(CACHE_FIELDS))
    with db.conn.cursor() as cur:
        for i in range(0, len(all_rows), 5000):
            chunk = all_rows[i:i + 5000]
            cur.executemany(f"INSERT IGNORE INTO `{CACHE_TABLE}` ({cols}) VALUES ({ph})", chunk)
            db.conn.commit()
            inserted += len(chunk)
    print(f"  Inserted {inserted} rows into {CACHE_TABLE} (total in table: {cache_count(db)}).")
    return inserted


def extract_seq(nota: str) -> str | None:
    """Returns the YYMM-SEQ segment of a nota, e.g. '2601-0521'."""
    parts = nota.split("/")
    tail = parts[-1].strip()
    if re.match(r"^\d{4}-\d+$", tail):
        return tail
    return None


def load_ledger(db: DatabaseManager) -> list[dict]:
    with db.conn.cursor() as cur:
        cur.execute(f"SELECT * FROM `{CACHE_TABLE}`")
        return [dict(row) for row in cur.fetchall()]


def match_staging(db: DatabaseManager, notas: list[dict], ledger: list[dict], verbose: bool):
    by_nobukti = {}
    for rec in ledger:
        nb = (rec.get("jproduk_nobukti") or "").strip().upper()
        if nb:
            by_nobukti.setdefault(nb, rec)

    seq_to_nobukti: dict[str, list[dict]] = {}
    for rec in ledger:
        nb = (rec.get("jproduk_nobukti") or "").strip().upper()
        tail = nb.split("/")[-1] if nb else ""
        if re.match(r"^\d{4}-\d+$", tail):
            seq_to_nobukti.setdefault(tail, []).append(rec)

    seq_to_keterangan: dict[str, list[dict]] = {}
    for rec in ledger:
        ket = (rec.get("jproduk_keterangan") or "") or ""
        for m in re.finditer(r"(?<![\d])(\d{4}-\d+)(?![\d])", ket):
            seq_to_keterangan.setdefault(m.group(1), []).append(rec)

    nota_to_keterangan: dict[str, list[dict]] = {}
    for rec in ledger:
        ket = (rec.get("jproduk_keterangan") or "") or ""
        for nota in notas:
            key = nota["nota"].strip().upper()
            if key and key in ket:
                nota_to_keterangan.setdefault(key, []).append(rec)

    summary = {"exact": 0, "seq_in_nobukti": 0, "seq_in_keterangan": 0,
               "nota_in_keterangan": 0, "tidak_ada": 0}

    results = []
    for row in notas:
        nota = (row["nota"] or "").strip().upper()
        seq = extract_seq(nota)
        cands = []

        nb_rec = by_nobukti.get(nota)
        if nb_rec:
            cands.append(("exact_nobukti", nb_rec))

        if seq:
            for rec in seq_to_nobukti.get(seq, []):
                if (rec.get("jproduk_nobukti") or "").strip().upper() != nota:
                    cands.append(("seq_in_nobukti", rec))
            for rec in seq_to_keterangan.get(seq, []):
                cands.append(("seq_in_keterangan", rec))

        if nota in nota_to_keterangan:
            for rec in nota_to_keterangan[nota]:
                cands.append(("nota_in_keterangan", rec))

        seen = set()
        dedup = []
        for strat, rec in cands:
            key = (strat, rec.get("jproduk_nobukti"), rec.get("jproduk_id"))
            if key in seen:
                continue
            seen.add(key)
            dedup.append((strat, rec))

        if not dedup:
            status = "tidak_ada"
            summary["tidak_ada"] += 1
        else:
            best = min(st["st"] for st, _ in dedup)
            order = {"exact_nobukti": 1, "seq_in_nobukti": 2, "seq_in_keterangan": 3,
                     "nota_in_keterangan": 4}
            best_key = min(dedup, key=lambda x: order[x[0]])[0]
            status = best_key
            summary[status] += 1

        kandidat_lines = []
        for strat, rec in dedup:
            kandidat_lines.append(
                f"[{strat}] {rec.get('jproduk_nobukti')} "
                f"(id={rec.get('jproduk_id')}, cabang={rec.get('jproduk_cabang_id')}, "
                f"tgl={rec.get('jproduk_tanggal')}, keterangan={rec.get('jproduk_keterangan')!r})"
            )
        results.append({
            "nota": nota, "seq": seq, "status": status,
            "kandidat": "\n".join(kandidat_lines) or None,
            "catatan": None,
        })
        if verbose:
            print(f"  {nota:28s} seq={seq or '-':10s} -> {status} ({len(dedup)} kandidat)")

    return results, summary


def nota_variants(nota: str) -> list[str]:
    """Keyword variants to try for a staging nota: full string, then (for
    3-segment BRANCH/USER/YYMM-SEQ) the branch-stripped USER/YYMM-SEQ form
    which is the shape commonly written in jproduk_keterangan references."""
    vs = [nota]
    parts = nota.split("/")
    if len(parts) == 3:
        stripped = "/".join(parts[1:])
        if stripped and stripped != nota:
            vs.append(stripped)
    seen = []
    for v in vs:
        if v not in seen:
            seen.append(v)
    return seen


def match_staging_fast(db: DatabaseManager, notas: list[dict], config: Config,
                       auth: AuthManager, workers: int, verbose: bool):
    """Match staging notas via API keyword search on the FULL nota string.

    A staging nota like NS/2601-0352 is usually a REFERENCE written inside the
    jproduk_keterangan of a real POS record (e.g. SB/FR/2601-0732). Searching the
    full string finds that record directly and uniquely. For 3-segment staging
    notas that are not real nobukti, also try the branch-stripped form.
    """
    summary = {"exact_nobukti": 0, "nota_in_keterangan": 0, "tidak_ada": 0}
    results = []
    errors = []

    def worker(n: str):
        cands = []
        for kw in nota_variants(n):
            data = api_get(client, config, auth, "/transaksi/pos", {
                "page": "1",
                "results_per_page": str(config.results_per_page),
                "jproduk_stat_dok": "Semua",
                "keywords": kw,
            }).get("data") or []
            for rec in data:
                nb = (rec.get("jproduk_nobukti") or "").strip().upper()
                ket = rec.get("jproduk_keterangan") or ""
                if nb == n:
                    cands.append(("exact_nobukti", rec))
                elif n in ket or kw in ket:
                    cands.append(("nota_in_keterangan", rec))
        return n, cands

    client = httpx.Client(base_url=config.base_url, timeout=config.request_timeout,
                          follow_redirects=True)
    try:
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_nota = {executor.submit(worker, n["nota"].strip().upper()): n
                              for n in notas}
            for future in as_completed(future_to_nota):
                n = future_to_nota[future]
                done += 1
                try:
                    nota, cands = future.result()
                except Exception as e:
                    errors.append((nota, str(e)))
                    results.append({"nota": nota, "seq": extract_seq(nota),
                                    "status": "error",
                                    "kandidat": None, "catatan": f"search: {e}"})
                    continue

                seen = set()
                dedup = []
                for strat, rec in cands:
                    key = (strat, rec.get("jproduk_nobukti"), rec.get("jproduk_id"))
                    if key in seen:
                        continue
                    seen.add(key)
                    dedup.append((strat, rec))

                if not dedup:
                    status = "tidak_ada"
                else:
                    order = {"exact_nobukti": 1, "nota_in_keterangan": 2}
                    status = min(dedup, key=lambda x: order[x[0]])[0]
                summary[status] += 1

                kandidat_lines = []
                for strat, rec in dedup:
                    kandidat_lines.append(
                        f"[{strat}] {rec.get('jproduk_nobukti')} "
                        f"(id={rec.get('jproduk_id')}, cabang={rec.get('jproduk_cabang_id')}, "
                        f"tgl={rec.get('jproduk_tanggal')}, keterangan={rec.get('jproduk_keterangan')!r})"
                    )
                results.append({
                    "nota": nota, "seq": extract_seq(nota), "status": status,
                    "kandidat": "\n".join(kandidat_lines) or None, "catatan": None,
                })
                if verbose:
                    print(f"  {nota:28s} -> {status} ({len(dedup)} kandidat)")
                if done % 100 == 0 or done == len(notas):
                    print(f"    [progress] {done}/{len(notas)} -> "
                          f"exact={summary['exact_nobukti']}, "
                          f"keterangan={summary['nota_in_keterangan']}, "
                          f"tidak_ada={summary['tidak_ada']}")
    finally:
        client.close()

    if errors:
        print(f"  Search errors ({len(errors)}): {errors[:20]}")
    return results, summary


def write_report(db: DatabaseManager, results: list[dict], csv_path: str):
    with db.conn.cursor() as cur:
        for r in results:
            cur.execute(f"""
                INSERT INTO `{REKON_TABLE}` (nota, seq, status, kandidat, catatan, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    seq = VALUES(seq), status = VALUES(status),
                    kandidat = VALUES(kandidat), catatan = VALUES(catatan),
                    updated_at = VALUES(updated_at)
            """, (r["nota"], r["seq"], r["status"], r["kandidat"], r["catatan"],
                  datetime.now()))
        db.conn.commit()

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["nota", "seq", "status", "kandidat"])
        for r in results:
            w.writerow([r["nota"], r["seq"], r["status"], (r["kandidat"] or "").replace("\n", " | ")])
    print(f"CSV report: {csv_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Rekonsiliasi nota staging terhadap seluruh ledger POS Brighter"
    )
    parser.add_argument("-e", "--env", action="store_true",
                        help="Load configuration from BRIGHTER_* environment variables")
    parser.add_argument("--refresh", action="store_true",
                        help="Unduh ulang seluruh ledger (drop cache dulu)")
    parser.add_argument("--fast", action="store_true",
                        help="Pakai pencarian keywords per segment (tanpa unduh ledger penuh)")
    parser.add_argument("--workers", type=int, default=5,
                        help="ThreadPool workers untuk unduh ledger (default: 5)")
    parser.add_argument("--csv", default=DEFAULT_CSV,
                        help=f"Path laporan CSV (default: {DEFAULT_CSV})")
    parser.add_argument("--request-delay", type=float, default=None,
                        help="Override delay antar request (detik)")
    parser.add_argument("--results-per-page", type=int, default=None,
                        help="Override ukuran halaman API (default: 100)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Log detail proses")
    args = parser.parse_args()

    config = Config.from_env()
    if args.request_delay is not None:
        config.request_delay = args.request_delay
    if args.results_per_page is not None:
        config.results_per_page = args.results_per_page

    auth = AuthManager(config)
    db = DatabaseManager(config, target_db="csb")
    db.connect()

    ensure_cache_table(db, drop=args.refresh)
    ensure_rekon_table(db)

    with db.conn.cursor() as cur:
        cur.execute(f"SELECT id, nota, status FROM `{STAGING_TABLE}` ORDER BY id")
        notas = [dict(r) for r in cur.fetchall()]
    print(f"Mencocokkan {len(notas)} nota staging...")

    if args.fast:
        print("Mode FAST: 1 request keyword per nota (tanpa unduh ledger penuh)...")
        results, summary = match_staging_fast(db, notas, config, auth, args.workers, args.verbose)
    else:
        client = httpx.Client(base_url=config.base_url, timeout=config.request_timeout,
                              follow_redirects=True)
        total, total_pages = ledger_total(client, config, auth)
        client.close()
        print(f"Ledger: {total} record, {total_pages} halaman (rpp={config.results_per_page}).")

        cached = cache_count(db)
        if args.refresh or cached < total:
            print(f"Cache saat ini {cached} baris, mengunduh ulang (~{total_pages} halaman, "
                  "butuh beberapa menit)...")
            download_ledger(db, config, auth, args.workers, total, total_pages, args.verbose)
        else:
            print(f"Cache sudah lengkap ({cached} baris), tanpa unduh ulang.")

        ledger = load_ledger(db)
        results, summary = match_staging(db, notas, ledger, args.verbose)

    write_report(db, results, args.csv)
    db.close()

    print("\n" + "=" * 60)
    print("RINGKASAN REKONSILIASI")
    for key in ("exact_nobukti", "seq_in_nobukti", "seq_in_keterangan",
                "nota_in_keterangan", "tidak_ada"):
        if key in summary:
            print(f"  {key:20s}: {summary[key]}")
    print(f"  {'TOTAL':20s}: {len(results)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
