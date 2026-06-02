"""Tests for the risk gate: caps, no-leverage, daily loss, kill switch, size clamping."""

from decimal import Decimal

import pytest

from kalshi_agent_trader.config import RiskConfig
from kalshi_agent_trader.risk import AccountState, ProposedOrder, RiskGate


def cfg(**over):
    base = dict(
        dry_run=True,
        max_total_exposure_usd=Decimal("100"),
        max_per_position_usd=Decimal("25"),
        max_contracts_per_order=1000,
        daily_loss_cap_usd=Decimal("20"),
        min_confidence=0.6,
        min_edge=0.05,
    )
    base.update(over)
    return RiskConfig(**base)


def order(**over):
    base = dict(ticker="KXFOO", side="yes", price=Decimal("0.50"),
               count=100, fair_prob=0.70, confidence=0.8)
    base.update(over)
    return ProposedOrder(**base)


def state(**over):
    base = dict(balance_usd=Decimal("1000"), total_exposure_usd=Decimal("0"),
                position_exposure_usd=Decimal("0"), realized_daily_pnl_usd=Decimal("0"))
    base.update(over)
    return AccountState(**base)


def gate(**over):
    return RiskGate(cfg(**over))


def test_low_confidence_rejected():
    r = gate().check(order(confidence=0.5), state())
    assert not r.allowed and "confidence" in r.reason


def test_low_edge_rejected():
    # fair_prob 0.52 vs price 0.50 -> edge 0.02 < 0.05
    r = gate().check(order(fair_prob=0.52), state())
    assert not r.allowed and "edge" in r.reason


def test_sell_order_requires_price_above_fair_probability():
    r = gate().check(order(action="sell", fair_prob=0.70, price=Decimal("0.50")), state())
    assert not r.allowed and "edge" in r.reason


def test_sell_order_uses_max_loss_for_exposure():
    # Selling a 5c contract risks 95c, so $25 of per-position room allows 26 contracts.
    r = gate().check(
        order(action="sell", price=Decimal("0.05"), fair_prob=0.0, count=100),
        state(),
    )
    assert r.allowed and r.approved_count == 26


def test_per_position_cap_clamps_size():
    # $25 / $0.50 = 50 contracts max for this position.
    r = gate().check(order(count=100), state())
    assert r.allowed and r.approved_count == 50 and "max_per_position_usd" in r.reason


def test_total_exposure_cap_binds():
    # total cap 100, already 90 used -> room $10 -> 20 contracts at 0.50,
    # but per-position cap (25 -> 50) is looser, so total binds at 20.
    r = gate().check(order(count=100), state(total_exposure_usd=Decimal("90")))
    assert r.allowed and r.approved_count == 20


def test_no_leverage_balance_caps():
    # Tiny balance dominates: balance 5, no exposure -> 10 contracts at 0.50.
    r = gate().check(
        order(count=100),
        state(balance_usd=Decimal("5")),
    )
    assert r.allowed and r.approved_count == 10 and "balance" in r.reason


def test_daily_loss_cap_halts():
    r = gate().check(order(), state(realized_daily_pnl_usd=Decimal("-20")))
    assert not r.allowed and "daily loss" in r.reason


def test_no_size_fits_rejected():
    r = gate().check(order(count=100), state(position_exposure_usd=Decimal("25")))
    assert not r.allowed and "no size fits" in r.reason


def test_clean_order_allowed_unclamped():
    r = gate().check(order(count=10), state())
    assert r.allowed and r.approved_count == 10 and r.reason == "ok"


def test_kill_switch(tmp_path, monkeypatch):
    import kalshi_agent_trader.risk as risk_mod
    kill = tmp_path / "KILL"
    monkeypatch.setattr(risk_mod, "KILL_SWITCH_PATH", kill)
    g = RiskGate(cfg())
    assert g.check(order(), state()).allowed
    kill.write_text("halt")
    r = g.check(order(), state())
    assert not r.allowed and "kill switch" in r.reason
