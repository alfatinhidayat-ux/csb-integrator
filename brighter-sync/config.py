import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Config:
    base_url: str = "https://brighter-api.koffiesoft.com"

    username: str = ""
    password: str = ""
    client_id: str = ""
    client_secret: str = ""

    db_host: str = "localhost"
    db_port: int = 3306
    db_name: str = "brighter_mirror"
    db_user: str = "root"
    db_password: str = ""

    cabang_ids: Optional[list[int]] = None
    results_per_page: int = 100
    request_delay: float = 0.1
    request_timeout: int = 60
    max_retries: int = 3

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            base_url=os.getenv("BRIGHTER_BASE_URL", "https://brighter-api.koffiesoft.com"),
            username=os.getenv("BRIGHTER_USERNAME", ""),
            password=os.getenv("BRIGHTER_PASSWORD", ""),
            client_id=os.getenv("BRIGHTER_CLIENT_ID", ""),
            client_secret=os.getenv("BRIGHTER_CLIENT_SECRET", ""),
            db_host=os.getenv("BRIGHTER_DB_HOST", "localhost"),
            db_port=int(os.getenv("BRIGHTER_DB_PORT", "3306")),
            db_name=os.getenv("BRIGHTER_DB_NAME", "brighter_mirror"),
            db_user=os.getenv("BRIGHTER_DB_USER", "root"),
            db_password=os.getenv("BRIGHTER_DB_PASSWORD", ""),
            cabang_ids=cls._parse_cabang_ids(os.getenv("BRIGHTER_CABANG_IDS", "")),
            results_per_page=int(os.getenv("BRIGHTER_RESULTS_PER_PAGE", "100")),
            request_delay=float(os.getenv("BRIGHTER_REQUEST_DELAY", "0.1")),
            request_timeout=int(os.getenv("BRIGHTER_REQUEST_TIMEOUT", "60")),
            max_retries=int(os.getenv("BRIGHTER_MAX_RETRIES", "3")),
        )

    @staticmethod
    def _parse_cabang_ids(raw: str) -> Optional[list[int]]:
        if not raw:
            return None
        try:
            return [int(x.strip()) for x in raw.split(",") if x.strip()]
        except ValueError:
            return None
