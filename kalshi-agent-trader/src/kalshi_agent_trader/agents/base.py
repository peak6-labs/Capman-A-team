"""Shared agent types.

Agents produce Signals; the deterministic compliance→risk→execution gate chain
disposes of them. No agent can bypass the gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Signal:
    ticker: str
    side: str           # "yes" | "no"
    fair_prob: float    # agent's estimated true probability for `side`
    confidence: float   # 0..1 — agent's self-reported confidence
    rationale: str
    source: str = "agent"
    recommended_action: str = "sell"  # "buy" | "sell" | "watch" | "avoid"
    main_risk: str = ""
    resolution_risk: str = ""
    liquidity_risk: str = ""
    news_dependency: str = ""

    def audit_rationale(self) -> str:
        """Compact rationale string for the SQLite decision journal."""
        parts = [self.rationale]
        for label, value in [
            ("action", self.recommended_action),
            ("main_risk", self.main_risk),
            ("resolution_risk", self.resolution_risk),
            ("liquidity_risk", self.liquidity_risk),
            ("news_dependency", self.news_dependency),
        ]:
            if value:
                parts.append(f"{label}: {value}")
        return " | ".join(p for p in parts if p)


@dataclass(frozen=True)
class MarketContext:
    """Small, serializable market snapshot passed to the Claude analyst."""

    ticker: str
    event_ticker: str
    title: str
    category: Optional[str]
    series: str = ""
    yes_bid: Optional[float] = None
    yes_ask: Optional[float] = None
    no_bid: Optional[float] = None
    no_ask: Optional[float] = None
    yes_spread: Optional[float] = None
    no_spread: Optional[float] = None
    best_spread: Optional[float] = None
    volume_fp: float = 0.0
    liquidity: float = 0.0
    hours_to_expiry: Optional[float] = None

    def to_prompt_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "event_ticker": self.event_ticker,
            "title": self.title,
            "category": self.category or "unknown",
            "series": self.series,
            "yes_bid": self.yes_bid,
            "yes_ask": self.yes_ask,
            "no_bid": self.no_bid,
            "no_ask": self.no_ask,
            "yes_spread": self.yes_spread,
            "no_spread": self.no_spread,
            "best_spread": self.best_spread,
            "volume_fp": self.volume_fp,
            "liquidity": self.liquidity,
            "hours_to_expiry": self.hours_to_expiry,
        }


class AgentError(RuntimeError):
    """Raised when the agent returns an unparseable or invalid response."""
