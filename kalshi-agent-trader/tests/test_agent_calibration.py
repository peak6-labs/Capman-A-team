"""Tests for analysis/calibration.py — Brier scoring of closed positions against
Kalshi settlement results. (Distinct from test_calibration.py, which covers the
tennis alpha-fitter in calibration.py.)"""

import pytest

from kalshi_agent_trader.analysis.calibration import score_calibration
from kalshi_agent_trader.journal import Journal
from kalshi_agent_trader.models import Market


class _StubMarketData:
    """Returns canned Market objects keyed by ticker; category is fixed."""

    def __init__(self, markets: dict):
        self._markets = markets

    def get_market(self, ticker: str) -> Market:
        return self._markets[ticker]

    def category_for_market(self, market: Market):
        return "Sports"


def _journal() -> Journal:
    return Journal(db_path=":memory:")


def _add_scored_position(journal: Journal, *, ticker: str, side: str, fair_prob: float):
    """Record a placed decision (carrying fair_prob) then a matching closed position."""
    journal.record_decision(
        outcome="dry_run",
        source="agent:analyst",
        market_ticker=ticker,
        side=side,
        fair_prob=fair_prob,
        confidence=0.7,
        rationale="test",
    )
    pid = journal.record_position(
        {
            "ticker": ticker,
            "side": side,
            "action": "buy",
            "entry_price": "0.20",
            "target_price": "0.03",
            "count": 1,
            "confidence": 0.7,
        }
    )
    journal.close_position(pid, reason="RESOLVED")


def test_scores_brier_for_settled_position():
    journal = _journal()
    _add_scored_position(journal, ticker="MKT-1", side="yes", fair_prob=0.2)
    md = _StubMarketData({"MKT-1": Market(ticker="MKT-1", event_ticker="E-1", result="yes")})

    report = score_calibration(journal, md)

    assert report.scored == 1
    # predicted=0.2, realized=1.0 (yes won) -> (0.2-1)^2 = 0.64
    assert report.overall.brier == pytest.approx(0.64)
    assert report.overall.mean_realized == pytest.approx(1.0)
    assert report.by_source["agent:analyst"].count == 1
    assert report.by_category["Sports"].count == 1


def test_skips_unsettled_market():
    journal = _journal()
    _add_scored_position(journal, ticker="MKT-1", side="yes", fair_prob=0.2)
    md = _StubMarketData({"MKT-1": Market(ticker="MKT-1", event_ticker="E-1", result="")})

    report = score_calibration(journal, md)

    assert report.scored == 0
    assert report.skipped_unsettled == 1


def test_skips_position_without_prediction():
    journal = _journal()
    pid = journal.record_position(
        {
            "ticker": "MKT-2", "side": "no", "action": "sell",
            "entry_price": "0.90", "target_price": "0.99", "count": 1, "confidence": 0.6,
        }
    )
    journal.close_position(pid, reason="RESOLVED")
    md = _StubMarketData({"MKT-2": Market(ticker="MKT-2", event_ticker="E-2", result="no")})

    report = score_calibration(journal, md)

    assert report.scored == 0
    assert report.skipped_no_prediction == 1


def test_flips_fair_prob_when_decision_side_differs():
    """Decision recorded for the YES side; position traded NO -> predicted flips to 1-p."""
    journal = _journal()
    journal.record_decision(
        outcome="placed", source="agent:analyst", market_ticker="MKT-3",
        side="yes", fair_prob=0.3, confidence=0.7, rationale="test",
    )
    pid = journal.record_position(
        {
            "ticker": "MKT-3", "side": "no", "action": "buy",
            "entry_price": "0.65", "target_price": "0.99", "count": 1, "confidence": 0.7,
        }
    )
    journal.close_position(pid, reason="RESOLVED")
    md = _StubMarketData({"MKT-3": Market(ticker="MKT-3", event_ticker="E-3", result="no")})

    report = score_calibration(journal, md)

    # predicted for NO = 1 - 0.3 = 0.7; realized = 1.0 (no won) -> (0.7-1)^2 = 0.09
    assert report.scored == 1
    assert report.overall.brier == pytest.approx(0.09)
