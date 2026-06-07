import time
from typing import Optional

import httpx

from config import Config


class AuthManager:
    def __init__(self, config: Config):
        self.config = config
        self.token: Optional[str] = None
        self.token_expires_at: float = 0
        self.session_id: Optional[int] = None

    def login(self) -> str:
        response = httpx.post(
            f"{self.config.base_url}/login",
            data={
                "grant_type": "password",
                "username": self.config.username,
                "password": self.config.password,
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.config.request_timeout,
        )
        response.raise_for_status()
        data = response.json()
        self.token = data["access_token"]
        self.session_id = data.get("session_id")
        expires_in = data.get("expires_in", 3600)
        self.token_expires_at = time.time() + expires_in - 60
        return self.token

    def is_expired(self) -> bool:
        return not self.token or time.time() >= self.token_expires_at

    def ensure_token(self) -> str:
        if self.is_expired():
            self.login()
        assert self.token is not None
        return self.token

    def get_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.ensure_token()}"}
