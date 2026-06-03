"""Shared dependencies: config loader, singleton KalshiClient, locks, and a simple TTL cache."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional, TypeVar

from kalshi_agent_trader.client import KalshiClient
from kalshi_agent_trader.config import load_config

_client_lock = threading.Lock()
_shared_client: Optional[KalshiClient] = None

# Protect config.yaml writes
config_write_lock = threading.Lock()


def get_config():
    """Fresh config read on every call (no in-process cache; load_config re-reads YAML)."""
    return load_config()


def get_client() -> KalshiClient:
    """Module-level singleton so the single RateLimiter governs all API calls."""
    global _shared_client
    with _client_lock:
        if _shared_client is None:
            cfg = load_config()
            _shared_client = KalshiClient(cfg)
        return _shared_client


def reset_client() -> None:
    """Call after config changes to force a fresh client on the next request."""
    global _shared_client
    with _client_lock:
        if _shared_client is not None:
            try:
                _shared_client.close()
            except Exception:
                pass
        _shared_client = None


# ---------------------------------------------------------------------------
# Simple TTL cache for expensive endpoints (e.g. calibration).
# ---------------------------------------------------------------------------

T = TypeVar("T")

_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def cached(key: str, ttl_seconds: int, fn: Callable[[], T]) -> T:
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry[0]) < ttl_seconds:
            return entry[1]
    result = fn()
    with _cache_lock:
        _cache[key] = (time.time(), result)
    return result


def invalidate_cache(key: str) -> None:
    with _cache_lock:
        _cache.pop(key, None)
