"""Synchronous Kalshi REST client.

Responsibilities:
  - Build URLs against the configured base.
  - Sign authenticated requests (signing the path WITHOUT query string).
  - Throttle client-side to stay under the Basic rate-limit tier.
  - Retry transient errors (timeouts, 5xx, 429) with backoff.

Read endpoints (markets/events/series/orderbook) are public and need no signing.
"""

from __future__ import annotations

import random
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

from .auth import KalshiSigner
from .config import AppConfig

# The path prefix Kalshi expects to be included in the signed message.
_API_PREFIX_MARKER = "/trade-api/"


class RateLimiter:
    """Simple thread-safe minimum-interval throttle."""

    def __init__(self, max_per_second: float) -> None:
        self._min_interval = 1.0 / max_per_second if max_per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_allowed = now + self._min_interval


class KalshiError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Kalshi API error {status}: {body}")
        self.status = status
        self.body = body


class KalshiClient:
    def __init__(self, config: AppConfig, max_retries: int = 3) -> None:
        self.config = config
        self.base_url = config.secrets.kalshi_api_base.rstrip("/")
        self._limiter = RateLimiter(config.runtime.max_requests_per_second)
        self._max_retries = max_retries
        self._http = httpx.Client(
            timeout=config.runtime.request_timeout_s,
            verify=config.runtime.verify_ssl,
        )

        self._signer: Optional[KalshiSigner] = None
        if config.secrets.kalshi_api_key_id and config.secrets.kalshi_private_key_path:
            self._signer = KalshiSigner.from_file(
                config.secrets.kalshi_api_key_id,
                config.secrets.kalshi_private_key_path,
            )

    # ------------------------------------------------------------------ #
    def can_authenticate(self) -> bool:
        """True if credentials are loaded and requests can be signed."""
        return self._signer is not None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "KalshiClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    def _signing_path(self, url: str) -> str:
        """Extract the path Kalshi signs: starts at /trade-api/..., no query string."""
        path = urlparse(url).path
        idx = path.find(_API_PREFIX_MARKER)
        return path[idx:] if idx != -1 else path

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        auth: bool = False,
    ) -> Dict[str, Any]:
        """Perform a request. `path` is relative to the API base, e.g. '/markets'."""
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json"}

        if auth:
            if not self._signer:
                raise RuntimeError(
                    "Authenticated request requires Kalshi credentials in .env "
                    "(KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY_PATH)."
                )
            headers.update(self._signer.headers(method, self._signing_path(url)))

        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            self._limiter.acquire()
            try:
                resp = self._http.request(
                    method, url, params=params, json=json, headers=headers
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
            else:
                if resp.status_code < 400:
                    return resp.json() if resp.content else {}
                # Retry on rate-limit / server errors; fail fast otherwise.
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_exc = KalshiError(resp.status_code, resp.text)
                else:
                    raise KalshiError(resp.status_code, resp.text)

            if attempt < self._max_retries:
                base = min(2 ** attempt * 0.5, 8.0)
                time.sleep(base + random.uniform(0, base * 0.1))

        assert last_exc is not None
        raise last_exc

    # Convenience wrappers ---------------------------------------------- #
    def get(self, path: str, *, params=None, auth: bool = False) -> Dict[str, Any]:
        return self.request("GET", path, params=params, auth=auth)

    def post(self, path: str, *, json=None, auth: bool = True) -> Dict[str, Any]:
        return self.request("POST", path, json=json, auth=auth)

    def delete(self, path: str, *, auth: bool = True) -> Dict[str, Any]:
        return self.request("DELETE", path, auth=auth)
