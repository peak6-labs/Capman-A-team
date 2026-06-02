"""Tests for the dip backtest engine (entry detection, exit simulation, aggregation)."""

from __future__ import annotations

from decimal import Decimal as D

from kalshi_agent_trader.dip_backtest import (
    Bar,
    aggregate,
    replay_episode,
    seed_anchor,
)
from kalshi_agent_trader.reversion import DipParams


def _bar(ts, m, t):
    """Bar with symmetric 1¢ books around match mid `m` and title mid `t`."""
    return Bar(ts, D(str(round(m - 0.01, 2))), D(str(round(m + 0.01, 2))),
               D(str(round(t - 0.01, 2))), D(str(round(t + 0.01, 2))))


# Pre-match baseline: match 0.80 / title 0.40 -> C = 0.5. (4h+ before close.)
def _pre(n=40, m=0.80, t=0.40):
    return [_bar(1000 + i * 60, m, t) for i in range(n)]


CUTOFF = 1000 + 40 * 60  # everything at/below here is "pre-match"
P = DipParams(p_revert=D("0.85"))   # conviction high enough to size the dip


def test_seed_anchor_favourite():
    a = seed_anchor(_pre(), "Fav", P, CUTOFF)
    assert a is not None
    assert a.c_ratio == D("0.5")


def test_seed_anchor_skips_underdog():
    # match mid 0.30 pre-match -> never anchored.
    assert seed_anchor(_pre(m=0.30, t=0.10), "Dog", P, CUTOFF) is None


def test_dip_that_reverts_is_a_win():
    bars = _pre()
    # in-play: match holds ~0.80 (fair 0.40), title craters to 0.25, then reverts to 0.40.
    bars += [_bar(CUTOFF + 60 * k, 0.80, 0.25) for k in range(1, 4)]   # the dip
    bars += [_bar(CUTOFF + 60 * k, 0.80, 0.40) for k in range(4, 7)]   # reversion
    t = replay_episode(bars, "Fav", P, CUTOFF)
    assert t is not None
    assert t.won and t.reason == "revert"
    assert t.entry_ask == D("0.26")        # bought the ask in the dip
    assert t.cents_pnl > 0                 # sold the bid near fair, net of fees
    assert t.stake > 0


def test_match_collapse_stops_out():
    bars = _pre()
    bars += [_bar(CUTOFF + 60, 0.80, 0.25)]          # dip detected (match still up)
    bars += [_bar(CUTOFF + 120 * k, 0.20, 0.12) for k in range(2, 5)]  # match craters
    t = replay_episode(bars, "Fav", P, CUTOFF)
    assert t is not None
    assert not t.won and t.reason == "stop_match"
    assert t.cents_pnl < 0


def test_no_dip_returns_none():
    # title tracks fair the whole time -> never a dip.
    bars = _pre() + [_bar(CUTOFF + 60 * k, 0.80, 0.40) for k in range(1, 6)]
    assert replay_episode(bars, "Fav", P, CUTOFF) is None


def test_price_stop_loss_triggers():
    p = DipParams(p_revert=D("0.85"), stop_loss=D("0.03"))
    bars = _pre()
    bars += [_bar(CUTOFF + 60, 0.80, 0.25)]                              # enter at ask 0.26
    bars += [_bar(CUTOFF + 120, 0.78, 0.20)]                             # title bid 0.19 <= 0.26-0.03
    t = replay_episode(bars, "Fav", p, CUTOFF)
    assert t is not None and t.reason == "stop_loss"


def test_aggregate_stats():
    # two reverts + one stop -> win_rate 2/3, sensible aggregates.
    win = _pre() + [_bar(CUTOFF + 60, 0.80, 0.25)] + [_bar(CUTOFF + 120 * k, 0.80, 0.40) for k in range(2, 5)]
    lose = _pre() + [_bar(CUTOFF + 60, 0.80, 0.25)] + [_bar(CUTOFF + 120 * k, 0.20, 0.12) for k in range(2, 5)]
    trades = [t for t in (replay_episode(b, "P", P, CUTOFF) for b in (win, win, lose)) if t]
    st = aggregate(trades)
    assert st.n == 3 and st.n_revert == 2
    assert abs(st.win_rate - 2 / 3) < 1e-9
    assert st.avg_cents_win > 0 > st.avg_cents_loss


def test_aggregate_empty():
    assert aggregate([]) is None


def test_maker_entry_is_cheaper_than_taker():
    # Same reverting dip, priced as maker vs taker. Maker enters at the bid (0.24)
    # not the ask (0.26) and pays 25% fees -> strictly more profit per contract.
    bars = _pre()
    bars += [_bar(CUTOFF + 60 * k, 0.80, 0.25) for k in range(1, 4)]   # dip (bid 0.24)
    bars += [_bar(CUTOFF + 60 * k, 0.80, 0.40) for k in range(4, 8)]   # reversion to fair 0.40
    taker = replay_episode(bars, "Fav", P, CUTOFF, maker=False)
    maker = replay_episode(bars, "Fav", P, CUTOFF, maker=True)
    assert taker.won and maker.won
    assert maker.entry_ask == D("0.24")          # rested a bid at the touch
    assert taker.entry_ask == D("0.26")          # lifted the ask
    assert maker.cents_pnl > taker.cents_pnl      # cheaper entry + 25% fees


def test_maker_missed_when_no_fill():
    # Dip detected then price immediately snaps back above the resting bid: no fill.
    bars = _pre()
    bars += [_bar(CUTOFF + 60, 0.80, 0.25)]                            # detect (bid 0.24)
    bars += [_bar(CUTOFF + 60 * k, 0.80, 0.42) for k in range(2, 8)]   # never trades back to 0.24
    assert replay_episode(bars, "Fav", P, CUTOFF, maker=True) is None
