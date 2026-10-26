"""Small JSON client shared by the CLI and external-agent MCP adapter."""
from __future__ import annotations

from urllib.parse import urlsplit

import httpx


class WorkbenchClient:
    def __init__(self, url: str | None = None, api_key: str | None = None):
        from src.platform.config.accessor import get_env_config
        self.url = (url or "http://127.0.0.1:8899").rstrip("/")
        parts = urlsplit(self.url)
        if parts.scheme != "http" or parts.hostname not in {"localhost", "127.0.0.1", "::1"} or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("workbench URL must be a local HTTP address")
        self.key = api_key if api_key is not None else get_env_config().api.api_auth_key

    def call(self, method: str, path: str, payload: dict | None = None):
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        try:
            response = httpx.request(method, self.url + path, json=payload, headers=headers, timeout=30, trust_env=False)
        except httpx.HTTPError as exc:
            raise RuntimeError("Workbench is unavailable. Start `quant-workbench serve --workspace <path>` first.") from exc
        if response.is_error:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text[:500]
            raise ValueError(f"HTTP {response.status_code}: {detail}")
        return response.json()
