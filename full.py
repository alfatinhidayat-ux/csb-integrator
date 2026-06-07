from datetime import datetime

from endpoints import Strategy
from base import BaseSyncer


class FullSyncer(BaseSyncer):
    def sync(self) -> int:
        records = []
        page = 1
        newest_timestamp: datetime | None = None

        while True:
            params = self._build_params(page=page)
            data = self._request(params)
            batch = self._extract_records(data)
            if not batch:
                break
            records.extend(batch)
            if not self._has_more_pages(data, page):
                break
            page += 1

        if not records:
            return 0

        sample = records[0]
        self.db.ensure_table(self.endpoint.table, sample)

        if self.endpoint.strategy == Strategy.FULL_PAGING:
            self.db.delete_cabang_data(self.endpoint.table, self.cabang_id)

        self.db.upsert_records(self.endpoint.table, records, self.cabang_id)

        self.db.update_sync_meta(self.endpoint.name, self.cabang_id, datetime.now())
        return len(records)
