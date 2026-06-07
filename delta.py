from datetime import datetime
from typing import Optional

from endpoints import Endpoint
from base import BaseSyncer


class DeltaSyncer(BaseSyncer):
    def __init__(
        self, *args, **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.last_synced: Optional[datetime] = None
        self.newest_timestamp: Optional[datetime] = None

    def _build_params(self, page: int = 1, timestamp: Optional[datetime] = None) -> dict:
        params = super()._build_params(page=page, timestamp=timestamp)
        params["timestamp_data"] = "true"
        return params

    def _get_record_timestamp(self, record: dict) -> Optional[datetime]:
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

    def sync(self) -> int:
        page = 1
        total_upserted = 0
        self.last_synced = self.db.get_last_synced(self.endpoint.name, self.cabang_id)
        newest_in_batch: Optional[datetime] = None

        while True:
            params = self._build_params(page=page, timestamp=self.last_synced)
            data = self._request(params)
            records = self._extract_records(data)
            if not records:
                break
            filtered = []
            for rec in records:
                rec_ts = self._get_record_timestamp(rec)
                if rec_ts:
                    if newest_in_batch is None or rec_ts > newest_in_batch:
                        newest_in_batch = rec_ts
                    if self.last_synced and rec_ts <= self.last_synced:
                        continue
                filtered.append(rec)
            if filtered:
                self.db.ensure_table(self.endpoint.table, filtered[0])
                self.db.upsert_records(self.endpoint.table, filtered, self.cabang_id)
                total_upserted += len(filtered)
            if not self._has_more_pages(data, page):
                break
            page += 1

        if newest_in_batch:
            self.db.update_sync_meta(self.endpoint.name, self.cabang_id, newest_in_batch)
        elif not self.last_synced:
            self.db.update_sync_meta(self.endpoint.name, self.cabang_id, datetime.now())

        return total_upserted
