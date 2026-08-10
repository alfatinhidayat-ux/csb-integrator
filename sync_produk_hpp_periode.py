import argparse
import logging
from collections import defaultdict
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
logger = logging.getLogger("produk-hpp-periode-sync")

TABLE = "produk_hpp_periode"
REKAP_PATH = "/laporan/lap_kartu_stok/rekap_produk"
COMMIT_BATCH_SIZE = 500
DEFAULT_CABANG_IDS = [1, 2, 4, 5, 6, 7]
PK_COLS = {"produk_id", "cabang_id", "periode_awal", "periode_akhir"}

DDL = f"""
CREATE TABLE IF NOT EXISTS `{TABLE}` (
  `produk_id` BIGINT NOT NULL,
  `produk_kode` VARCHAR(100) NULL,
  `produk_nama` VARCHAR(255) NULL,
  `satuan_id` BIGINT NULL,
  `satuan_kode` VARCHAR(20) NULL,
  `cabang_id` INT NOT NULL,
  `cabang_kode` VARCHAR(20) NULL,
  `cabang_nama` VARCHAR(100) NULL,
  `periode_awal` DATE NOT NULL,
  `periode_akhir` DATE NOT NULL,
  `stok_awal` DECIMAL(18,4) NULL,
  `stok_masuk` DECIMAL(18,4) NULL,
  `stok_keluar` DECIMAL(18,4) NULL,
  `stok_akhir` DECIMAL(18,4) NULL,
  `qty_beli` DECIMAL(18,4) NULL,
  `total_biaya_beli` DECIMAL(18,2) NULL,
  `hpp_moving_average` DECIMAL(18,4) NULL,
  `sumber_hpp` VARCHAR(20) NULL,
  `nilai_persediaan` DECIMAL(18,2) NULL,
  `synced_at` DATETIME NOT NULL,
  PRIMARY KEY (`produk_id`, `cabang_id`, `periode_awal`, `periode_akhir`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def last_month_range(today: Optional[date] = None) -> tuple[date, date]:
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
                records = resp.json().get("data")
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


def load_purchase_totals(conn: pymysql.Connection, sampai: date) -> dict:
    """Qty & biaya pembelian KUMULATIF s/d tanggal tertentu, per (produk, cabang),
    dari tabel pembelian_detail. Harga efektif = harga_setelah_diskon (fallback
    harga_satuan)."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT d.produk_id, p.cabang_id,
               SUM(d.qty_dasar_diterima) AS qty_beli,
               SUM(d.qty_diterima * COALESCE(d.harga_setelah_diskon, d.harga_satuan, 0)) AS total_biaya_beli
        FROM pembelian_detail d
        JOIN pembelian p ON p.id = d.pembelian_id
        WHERE p.status = 'selesai'
          AND p.tanggal_selesai <= %s
          AND d.qty_dasar_diterima > 0
        GROUP BY d.produk_id, p.cabang_id
        """,
        (sampai,),
    )
    result = {}
    for r in cur.fetchall():
        result[(r["produk_id"], r["cabang_id"])] = {
            "qty_beli": r["qty_beli"],
            "total_biaya_beli": r["total_biaya_beli"],
        }
    return result


def load_riwayat_self(conn: pymysql.Connection, produk_ids: set, sampai: date) -> dict:
    """HPP terakhir yang pernah dihitung di periode SEBELUMNYA dari tabel
    produk_hpp_periode sendiri (per (produk, cabang)). Ringan karena membaca
    tabel hasil sendiri, bukan tabel besar."""
    if not produk_ids:
        return {}
    result: dict = {}
    ids = sorted(produk_ids)
    cur = conn.cursor()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        fmt = ", ".join(["%s"] * len(chunk))
        cur.execute(
            f"""
            SELECT produk_id, cabang_id, periode_akhir, hpp_moving_average
            FROM `{TABLE}`
            WHERE produk_id IN ({fmt})
              AND periode_akhir < %s
              AND hpp_moving_average > 0
            ORDER BY periode_akhir DESC
            """,
            (*chunk, sampai),
        )
        for r in cur.fetchall():
            key = (r["produk_id"], r["cabang_id"])
            if key not in result:
                result[key] = r["hpp_moving_average"]
    return result


def load_riwayat_hpp(conn: pymysql.Connection, produk_ids: set, sampai: date) -> dict:
    """HPP terakhir yang pernah tercatat sistem (hpp_nilai_satuan > 0) dari
    lap_kartu_stok_rekap, per (produk, cabang), TANPA batas tanggal (diambil yang
    terbaru). Dipakai sebagai fallback saat API rekap sudah tidak memberi HPP;
    tanpa batas tanggal sekaligus jadi forward-fill untuk produk yang baru punya
    HPP tercatat setelah periode jual."""
    if not produk_ids:
        return {}
    result: dict = {}
    ids = sorted(produk_ids)
    cur = conn.cursor()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        fmt = ", ".join(["%s"] * len(chunk))
        cur.execute(
            f"""
            SELECT produk_id, cabang_id, tanggal_akhir, hpp_nilai_satuan
            FROM lap_kartu_stok_rekap
            WHERE produk_id IN ({fmt})
              AND hpp_nilai_satuan > 0
            ORDER BY tanggal_akhir DESC
            """,
            (*chunk,),
        )
        for r in cur.fetchall():
            key = (r["produk_id"], r["cabang_id"])
            if key not in result:
                result[key] = r["hpp_nilai_satuan"]
    return result


def load_riwayat_pembelian(conn: pymysql.Connection, produk_ids: set) -> dict:
    """HPP rata-rata dari seluruh pembelian yang pernah selesai (tanpa batas
    tanggal), per (produk, cabang). Forward-fill untuk produk yang pembeliannya
    tercatat setelah periode jual."""
    if not produk_ids:
        return {}
    result: dict = {}
    ids = sorted(produk_ids)
    cur = conn.cursor()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        fmt = ", ".join(["%s"] * len(chunk))
        cur.execute(
            f"""
            SELECT d.produk_id, p.cabang_id,
                   SUM(d.qty_dasar_diterima) AS qty_beli,
                   SUM(d.qty_diterima * COALESCE(d.harga_setelah_diskon, d.harga_satuan, 0)) AS total_biaya_beli
            FROM pembelian_detail d
            JOIN pembelian p ON p.id = d.pembelian_id
            WHERE p.status = 'selesai'
              AND d.qty_dasar_diterima > 0
              AND d.produk_id IN ({fmt})
            GROUP BY d.produk_id, p.cabang_id
            """,
            (*chunk,),
        )
        for r in cur.fetchall():
            if r["qty_beli"]:
                result[(r["produk_id"], r["cabang_id"])] = float(r["total_biaya_beli"]) / float(r["qty_beli"])
    return result


def build_rows(records: list[dict], purchase_totals: dict, tanggal_awal: date,
               tanggal_akhir: date, synced_at: datetime) -> list[dict]:
    rows: list[dict] = []
    for rec in records:
        produk_id = rec.get("produk_id")
        base = {
            "produk_id": produk_id,
            "produk_kode": rec.get("produk_kode"),
            "produk_nama": rec.get("produk_nama"),
            "satuan_id": rec.get("satuan_id"),
            "satuan_kode": rec.get("satuan_kode"),
            "periode_awal": tanggal_awal,
            "periode_akhir": tanggal_akhir,
            "synced_at": synced_at,
        }
        for idx in range(1, 100):
            cabang_id = rec.get(f"cabang_id_{idx}")
            if cabang_id is None:
                break
            stok_akhir = rec.get(f"stok_akhir_{idx}")
            pt = purchase_totals.get((produk_id, cabang_id))
            qty_beli = pt["qty_beli"] if pt else None
            total_biaya_beli = pt["total_biaya_beli"] if pt else None

            if rec.get("hpp_nilai_satuan"):
                hpp_ma = rec.get("hpp_nilai_satuan")
                sumber = "hpp_sistem"
            elif qty_beli:
                hpp_ma = round(float(total_biaya_beli) / float(qty_beli), 4)
                sumber = "pembelian"
            elif rec.get("produk_harga_beli_terakhir"):
                hpp_ma = rec.get("produk_harga_beli_terakhir")
                sumber = "harga_beli_terakhir"
            else:
                hpp_ma = None
                sumber = None

            row = dict(base)
            row.update({
                "cabang_id": cabang_id,
                "cabang_kode": rec.get(f"cabang_kode_{idx}"),
                "cabang_nama": rec.get(f"cabang_nama_{idx}"),
                "stok_awal": rec.get(f"stok_awal_{idx}"),
                "stok_masuk": rec.get(f"stok_masuk_{idx}"),
                "stok_keluar": rec.get(f"stok_keluar_{idx}"),
                "stok_akhir": stok_akhir,
                "qty_beli": qty_beli,
                "total_biaya_beli": total_biaya_beli,
                "hpp_moving_average": hpp_ma,
                "sumber_hpp": sumber,
            })
            if stok_akhir is not None and hpp_ma is not None:
                try:
                    row["nilai_persediaan"] = round(float(stok_akhir) * float(hpp_ma), 2)
                except (TypeError, ValueError):
                    pass
            rows.append(row)
    return rows


def upsert_batch(conn: pymysql.Connection, rows: list[dict]) -> None:
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
        description="HPP rata-rata per periode dari data pembelian (pembelian_detail) "
                    f"+ stok dari rekap kartu stok, disimpan ke csb_db.{TABLE} (selalu upsert, tanpa hapus). "
                    "Fallback HPP: hpp_sistem -> harga_beli_terakhir -> riwayat sendiri -> lap_kartu_stok_rekap."
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

        logger.info("Fetching rekap: %s s/d %s, cabang=%s", tanggal_awal, tanggal_akhir, cabang_ids)
        records = fetch_rekap(config, auth, tanggal_awal, tanggal_akhir, cabang_ids, args.opsi_satuan)
        logger.info("API returned %d produk", len(records))

        purchase_totals = {}
        if conn:
            purchase_totals = load_purchase_totals(conn, tanggal_akhir)
            logger.info("Purchase totals (kumulatif s/d %s): %d produk x cabang", tanggal_akhir, len(purchase_totals))

        rows = build_rows(records, purchase_totals, tanggal_awal, tanggal_akhir, datetime.now())

        missing = [
            (i, r["produk_id"], r["cabang_id"])
            for i, r in enumerate(rows)
            if r["hpp_moving_average"] is None
        ]
        if conn and missing:
            pids = {pid for _, pid, _ in missing}

            riwayat_self = load_riwayat_self(conn, pids, tanggal_akhir)
            for i, pid, cid in missing:
                val = riwayat_self.get((pid, cid))
                if val:
                    rows[i]["hpp_moving_average"] = round(float(val), 4)
                    rows[i]["sumber_hpp"] = "riwayat"
            filled_self = sum(1 for i, _, _ in missing if rows[i]["sumber_hpp"] == "riwayat")

            sisa = [m for m in missing if rows[m[0]]["sumber_hpp"] != "riwayat"]
            if sisa:
                riwayat_rekap = load_riwayat_hpp(conn, {pid for _, pid, _ in sisa}, tanggal_akhir)
                for i, pid, cid in sisa:
                    val = riwayat_rekap.get((pid, cid))
                    if val:
                        rows[i]["hpp_moving_average"] = round(float(val), 4)
                        rows[i]["sumber_hpp"] = "riwayat_rekap"
            filled_rekap = sum(1 for i, _, _ in missing if rows[i]["sumber_hpp"] == "riwayat_rekap")

            sisa2 = [m for m in missing if rows[m[0]]["sumber_hpp"] not in ("riwayat", "riwayat_rekap")]
            if sisa2:
                riwayat_beli = load_riwayat_pembelian(conn, {pid for _, pid, _ in sisa2})
                for i, pid, cid in sisa2:
                    val = riwayat_beli.get((pid, cid))
                    if val:
                        rows[i]["hpp_moving_average"] = round(float(val), 4)
                        rows[i]["sumber_hpp"] = "riwayat_pembelian"
            filled_beli = sum(1 for i, _, _ in missing if rows[i]["sumber_hpp"] == "riwayat_pembelian")

            for i, _, _ in missing:
                stok = rows[i]["stok_akhir"]
                hpp = rows[i]["hpp_moving_average"]
                if stok is not None and hpp is not None:
                    try:
                        rows[i]["nilai_persediaan"] = round(float(stok) * float(hpp), 2)
                    except (TypeError, ValueError):
                        pass
            logger.info("Backfill HPP: %d dari riwayat %s, %d dari lap_kartu_stok_rekap, %d dari pembelian, total %d",
                        filled_self, TABLE, filled_rekap, filled_beli,
                        filled_self + filled_rekap + filled_beli)

        logger.info("Built %d rows (produk x cabang)", len(rows))

        if args.dry_run:
            for r in rows[:8]:
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
