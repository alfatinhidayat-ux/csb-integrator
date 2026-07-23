import concurrent.futures
import json
import logging
import time
import uuid

import httpx

from auth import AuthManager
from config import Config
from db import DatabaseManager

logger = logging.getLogger("brighter-sync")

CUSTOMER_PATH = "/master/customer"
CUSTOMER_TABLE = "customer"

DEPOSIT_PATH = "/master/deposit"
DEPOSIT_TABLE = "deposit_customer"

DEPOSIT_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS `{DEPOSIT_TABLE}` (
    `deposit_id` BIGINT NOT NULL,
    `deposit_cabang_id` INT NULL,
    `deposit_customer_id` BIGINT NULL,
    `deposit_transfer_bank_id` BIGINT NULL,
    `deposit_transfer_bank_nama` VARCHAR(150) NULL,
    `deposit_status_dokumen` VARCHAR(50) NULL,
    `deposit_no_faktur` VARCHAR(100) NULL,
    `deposit_customer_nama` VARCHAR(150) NULL,
    `deposit_jumlah` DECIMAL(15,2) NULL,
    `deposit_tanggal` DATE NULL,
    `deposit_cara_bayar` VARCHAR(50) NULL,
    `deposit_customer_data` JSON NULL,
    `created_by` VARCHAR(100) NULL,
    `updated_by` VARCHAR(100) NULL,
    `deleted_by` VARCHAR(100) NULL,
    `revised` INT NULL,
    `created_at` DATETIME NULL,
    `updated_at` DATETIME NULL,
    `deleted_at` DATETIME NULL,
    `synced_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`deposit_id`),
    KEY `deposit_customer_cabang_idx` (`deposit_cabang_id`),
    KEY `deposit_customer_customer_idx` (`deposit_customer_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

DEPOSIT_INSERT_COLUMNS = [
    "deposit_id", "deposit_cabang_id", "deposit_customer_id",
    "deposit_transfer_bank_id", "deposit_transfer_bank_nama",
    "deposit_status_dokumen", "deposit_no_faktur", "deposit_customer_nama",
    "deposit_jumlah", "deposit_tanggal", "deposit_cara_bayar",
    "deposit_customer_data",
    "created_by", "updated_by", "deleted_by", "revised",
    "created_at", "updated_at", "deleted_at",
]

PINJAMAN_PATH = "/personalia/pengajuan_pinjaman_karyawan"
PINJAMAN_TABLE = "pengajuan_pinjaman_karyawan"

PINJAMAN_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS `{PINJAMAN_TABLE}` (
    `ppinjaman_id` BIGINT NOT NULL,
    `ppinjaman_cabang_id` INT NULL,
    `ppinjaman_no` VARCHAR(100) NULL,
    `ppinjaman_tanggal` DATE NULL,
    `ppinjaman_karyawan_id` BIGINT UNSIGNED NULL,
    `ppinjaman_karyawan_lain` VARCHAR(191) NULL,
    `ppinjaman_karyawan_atasan_id` BIGINT NULL,
    `ppinjaman_departemen_id` BIGINT NULL,
    `ppinjaman_jabatan_id` BIGINT NULL,
    `ppinjaman_golongan_id` BIGINT NULL,
    `ppinjaman_level_id` BIGINT NULL,
    `ppinjaman_jenis` VARCHAR(50) NULL,
    `ppinjaman_status` VARCHAR(50) NULL,
    `ppinjaman_aktif` VARCHAR(20) NULL,
    `ppinjaman_keterangan` TEXT NULL,
    `ppinjaman_nilai` DECIMAL(15,2) NULL,
    `ppinjaman_pelunasan` DECIMAL(15,2) NULL,
    `ppinjaman_sisa` DECIMAL(15,2) NULL,
    `ppinjaman_tgl_awal_pelunasan` DATE NULL,
    `ppinjaman_termin_waktu` VARCHAR(50) NULL,
    `ppinjaman_setuju_1` VARCHAR(100) NULL,
    `ppinjaman_setuju_1_status` VARCHAR(50) NULL,
    `ppinjaman_setuju_2` VARCHAR(100) NULL,
    `ppinjaman_setuju_2_status` VARCHAR(50) NULL,
    `ppinjaman_setuju_3` VARCHAR(100) NULL,
    `ppinjaman_setuju_3_status` VARCHAR(50) NULL,
    `ppinjaman_dkasbank_pinjaman_karyawan_id` BIGINT NULL,
    `created_by` VARCHAR(100) NULL,
    `updated_by` VARCHAR(100) NULL,
    `deleted_by` VARCHAR(100) NULL,
    `revised` INT NULL,
    `created_at` DATETIME NULL,
    `updated_at` DATETIME NULL,
    `deleted_at` DATETIME NULL,
    `synced_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`ppinjaman_id`),
    KEY `ppinjaman_cabang_idx` (`ppinjaman_cabang_id`),
    KEY `ppinjaman_karyawan_idx` (`ppinjaman_karyawan_id`),
    CONSTRAINT `fk_ppinjaman_karyawan`
        FOREIGN KEY (`ppinjaman_karyawan_id`) REFERENCES `karyawan` (`id`)
        ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# Kolom API langsung (selain timestamp_data yang di-flatten)
PINJAMAN_API_COLUMNS = [
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

TIMESTAMP_COLUMNS = [
    "created_by", "updated_by", "deleted_by", "revised",
    "created_at", "updated_at", "deleted_at",
]

LOG_INSERTED = "  -> %d records inserted into %s (old data truncated)"

PINJAMAN_INSERT_COLUMNS = PINJAMAN_API_COLUMNS + TIMESTAMP_COLUMNS

# Kolom customer csb_db yang diisi dari API (urutan tetap untuk INSERT)
INSERT_COLUMNS = [
    "id", "uuid", "kode", "nama", "kelamin", "tanggal_lahir", "alamat",
    "notelp", "email", "npwp", "no_identitas", "jns_identitas", "kategori_id",
    "deposit_rp", "preward_total", "preward_exp", "preward_exp_date",
    "foto_path", "alamat_detail", "keterangan", "aktif", "cabang_id",
    "created_by", "updated_by", "deleted_by", "revised",
    "created_at", "updated_at", "deleted_at",
]

# Batas panjang varchar di table customer, string lebih panjang dipotong
VARCHAR_LIMITS = {
    "kode": 50, "nama": 150, "notelp": 50, "email": 191, "npwp": 50,
    "no_identitas": 50, "jns_identitas": 20, "foto_path": 255,
    "created_by": 100, "updated_by": 100, "deleted_by": 100,
}


def _clip(value, column):
    if value is None:
        return None
    value = str(value)
    limit = VARCHAR_LIMITS.get(column)
    if limit and len(value) > limit:
        return value[:limit]
    return value


class CustomerSyncer:
    """Sync /master/customer Brighter ke table `customer` csb_db.

    Loop per cabang_id, ambil semua halaman sesuai paging, lalu
    truncate + isi ulang table customer (kolom cabang_id ditambahkan
    bila belum ada).
    """

    def __init__(self, config: Config, auth: AuthManager, db: DatabaseManager):
        self.config = config
        self.auth = auth
        self.db = db

    def ensure_cabang_column(self):
        columns = self.db.get_table_columns(CUSTOMER_TABLE)
        if "cabang_id" not in columns:
            self.db._execute(
                f"ALTER TABLE `{CUSTOMER_TABLE}` "
                "ADD COLUMN `cabang_id` INT NULL AFTER `kategori_id`, "
                "ADD INDEX `customer_cabang_id_index` (`cabang_id`)"
            )
            self.db.conn.commit()
            logger.info("Column cabang_id added to %s", CUSTOMER_TABLE)

    def load_valid_kategori_ids(self) -> set:
        try:
            cur = self.db._execute("SELECT id FROM customer_kategori")
            return {row["id"] for row in cur.fetchall()}
        except Exception:
            return set()

    def _fetch_page(self, path: str, base_params: dict, page: int) -> tuple[list[dict], dict]:
        params = dict(base_params)
        params["page"] = str(page)
        params["results_per_page"] = str(self.config.results_per_page)
        for attempt in range(self.config.max_retries):
            try:
                self.auth.ensure_token()
                resp = httpx.get(
                    f"{self.config.base_url}{path}",
                    params=params,
                    headers=self.auth.get_headers(),
                    timeout=self.config.request_timeout,
                )
                if resp.status_code == 401 and attempt < self.config.max_retries - 1:
                    self.auth.login()
                    continue
                resp.raise_for_status()
                data = resp.json()
                batch = data.get("data", [])
                if not isinstance(batch, list):
                    batch = []
                paging = data.get("paging") or {}
                return batch, paging
            except Exception:
                if attempt < self.config.max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise

    def fetch_all_pages(
        self, path: str, base_params: dict, label: str, max_workers: int = 8
    ) -> list[dict]:
        first_batch, paging = self._fetch_page(path, base_params, 1)
        total_pages = int(paging.get("total_pages", 1) or 1)
        total_records = int(paging.get("total_records", len(first_batch)) or 0)
        logger.info("  %s: %d records / %d pages", label, total_records, total_pages)

        records = list(first_batch)
        if total_pages > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(self._fetch_page, path, base_params, page): page
                    for page in range(2, total_pages + 1)
                }
                for future in concurrent.futures.as_completed(futures):
                    batch, _ = future.result()
                    records.extend(batch)

        if len(records) != total_records:
            logger.warning(
                "  %s: fetched %d records but paging said %d, refetching sequentially",
                label, len(records), total_records,
            )
            records = list(first_batch)
            for page in range(2, total_pages + 1):
                batch, _ = self._fetch_page(path, base_params, page)
                records.extend(batch)
            if len(records) != total_records:
                logger.warning(
                    "  %s: still %d vs %d after refetch (data may have changed on server)",
                    label, len(records), total_records,
                )
        return records

    def fetch_all_customers(self) -> list[dict]:
        params = {}
        return self.fetch_all_pages(CUSTOMER_PATH, params, "All Customers")

    def map_record(self, rec: dict, cabang_id: int, valid_kategori: set) -> dict:
        ts = rec.get("timestamp_data") or {}
        cust_id = rec.get("cust_id")

        kelamin = rec.get("cust_kelamin")
        if kelamin not in ("L", "P"):
            kelamin = None

        kategori_id = rec.get("cust_kategori_id")
        if valid_kategori and kategori_id not in valid_kategori:
            kategori_id = None

        alamat_detail = rec.get("cust_alamat_detail")
        if isinstance(alamat_detail, (dict, list)):
            alamat_detail = json.dumps(alamat_detail, ensure_ascii=False)

        foto = rec.get("cust_foto_data")
        if isinstance(foto, (dict, list)):
            foto = None

        row = {
            "id": cust_id,
            "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"brighter-customer-{cust_id}")),
            "kode": _clip(rec.get("cust_no") or str(cust_id), "kode"),
            "nama": _clip(rec.get("cust_nama") or "", "nama"),
            "kelamin": kelamin,
            "tanggal_lahir": rec.get("cust_tgllahir") or None,
            "alamat": rec.get("cust_alamat"),
            "notelp": _clip(rec.get("cust_hp"), "notelp"),
            "email": _clip(rec.get("cust_email"), "email"),
            "npwp": _clip(rec.get("cust_npwp"), "npwp"),
            "no_identitas": _clip(rec.get("cust_no_identitas"), "no_identitas"),
            "jns_identitas": _clip(rec.get("cust_jns_identitas"), "jns_identitas"),
            "kategori_id": kategori_id,
            "deposit_rp": rec.get("cust_deposit_rp") or 0,
            "preward_total": rec.get("cust_preward_total") or 0,
            "preward_exp": rec.get("cust_preward_exp") or 0,
            "preward_exp_date": rec.get("cust_preward_exp_date") or None,
            "foto_path": _clip(foto, "foto_path"),
            "alamat_detail": alamat_detail,
            "keterangan": rec.get("cust_keterangan"),
            "aktif": 1 if rec.get("cust_aktif") == "Aktif" else 0,
            "cabang_id": rec.get("cust_cabang_id") or cabang_id,
            "created_by": _clip(ts.get("created_by"), "created_by"),
            "updated_by": _clip(ts.get("updated_by"), "updated_by"),
            "deleted_by": _clip(ts.get("deleted_by"), "deleted_by"),
            "revised": ts.get("revised"),
            "created_at": ts.get("created_at") or None,
            "updated_at": ts.get("updated_at") or None,
            "deleted_at": ts.get("deleted_at") or None,
        }
        return row

    def upsert_all(self, rows: list[dict], chunk_size: int = 500):
        updatable = [c for c in INSERT_COLUMNS if c not in ("id",)]
        col_names = ", ".join(f"`{c}`" for c in INSERT_COLUMNS)
        placeholders = ", ".join(["%s"] * len(INSERT_COLUMNS))
        updates = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in updatable)
        sql = (
            f"INSERT INTO `{CUSTOMER_TABLE}` ({col_names}) VALUES ({placeholders}) "
            f"ON DUPLICATE KEY UPDATE {updates}"
        )
        cur = self.db.conn.cursor()
        for start in range(0, len(rows), chunk_size):
            chunk = rows[start:start + chunk_size]
            batch = [[row[c] for c in INSERT_COLUMNS] for row in chunk]
            cur.executemany(sql, batch)
        self.db.conn.commit()

    def map_deposit_record(self, rec: dict) -> dict:
        ts = rec.get("timestamp_data") or {}
        customer_data = rec.get("deposit_customer_data")
        if isinstance(customer_data, (dict, list)):
            customer_data = json.dumps(customer_data, ensure_ascii=False)
        return {
            "deposit_id": rec.get("deposit_id"),
            "deposit_cabang_id": rec.get("deposit_cabang_id"),
            "deposit_customer_id": rec.get("deposit_customer_id"),
            "deposit_transfer_bank_id": rec.get("deposit_transfer_bank_id"),
            "deposit_transfer_bank_nama": _clip(rec.get("deposit_transfer_bank_nama"), "nama"),
            "deposit_status_dokumen": rec.get("deposit_status_dokumen"),
            "deposit_no_faktur": rec.get("deposit_no_faktur"),
            "deposit_customer_nama": _clip(rec.get("deposit_customer_nama"), "nama"),
            "deposit_jumlah": rec.get("deposit_jumlah") or 0,
            "deposit_tanggal": rec.get("deposit_tanggal") or None,
            "deposit_cara_bayar": rec.get("deposit_cara_bayar"),
            "deposit_customer_data": customer_data,
            "created_by": _clip(ts.get("created_by"), "created_by"),
            "updated_by": _clip(ts.get("updated_by"), "updated_by"),
            "deleted_by": _clip(ts.get("deleted_by"), "deleted_by"),
            "revised": ts.get("revised"),
            "created_at": ts.get("created_at") or None,
            "updated_at": ts.get("updated_at") or None,
            "deleted_at": ts.get("deleted_at") or None,
        }

    def sync_deposits(self, chunk_size: int = 500) -> int:
        logger.info("  SYNC %-40s %s", "Brighter Deposit Customer", DEPOSIT_PATH)
        params = {"timestamp_data": "true", "deposit_customer_data": "true"}
        records = self.fetch_all_pages(DEPOSIT_PATH, params, "Deposit")

        rows_by_id: dict = {}
        for rec in records:
            row = self.map_deposit_record(rec)
            if row["deposit_id"] is None:
                continue
            if row["deposit_id"] in rows_by_id:
                logger.warning(
                    "  Duplicate deposit_id %s, keeping first occurrence", row["deposit_id"]
                )
                continue
            rows_by_id[row["deposit_id"]] = row
        rows = list(rows_by_id.values())

        self.db.reconnect()
        self.db._execute(DEPOSIT_CREATE_SQL)
        self.db._execute(f"TRUNCATE TABLE `{DEPOSIT_TABLE}`")
        col_names = ", ".join(f"`{c}`" for c in DEPOSIT_INSERT_COLUMNS)
        placeholders = ", ".join(["%s"] * len(DEPOSIT_INSERT_COLUMNS))
        sql = f"INSERT INTO `{DEPOSIT_TABLE}` ({col_names}) VALUES ({placeholders})"
        cur = self.db.conn.cursor()
        for start in range(0, len(rows), chunk_size):
            chunk = rows[start:start + chunk_size]
            batch = [[row[c] for c in DEPOSIT_INSERT_COLUMNS] for row in chunk]
            cur.executemany(sql, batch)
        self.db.conn.commit()
        logger.info(LOG_INSERTED, len(rows), DEPOSIT_TABLE)
        return len(rows)

    def map_pinjaman_record(self, rec: dict) -> dict:
        ts = rec.get("timestamp_data") or {}
        row = {col: rec.get(col) for col in PINJAMAN_API_COLUMNS}
        for col in ("ppinjaman_tanggal", "ppinjaman_tgl_awal_pelunasan"):
            row[col] = row[col] or None
        for col in (
            "ppinjaman_karyawan_lain", "ppinjaman_termin_waktu",
            "ppinjaman_setuju_1", "ppinjaman_setuju_2", "ppinjaman_setuju_3",
        ):
            if row[col] is not None:
                row[col] = str(row[col])
        row.update({
            "created_by": _clip(ts.get("created_by"), "created_by"),
            "updated_by": _clip(ts.get("updated_by"), "updated_by"),
            "deleted_by": _clip(ts.get("deleted_by"), "deleted_by"),
            "revised": ts.get("revised"),
            "created_at": ts.get("created_at") or None,
            "updated_at": ts.get("updated_at") or None,
            "deleted_at": ts.get("deleted_at") or None,
        })
        return row

    def load_valid_karyawan_ids(self) -> set:
        try:
            cur = self.db._execute("SELECT id FROM karyawan")
            return {row["id"] for row in cur.fetchall()}
        except Exception:
            return set()

    def sync_pinjaman_karyawan(self, chunk_size: int = 500) -> int:
        logger.info("  SYNC %-40s %s", "Brighter Pengajuan Pinjaman Karyawan", PINJAMAN_PATH)
        params = {"timestamp_data": "true"}
        records = self.fetch_all_pages(PINJAMAN_PATH, params, "Pinjaman Karyawan")
        valid_karyawan = self.load_valid_karyawan_ids()

        rows_by_id: dict = {}
        unknown_karyawan = 0
        for rec in records:
            row = self.map_pinjaman_record(rec)
            if row["ppinjaman_id"] is None:
                continue
            if row["ppinjaman_id"] in rows_by_id:
                logger.warning(
                    "  Duplicate ppinjaman_id %s, keeping first occurrence", row["ppinjaman_id"]
                )
                continue
            # FK ke karyawan(id): id yang tidak dikenal di-NULL-kan supaya
            # insert tidak gagal, tapi tetap dihitung dan dilaporkan.
            if (
                valid_karyawan
                and row["ppinjaman_karyawan_id"] is not None
                and row["ppinjaman_karyawan_id"] not in valid_karyawan
            ):
                logger.warning(
                    "  ppinjaman_id %s: karyawan_id %s tidak ada di table karyawan, set NULL",
                    row["ppinjaman_id"], row["ppinjaman_karyawan_id"],
                )
                row["ppinjaman_karyawan_id"] = None
                unknown_karyawan += 1
            rows_by_id[row["ppinjaman_id"]] = row
        rows = list(rows_by_id.values())
        if unknown_karyawan:
            logger.warning(
                "  %d record punya karyawan_id yang tidak dikenal (di-set NULL)",
                unknown_karyawan,
            )

        self.db.reconnect()
        # Drop + create supaya skema (termasuk FK ke karyawan) selalu
        # mengikuti definisi di kode; datanya memang full reload tiap run.
        self.db._execute(f"DROP TABLE IF EXISTS `{PINJAMAN_TABLE}`")
        self.db._execute(PINJAMAN_CREATE_SQL)
        col_names = ", ".join(f"`{c}`" for c in PINJAMAN_INSERT_COLUMNS)
        placeholders = ", ".join(["%s"] * len(PINJAMAN_INSERT_COLUMNS))
        sql = f"INSERT INTO `{PINJAMAN_TABLE}` ({col_names}) VALUES ({placeholders})"
        cur = self.db.conn.cursor()
        for start in range(0, len(rows), chunk_size):
            chunk = rows[start:start + chunk_size]
            batch = [[row[c] for c in PINJAMAN_INSERT_COLUMNS] for row in chunk]
            cur.executemany(sql, batch)
        self.db.conn.commit()
        logger.info(LOG_INSERTED, len(rows), PINJAMAN_TABLE)
        return len(rows)

    def run(self) -> int:
        self.ensure_cabang_column()
        valid_kategori = self.load_valid_kategori_ids()

        logger.info("  SYNC %-40s %s", "Brighter Customer", CUSTOMER_PATH)
        records = self.fetch_all_customers()

        rows_by_id: dict = {}
        for rec in records:
            row = self.map_record(rec, 1, valid_kategori)
            if row["id"] is None:
                continue
            if row["id"] in rows_by_id:
                logger.warning(
                    "  Duplicate cust_id %s, keeping first occurrence", row["id"],
                )
                continue
            rows_by_id[row["id"]] = row
        logger.info("    -> %d records fetched", len(rows_by_id))

        rows = list(rows_by_id.values())
        self.db.reconnect()
        self.upsert_all(rows)
        logger.info("  -> %d records upserted into %s", len(rows), CUSTOMER_TABLE)

        deposit_count = self.sync_deposits()
        pinjaman_count = self.sync_pinjaman_karyawan()
        return len(rows) + deposit_count + pinjaman_count
