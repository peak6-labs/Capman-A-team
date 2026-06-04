"""Market analyzers for Live Agent Queue candidate ranking."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Literal

from kalshi_agent_trader.models import Market
from kalshi_agent_trader.util import hours_until


@dataclass(frozen=True)
class SignalCandidate:
    ticker: str
    title: str
    side: Literal["yes", "no"]
    price: float
    spread: float
    volume_24h: float
    open_interest: float
    hours_left: float
    score: float
    source: Literal["tail", "short_expiry"]


def find_tail_opportunities(
    markets: Iterable[Market],
    *,
    tail_threshold: float = 0.08,
    max_spread: float = 0.05,
    min_volume_24h: float = 100.0,
    min_hours: float = 2.0,
) -> list[SignalCandidate]:
    """Find liquid extreme-priced markets where selling the tail is plausible."""
    results = []

    for market in markets:
        if market.status not in ("active", "open"):
            continue
        if market.yes_bid is None or market.yes_ask is None:
            continue

        yes_bid = float(market.yes_bid)
        yes_ask = float(market.yes_ask)
        if yes_bid <= 0 or yes_ask <= 0:
            continue

        spread = yes_ask - yes_bid
        if spread > max_spread:
            continue

        yes_mid = (yes_bid + yes_ask) / 2
        tail_price = min(yes_mid, 1.0 - yes_mid)
        if tail_price > tail_threshold:
            continue

        volume_24h = market.volume_24h_fp or 0.0
        if volume_24h < min_volume_24h:
            continue

        hours_left = hours_until(market.expected_expiration_time or market.expiration_time)
        if hours_left is None or hours_left < min_hours:
            continue

        score = volume_24h * (tail_threshold - tail_price) / max(spread, 0.005)

        results.append(SignalCandidate(
            ticker=market.ticker,
            title=market.yes_sub_title or market.title or market.ticker,
            side="yes" if yes_mid < 0.5 else "no",
            price=round(tail_price, 4),
            spread=round(spread, 4),
            volume_24h=round(volume_24h),
            open_interest=round(market.open_interest_fp or 0.0),
            hours_left=round(hours_left, 1),
            score=round(score, 1),
            source="tail",
        ))

    return sorted(results, key=lambda candidate: candidate.score, reverse=True)


def find_short_expiry_liquid(
    markets: Iterable[Market],
    *,
    max_hours: float = 72.0,
    min_hours: float = 1.0,
    min_volume_24h: float = 200.0,
) -> list[SignalCandidate]:
    """Find near-expiry markets with strong trading activity."""
    results = []

    for market in markets:
        if market.status not in ("active", "open"):
            continue

        hours_left = hours_until(market.expected_expiration_time or market.expiration_time)
        if hours_left is None or not (min_hours < hours_left <= max_hours):
            continue

        volume_24h = market.volume_24h_fp or 0.0
        if volume_24h < min_volume_24h:
            continue

        yes_bid = float(market.yes_bid) if market.yes_bid is not None else 0.0
        yes_ask = float(market.yes_ask) if market.yes_ask is not None else 0.0
        if yes_bid > 0 and yes_ask > 0:
            yes_mid = (yes_bid + yes_ask) / 2.0
            spread = yes_ask - yes_bid
        else:
            yes_mid = float(market.last_price) if market.last_price is not None else 0.0
            spread = 0.0

        open_interest = market.open_interest_fp or 0.0
        score = volume_24h * math.sqrt(max(open_interest, 1)) / hours_left

        results.append(SignalCandidate(
            ticker=market.ticker,
            title=market.yes_sub_title or market.title or market.ticker,
            side="yes",
            price=round(yes_mid, 4),
            spread=round(spread, 4),
            volume_24h=round(volume_24h),
            open_interest=round(open_interest),
            hours_left=round(hours_left, 1),
            score=round(score, 1),
            source="short_expiry",
        ))

    return sorted(results, key=lambda candidate: candidate.score, reverse=True)
