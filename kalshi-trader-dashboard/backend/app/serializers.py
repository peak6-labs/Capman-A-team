"""Response shaping helpers for dashboard API contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from .market_meta import MarketMeta, resolve_name, side_from_position


def fmt(value: Any) -> str | None:
    return str(value) if value is not None else None


def normalize_position(
    position: dict[str, Any], meta: MarketMeta | None = None
) -> dict[str, str | None] | None:
    ticker = position.get("ticker")
    if not ticker:
        return None

    side = side_from_position(position.get("position_fp"))
    return {
        "ticker": str(ticker),
        "position": fmt(position.get("position_fp")),
        "exposure_usd": fmt(position.get("market_exposure_dollars")),
        "cost_usd": fmt(
            position.get("total_traded_dollars") or position.get("position_cost_dollars")
        ),
        "realized_pnl_usd": fmt(position.get("realized_pnl_dollars") or position.get("realized_pnl")),
        "unrealized_pnl_usd": fmt(position.get("unrealized_pnl_dollars")),
        "side": side,
        "title": meta.title if meta else None,
        "name": resolve_name(meta, side),
    }


def normalize_order(
    order: dict[str, Any], meta: MarketMeta | None = None
) -> dict[str, str | None] | None:
    ticker = order.get("ticker")
    if not ticker:
        return None

    side = order.get("side")
    return {
        "ticker": str(ticker),
        "action": fmt(order.get("action")),
        "side": fmt(side),
        "price_usd": fmt(order.get("price_dollars")),
        "count": fmt(order.get("count")),
        "status": fmt(order.get("status")),
        "title": meta.title if meta else None,
        "name": resolve_name(meta, str(side).lower() if side else None),
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


def normalize_fill(
    fill: dict[str, Any], meta: MarketMeta | None = None
) -> dict[str, Any] | None:
    ticker = fill.get("ticker") or fill.get("market_ticker")
    ts = normalize_timestamp(fill.get("ts") or fill.get("created_time"))
    if not ticker or ts is None:
        return None

    side = fill.get("side")
    count = (
        fill.get("count")
        or fill.get("count_fp")
        or fill.get("fill_count")
        or fill.get("filled_count")
        or fill.get("quantity")
        or fill.get("contracts")
    )
    yes_price = fill.get("yes_price_dollars") or fill.get("yes_price")
    no_price = fill.get("no_price_dollars") or fill.get("no_price")
    price = (
        fill.get("price_dollars")
        or fill.get("price")
        or fill.get("average_fill_price_dollars")
        or fill.get("average_fill_price")
        or fill.get("fill_price_dollars")
        or fill.get("fill_price")
        or yes_price
        or no_price
    )
    fee = (
        fill.get("fee_cost")
        or fill.get("fee_dollars")
        or fill.get("fees_dollars")
        or fill.get("fee_paid_dollars")
        or fill.get("fees_paid_dollars")
        or fill.get("fee_paid")
        or fill.get("fees_paid")
    )
    return {
        "ts": ts,
        "ticker": str(ticker),
        "side": fmt(side),
        "action": fmt(fill.get("action")),
        "count": fmt(count),
        "price_usd": fmt(price),
        "yes_price_usd": fmt(yes_price),
        "no_price_usd": fmt(no_price),
        "fee_usd": fmt(fee),
        "is_taker": fill.get("is_taker"),
        "fill_id": fmt(fill.get("fill_id") or fill.get("trade_id") or fill.get("id")),
        "title": meta.title if meta else None,
        "name": resolve_name(meta, str(side).lower() if side else None),
    }


def fmt_decimal(value: Decimal | None) -> str | None:
    return fmt(value)
