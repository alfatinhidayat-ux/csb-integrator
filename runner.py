import logging
import time
from datetime import datetime
from typing import Optional

import httpx

from auth import AuthManager
from config import Config
from db import DatabaseManager
from endpoints import ENDPOINTS, Strategy
from base import SyncError
from delta import DeltaSyncer
from full import FullSyncer

logger = logging.getLogger("brighter-sync")


class SyncRunner:
    def __init__(self, config: Config):
        self.config = config
        self.auth = AuthManager(config)
        self.db = DatabaseManager(config)
        self.stats: dict = {"cabang": 0, "endpoint": 0, "records": 0, "errors": 0, "skipped": 0}

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
        if ep.strategy == Strategy.DELTA:
            syncer = DeltaSyncer(self.config, self.auth, self.db, ep, cabang_id)
        else:
            syncer = FullSyncer(self.config, self.auth, self.db, ep, cabang_id)
        try:
            count = syncer.sync()
            return count
        finally:
            syncer.close()

    def clean_start(self):
        tables = set()
        for ep in ENDPOINTS:
            if ep.table and not ep.skip:
                tables.add(ep.table)
        for table in sorted(tables):
            try:
                self.db.truncate_table(table)
            except Exception:
                pass
        self.db._execute("UPDATE sync_meta SET last_synced_at = NULL")
        self.db.conn.commit()
        logger.info("All tables truncated, sync meta reset")

    def run_all(self):
        self.db.connect()
        self.db.ensure_sync_meta()
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

        self.run_child_endpoints()

        self.db.close()
        self._log_summary()

    def run_child_endpoints(self):
        import concurrent.futures

        self.db.connect()
        self.db.ensure_sync_meta()
        self.auth.login()
        logger.info("=== Running child endpoints only (resume mode) ===")
        for ep in ENDPOINTS:
            if not ep.parent_table or not ep.parent_key:
                continue
            logger.info("=== Child Endpoint: %s ===", ep.name)
            self.db.reconnect()
            col = ep.parent_column or ep.parent_key
            try:
                parent_tuples = self.db.get_distinct_with_cabang(ep.parent_table, col)
            except Exception:
                logger.warning("  SKIP %s: table %s or column %s not found", ep.name, ep.parent_table, col)
                continue
            parent_ids = [p[0] for p in parent_tuples]
            pid_to_cabang = {p[0]: p[1] for p in parent_tuples}
            errors = 0

            def fetch_child(pid):
                path = ep.path.replace(f":{ep.parent_key}", str(pid))
                params = dict(ep.params)
                params["page"] = "1"
                params["results_per_page"] = "100"
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
                        cabang_id = pid_to_cabang.get(pid, 1)
                        for rec in batch:
                            if ep.parent_key not in rec:
                                rec[ep.parent_key] = pid
                            if "cabang_id" not in rec:
                                rec["cabang_id"] = cabang_id
                        return batch
                    except Exception:
                        if attempt < self.config.max_retries - 1:
                            time.sleep(2 ** attempt)
                            continue
                        return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                futures = {pool.submit(fetch_child, pid): pid for pid in parent_ids}
                all_records = []
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    if result is not None:
                        all_records.extend(result)
                    else:
                        errors += 1

            if all_records:
                self.db.reconnect()
                self.db.ensure_table(ep.table, all_records[0])
                cabang_id = pid_to_cabang.get(parent_ids[0], 1) if parent_ids else 1
                self.db.upsert_records(ep.table, all_records, cabang_id)
            logger.info("  -> %d records upserted (%d skipped)", len(all_records), errors)
            self.stats["endpoint"] += 1
            self.stats["records"] += len(all_records)
        self.db.close()
        self._log_summary()

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
