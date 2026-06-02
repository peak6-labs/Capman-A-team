"""Targeted sportsbook odds scraping for already-proposed Kalshi trades.

This module intentionally does not discover or poll sportsbook events. It only
fetches per-ticker URLs configured in `sportsbook_scrape.market_urls`, after a
candidate Kalshi order already exists.
"""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

import httpx

from .agents.base import Signal
from .config import SportsbookScrapeConfig, SportsbookTargetConfig

_ODDS_RE = re.compile(r"(?<![\w.])([+-]\d{3,4})(?![\w.])")
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class SportsbookQuote:
    source: str
    url: str
    outcome: str
    side: str
    american_odds: int
    implied_prob: Decimal
    confidence: float
    ts: float
    snippet: str = ""


def american_to_implied_prob(american_odds: int) -> Decimal:
    """Convert American odds to raw implied probability."""
    odds = Decimal(abs(american_odds))
    if american_odds > 0:
        return Decimal("100") / (odds + Decimal("100"))
    if american_odds < 0:
        return odds / (odds + Decimal("100"))
    raise ValueError("american_odds cannot be 0")


def _normalize(text: str) -> str:
    return _SPACE_RE.sub(" ", re.sub(r"[^a-z0-9 ]", " ", text.lower())).strip()


def _visible_text(markup: str) -> str:
    # Keep script JSON text too; sportsbook pages often render odds from embedded data.
    text = html.unescape(markup)
    text = _TAG_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text)


def parse_american_odds_near_outcome(
    markup: str,
    *,
    outcome: str,
    source: str,
    url: str,
    side: str,
) -> Optional[SportsbookQuote]:
    """Find the best American odds value near a configured outcome label."""
    text = _visible_text(markup)
    normalized_outcome = _normalize(outcome)
    if not normalized_outcome:
        return None

    best: Optional[tuple[float, int, str]] = None
    for match in _ODDS_RE.finditer(text):
        start, end = match.span()
        left = max(0, start - 300)
        right = min(len(text), end + 140)
        window = text[left:right]
        norm_window = _normalize(window)
        if normalized_outcome not in norm_window:
            continue

        distance = max(1, abs(norm_window.rfind(normalized_outcome) - len(norm_window) // 2))
        confidence = max(0.55, min(0.95, 1.0 - distance / 500))
        american_odds = int(match.group(1))
        if best is None or confidence > best[0]:
            best = (confidence, american_odds, window.strip())

    if best is None:
        return None

    confidence, american_odds, snippet = best
    return SportsbookQuote(
        source=source,
        url=url,
        outcome=outcome,
        side=side.lower(),
        american_odds=american_odds,
        implied_prob=american_to_implied_prob(american_odds),
        confidence=round(confidence, 3),
        ts=time.time(),
        snippet=snippet[:500],
    )


class TargetedSportsbookScraper:
    """Fetch configured sportsbook pages for a specific agent signal."""

    def __init__(self, config: SportsbookScrapeConfig) -> None:
        self.config = config

    def fetch_for_signal(self, signal: Signal) -> List[SportsbookQuote]:
        if not self.config.enabled:
            return []

        targets = self.config.market_urls.get(signal.ticker, [])
        if not targets:
            return []

        quotes: List[SportsbookQuote] = []
        with httpx.Client(
            timeout=self.config.timeout_s,
            headers={"User-Agent": self.config.user_agent},
            follow_redirects=True,
        ) as client:
            for target in targets:
                quote = self._fetch_one(client, target)
                if quote is not None and quote.confidence >= self.config.min_parse_confidence:
                    quotes.append(quote)
        return quotes

    def _fetch_one(
        self,
        client: httpx.Client,
        target: SportsbookTargetConfig,
    ) -> Optional[SportsbookQuote]:
        try:
            resp = client.get(target.url)
            resp.raise_for_status()
        except Exception:
            return None

        body = resp.text[: self.config.max_response_bytes]
        return parse_american_odds_near_outcome(
            body,
            outcome=target.outcome,
            source=target.source,
            url=target.url,
            side=target.side,
        )


def reference_prob_for_signal(signal: Signal, quote: SportsbookQuote) -> Decimal:
    """Return the quote probability in the same YES/NO space as the agent signal."""
    prob = quote.implied_prob
    if quote.side.lower() == signal.side.lower():
        return prob
    return Decimal("1") - prob


def blended_signal_from_quotes(
    signal: Signal,
    quotes: List[SportsbookQuote],
    *,
    blend_weight: float,
) -> Signal:
    """Blend agent fair probability with targeted sportsbook reference quotes."""
    if not quotes:
        return signal
    ref = sum(
        (reference_prob_for_signal(signal, q) for q in quotes),
        Decimal("0"),
    ) / Decimal(len(quotes))
    w = Decimal(str(blend_weight))
    fair = Decimal(str(signal.fair_prob)) * (Decimal("1") - w) + ref * w
    confidence = min(0.99, max(signal.confidence, sum(q.confidence for q in quotes) / len(quotes)))
    return Signal(
        ticker=signal.ticker,
        side=signal.side,
        fair_prob=float(fair),
        confidence=round(confidence, 3),
        rationale=(
            f"{signal.rationale} Sportsbook scrape blend: "
            f"{', '.join(f'{q.source} {q.outcome} {q.american_odds:+d}' for q in quotes)}."
        ),
        source=signal.source,
    )
