"""Tests for targeted sportsbook scrape parsing and signal blending."""

from decimal import Decimal

from kalshi_agent_trader.agents.base import Signal
from kalshi_agent_trader.sportsbook_scrape import (
    SportsbookQuote,
    american_to_implied_prob,
    blended_signal_from_quotes,
    parse_american_odds_near_outcome,
    reference_prob_for_signal,
)


def test_american_to_implied_prob_positive_odds():
    assert american_to_implied_prob(150) == Decimal("0.4")


def test_american_to_implied_prob_negative_odds():
    assert american_to_implied_prob(-150) == Decimal("0.6")


def test_parse_american_odds_near_configured_outcome():
    html = """
    <html><body>
      <div class="event">
        <span>Team A</span>
        <span>+145</span>
        <span>Team B</span>
        <span>-170</span>
      </div>
    </body></html>
    """
    quote = parse_american_odds_near_outcome(
        html,
        outcome="Team A",
        source="draftkings",
        url="https://example.test/event",
        side="yes",
    )
    assert quote is not None
    assert quote.source == "draftkings"
    assert quote.american_odds == 145
    assert quote.implied_prob == Decimal("100") / Decimal("245")


def test_parse_returns_none_when_outcome_missing():
    quote = parse_american_odds_near_outcome(
        "<span>Team B</span><span>-170</span>",
        outcome="Team A",
        source="fanduel",
        url="https://example.test/event",
        side="yes",
    )
    assert quote is None


def test_reference_prob_inverts_when_quote_side_differs_from_signal_side():
    signal = Signal(
        ticker="KXTEST",
        side="no",
        fair_prob=0.45,
        confidence=0.8,
        rationale="x",
    )
    quote = SportsbookQuote(
        source="draftkings",
        url="u",
        outcome="Team A",
        side="yes",
        american_odds=150,
        implied_prob=Decimal("0.4"),
        confidence=0.8,
        ts=1.0,
    )
    assert reference_prob_for_signal(signal, quote) == Decimal("0.6")


def test_blended_signal_uses_targeted_sportsbook_reference():
    signal = Signal(
        ticker="KXTEST",
        side="yes",
        fair_prob=0.50,
        confidence=0.7,
        rationale="agent",
    )
    quote = SportsbookQuote(
        source="fanduel",
        url="u",
        outcome="Team A",
        side="yes",
        american_odds=-150,
        implied_prob=Decimal("0.6"),
        confidence=0.85,
        ts=1.0,
    )
    blended = blended_signal_from_quotes(signal, [quote], blend_weight=0.5)
    assert blended.fair_prob == 0.55
    assert blended.confidence == 0.85
    assert "fanduel Team A -150" in blended.rationale
