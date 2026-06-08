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

    def run_all(self):
        self.db.connect()
        self.db.ensure_sync_meta()
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
        for ep in ENDPOINTS:
            if not ep.parent_table or not ep.parent_key:
                continue
            logger.info("=== Child Endpoint: %s ===", ep.name)
            try:
                parent_ids = self.db.get_distinct(ep.parent_table, ep.parent_key)
            except Exception:
                logger.warning("  SKIP %s: table %s or column %s not found", ep.name, ep.parent_table, ep.parent_key)
                continue
            all_records = []
            errors = 0
            for pid in parent_ids:
                path = ep.path.replace(f":{ep.parent_key}", str(pid))
                params = dict(ep.params)
                params["page"] = "1"
                params["results_per_page"] = "100"
                self.auth.ensure_token()
                try:
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
                    all_records.extend(batch)
                except Exception as e:
                    errors += 1
                    continue
            if all_records:
                self.db.ensure_table(ep.table, all_records[0])
                if ep.strategy == Strategy.FULL_PAGING:
                    self.db.truncate_table(ep.table)
                self.db.upsert_records(ep.table, all_records, 1)
            logger.info("  -> %d records upserted (%d skipped)", len(all_records), errors)
            self.stats["endpoint"] += 1
            self.stats["records"] += len(all_records)

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
