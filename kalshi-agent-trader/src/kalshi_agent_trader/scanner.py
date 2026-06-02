"""Market scanner: find cheap-tail markets worth selling.

Scoring factors (same thresholds as the root-level scanner.py prototype):
  1. Price: yes/no bid strictly between MIN_PRICE and MAX_PRICE (the cheap-tail range)
  2. Liquidity: lifetime volume >= MIN_VOLUME_FP and spread < MAX_SPREAD
  3. Time: MIN_HOURS to MAX_HOURS until expected expiry
  4. Compliance: PEAK6 category gate applied at scan time — prohibited markets never enter the queue

Each qualifying side of a market is represented as a ScanCandidate. When both sides
qualify, only the cheaper one is kept (higher score = better premium per hour of risk).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from .compliance import ComplianceGate
from .market_data import MarketData
from .models import Market
from .util import hours_until, volume_fp

MIN_PRICE = Decimal("0.01")
MAX_PRICE = Decimal("0.10")
MIN_HOURS = 4.0
MAX_HOURS = 48.0
MAX_SPREAD = Decimal("0.50")
MIN_VOLUME_FP = 10.0


@dataclass
class ScanCandidate:
    ticker: str
    title: str
    category: Optional[str]
    side: str               # "yes" | "no"
    price: Decimal          # bid on the side we'd sell
    spread: Decimal
    hours_to_expiry: float
    volume_fp: float
    score: float            # price * hours — higher = more premium for time held


class Scanner:
    def __init__(self, market_data: MarketData, compliance: ComplianceGate) -> None:
        self._md = market_data
        self._compliance = compliance

    def scan(self, max_pages: int = 20) -> List[ScanCandidate]:
        """Return compliance-approved, price/liquidity/time-filtered candidates."""
        candidates: List[ScanCandidate] = []
        cursor: Optional[str] = None

        for _ in range(max_pages):
            markets, cursor = self._md.list_markets(
                status="open", limit=200, cursor=cursor
            )
            for market in markets:
                entry = self._evaluate(market)
                if entry:
                    candidates.append(entry)
            if not cursor:
                break

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    def _evaluate(self, market: Market) -> Optional[ScanCandidate]:
        # Compliance first — cheapest possible rejection path.
        result = self._compliance.check_market(market, self._md)
        if not result.allowed:
            return None

        # Volume filter.
        vol = volume_fp(market)
        if vol < MIN_VOLUME_FP:
            return None

        # Time filter — prefer expected_expiration_time (resolution), fall back to expiration_time.
        expiry = market.expected_expiration_time or market.expiration_time
        hours = hours_until(expiry)
        if hours is None or not (MIN_HOURS <= hours <= MAX_HOURS):
            return None

        # Price + spread filter for each side; keep the cheaper qualifying side.
        best: Optional[ScanCandidate] = None
        for side, bid, ask in [
            ("yes", market.yes_bid, market.yes_ask),
            ("no", market.no_bid, market.no_ask),
        ]:
            if bid is None or not (MIN_PRICE <= bid <= MAX_PRICE):
                continue
            if ask is None:
                continue
            spread = ask - bid
            if spread >= MAX_SPREAD:
                continue

            score = float(bid) * hours
            candidate = ScanCandidate(
                ticker=market.ticker,
                title=market.title or market.ticker,
                category=result.category,
                side=side,
                price=bid,
                spread=spread,
                hours_to_expiry=round(hours, 1),
                volume_fp=vol,
                score=round(score, 3),
            )
            if best is None or bid < best.price:
                best = candidate

        return best
