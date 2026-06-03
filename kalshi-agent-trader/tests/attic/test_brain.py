"""Tests for Brain: Kelly math, probability blending, and proposal generation."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from kalshi_agent_trader.brain import (
    MAX_KELLY,
    MAX_THESES,
    MIN_CONFIDENCE,
    Brain,
    _heuristic_discount,
)
from kalshi_agent_trader.polymarket import ReferencePrice
from kalshi_agent_trader.risk import ProposedOrder
from kalshi_agent_trader.scanner import ScanCandidate


def _candidate(price="0.05", side="yes", hours=24.0, volume=50.0) -> ScanCandidate:
    p = Decimal(price)
    return ScanCandidate(
        ticker="T-1",
        title="Will it rain in Denver?",
        category="Climate and Weather",
        side=side,
        price=p,
        spread=Decimal("0.05"),
        hours_to_expiry=hours,
        volume_fp=volume,
        score=float(p) * hours,
    )


def test_heuristic_discount_low_price():
    p = _heuristic_discount(0.01, 5.0)
    assert p < 0.01
    assert p == pytest.approx(0.01 * 0.80)


def test_heuristic_discount_mid_price():
    p = _heuristic_discount(0.04, 5.0)
    assert p == pytest.approx(0.04 * 0.88)


def test_heuristic_discount_near_max():
    p = _heuristic_discount(0.09, 5.0)
    assert p == pytest.approx(0.09 * 0.93)


def test_estimate_probability_no_polymarket():
    brain = Brain(polymarket=None)
    c = _candidate(price="0.05")
    p_yes, conf = brain.estimate_probability(c)
    assert p_yes < 0.05
    assert 0 < conf < 1


def test_estimate_probability_with_polymarket():
    poly = MagicMock()
    poly.fetch_reference.return_value = ReferencePrice(
        question="Will it rain in Denver this week?",
        yes_price=Decimal("0.03"),
        similarity=0.85,
    )
    brain = Brain(polymarket=poly)
    c = _candidate(price="0.05")
    p_yes, conf = brain.estimate_probability(c)
    # Blend: 0.30 * heuristic + 0.70 * 0.03 — result should be below 0.05.
    assert p_yes < 0.05
    assert conf > 0


def test_polymarket_no_side_inverts_price():
    poly = MagicMock()
    poly.fetch_reference.return_value = ReferencePrice(
        question="similar question",
        yes_price=Decimal("0.92"),  # NO side is very cheap: 1-0.92 = 0.08
        similarity=0.8,
    )
    brain = Brain(polymarket=poly)
    c = _candidate(price="0.05", side="no")
    p_yes, _ = brain.estimate_probability(c)
    # For the NO side, poly_for_side = 1 - 0.92 = 0.08
    # blend = 0.30 * heuristic + 0.70 * 0.08  — should stay small
    assert p_yes < 0.12


def test_kelly_fraction_capped_at_max_kelly():
    brain = Brain()
    kf = brain.kelly_fraction(0.01, 0.09)
    assert 0 < kf <= MAX_KELLY


def test_kelly_fraction_zero_when_no_edge():
    brain = Brain()
    # If p_yes_est >= market_price, the Kelly formula yields <= 0.
    kf = brain.kelly_fraction(0.10, 0.05)
    assert kf == 0.0


def test_contract_count_at_least_one():
    brain = Brain()
    count = brain.contract_count(0.25, 0.05)
    assert count >= 1


def test_propose_returns_proposed_orders():
    brain = Brain(polymarket=None)
    candidates = [_candidate(price="0.04"), _candidate(price="0.06")]
    proposals = brain.propose(candidates)
    assert all(isinstance(p, ProposedOrder) for p in proposals)


def test_propose_caps_at_max_theses():
    brain = Brain(polymarket=None)
    candidates = [_candidate(price="0.05") for _ in range(MAX_THESES + 5)]
    proposals = brain.propose(candidates)
    assert len(proposals) <= MAX_THESES


def test_propose_skips_low_confidence():
    brain = Brain(polymarket=None)
    # Very short time to expiry lowers confidence below MIN_CONFIDENCE threshold.
    c = _candidate(price="0.09", hours=1.0)
    proposals = brain.propose([c])
    # May or may not be empty depending on confidence after 1h penalty; just verify types.
    assert all(p.confidence >= MIN_CONFIDENCE for p in proposals)
