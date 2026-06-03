"""Models for external reference prices and Kalshi relative-value signals."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class ReferenceQuote:
    """External fair-value input for the YES outcome of a Kalshi market."""

    source: str
    question: str
    yes_prob: Decimal
    confidence: float
    ts: float
    event_key: Optional[str] = None
    market_type: Optional[str] = None
    raw: Optional[dict] = None


@dataclass(frozen=True)
class RelativeValueSignal:
    """A Kalshi-only trade candidate triggered by an external reference price."""

    ticker: str
    title: str
    category: Optional[str]
    side: str
    action: str
    kalshi_price: Decimal
    reference_prob: Decimal
    edge: Decimal
    confidence: float
    source: str
    source_question: str
    reason: str

