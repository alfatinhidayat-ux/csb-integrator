import argparse
import logging
from datetime import date, datetime, timedelta
from typing import Optional

import httpx
import pymysql
from pymysql.cursors import DictCursor

from auth import AuthManager
from config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("produk-stok-hpp-sync")

TABLE = "produk_stok_hpp"
REKAP_PATH = "/laporan/lap_kartu_stok/rekap_produk"
COMMIT_BATCH_SIZE = 500
DEFAULT_CABANG_IDS = [1, 2, 4, 5, 6, 7]
PK_COLS = {"produk_id", "cabang_id", "tanggal_awal", "tanggal_akhir"}

DDL = f"""
CREATE TABLE IF NOT EXISTS `{TABLE}` (
  `produk_id` BIGINT NOT NULL,
  `produk_kode` VARCHAR(100) NULL,
  `produk_nama` VARCHAR(255) NULL,
  `satuan_id` BIGINT NULL,
  `satuan_kode` VARCHAR(20) NULL,
  `produk_harga_beli_terakhir` DECIMAL(18,2) NULL,
  `hpp_nilai_satuan` DECIMAL(18,2) NULL,
  `hpp_nilai_satuan_edit_by` VARCHAR(255) NULL,
  `cabang_id` INT NOT NULL,
  `cabang_kode` VARCHAR(20) NULL,
  `cabang_nama` VARCHAR(100) NULL,
  `stok_awal` DECIMAL(18,4) NULL,
  `stok_masuk` DECIMAL(18,4) NULL,
  `stok_keluar` DECIMAL(18,4) NULL,
  `stok_akhir` DECIMAL(18,4) NULL,
  `nilai_persediaan` DECIMAL(18,2) NULL,
  `tanggal_awal` DATE NOT NULL,
  `tanggal_akhir` DATE NOT NULL,
  `synced_at` DATETIME NOT NULL,
  PRIMARY KEY (`produk_id`, `cabang_id`, `tanggal_awal`, `tanggal_akhir`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def last_month_range(today: Optional[date] = None) -> tuple[date, date]:
    """Default periode: seluruh bulan kalender sebelumnya."""
    today = today or date.today()
    end_of_last = today.replace(day=1) - timedelta(days=1)
    start_of_last = end_of_last.replace(day=1)
    return start_of_last, end_of_last


def fetch_rekap(config: Config, auth: AuthManager, tanggal_awal: date, tanggal_akhir: date,
                cabang_ids: list[int], opsi_satuan: str) -> list[dict]:
    params = [
        ("tanggal_awal", tanggal_awal.isoformat()),
        ("tanggal_akhir", tanggal_akhir.isoformat()),
        ("order_by", "produk_nama"),
        ("order_dir", "asc"),
        ("opsi_satuan", opsi_satuan),
    ]
    for cid in cabang_ids:
        params.append(("cabang_ids", str(cid)))

    client = httpx.Client(base_url=config.base_url, timeout=config.request_timeout, follow_redirects=True)
    try:
        for attempt in range(config.max_retries):
            try:
                auth.ensure_token()
                resp = client.get(REKAP_PATH, params=params, headers=auth.get_headers())
                resp.raise_for_status()
                data = resp.json()
                records = data.get("data")
                if not isinstance(records, list):
                    raise RuntimeError(f"unexpected response shape: data={type(records).__name__}")
                return records
            except (httpx.HTTPStatusError, httpx.RequestError):
                if attempt < config.max_retries - 1:
                    continue
                raise
        raise RuntimeError("unreachable")
    finally:
        client.close()


def normalize_records(records: list[dict], tanggal_awal: date, tanggal_akhir: date,
                      synced_at: datetime) -> list[dict]:
    """Kolom per-cabang (cabang_id_1..6, stok_awal_1..6, ...) dipecah menjadi satu
    baris per (produk, cabang)."""
    rows: list[dict] = []
    for rec in records:
        base = {
            "produk_id": rec.get("produk_id"),
            "produk_kode": rec.get("produk_kode"),
            "produk_nama": rec.get("produk_nama"),
            "satuan_id": rec.get("satuan_id"),
            "satuan_kode": rec.get("satuan_kode"),
            "produk_harga_beli_terakhir": rec.get("produk_harga_beli_terakhir"),
            "hpp_nilai_satuan": rec.get("hpp_nilai_satuan"),
            "hpp_nilai_satuan_edit_by": rec.get("hpp_nilai_satuan_edit_by"),
            "tanggal_awal": tanggal_awal,
            "tanggal_akhir": tanggal_akhir,
            "synced_at": synced_at,
        }
        for idx in range(1, 100):
            cabang_id = rec.get(f"cabang_id_{idx}")
            if cabang_id is None:
                break
            stok_akhir = rec.get(f"stok_akhir_{idx}")
            row = dict(base)
            row.update({
                "cabang_id": cabang_id,
                "cabang_kode": rec.get(f"cabang_kode_{idx}"),
                "cabang_nama": rec.get(f"cabang_nama_{idx}"),
                "stok_awal": rec.get(f"stok_awal_{idx}"),
                "stok_masuk": rec.get(f"stok_masuk_{idx}"),
                "stok_keluar": rec.get(f"stok_keluar_{idx}"),
                "stok_akhir": stok_akhir,
            })
            hpp = row["produk_harga_beli_terakhir"]
            if stok_akhir is not None and hpp is not None:
                try:
                    row["nilai_persediaan"] = round(float(stok_akhir) * float(hpp), 2)
                except (TypeError, ValueError):
                    pass
            rows.append(row)
    return rows


def upsert_batch(conn: pymysql.Connection, rows: list[dict]) -> None:
    """Multi-row INSERT ... ON DUPLICATE KEY UPDATE. Nilai None diabaikan sehingga
    data lama tidak tertimpa null dari API. Rows dikelompokkan berdasarkan kolom
    non-None yang sama, lalu dikirim dalam statement berukuran <= 500 baris."""
    groups: dict[tuple, list] = {}
    for row in rows:
        cols = tuple(c for c in row if row[c] is not None)
        if cols:
            groups.setdefault(cols, []).append(row)

    for cols, group in groups.items():
        col_names = ", ".join(f"`{c}`" for c in cols)
        n = len(cols)
        update_cols = [c for c in cols if c not in PK_COLS]
        if not update_cols:
            update_set = "`synced_at` = VALUES(`synced_at`)"
        else:
            update_set = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in update_cols)

        for start in range(0, len(group), COMMIT_BATCH_SIZE):
            chunk = group[start:start + COMMIT_BATCH_SIZE]
            row_tuples = ", ".join(["(" + ", ".join(["%s"] * n) + ")"] * len(chunk))
            sql = (
                f"INSERT INTO `{TABLE}` ({col_names}) VALUES {row_tuples} "
                f"ON DUPLICATE KEY UPDATE {update_set}"
            )
            values = [v for row in chunk for v in (row[c] for c in cols)]
            with conn.cursor() as cur:
                cur.execute(sql, values)


def main():
    parser = argparse.ArgumentParser(
        description="Sync stok & HPP produk dari Brighter API (lap_kartu_stok/rekap_produk) "
                    f"ke tabel csb_db.{TABLE} (selalu upsert, tanpa hapus)."
    )
    parser.add_argument("--env", action="store_true", help="load credentials from .env")
    parser.add_argument("--tanggal-awal", type=str, default=None, help="YYYY-MM-DD (default: awal bulan lalu)")
    parser.add_argument("--tanggal-akhir", type=str, default=None, help="YYYY-MM-DD (default: akhir bulan lalu)")
    parser.add_argument("--cabang-ids", type=str, default=None,
                        help="comma-separated cabang ids (default: 1,2,4,5,6,7)")
    parser.add_argument("--opsi-satuan", type=str, default="default")
    parser.add_argument("--dry-run", action="store_true", help="fetch & map only, no writes")
    parser.add_argument("--no-create-table", action="store_true", help="skip CREATE TABLE IF NOT EXISTS")
    args = parser.parse_args()

    config = Config.from_env()

    today = date.today()
    tanggal_awal = date.fromisoformat(args.tanggal_awal) if args.tanggal_awal else last_month_range(today)[0]
    tanggal_akhir = date.fromisoformat(args.tanggal_akhir) if args.tanggal_akhir else last_month_range(today)[1]
    if tanggal_akhir < tanggal_awal:
        raise SystemExit("--tanggal-akhir must be >= --tanggal-awal")
    cabang_ids = [int(x.strip()) for x in args.cabang_ids.split(",") if x.strip()] if args.cabang_ids else DEFAULT_CABANG_IDS

    auth = AuthManager(config)
    auth.login()

    conn = None
    try:
        if not args.dry_run:
            conn = pymysql.connect(
                **config.csb_db_kwargs(),
                charset="utf8mb4",
                cursorclass=DictCursor,
                autocommit=False,
            )
            if not args.no_create_table:
                with conn.cursor() as cur:
                    cur.execute(DDL)
                conn.commit()
                logger.info("Table `%s` ready", TABLE)

        logger.info("Fetching rekap: %s s/d %s, cabang=%s, opsi_satuan=%s",
                    tanggal_awal, tanggal_akhir, cabang_ids, args.opsi_satuan)
        records = fetch_rekap(config, auth, tanggal_awal, tanggal_akhir, cabang_ids, args.opsi_satuan)
        logger.info("API returned %d produk", len(records))

        rows = normalize_records(records, tanggal_awal, tanggal_akhir, datetime.now())
        logger.info("Normalized to %d rows (produk x cabang)", len(rows))

        if args.dry_run:
            for r in rows[:5]:
                logger.info("DRY: %s", r)
            return

        total = len(rows)
        for i in range(0, total, COMMIT_BATCH_SIZE):
            chunk = rows[i:i + COMMIT_BATCH_SIZE]
            try:
                upsert_batch(conn, chunk)
            except Exception:
                logger.exception("failed upsert for rows %d-%d", i, i + len(chunk) - 1)
            conn.commit()
            logger.info("committed %d/%d rows", i + len(chunk), total)
        logger.info("Done: %d rows upserted (selalu upsert, tanpa hapus)", total)
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    main()
