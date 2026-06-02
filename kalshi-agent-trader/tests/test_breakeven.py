"""Tests for the pure breakeven math."""

from decimal import Decimal

import pytest

from kalshi_agent_trader.breakeven import (
    BreakevenInputs,
    FadeInputs,
    compute_breakeven,
    compute_fade,
    trading_fee,
)


def test_trading_fee_quadratic_and_ceiled():
    # 0.07 * 100 * 0.5 * 0.5 = 1.75 exactly.
    assert trading_fee(100, Decimal("0.50")) == Decimal("1.75")
    # 0.07 * 200 * 0.10 * 0.90 = 1.26 -> already on a cent.
    assert trading_fee(200, Decimal("0.10")) == Decimal("1.26")
    # rounds UP to the next cent.
    assert trading_fee(10, Decimal("0.37")) == Decimal("0.17")  # raw 0.16317 -> 0.17
    # degenerate prices and sizes -> no fee.
    assert trading_fee(0, Decimal("0.50")) == Decimal("0")
    assert trading_fee(100, Decimal("0")) == Decimal("0")
    assert trading_fee(100, Decimal("1")) == Decimal("0")


def _inp(n, t0, s_no="100", s_t="100"):
    return BreakevenInputs(
        no_ask=Decimal(n), yes_ask_tourney=Decimal(t0),
        stake_no=Decimal(s_no), stake_tourney=Decimal(s_t),
    )


def test_users_example():
    # n=0.38, t0=0.17, equal $100 stakes -> t* = 0.34 ("above 17c").
    res = compute_breakeven(_inp("0.38", "0.17"))
    assert res.breakeven_tourney_price == Decimal("0.34")
    assert res.breakeven_multiple == Decimal("2")


def test_equal_stakes_boundary():
    # n=0.50 -> Q_no=200=C -> lose_net exactly 0; t0=0.10 -> t*=0.20.
    res = compute_breakeven(_inp("0.50", "0.10"))
    assert res.q_no == Decimal("200")
    assert res.total_cost == Decimal("200")
    assert res.lose_net == Decimal("0")
    assert res.lose_profitable is False
    assert res.breakeven_tourney_price == Decimal("0.20")
    assert res.breakeven_multiple == Decimal("2")


def test_profitable_lose_floor():
    res = compute_breakeven(_inp("0.40", "0.17"))
    assert res.q_no == Decimal("250")
    assert res.lose_net == Decimal("50")
    assert res.lose_profitable is True


def test_infeasible_breakeven():
    # t0=0.60 with equal stakes -> t* = 1.20 > 1, not achievable.
    res = compute_breakeven(_inp("0.40", "0.60"))
    assert res.breakeven_tourney_price == Decimal("1.20")
    assert res.breakeven_feasible is False


def test_unequal_stakes_formula():
    # t* == t0 * C / S_t  (NO-leg price does not enter the breakeven).
    res = compute_breakeven(_inp("0.40", "0.20", s_no="50", s_t="150"))
    t0, c, s_t = Decimal("0.20"), Decimal("200"), Decimal("150")
    assert res.breakeven_tourney_price == t0 * c / s_t


def test_zero_asks_raise():
    with pytest.raises(ValueError):
        compute_breakeven(_inp("0", "0.17"))
    with pytest.raises(ValueError):
        compute_breakeven(_inp("0.38", "0"))


# ----- fade (buy match YES + sell title YES) ----------------------------- #

def _fade(y, q, s_y="100", s_q="100"):
    return FadeInputs(
        match_yes_ask=Decimal(y), tourney_no_ask=Decimal(q),
        stake_match=Decimal(s_y), stake_tourney=Decimal(s_q),
    )


def test_fade_ceiling_formula():
    # y=0.60, q=0.85, equal $100: Q_match=166.67, Q_no=117.65, C=200.
    # t** = 1 - (C - Q_match)/Q_no.
    res = compute_fade(_fade("0.60", "0.85"))
    q_match = Decimal("100") / Decimal("0.60")
    q_no = Decimal("100") / Decimal("0.85")
    expected = Decimal("1") - (Decimal("200") - q_match) / q_no
    assert res.breakeven_tourney_ceiling == expected
    assert res.win_branch_locked is False


def test_fade_locked_when_match_underdog():
    # y=0.50 -> match win alone pays Q_match=200 = C -> locked, ceiling capped at 1.
    res = compute_fade(_fade("0.50", "0.85"))
    assert res.win_branch_locked is True
    assert res.breakeven_tourney_ceiling == Decimal("1")


def test_fade_lose_branch_is_risk_for_longshot():
    # Backing a title longshot: title NO is expensive (q=0.95), so losing the
    # match leaves you down — lose_match_net negative.
    res = compute_fade(_fade("0.60", "0.95"))
    assert res.lose_match_net < 0
    assert res.lose_match_profitable is False


def test_fade_scenario_nets():
    # y=0.60, q=0.85, equal $100: Q_match=166.67, Q_no=117.65, C=200.
    res = compute_fade(_fade("0.60", "0.85"))
    q_match = Decimal("100") / Decimal("0.60")
    q_no = Decimal("100") / Decimal("0.85")
    assert res.lose_match_net == q_no - Decimal("200")            # loses match
    assert res.advance_net == q_match + q_no - Decimal("200")     # wins match, no title (best)
    assert res.win_title_net == q_match - Decimal("200")          # wins title (NO leg dies)
    # advancing without the title pays both legs -> always the best outcome.
    assert res.advance_net > res.lose_match_net
    assert res.advance_net > res.win_title_net
    assert res.no_max_loss is False                               # y>0.5 -> real downside


def test_fade_no_max_loss_when_match_underdog():
    # y=0.40 -> match win alone pays 250 > 200 -> win_title_net positive, no max loss.
    res = compute_fade(_fade("0.40", "0.85"))
    assert res.win_title_net > 0
    assert res.no_max_loss is True


def test_fade_zero_asks_raise():
    with pytest.raises(ValueError):
        compute_fade(_fade("0", "0.85"))
    with pytest.raises(ValueError):
        compute_fade(_fade("0.60", "0"))
