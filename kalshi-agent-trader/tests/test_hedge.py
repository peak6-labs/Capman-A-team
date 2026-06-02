"""Tests for the pure hedge math.

Locks the core invariants, including the lesson that motivated the engine: a
clean one-for-one hedge can be *dominated by simply exiting*. The numbers mirror
the live Fonseca case (30 lots @ 0.65, exit bid 0.54, opposite hedge ask 0.48).
"""

from decimal import Decimal

from kalshi_agent_trader.hedge import (HedgeQuote, Position, Rel, evaluate,
                                       exit_pnl, rank)


def _fonseca():
    return Position(ticker="KXATPMATCH-26JUN02MENFON-FON", side="yes",
                    count=30, avg_cost=Decimal("0.65"))


def test_exit_pnl():
    pos = _fonseca()
    assert exit_pnl(pos, Decimal("0.54")) == Decimal("30") * Decimal("-0.11")


def test_opposite_full_hedge_locked_pnl():
    pos = _fonseca()
    q = HedgeQuote("Mensik YES", "…-MEN", "yes", Decimal("0.48"),
                   fair=Decimal("0.47"), rel=Rel.OPPOSITE)
    e = evaluate(pos, q, exit_bid=Decimal("0.54"))
    # locked = 30 * (1 - 0.65 - 0.48) = 30 * -0.13
    assert e.locked_pnl == Decimal("30") * Decimal("-0.13")
    assert e.full_hedge_cost == Decimal("30") * Decimal("0.48")


def test_exit_dominates_hedge_flagged():
    """The Fonseca lesson: selling at 0.54 (-$3.30) beats the locked hedge
    (-$3.90), so the hedge must be flagged as dominated."""
    pos = _fonseca()
    q = HedgeQuote("Mensik YES", "…-MEN", "yes", Decimal("0.48"),
                   fair=Decimal("0.47"), rel=Rel.OPPOSITE)
    e = evaluate(pos, q, exit_bid=Decimal("0.54"))
    assert exit_pnl(pos, Decimal("0.54")) > e.locked_pnl  # -3.30 > -3.90
    assert e.dominated_by_exit is True


def test_edge_sign():
    pos = _fonseca()
    cheap = HedgeQuote("Mensik @0.40", "…-MEN", "yes", Decimal("0.40"),
                       fair=Decimal("0.47"), rel=Rel.OPPOSITE)
    rich = HedgeQuote("Mensik @0.50", "…-MEN", "yes", Decimal("0.50"),
                      fair=Decimal("0.47"), rel=Rel.OPPOSITE)
    assert evaluate(pos, cheap).edge_per_contract == Decimal("0.07")   # +EV
    assert evaluate(pos, rich).edge_per_contract == Decimal("-0.03")   # -EV


def test_correlated_partial_offset_no_lock():
    """A title-NO hedge: gains ~0.15 if the match is lost, so it offsets only a
    fraction of the per-contract loss and carries no clean lock."""
    pos = _fonseca()
    q = HedgeQuote("Fonseca title NO", "KXFOMEN-26-FON", "no", Decimal("0.88"),
                   fair=Decimal("0.92"), rel=Rel.CORRELATED,
                   payoff_if_lose=Decimal("0.12"))
    e = evaluate(pos, q)
    assert e.locked_pnl is None and e.full_hedge_cost is None
    assert e.edge_per_contract == Decimal("0.04")          # +EV (the fade)
    assert e.hedge_ratio == Decimal("0.12") / Decimal("0.65")  # partial only


def test_rank_orders_clean_before_correlated():
    pos = _fonseca()
    quotes = [
        HedgeQuote("title NO", "…", "no", Decimal("0.88"), Decimal("0.92"),
                   Rel.CORRELATED, payoff_if_lose=Decimal("0.12")),
        HedgeQuote("Mensik YES", "…", "yes", Decimal("0.48"), Decimal("0.47"),
                   Rel.OPPOSITE),
    ]
    ranked = rank(pos, quotes, exit_bid=Decimal("0.54"))
    assert ranked[0].rel == Rel.OPPOSITE  # clean hedge ranked ahead of correlated
