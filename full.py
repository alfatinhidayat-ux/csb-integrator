from datetime import datetime
import hashlib
import re

from endpoints import Strategy
from base import BaseSyncer


def _base36_2(value: int) -> str:
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return alphabet[(value // 36) % 36] + alphabet[value % 36]


def _username_code_candidates(user_name: str) -> list[str]:
    clean = re.sub(r"[^A-Za-z0-9]", "", str(user_name or "")).upper()
    if not clean:
        clean = "US"

    candidates = []
    candidates.append((clean + "X")[:2])
    for i in range(len(clean)):
        for j in range(i + 1, len(clean)):
            candidates.append(clean[i] + clean[j])
    return list(dict.fromkeys(candidates))


class FullSyncer(BaseSyncer):
    def _existing_user_codes(self) -> set[str]:
        try:
            cur = self.db._execute(
                "SELECT DISTINCT user_kode FROM `sistem_users` "
                "WHERE user_kode IS NOT NULL AND TRIM(user_kode) <> ''"
            )
            return {str(row["user_kode"]).strip().upper() for row in cur.fetchall()}
        except Exception:
            return set()

    def _fill_missing_user_codes(self, records: list[dict]):
        if self.endpoint.table != "sistem_users":
            return

        used = self._existing_user_codes()
        for rec in records:
            current = rec.get("user_kode")
            if current is not None and str(current).strip():
                used.add(str(current).strip().upper())
                continue

            user_name = rec.get("user_name") or rec.get("username") or ""
            chosen = None
            for candidate in _username_code_candidates(user_name):
                if candidate not in used:
                    chosen = candidate
                    break

            if chosen is None:
                seed = f"{user_name}|{rec.get('user_id', '')}|{rec.get('cabang_id', self.cabang_id)}"
                digest = int(hashlib.sha1(seed.encode("utf-8")).hexdigest(), 16)
                for offset in range(36 * 36):
                    candidate = _base36_2(digest + offset)
                    if candidate not in used:
                        chosen = candidate
                        break

            rec["user_kode"] = chosen
            used.add(chosen)

    def sync(self) -> int:
        records = []
        page = 1

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

        self._fill_missing_user_codes(records)

        sample = records[0]
        self.db.ensure_table(self.endpoint.table, sample)

        if self.endpoint.strategy == Strategy.FULL_PAGING:
            self.db.truncate_table(self.endpoint.table)

        self.db.upsert_records(self.endpoint.table, records, self.cabang_id)

        self.db.update_sync_meta(self.endpoint.name, self.cabang_id, datetime.now())
        return len(records)
