"""Authoritative market-name enrichment for portfolio data.

Kalshi's portfolio endpoints (/portfolio/positions, /orders, /fills) return only a
`ticker` — never a human-readable name. The names live on the MARKET object
(`title`, `yes_sub_title`, `no_sub_title`). This module joins the two so the
chatbot and the dashboard show real player/contract names and sides instead of
guessing from ticker suffixes.

`market_meta()` fetches each market once (cached, since titles are static for a
market's life) and tolerates lookups that fail (settled/closed/404) by yielding
None for that ticker — callers then fall back to the bare ticker.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional

from kalshi_agent_trader.client import KalshiClient
from kalshi_agent_trader.market_data import MarketData

from .deps import cached

# Titles/sub-titles don't change over a market's life; cache aggressively so the
# 30s dashboard auto-refresh doesn't re-fetch every market each cycle.
_META_TTL = 300


@dataclass(frozen=True)
class MarketMeta:
    title: Optional[str] = None
    yes_sub_title: Optional[str] = None
    no_sub_title: Optional[str] = None
    event_ticker: Optional[str] = None
    result: Optional[str] = None


def _fetch_one(client: KalshiClient, ticker: str) -> Optional[MarketMeta]:
    """Fetch one market's metadata, cached. Returns None if the lookup fails."""

    def _load() -> Optional[MarketMeta]:
        try:
            m = MarketData(client).get_market(ticker)
        except Exception:
            return None
        return MarketMeta(
            title=m.title,
            yes_sub_title=m.yes_sub_title,
            no_sub_title=m.no_sub_title,
            event_ticker=m.event_ticker,
            result=m.result,
        )

    return cached(f"market_meta:{ticker}", _META_TTL, _load)


def market_meta(client: KalshiClient, tickers: Iterable[str]) -> dict[str, MarketMeta]:
    """Map each unique ticker to its authoritative MarketMeta (missing ⇒ absent)."""
    out: dict[str, MarketMeta] = {}
    for ticker in {t for t in tickers if t}:
        meta = _fetch_one(client, ticker)
        if meta is not None:
            out[ticker] = meta
    return out


def side_from_position(position_fp: Any) -> Optional[str]:
    """Derive the held side from a position's signed fractional quantity.

    Positive ⇒ YES, negative ⇒ NO. Returns None for a flat/unparseable position.
    """
    if position_fp in (None, ""):
        return None
    try:
        qty = Decimal(str(position_fp))
    except (InvalidOperation, ValueError):
        return None
    if qty > 0:
        return "yes"
    if qty < 0:
        return "no"
    return None


def resolve_name(meta: Optional[MarketMeta], side: Optional[str]) -> Optional[str]:
    """The contract name for the given side: that side's sub-title, else the title."""
    if meta is None:
        return None
    if side == "yes" and meta.yes_sub_title:
        return meta.yes_sub_title
    if side == "no" and meta.no_sub_title:
        return meta.no_sub_title
    return meta.title
