import json
import logging
import time
import hashlib
import re
import uuid as _uuid
from datetime import datetime
from typing import Optional

import httpx

from auth import AuthManager
from config import Config
from db import DatabaseManager
from endpoints import ENDPOINTS, FINANCE_ENDPOINTS, SALES_ENDPOINTS, Strategy
from base import SyncError
from delta import DeltaSyncer
from full import FullSyncer

logger = logging.getLogger("brighter-sync")


def _base36_2(value: int) -> str:
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return alphabet[(value // 36) % 36] + alphabet[value % 36]


def _user_code_candidates(user_name: str) -> list[str]:
    clean = re.sub(r"[^A-Za-z0-9]", "", str(user_name or "")).upper()
    if not clean:
        clean = "US"
    candidates = [(clean + "X")[:2]]
    for i in range(len(clean)):
        for j in range(i + 1, len(clean)):
            candidates.append(clean[i] + clean[j])
    return list(dict.fromkeys(candidates))


class SyncRunner:
    def __init__(self, config: Config):
        self.config = config
        self.auth = AuthManager(config)
        self.db = DatabaseManager(config)
        self.csb_db = DatabaseManager(config, target_db="csb")
        self.stats: dict = {"cabang": 0, "endpoint": 0, "records": 0, "errors": 0, "skipped": 0}

    def _db_for_endpoint(self, ep) -> DatabaseManager:
        return self.csb_db if getattr(ep, "target_db", "brighter") == "csb" else self.db

    def _connected_dbs(self) -> list[DatabaseManager]:
        seen = set()
        dbs = []
        for ep in ENDPOINTS:
            if ep.skip:
                continue
            db = self._db_for_endpoint(ep)
            if id(db) not in seen:
                seen.add(id(db))
                dbs.append(db)
        return dbs

    def _ensure_dbs_connected(self):
        for db in self._connected_dbs():
            if db.conn is None:
                db.connect()
            else:
                db.reconnect()
            db.ensure_sync_meta()

    def _close_dbs(self):
        for db in self._connected_dbs():
            db.close()

    def discover_cabangs(self) -> list[dict]:
        logger.info("Discovering cabangs from API...")
        cabangs = []
        page = 1
        while True:
            resp = httpx.get(
                f"{self.config.base_url}/master/cabang",
                params={"page": str(page), "results_per_page": "100", "cabang_aktif": "Aktif"},
                timeout=self.config.request_timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("data", [])
            if not batch:
                break
            cabangs.extend(batch)
            total = data.get("total", 0)
            if page * 100 >= total:
                break
            page += 1
        logger.info("Found %d active cabangs", len(cabangs))
        if cabangs:
            self.db.ensure_table("master_cabang", cabangs[0])
            self.db.upsert_records("master_cabang", cabangs, cabangs[0]["cabang_id"])
        return cabangs

    def get_cabang_ids(self) -> list[int]:
        if self.config.cabang_ids:
            return self.config.cabang_ids
        cabangs = self.discover_cabangs()
        return [c["cabang_id"] for c in cabangs]

    def run_endpoint(self, ep, cabang_id: int) -> int:
        self.auth.ensure_token()
        db = self._db_for_endpoint(ep)
        if ep.strategy == Strategy.DELTA:
            syncer = DeltaSyncer(self.config, self.auth, db, ep, cabang_id)
        else:
            syncer = FullSyncer(self.config, self.auth, db, ep, cabang_id)
        try:
            count = syncer.sync()
            return count
        finally:
            syncer.close()

    def clean_start(self):
        tables_by_db = {}
        for ep in ENDPOINTS:
            if ep.table and not ep.skip:
                db = self._db_for_endpoint(ep)
                tables_by_db.setdefault(db, set()).add(ep.table)
        for db, tables in tables_by_db.items():
            for table in sorted(tables):
                try:
                    db.truncate_table(table)
                except Exception:
                    pass
            db._execute("UPDATE sync_meta SET last_synced_at = NULL")
            db.conn.commit()
        logger.info("All tables truncated, sync meta reset")

    def clean_endpoint_tables(self, endpoints, db=None):
        for ep in endpoints:
            target = db or self._db_for_endpoint(ep)
            target.reconnect()
            try:
                target.truncate_table(ep.table)
            except Exception:
                pass
            target._execute(
                "DELETE FROM sync_meta WHERE endpoint = %s",
                (ep.name,),
            )
            target.conn.commit()
        logger.info("Selected endpoint tables truncated, sync meta cleared")

    def run_all(self):
        self._ensure_dbs_connected()
        self.clean_start()
        logger.info("Starting sync at %s", datetime.now().isoformat())

        cabang_ids = self.get_cabang_ids()
        self.stats["cabang"] = len(cabang_ids)
        logger.info("Will sync %d cabang(s): %s", len(cabang_ids), cabang_ids)

        for cabang_id in cabang_ids:
            logger.info("=== Cabang %d ===", cabang_id)
            for ep in ENDPOINTS:
                if ep.skip:
                    logger.debug("  SKIP %s (%s)", ep.name, ep.path)
                    self.stats["skipped"] += 1
                    continue
                if not self._should_sync(ep, cabang_id):
                    logger.debug("  SKIP %s (path param)", ep.name)
                    self.stats["skipped"] += 1
                    continue
                logger.info("  SYNC %-30s %-10s %s", ep.name, f"[{ep.strategy.value}]", ep.path)
                try:
                    count = self.run_endpoint(ep, cabang_id)
                    logger.info("    -> %d records upserted", count)
                    self.stats["endpoint"] += 1
                    self.stats["records"] += count
                except SyncError as e:
                    logger.error("    ERROR: %s", e)
                    self.stats["errors"] += 1
                except Exception as e:
                    logger.exception("    UNEXPECTED ERROR: %s", e)
                    self.stats["errors"] += 1

        self.run_child_endpoints(finalize=False)

        self._close_dbs()
        self._log_summary()

    def run_users_only(self):
        self._ensure_dbs_connected()
        self.auth.login()
        users_ep = next(ep for ep in ENDPOINTS if ep.name == "Users")
        detail_ep = next(ep for ep in ENDPOINTS if ep.name == "User by ID")
        self.clean_endpoint_tables([users_ep, detail_ep])

        cabang_ids = self.get_cabang_ids()
        self.stats["cabang"] = len(cabang_ids)
        logger.info("Will sync users for %d cabang(s): %s", len(cabang_ids), cabang_ids)

        all_users = []
        for cabang_id in cabang_ids:
            logger.info("  SYNC %-30s %-10s %s", users_ep.name, f"[{users_ep.strategy.value}]", users_ep.path)
            records = self.fetch_endpoint_records(users_ep, cabang_id)
            all_users.extend(records)
            count = len(records)
            logger.info("    -> %d records upserted", count)
            self.stats["endpoint"] += 1
            self.stats["records"] += count
        self.normalize_user_codes(all_users)
        if all_users:
            db = self._db_for_endpoint(users_ep)
            db.ensure_table(users_ep.table, all_users[0])
            db.upsert_records(users_ep.table, all_users, 1)
            self.sync_karyawan_kode_user()
            for cabang_id in cabang_ids:
                db.update_sync_meta(users_ep.name, cabang_id, datetime.now())

        self.run_child_endpoints(only_names={detail_ep.name}, finalize=False)
        self._close_dbs()
        self._log_summary()

    def run_karyawan_only(self):
        self._ensure_dbs_connected()
        self.auth.login()

        ep = next(ep for ep in ENDPOINTS if ep.name == "Karyawan")
        db = self.csb_db

        existing_cols = db.get_table_columns("karyawan")
        if not existing_cols:
            logger.error("Table 'karyawan' not found in csb_db")
            return
        logger.info("csb_db.karyawan columns: %s", sorted(existing_cols))

        logger.info("Fetching all karyawan from API ...")
        all_records = []
        page = 1
        while True:
            params = dict(ep.params)
            params["page"] = str(page)
            params["results_per_page"] = str(self.config.results_per_page)
            data = self.auth.ensure_token() or None
            resp = httpx.get(
                f"{self.config.base_url}{ep.path}",
                params=params,
                headers=self.auth.get_headers(),
                timeout=self.config.request_timeout,
            )
            resp.raise_for_status()
            body = resp.json()
            batch = body.get("data", [])
            if not isinstance(batch, list):
                batch = [batch] if batch else []
            all_records.extend(batch)
            paging = body.get("paging", {})
            total_pages = paging.get("total_pages", 1)
            logger.info("  Page %d/%d — %d records", page, total_pages, len(batch))
            if page >= total_pages:
                break
            page += 1

        if not all_records:
            logger.warning("No karyawan records fetched from API")
            self._close_dbs()
            self._log_summary()
            return

        logger.info("Total fetched: %d records", len(all_records))

        FIELD_MAP = {
            "karyawan_id": "id",
            "karyawan_cabang_id": "cabang_id",
            "karyawan_no": "kode",
            "karyawan_nama": "nama",
            "karyawan_kelamin": "kelamin",
            "karyawan_agama": "agama",
            "karyawan_tgllahir": "tanggal_lahir",
            "karyawan_email": "email",
            "karyawan_notelp": "notelp",
            "karyawan_ktp": "noktp",
            "karyawan_foto_path": "foto_path",
            "karyawan_aktif": "aktif",
        }

        filtered = []
        for rec in all_records:
            row = {}
            for api_key, db_col in FIELD_MAP.items():
                if db_col not in existing_cols:
                    continue
                v = rec.get(api_key)
                if v is None:
                    continue
                if db_col == "aktif":
                    v = 1 if v == "Aktif" else 0
                row[db_col] = v
            if "karyawan_departemen_data" in rec and "departement_id" in existing_cols:
                dept = rec["karyawan_departemen_data"]
                if isinstance(dept, dict) and dept.get("departemen_id"):
                    row["departement_id"] = dept["departemen_id"]
            if "karyawan_jabatan_data" in rec:
                jab = rec["karyawan_jabatan_data"]
                if isinstance(jab, dict):
                    if "jabatan_id" in existing_cols and jab.get("jabatan_id"):
                        row["jabatan_id"] = jab["jabatan_id"]
                    if "karyawan_jabatan_data" in existing_cols:
                        row["karyawan_jabatan_data"] = json.dumps(jab, ensure_ascii=False)
            if "cabang_id" not in row:
                row["cabang_id"] = rec.get("karyawan_cabang_id", 1)
            if "uuid" not in row:
                row["uuid"] = str(_uuid.uuid4())
            filtered.append(row)

        if not filtered:
            logger.warning("No karyawan records matched csb_db.karyawan columns")
            self._close_dbs()
            self._log_summary()
            return

        logger.info("Upserting %d karyawan records to csb_db.karyawan ...", len(filtered))

        first = filtered[0]
        cols = list(first.keys())
        placeholders = ", ".join(["%s"] * len(cols))
        col_names = ", ".join(f"`{c}`" for c in cols)
        updates = ", ".join(
            f"`{c}` = VALUES(`{c}`)" for c in cols if c not in ("id", "uuid")
        )
        sql = f"INSERT INTO `karyawan` ({col_names}) VALUES ({placeholders})"
        if updates:
            sql += f" ON DUPLICATE KEY UPDATE {updates}"

        batch = []
        for row in filtered:
            batch.append([row.get(c) for c in cols])

        cur = db.conn.cursor()
        batch_size = 500
        for i in range(0, len(batch), batch_size):
            chunk = batch[i : i + batch_size]
            cur.executemany(sql, chunk)
            db.conn.commit()
            logger.info("    upserted %d/%d", min(i + batch_size, len(batch)), len(batch))

        self.stats["endpoint"] += 1
        self.stats["records"] += len(filtered)
        logger.info("Done: %d karyawan records upserted to csb_db.karyawan", len(filtered))

        self._close_dbs()
        self._log_summary()

    def fetch_endpoint_records(
        self,
        ep,
        cabang_id: int,
        path: str = None,
        tolerate_not_found: bool = False,
    ) -> list[dict]:
        records = []
        page = 1
        request_path = path or ep.path
        while True:
            params = dict(ep.params)
            params["page"] = str(page)
            params["results_per_page"] = str(self.config.results_per_page)
            if ep.cabang_param:
                params[ep.cabang_param] = str(cabang_id)
            for attempt in range(self.config.max_retries):
                try:
                    self.auth.ensure_token()
                    resp = httpx.get(
                        f"{self.config.base_url}{request_path}",
                        params=params,
                        headers=self.auth.get_headers(),
                        timeout=self.config.request_timeout,
                    )
                    if resp.status_code == 401 and attempt < self.config.max_retries - 1:
                        self.auth.login()
                        continue
                    if tolerate_not_found and resp.status_code == 404:
                        return records
                    resp.raise_for_status()
                    data = resp.json()
                    batch = data.get(ep.response_root, [])
                    if isinstance(batch, dict):
                        batch = [batch]
                    if not isinstance(batch, list):
                        batch = []
                    for rec in batch:
                        rec["cabang_id"] = cabang_id
                    records.extend(batch)
                    break
                except Exception:
                    if attempt < self.config.max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    raise
            if not batch or not self._has_more(data, page, ep.response_root):
                break
            page += 1
        return records

    def fetch_endpoint_records_parallel(self, ep, cabang_id: int, max_workers: int = 8) -> list[dict]:
        import concurrent.futures

        def fetch_page(page: int):
            params = dict(ep.params)
            params["page"] = str(page)
            params["results_per_page"] = str(self.config.results_per_page)
            if ep.cabang_param:
                params[ep.cabang_param] = str(cabang_id)
            for attempt in range(self.config.max_retries):
                try:
                    self.auth.ensure_token()
                    resp = httpx.get(
                        f"{self.config.base_url}{ep.path}",
                        params=params,
                        headers=self.auth.get_headers(),
                        timeout=self.config.request_timeout,
                    )
                    if resp.status_code == 401 and attempt < self.config.max_retries - 1:
                        self.auth.login()
                        continue
                    if resp.status_code == 404:
                        return [], 0
                    resp.raise_for_status()
                    data = resp.json()
                    batch = data.get(ep.response_root, [])
                    if isinstance(batch, dict):
                        batch = [batch]
                    if not isinstance(batch, list):
                        batch = []
                    for rec in batch:
                        rec["cabang_id"] = cabang_id
                    paging = data.get("paging") if isinstance(data, dict) else None
                    total_pages = int(paging.get("total_pages", 0)) if paging else 0
                    return batch, total_pages
                except Exception:
                    if attempt < self.config.max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    raise

        first_batch, total_pages = fetch_page(1)
        if total_pages <= 1:
            return first_batch

        records = list(first_batch)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(fetch_page, page): page for page in range(2, total_pages + 1)}
            for future in concurrent.futures.as_completed(futures):
                batch, _ = future.result()
                records.extend(batch)
        return records

    def ensure_placeholder_table(self, db: DatabaseManager, table: str):
        db._execute(
            f"""
            CREATE TABLE IF NOT EXISTS `{table}` (
                id INT AUTO_INCREMENT,
                cabang_id INT NOT NULL DEFAULT 1,
                synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (`id`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        db.conn.commit()

    def run_sales_only(self):
        self.run_brighter_endpoint_group(SALES_ENDPOINTS, "sales")

    def run_customer_only(self):
        from customer_sync import CustomerSyncer

        self._ensure_dbs_connected()
        self.auth.login()

        logger.info("Will sync all customers (no cabang filter)")

        syncer = CustomerSyncer(self.config, self.auth, self.csb_db)
        count = syncer.run()
        self.stats["endpoint"] += 1
        self.stats["records"] += count

        self._close_dbs()
        self._log_summary()

    def run_finance_only(self):
        self.run_brighter_endpoint_group(FINANCE_ENDPOINTS, "finance")

    def run_kasbank_only(self):
        """Clear + full re-sync tabel akuntansi kas/bank (masuk & keluar + child-nya)
        agar satu snapshot dengan tabel lain (mis. pinjaman_karyawan) sebelum rekon.

        Child `_item` (jurnal akun) dan `_bukti_pelunasan` DILEWATI karena hampir
        selalu kosong (404) — buang ±3000 request yang tidak menghasilkan data.
        Tabel yang relevan untuk rekon: header + `_piutang_karyawan`,
        `_penerimaan_lain`, `_pengeluaran_lain`."""
        skip_tables = {
            "akuntansi_kasbank_masuk_item",      # jurnal akun (jarang terisi)
            "akuntansi_kasbank_masuk_bukti_pelunasan",
        }
        kasbank_eps = [
            ep for ep in ENDPOINTS
            if ep.table
            and ep.table.startswith("akuntansi_kasbank")
            and ep.table not in skip_tables
            and not ep.skip
            and not (":" in ep.path and not ep.parent_table)
        ]
        self.run_brighter_endpoint_group(kasbank_eps, "kasbank")

    def run_brighter_endpoint_group(self, endpoints, label: str):
        t_group = time.time()
        self._ensure_dbs_connected()
        self.auth.login()
        db = self.csb_db
        self.clean_endpoint_tables(endpoints, db)
        for ep in endpoints:
            self.ensure_placeholder_table(db, ep.table)

        cabang_ids = self.get_cabang_ids()
        self.stats["cabang"] = len(cabang_ids)
        logger.info("Will sync %s endpoints for %d cabang(s): %s", label, len(cabang_ids), cabang_ids)

        main_endpoints = [ep for ep in endpoints if not ep.parent_table]
        child_endpoints = [ep for ep in endpoints if ep.parent_table]

        for ep in main_endpoints:
            all_records = []
            endpoint_cabang_ids = cabang_ids if ep.cabang_param else [1]
            t_ep = time.time()
            for cabang_id in endpoint_cabang_ids:
                logger.info("  SYNC %-40s %s cabang=%s", ep.name, ep.path, cabang_id)
                records = self.fetch_endpoint_records_parallel(ep, cabang_id)
                all_records.extend(records)
                logger.info("    -> %d records fetched", len(records))
            if all_records:
                db.ensure_table(ep.table, all_records[0])
                db.upsert_records(ep.table, all_records, 1)
            self.stats["endpoint"] += 1
            self.stats["records"] += len(all_records)
            logger.info("  -> %d records upserted into %s (%ds)", len(all_records), ep.table, int(time.time() - t_ep))

        for ep in child_endpoints:
            logger.info("=== Child Endpoint: %s (%s) ===", ep.name, ep.table)
            col = ep.parent_column or ep.parent_key
            try:
                parent_tuples = db.get_distinct_with_cabang(ep.parent_table, col, ep.parent_filter or "")
            except Exception:
                logger.warning("  SKIP %s: table %s or column %s not found", ep.name, ep.parent_table, col)
                continue

            seen = {}
            for pid, cid in parent_tuples:
                if pid is not None and pid not in seen:
                    seen[pid] = cid
            total = len(seen)
            if not total:
                logger.info("    tidak ada parent — dilewati")
                continue
            logger.info("    Akan fetch dari %d parent", total)

            import concurrent.futures
            all_records = []
            errors = 0
            t0 = time.time()
            done = 0
            _lock = __import__("threading").Lock()

            def fetch_one(item):
                pid, cabang_id = item
                path = ep.path.replace(f":{ep.parent_key}", str(pid))
                try:
                    records = self.fetch_endpoint_records(
                        ep,
                        cabang_id,
                        path=path,
                        tolerate_not_found=True,
                    )
                except Exception as exc:
                    return ("error", pid, exc, [])
                for rec in records:
                    if ep.parent_key not in rec:
                        rec[ep.parent_key] = pid
                    if col not in rec:
                        rec[col] = pid
                    if "cabang_id" not in rec:
                        rec["cabang_id"] = cabang_id
                return ("ok", pid, None, records)

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                futures = {pool.submit(fetch_one, item): item for item in seen.items()}
                for fut in concurrent.futures.as_completed(futures):
                    status, pid, exc, records = fut.result()
                    if status == "ok":
                        with _lock:
                            all_records.extend(records)
                    else:
                        logger.warning("  SKIP %s parent=%s: %s", ep.name, pid, exc)
                        errors += 1
                    done += 1
                    if done % 25 == 0 or done == total:
                        est = time.time() - t0
                        eta = est / done * (total - done) if done else 0
                        logger.info("    %s %d/%d (%.0f%%) | %d records, %d skip | sudah %dm%02ds, sisa ~%dm%02ds",
                                    ep.table, done, total, 100.0 * done / total,
                                    len(all_records), errors,
                                    int(est // 60), int(est % 60), int(eta // 60), int(eta % 60))

            if all_records:
                db.ensure_table(ep.table, all_records[0])
                db.upsert_records(ep.table, all_records, 1)
            self.stats["endpoint"] += 1
            self.stats["records"] += len(all_records)
            logger.info("  -> %d records upserted into %s (%d skipped)", len(all_records), ep.table, errors)

        logger.info("  Kasbank group selesai (%ds, total %d records)",
                    int(time.time() - t_group), self.stats["records"])
        self._close_dbs()
        self._log_summary()

    def normalize_user_codes(self, records: list[dict]):
        used = set()
        for rec in sorted(records, key=lambda r: (r.get("cabang_id", 0), r.get("user_id", 0))):
            current = rec.get("user_kode")
            code = str(current).strip().upper() if current is not None else ""
            if code and code not in used:
                rec["user_kode"] = code
                used.add(code)
                continue

            chosen = None
            for candidate in _user_code_candidates(rec.get("user_name", "")):
                if candidate not in used:
                    chosen = candidate
                    break
            if chosen is None:
                seed = f"{rec.get('user_name', '')}|{rec.get('user_id', '')}|{rec.get('cabang_id', '')}"
                digest = int(hashlib.sha1(seed.encode("utf-8")).hexdigest(), 16)
                for offset in range(36 * 36):
                    candidate = _base36_2(digest + offset)
                    if candidate not in used:
                        chosen = candidate
                        break
            rec["user_kode"] = chosen
            used.add(chosen)

    def sync_karyawan_kode_user(self):
        db = self.csb_db
        columns = db.get_table_columns("karyawan")
        if "kode_user" not in columns:
            db._execute("ALTER TABLE `karyawan` ADD COLUMN `kode_user` VARCHAR(50) NULL AFTER `kode`")
            db.conn.commit()

        db._execute("UPDATE `karyawan` SET `kode_user` = NULL")
        db._execute(
            """
            UPDATE `karyawan` k
            JOIN (
                SELECT su.user_karyawan, su.user_kode
                FROM `sistem_users` su
                JOIN (
                    SELECT user_karyawan, MIN(user_id) AS user_id
                    FROM `sistem_users`
                    WHERE user_karyawan IS NOT NULL
                      AND user_karyawan <> 0
                      AND user_kode IS NOT NULL
                      AND TRIM(user_kode) <> ''
                    GROUP BY user_karyawan
                ) chosen
                  ON chosen.user_karyawan = su.user_karyawan
                 AND chosen.user_id = su.user_id
            ) u
              ON u.user_karyawan = k.id
            SET k.kode_user = u.user_kode
            """
        )
        db.conn.commit()

    def run_child_endpoints(
        self,
        resume_from: str = None,
        only_names: set[str] = None,
        finalize: bool = True,
    ):
        import concurrent.futures

        self._ensure_dbs_connected()
        self.auth.login()
        if resume_from:
            logger.info("=== Resuming child endpoints from '%s' ===", resume_from)
        else:
            logger.info("=== Running all child endpoints ===")
        skipping = bool(resume_from)
        for ep in ENDPOINTS:
            if not ep.parent_table or not ep.parent_key:
                continue
            if only_names is not None and ep.name not in only_names:
                continue
            if skipping:
                if ep.name == resume_from:
                    skipping = False
                else:
                    logger.debug("  SKIP %s (before resume point)", ep.name)
                    continue
            logger.info("=== Child Endpoint: %s ===", ep.name)
            db = self._db_for_endpoint(ep)
            db.reconnect()
            col = ep.parent_column or ep.parent_key
            try:
                parent_tuples = db.get_distinct_with_cabang(ep.parent_table, col, ep.parent_filter or "")
            except Exception:
                logger.warning("  SKIP %s: table %s or column %s not found", ep.name, ep.parent_table, col)
                continue
            seen = {}
            for pid, cid in parent_tuples:
                if pid not in seen:
                    seen[pid] = cid
            parent_ids = list(seen.keys())
            pid_to_cabang = seen
            errors = 0

            def fetch_child(pid):
                path = ep.path.replace(f":{ep.parent_key}", str(pid))
                cabang_id = pid_to_cabang.get(pid, 1)
                all_batches = []
                page = 1
                while True:
                    params = dict(ep.params)
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
                            resp.raise_for_status()
                            data = resp.json()
                            batch = data.get(ep.response_root, [])
                            if isinstance(batch, dict):
                                batch = [batch]
                            if not isinstance(batch, list):
                                batch = []
                            for rec in batch:
                                if "id" in rec and rec["id"] is None:
                                    continue
                                if ep.parent_key not in rec:
                                    rec[ep.parent_key] = pid
                                if "cabang_id" not in rec:
                                    rec["cabang_id"] = cabang_id
                            all_batches.extend(batch)
                            break
                        except Exception:
                            if attempt < self.config.max_retries - 1:
                                time.sleep(2 ** attempt)
                                continue
                            return None
                    if not batch or not self._has_more(data, page, ep.response_root):
                        break
                    page += 1
                return all_batches

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                futures = {pool.submit(fetch_child, pid): pid for pid in parent_ids}
                all_records = []
                total_p = len(futures)
                done_p = 0
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    if result is not None:
                        all_records.extend(result)
                    else:
                        errors += 1
                    done_p += 1
                    if done_p % 50 == 0 or done_p == total_p:
                        logger.info("    progress %s: %d/%d parent (records=%d, skipped=%d)",
                                    ep.table, done_p, total_p, len(all_records), errors)

            if all_records:
                db.reconnect()
                db.ensure_table(ep.table, all_records[0])
                cabang_id = pid_to_cabang.get(parent_ids[0], 1) if parent_ids else 1
                db.upsert_records(ep.table, all_records, cabang_id)
            logger.info("  -> %d records upserted (%d skipped)", len(all_records), errors)
            self.stats["endpoint"] += 1
            self.stats["records"] += len(all_records)
        if finalize:
            self._close_dbs()
            self._log_summary()

    def _has_more(self, data: dict, page: int, response_root: str = "data") -> bool:
        if not isinstance(data, dict):
            return False
        paging = data.get("paging")
        if paging:
            return page < paging.get("total_pages", 0)
        total = data.get("total", 0)
        rpp = self.config.results_per_page
        total_pages = (total + rpp - 1) // rpp if total else 0
        if total_pages:
            return page < total_pages
        records = data.get(response_root, [])
        if isinstance(records, list):
            return len(records) >= rpp
        return False

    def _has_path_param(self, path: str) -> bool:
        return ":id" in path or ":produk_id" in path or ":cust_id" in path

    def _should_sync(self, ep, cabang_id: int) -> bool:
        if ep.path == "/master/cabang":
            return cabang_id == 1
        if self._has_path_param(ep.path):
            return False
        if not ep.cabang_param:
            return cabang_id == 1
        return True

    def _log_summary(self):
        logger.info("=" * 50)
        logger.info("SYNC COMPLETE")
        logger.info("  Cabangs synced:   %d", self.stats["cabang"])
        logger.info("  Endpoints synced: %d", self.stats["endpoint"])
        logger.info("  Records upserted: %d", self.stats["records"])
        logger.info("  Errors:           %d", self.stats["errors"])
        logger.info("  Skipped:          %d", self.stats["skipped"])
        logger.info("=" * 50)
