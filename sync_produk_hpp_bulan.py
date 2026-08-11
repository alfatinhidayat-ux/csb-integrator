import argparse
import logging
from datetime import datetime

import httpx
import pymysql
from pymysql.cursors import DictCursor

from config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("produk-hpp-bulan")

TABLE = "produk_hpp_periode"
URL = "https://brighter-kairatu-api.koffiesoft.com/akuntansi/hpp_bulan"
DEFAULT_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJyaW9uIiwidXNlcl9pZCI6MiwidXNlcl9uYW1lIjoicmlvbiIsImdydXAiOiJEaXJla3R1ciIsInNlc3Npb25faWQiOjUwMzksImV4cCI6MTc4ODk5OTkwOH0.P4TfUk7mBVoLXxit0lxNLCMG_V6WmFr4JOuLD-i-s9E"

SUMBER = "hpp_bulan"


def fetch_hpp(token: str, tahun: int, results_per_page: int) -> list[dict]:
    """Fetch semua halaman endpoint hpp_bulan utk satu tahun."""
    base = {
        "hpp_tahun": tahun,
        "results_per_page": results_per_page,
        "order_by": "hpp_bulan",
        "order_dir": "asc",
        "hpp_produk_data": "true",
        "hpp_cabang_data": "true",
        "hpp_satuan_data": "true",
        "hpp_tampilkan_nol": "true",
    }
    records: list[dict] = []
    page = 1
    headers = {"authorization": f"Bearer {token}"}
    while True:
        resp = httpx.get(URL, params=dict(base, page=page), headers=headers, timeout=120)
        resp.raise_for_status()
        j = resp.json()
        records.extend(j["data"])
        total_pages = j["paging"]["total_pages"]
        logger.info("fetch page %d/%d (total %d records)", page, total_pages, len(records))
        if page >= total_pages:
            break
        page += 1
    return records


def missing_rows(conn, periode_awal: str, periode_akhir: str, semua: bool) -> tuple[list, list]:
    """Produk HPP kosong yang terjual (A) dan produk terjual tanpa baris (B)."""
    cur = conn.cursor()
    sold = "" if semua else (
        "AND EXISTS (SELECT 1 FROM pos_transaction_items i JOIN pos_transactions t ON t.id=i.pos_transaction_id "
        f"WHERE i.product_id = h.produk_id AND DATE_FORMAT(t.waktu_transaksi,'%%Y-%%m-01') = h.periode_awal)"
    )
    cur.execute(
        f"""SELECT DISTINCT h.produk_id, h.periode_awal, h.produk_kode, h.produk_nama
        FROM `{TABLE}` h
        WHERE h.periode_awal BETWEEN %s AND %s
          AND (h.hpp_moving_average IS NULL OR h.hpp_moving_average = 0)
          {sold}
        ORDER BY h.produk_id, h.periode_awal""",
        (periode_awal, periode_akhir),
    )
    A = [(r["produk_id"], r["periode_awal"].month, r["produk_kode"], r["produk_nama"]) for r in cur.fetchall()]

    sold_b = "" if semua else (
        "AND EXISTS (SELECT 1 FROM pos_transaction_items i JOIN pos_transactions t ON t.id=i.pos_transaction_id "
        f"WHERE i.product_id = p.produk_id AND DATE_FORMAT(t.waktu_transaksi,'%%Y-%%m-01') BETWEEN %s AND %s)"
    )
    cur.execute(
        f"""SELECT DISTINCT p.produk_id, p.produk_kode, p.produk_nama
        FROM produk p
        WHERE NOT EXISTS (SELECT 1 FROM `{TABLE}` h WHERE h.produk_id = p.produk_id)
          {sold_b}
        ORDER BY p.produk_id""",
        (periode_awal, periode_akhir) if not semua else (),
    )
    B = [(r["produk_id"], r["produk_kode"], r["produk_nama"]) for r in cur.fetchall()]
    return A, B


def cabangs(conn) -> list[dict]:
    cur = conn.cursor()
    cur.execute(f"SELECT DISTINCT cabang_id, cabang_kode, cabang_nama FROM `{TABLE}` ORDER BY cabang_id")
    return cur.fetchall()


def update_kosong(conn, A: list, lookup: dict, periode_awal: str, synced_at: datetime) -> tuple[int, int]:
    """Isi HPP utk (produk x periode) yang kosong, semua cabang. sumber_hpp = hpp_bulan."""
    cur = conn.cursor()
    updated = 0
    produk_ids = set()
    for produk_id, bulan, kode, nama in A:
        rec = lookup.get((produk_id, bulan))
        nilai = rec.get("hpp_nilai_satuan") if rec else None
        if not nilai or float(nilai) <= 0:
            continue
        cur.execute(
            f"""UPDATE `{TABLE}` SET hpp_moving_average = %s, sumber_hpp = %s, synced_at = %s
            WHERE produk_id = %s AND periode_awal = %s
              AND (hpp_moving_average IS NULL OR hpp_moving_average = 0)""",
            (float(nilai), SUMBER, synced_at, produk_id, f"{periode_awal[:4]}-{bulan:02d}-01"),
        )
        updated += cur.rowcount
        produk_ids.add(produk_id)
    return updated, len(produk_ids)


def periods(conn, periode_awal: str, periode_akhir: str) -> list[tuple]:
    """Pasangan (periode_awal, periode_akhir) yang benar-benar ada di tabel."""
    cur = conn.cursor()
    cur.execute(
        f"SELECT DISTINCT periode_awal, periode_akhir FROM `{TABLE}` "
        "WHERE periode_awal BETWEEN %s AND %s ORDER BY periode_awal",
        (periode_awal, periode_akhir),
    )
    return [(r["periode_awal"], r["periode_akhir"]) for r in cur.fetchall()]


def insert_kosong(conn, B: list, lookup: dict, cabangs: list, periods: list,
                  synced_at: datetime) -> tuple[int, int]:
    """INSERT baris utk produk yang tidak ada di tabel, per (produk x cabang x periode)
    hanya utk bulan yang punya nilai HPP dari API."""
    cur = conn.cursor()
    inserted = 0
    produk_ids = set()
    for produk_id, kode, nama in B:
        for bulan_awal, bulan_akhir in periods:
            rec = lookup.get((produk_id, bulan_awal.month))
            nilai = rec.get("hpp_nilai_satuan") if rec else None
            if not nilai or float(nilai) <= 0:
                continue
            satuan_id = rec.get("hpp_satuan_id")
            satuan_kode = (rec.get("hpp_satuan_data") or {}).get("satuan_kode")
            for cab in cabangs:
                cur.execute(
                    f"""INSERT IGNORE INTO `{TABLE}`
                    (produk_id, produk_kode, produk_nama, satuan_id, satuan_kode,
                     cabang_id, cabang_kode, cabang_nama, periode_awal, periode_akhir,
                     hpp_moving_average, sumber_hpp, synced_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (produk_id, kode, nama, satuan_id, satuan_kode,
                     cab["cabang_id"], cab["cabang_kode"], cab["cabang_nama"],
                     bulan_awal, bulan_akhir, float(nilai), SUMBER, synced_at),
                )
                inserted += cur.rowcount
            produk_ids.add(produk_id)
    return inserted, len(produk_ids)


def main():
    parser = argparse.ArgumentParser(
        description=f"Fill HPP kosong di {TABLE} dari API akuntansi/hpp_bulan "
                    "(sumber kebenaran HPP). Per (produk, bulan) -> semua cabang."
    )
    parser.add_argument("--env", action="store_true", help="load credentials dari .env")
    parser.add_argument("--tahun", type=int, default=2026)
    parser.add_argument("--token", type=str, default=DEFAULT_TOKEN, help="Bearer token (default token dari hpp.md)")
    parser.add_argument("--periode-awal", type=str, default="2026-01-01")
    parser.add_argument("--periode-akhir", type=str, default="2026-08-31")
    parser.add_argument("--results-per-page", type=int, default=1000)
    parser.add_argument("--semua", action="store_true",
                        help="isi SEMUA baris HPP kosong (tanpa filter produk terjual)")
    parser.add_argument("--dry-run", action="store_true", help="hitung & tampilkan rencana, tanpa menulis DB")
    args = parser.parse_args()

    config = Config.from_env()
    synced_at = datetime.now()

    records = fetch_hpp(args.token, args.tahun, args.results_per_page)
    lookup = {}
    for rec in records:
        lookup[(rec["hpp_produk_id"], rec["hpp_bulan"])] = rec
    logger.info("HPP API loaded: %d (produk x bulan)", len(lookup))

    conn = pymysql.connect(
        **config.csb_db_kwargs(), charset="utf8mb4", cursorclass=DictCursor, autocommit=False,
    )
    try:
        A, B = missing_rows(conn, args.periode_awal, args.periode_akhir, args.semua)
        logger.info("Kosong-terjual (produk x periode): %d | tanpa-baris: %d", len(A), len(B))

        if args.dry_run:
            for produk_id, bulan, kode, nama in A[:10]:
                rec = lookup.get((produk_id, bulan))
                print(f"  UPDATE {kode} ({produk_id}) bulan {bulan}: API nilai = "
                      f"{rec.get('hpp_nilai_satuan') if rec else 'TIDAK ADA DI API'}")
            print(f"  -> total baris UPDATE direncanakan: {len(A)}")
            print(f"  -> total produk INSERT direncanakan: {len(B)}")
            return

        upd, upd_produk = update_kosong(conn, A, lookup, args.periode_awal, synced_at)
        conn.commit()
        logger.info("UPDATE: %d baris (%d produk) terisi dari API hpp_bulan", upd, upd_produk)

        cabs = cabangs(conn)
        per = periods(conn, args.periode_awal, args.periode_akhir)
        ins, ins_produk = insert_kosong(conn, B, lookup, cabs, per, synced_at)
        conn.commit()
        logger.info("INSERT: %d baris (%d produk) ditambahkan", ins, ins_produk)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
