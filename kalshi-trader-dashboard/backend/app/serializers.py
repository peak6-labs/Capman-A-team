"""Response shaping helpers for dashboard API contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any


def fmt(value: Any) -> str | None:
    return str(value) if value is not None else None


def normalize_position(position: dict[str, Any]) -> dict[str, str | None] | None:
    ticker = position.get("ticker")
    if not ticker:
        return None

    return {
        "ticker": str(ticker),
        "position": fmt(position.get("position_fp")),
        "exposure_usd": fmt(position.get("market_exposure_dollars")),
        "cost_usd": fmt(
            position.get("total_traded_dollars") or position.get("position_cost_dollars")
        ),
        "realized_pnl_usd": fmt(position.get("realized_pnl_dollars") or position.get("realized_pnl")),
        "unrealized_pnl_usd": fmt(position.get("unrealized_pnl_dollars")),
    }


def normalize_order(order: dict[str, Any]) -> dict[str, str | None] | None:
    ticker = order.get("ticker")
    if not ticker:
        return None

    return {
        "ticker": str(ticker),
        "action": fmt(order.get("action")),
        "side": fmt(order.get("side")),
        "price_usd": fmt(order.get("price_dollars")),
        "count": fmt(order.get("count")),
        "status": fmt(order.get("status")),
    }


def normalize_timestamp(raw: Any) -> int | None:
    """Normalize any Kalshi timestamp to milliseconds for JavaScript Date()."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except Exception:
            return None
    v = int(raw)
    if v > 10**16:      # nanoseconds (~1.7e18 in 2026)
        return v // 1_000_000
    if v > 10**13:      # microseconds (~1.7e15 in 2026)
        return v // 1_000
    if v > 10**10:      # milliseconds (~1.7e12 in 2026)
        return v
    return v * 1000     # seconds


def normalize_fill(fill: dict[str, Any]) -> dict[str, str | int | None] | None:
    ticker = fill.get("ticker") or fill.get("market_ticker")
    ts = normalize_timestamp(fill.get("ts") or fill.get("created_time"))
    if not ticker or ts is None:
        return None

    return {
        "ts": ts,
        "ticker": str(ticker),
        "side": fmt(fill.get("side")),
        "action": fmt(fill.get("action")),
        "count": fmt(fill.get("count")),
        "price_usd": fmt(fill.get("price_dollars") or fill.get("price")),
        "fill_id": fmt(fill.get("fill_id") or fill.get("id")),
    }


def fmt_decimal(value: Decimal | None) -> str | None:
    return fmt(value)
