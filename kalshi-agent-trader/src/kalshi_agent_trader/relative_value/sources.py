"""External reference-price sources for Kalshi relative-value signals."""

from __future__ import annotations

import time
from typing import Optional, Protocol

from kalshi_agent_trader.models import Market
from kalshi_agent_trader.polymarket import PolymarketClient

from .models import ReferenceQuote


class ReferenceSource(Protocol):
    source_name: str

    def fetch_for_market(self, market: Market) -> Optional[ReferenceQuote]: ...


class PolymarketReferenceSource:
    """Use the existing read-only Polymarket client as a fair-price reference."""

    source_name = "polymarket"

    def __init__(self, client: PolymarketClient) -> None:
        self.client = client

    def fetch_for_market(self, market: Market) -> Optional[ReferenceQuote]:
        title = market.title or market.ticker
        ref = self.client.fetch_reference(title)
        if ref is None:
            return None
        return ReferenceQuote(
            source=self.source_name,
            question=ref.question,
            yes_prob=ref.yes_price,
            confidence=ref.similarity,
            ts=time.time(),
            event_key=market.event_ticker,
        )

