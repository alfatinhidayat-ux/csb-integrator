import time
from datetime import datetime
from typing import Any, Optional

import httpx

from auth import AuthManager
from config import Config
from db import DatabaseManager
from endpoints import Endpoint


class SyncError(Exception):
    pass


class BaseSyncer:
    def __init__(
        self,
        config: Config,
        auth: AuthManager,
        db: DatabaseManager,
        endpoint: Endpoint,
        cabang_id: int,
    ):
        self.config = config
        self.auth = auth
        self.db = db
        self.endpoint = endpoint
        self.cabang_id = cabang_id
        self.client = httpx.Client(
            base_url=config.base_url,
            timeout=config.request_timeout,
            follow_redirects=True,
        )
        self._last_request_time = 0.0

    def close(self):
        self.client.close()

    def _rate_limit(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < self.config.request_delay:
            time.sleep(self.config.request_delay - elapsed)

    def _build_params(self, page: int = 1, timestamp: Optional[datetime] = None) -> dict:
        params = dict(self.endpoint.params)
        params["page"] = str(page)
        params["results_per_page"] = str(self.config.results_per_page)
        if self.endpoint.cabang_param:
            params[self.endpoint.cabang_param] = str(self.cabang_id)
        return params

    def _request(self, params: dict) -> dict:
        self._rate_limit()
        self.auth.ensure_token()
        for attempt in range(self.config.max_retries):
            try:
                resp = self.client.get(
                    self.endpoint.path,
                    params=params,
                    headers=self.auth.get_headers(),
                )
                resp.raise_for_status()
                self._last_request_time = time.time()
                return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401 and attempt < self.config.max_retries - 1:
                    self.auth.login()
                    continue
                raise SyncError(
                    f"HTTP {e.response.status_code} for {self.endpoint.path}: {e.response.text[:200]}"
                )
            except httpx.RequestError as e:
                if attempt < self.config.max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise SyncError(f"Request failed for {self.endpoint.path}: {e}")

    def _extract_records(self, data: dict) -> list[dict]:
        if isinstance(data, dict):
            root = data.get(self.endpoint.response_root, data)
            if isinstance(root, dict) and self.endpoint.response_root in root:
                root = root[self.endpoint.response_root]
        else:
            root = data
        if isinstance(root, dict):
            return [root]
        if isinstance(root, list):
            return root
        return []

    def _has_more_pages(self, data: dict, page: int) -> bool:
        total = data.get("total", 0) if isinstance(data, dict) else 0
        rpp = self.config.results_per_page
        total_pages = (total + rpp - 1) // rpp if total else 0
        return page < total_pages if total_pages else (len(self._extract_records(data)) >= rpp)

    def sync(self) -> int:
        raise NotImplementedError
