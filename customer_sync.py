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
    """Sync /master/customer Brighter ke table `customer` csb_db (upsert only).

    Ambil semua halaman /master/customer (kolom cust_cabang_id per record),
    lalu upsert ke table `customer` csb_db — TANPA menyentuh tabel lain.
    Kolom cabang_id ditambahkan ke table customer bila belum ada.
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
        return len(rows)
