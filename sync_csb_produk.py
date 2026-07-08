import argparse
import concurrent.futures
import logging
from datetime import datetime
from typing import Optional

import httpx

from auth import AuthManager
from base import BaseSyncer, SyncError
from config import Config
from csb_produk_config import RESOURCES
from csb_writer import CsbSafeWriter
from db import DatabaseManager
from endpoints import Strategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("csb-produk-sync")

COMMIT_BATCH_SIZE = 500


def get_record_timestamp(record: dict) -> Optional[datetime]:
    for key in ("updated_at", "created_at", "updatedAt", "createdAt", "tgl_sync"):
        val = record.get(key)
        if val:
            try:
                if isinstance(val, (int, float)):
                    return datetime.fromtimestamp(val)
                return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
    return None


def fetch_all_pages(syncer: BaseSyncer, extra_params: Optional[dict] = None) -> list[dict]:
    records: list[dict] = []
    page = 1
    while True:
        params = syncer._build_params(page=page)
        if extra_params:
            params.update(extra_params)
        data = syncer._request(params)
        batch = syncer._extract_records(data)
        if not batch:
            break
        records.extend(batch)
        if not syncer._has_more_pages(data, page):
            break
        page += 1
    return records


class Stats:
    def __init__(self):
        self.inserted = 0
        self.updated = 0
        self.skipped = 0
        self.errors = 0

    def add(self, outcome: str):
        if outcome == "insert":
            self.inserted += 1
        elif outcome == "update":
            self.updated += 1
        else:
            self.skipped += 1

    def __str__(self):
        return f"{self.inserted} inserted, {self.updated} updated, {self.skipped} skipped, {self.errors} errors"


def sync_top_level(resource, config: Config, auth: AuthManager, writer: CsbSafeWriter,
                    meta_db: DatabaseManager, limit: Optional[int], dry_run: bool) -> Stats:
    endpoint = resource["endpoint"]
    is_delta = endpoint.strategy == Strategy.DELTA
    meta_name = f"csb_produk:{resource['key']}"
    stats = Stats()

    syncer = BaseSyncer(config, auth, None, endpoint, cabang_id=1)
    try:
        last_synced = meta_db.get_last_synced(meta_name, 1) if is_delta else None
        extra = {"timestamp_data": "true"} if is_delta else None
        records = fetch_all_pages(syncer, extra_params=extra)
    finally:
        syncer.close()

    logger.info("[%s] fetched %d records from API", resource["key"], len(records))

    if not dry_run:
        writer.ping()  # fetch above may have taken a while; reconnect if the DB dropped us

    newest_in_batch: Optional[datetime] = None
    processed = 0
    for rec in records:
        if limit is not None and processed >= limit:
            logger.info("[%s] --limit %d reached, stopping", resource["key"], limit)
            break
        if is_delta:
            rec_ts = get_record_timestamp(rec)
            if rec_ts:
                if newest_in_batch is None or rec_ts > newest_in_batch:
                    newest_in_batch = rec_ts
                if last_synced and rec_ts <= last_synced:
                    continue
        try:
            outcome = writer.upsert(resource["table"], resource["pk"], rec, resource["update_cols"], dry_run=dry_run)
            stats.add(outcome)
        except Exception:
            logger.exception("[%s] failed to upsert record %s", resource["key"], rec.get(resource["pk"]))
            stats.errors += 1
        processed += 1
        if not dry_run and processed % COMMIT_BATCH_SIZE == 0:
            writer.commit()
            logger.info("[%s] committed %d/%d", resource["key"], processed, len(records))

    if not dry_run:
        writer.commit()
        if is_delta:
            if newest_in_batch:
                meta_db.update_sync_meta(meta_name, 1, newest_in_batch)
            elif not last_synced:
                meta_db.update_sync_meta(meta_name, 1, datetime.now())

    return stats


def fetch_child_records(config: Config, auth: AuthManager, endpoint, produk_id) -> list[dict]:
    path = endpoint.path.replace(":produk_id", str(produk_id))
    client = httpx.Client(base_url=config.base_url, timeout=config.request_timeout, follow_redirects=True)
    try:
        records: list[dict] = []
        page = 1
        while True:
            params = dict(endpoint.params)
            params["page"] = str(page)
            params["results_per_page"] = str(config.results_per_page)
            for attempt in range(config.max_retries):
                try:
                    auth.ensure_token()
                    resp = client.get(path, params=params, headers=auth.get_headers())
                    resp.raise_for_status()
                    data = resp.json()
                    break
                except (httpx.HTTPStatusError, httpx.RequestError):
                    if attempt < config.max_retries - 1:
                        continue
                    raise
            root = data.get(endpoint.response_root, data) if isinstance(data, dict) else data
            batch = [root] if isinstance(root, dict) else (root if isinstance(root, list) else [])
            if not batch:
                break
            records.extend(batch)
            total = data.get("total", 0) if isinstance(data, dict) else 0
            rpp = config.results_per_page
            total_pages = (total + rpp - 1) // rpp if total else 0
            if total_pages and page >= total_pages:
                break
            if not total_pages and len(batch) < rpp:
                break
            page += 1
        return records
    finally:
        client.close()


def sync_child(resource, config: Config, auth: AuthManager, writer: CsbSafeWriter,
                produk_ids: list, limit: Optional[int], dry_run: bool) -> Stats:
    endpoint = resource["endpoint"]
    stats = Stats()
    ids = produk_ids if limit is None else produk_ids[:limit]

    logger.info("[%s] fetching child records for %d products", resource["key"], len(ids))
    all_records: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fetch_child_records, config, auth, endpoint, pid): pid for pid in ids}
        for future in concurrent.futures.as_completed(futures):
            pid = futures[future]
            try:
                all_records.extend(future.result())
            except Exception:
                logger.exception("[%s] fetch failed for produk_id=%s", resource["key"], pid)
                stats.errors += 1

    logger.info("[%s] fetched %d child records, writing...", resource["key"], len(all_records))
    if not dry_run and all_records:
        writer.ping()  # fetch above may have taken a while; reconnect if the DB dropped us

    processed = 0
    for rec in all_records:
        try:
            outcome = writer.upsert(resource["table"], resource["pk"], rec, resource["update_cols"], dry_run=dry_run)
            stats.add(outcome)
        except Exception:
            logger.exception("[%s] failed to upsert record %s", resource["key"], rec.get(resource["pk"]))
            stats.errors += 1
        processed += 1
        if not dry_run and processed % COMMIT_BATCH_SIZE == 0:
            writer.commit()
            logger.info("[%s] committed %d/%d", resource["key"], processed, len(all_records))

    if not dry_run:
        writer.commit()

    return stats


def _safe_recover(writer: CsbSafeWriter):
    """Best-effort cleanup after a resource fails, so a dead/broken connection
    doesn't take down the remaining resources in the run."""
    try:
        writer.rollback()
    except Exception:
        try:
            writer.ping()
        except Exception:
            logger.exception("could not recover csb_db connection")


def main():
    parser = argparse.ArgumentParser(description="Sync Brighter product data into existing csb_db tables (upsert-only)")
    parser.add_argument("--env", action="store_true", help="load credentials from .env")
    parser.add_argument("--dry-run", action="store_true", help="fetch and map only, no writes")
    parser.add_argument("--limit", type=int, default=None, help="max records per resource (testing)")
    parser.add_argument("--tables", type=str, default=None, help="comma-separated resource keys to sync (default: all)")
    args = parser.parse_args()

    config = Config.from_env()
    resources = RESOURCES
    if args.tables:
        wanted = {t.strip() for t in args.tables.split(",")}
        resources = [r for r in RESOURCES if r["key"] in wanted]
        missing = wanted - {r["key"] for r in resources}
        if missing:
            raise SystemExit(f"Unknown resource key(s): {', '.join(sorted(missing))}")

    auth = AuthManager(config)
    auth.login()

    writer = CsbSafeWriter(config)
    writer.connect()

    meta_db = DatabaseManager(config)
    meta_db.connect()
    meta_db.ensure_sync_meta()

    if args.dry_run:
        logger.info("=== DRY RUN - no writes will be committed ===")

    try:
        produk_ids = None
        for resource in resources:
            logger.info("=== Resource: %s ===", resource["key"])
            try:
                if resource["parent"]:
                    if produk_ids is None:
                        cur = writer.conn.cursor()
                        cur.execute("SELECT produk_id FROM produk")
                        produk_ids = [row["produk_id"] for row in cur.fetchall()]
                        logger.info("Loaded %d produk_id from csb_db.produk", len(produk_ids))
                    stats = sync_child(resource, config, auth, writer, produk_ids, args.limit, args.dry_run)
                else:
                    stats = sync_top_level(resource, config, auth, writer, meta_db, args.limit, args.dry_run)
                logger.info("[%s] done: %s", resource["key"], stats)
            except SyncError as e:
                logger.error("[%s] sync error: %s", resource["key"], e)
                _safe_recover(writer)
            except Exception:
                logger.exception("[%s] unexpected error, skipping to next resource", resource["key"])
                _safe_recover(writer)
    finally:
        writer.close()
        meta_db.close()


if __name__ == "__main__":
    main()
