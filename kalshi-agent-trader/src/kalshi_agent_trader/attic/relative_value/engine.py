"""Relative-value edge detection for Kalshi-only execution."""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable, List, Optional

from kalshi_agent_trader.models import Market
from kalshi_agent_trader.risk import ProposedOrder

from .models import ReferenceQuote, RelativeValueSignal


def _edge_for(
    *,
    side: str,
    action: str,
    price: Optional[Decimal],
    fair_prob: Decimal,
    min_edge: Decimal,
) -> Optional[tuple[Decimal, Decimal]]:
    if price is None or price <= 0 or price >= 1:
        return None
    edge = fair_prob - price if action == "buy" else price - fair_prob
    if edge < min_edge:
        return None
    return price, edge


class RelativeValueEngine:
    """Compare Kalshi prices to external fair probabilities and emit signals."""

    def __init__(
        self,
        *,
        min_edge: Decimal,
        min_confidence: float,
        max_spread: Decimal,
    ) -> None:
        self.min_edge = min_edge
        self.min_confidence = min_confidence
        self.max_spread = max_spread

    def signals_for_market(
        self,
        market: Market,
        quote: ReferenceQuote,
        *,
        category: Optional[str],
    ) -> List[RelativeValueSignal]:
        if quote.confidence < self.min_confidence:
            return []
        if quote.yes_prob <= 0 or quote.yes_prob >= 1:
            return []

        title = market.title or market.ticker
        yes_prob = quote.yes_prob
        no_prob = Decimal("1") - yes_prob

        candidates = [
            ("yes", "buy", market.yes_ask, yes_prob),
            ("yes", "sell", market.yes_bid, yes_prob),
            ("no", "buy", market.no_ask, no_prob),
            ("no", "sell", market.no_bid, no_prob),
        ]

        signals: List[RelativeValueSignal] = []
        for side, action, price, fair_prob in candidates:
            if not self._spread_ok(market, side):
                continue
            edge_entry = _edge_for(
                side=side,
                action=action,
                price=price,
                fair_prob=fair_prob,
                min_edge=self.min_edge,
            )
            if edge_entry is None:
                continue
            kalshi_price, edge = edge_entry
            direction = "undervalued" if action == "buy" else "overvalued"
            signals.append(
                RelativeValueSignal(
                    ticker=market.ticker,
                    title=title,
                    category=category,
                    side=side,
                    action=action,
                    kalshi_price=kalshi_price,
                    reference_prob=fair_prob,
                    edge=edge,
                    confidence=quote.confidence,
                    source=quote.source,
                    source_question=quote.question,
                    reason=(
                        f"{side.upper()} {direction}: Kalshi {kalshi_price} vs "
                        f"{quote.source} fair {fair_prob}"
                    ),
                )
            )

        signals.sort(key=lambda s: (s.edge, Decimal(str(s.confidence))), reverse=True)
        return signals[:1]

    def best_signals(
        self,
        market_quotes: Iterable[tuple[Market, ReferenceQuote, Optional[str]]],
        *,
        limit: int,
    ) -> List[RelativeValueSignal]:
        signals: List[RelativeValueSignal] = []
        for market, quote, category in market_quotes:
            signals.extend(self.signals_for_market(market, quote, category=category))
        signals.sort(key=lambda s: (s.edge, Decimal(str(s.confidence))), reverse=True)
        return signals[:limit]

    def to_order(self, signal: RelativeValueSignal, *, count: int) -> ProposedOrder:
        return ProposedOrder(
            ticker=signal.ticker,
            side=signal.side,
            action=signal.action,
            price=signal.kalshi_price,
            count=count,
            fair_prob=float(signal.reference_prob),
            confidence=signal.confidence,
        )

    def _spread_ok(self, market: Market, side: str) -> bool:
        bid = market.yes_bid if side == "yes" else market.no_bid
        ask = market.yes_ask if side == "yes" else market.no_ask
        if bid is None or ask is None:
            return False
        return (ask - bid) <= self.max_spread
