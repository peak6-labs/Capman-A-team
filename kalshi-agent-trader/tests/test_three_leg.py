"""Tests for the three-leg fatigue-hedge strategy: Kelly + fatigue sizing,
exact-score discovery/de-vig, favourite selection, and outcome P&L."""

from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from kalshi_agent_trader.models import Market
from kalshi_agent_trader.strategies.three_leg import compute, length
from kalshi_agent_trader.strategies.three_leg.orders import proposed_orders
from kalshi_agent_trader.strategies.three_leg.screen import ThreeLegParams, build_plans

D = Decimal
MATCH_EVT = "KXATPMATCH-26JUN03AAABBB"
EXACT_EVT = "KXATPEXACTMATCH-26JUN03AAABBB"


# ---- builders ------------------------------------------------------------- #
def _match(cid, name, yes_bid, yes_ask, event=MATCH_EVT):
    return Market(
        ticker=f"{event}-{cid}", event_ticker=event, status="open",
        yes_sub_title=name, custom_strike={"tennis_competitor": cid},
        yes_bid_dollars=yes_bid, yes_ask_dollars=yes_ask,
        no_bid_dollars="0.0", no_ask_dollars="1.0",
    )


def _title(cid, name, yes_bid="0.16", yes_ask="0.20"):
    return Market(
        ticker=f"KXFOMEN-26-{cid}", event_ticker="KXFOMEN-26", status="open",
        yes_sub_title=name, custom_strike={"tennis_competitor": cid},
        yes_bid_dollars=yes_bid, yes_ask_dollars=yes_ask,
    )


def _score(name, a, b, yes_bid, yes_ask):
    return Market(
        ticker=f"{EXACT_EVT}-{name[:3].upper()}{a}{b}", event_ticker=EXACT_EVT, status="open",
        yes_sub_title=f"{name} wins {a}-{b}",
        yes_bid_dollars=yes_bid, yes_ask_dollars=yes_ask,
    )


class FakeMD:
    """Serves canned markets by series_ticker OR event_ticker."""

    def __init__(self, by_series: Dict[str, List[Market]], by_event: Dict[str, List[Market]]):
        self.by_series = by_series
        self.by_event = by_event

    def list_markets(self, *, status=None, series_ticker=None, event_ticker=None,
                     limit=100, cursor=None) -> Tuple[List[Market], Optional[str]]:
        if event_ticker is not None:
            return self.by_event.get(event_ticker, []), None
        return self.by_series.get(series_ticker, []), None


def _fav_match():
    return _match("u1", "Alice Ace", "0.60", "0.62")


def _opp_match():
    return _match("u2", "Bob Base", "0.40", "0.42")


def _exact_markets():
    return [
        _score("Alice Ace", 3, 0, "0.21", "0.25"),
        _score("Alice Ace", 3, 1, "0.16", "0.20"),
        _score("Alice Ace", 3, 2, "0.11", "0.15"),
        _score("Bob Base", 3, 0, "0.03", "0.05"),
        _score("Bob Base", 3, 1, "0.05", "0.07"),
        _score("Bob Base", 3, 2, "0.08", "0.10"),
    ]


def _md():
    return FakeMD(
        by_series={
            "KXATPMATCH": [_fav_match(), _opp_match()],
            "KXFOMEN": [_title("u1", "Alice Ace"), _title("u2", "Bob Base", "0.02", "0.04")],
            "KXWTAMATCH": [], "KXFOWOMEN": [],
        },
        by_event={EXACT_EVT: _exact_markets()},
    )


# ---- pure math ------------------------------------------------------------ #
def test_kelly_zero_without_edge():
    assert compute.kelly_fraction(D("0.50"), D("0.50")) == 0
    assert compute.kelly_fraction(D("0.40"), D("0.50")) == 0  # fair < price
    assert compute.kelly_fraction(D("0.60"), D("0.50")) > 0   # edge ⇒ positive


def test_kelly_degenerate_price():
    assert compute.kelly_fraction(D("0.9"), D("0")) == 0
    assert compute.kelly_fraction(D("0.9"), D("1")) == 0


def test_fatigue_premium_scales_inverse_rest_days():
    assert compute.fatigue_premium(D("0.20"), 2, 1) == D("0.40")
    assert compute.fatigue_premium(D("0.20"), 2, 2) == D("0.20")
    assert compute.fatigue_premium(D("0.20"), 1, 0) == D("0.20")  # rest floored at 1


def test_size_leg_floors_contracts_and_respects_edge():
    no_edge = compute.size_leg("m", "T", price=D("0.62"), market_fair=D("0.598"),
                               bankroll=D("100"), kelly_fraction_cap=D("0.5"))
    assert no_edge.contracts == 0                      # fair == market, no edge
    with_edge = compute.size_leg("m", "T", price=D("0.62"), market_fair=D("0.598"),
                                 edge=D("0.10"), bankroll=D("100"), kelly_fraction_cap=D("0.5"))
    assert with_edge.contracts >= 1
    assert with_edge.cost == D(with_edge.contracts) * D("0.62")


# ---- discovery ------------------------------------------------------------ #
def test_exact_event_mapping():
    assert length.exact_event_for(_fav_match()) == EXACT_EVT
    assert length.exact_event_for(_title("u1", "x")) is None  # not a match series


def test_discover_returns_both_players_and_devigs():
    cands, note = length.discover(_md(), _fav_match(), "Alice Ace")
    assert note == ""
    assert len(cands) == 6                               # both players, all scores
    mp = [c.score_label for c in cands if c.winner_is_match]      # match player = Alice
    other = [c.score_label for c in cands if not c.winner_is_match]
    assert mp == ["3-0", "3-1", "3-2"] and other == ["3-0", "3-1", "3-2"]
    total = sum(c.devig_prob for c in cands)
    assert D("0.98") < total < D("1.02")                 # full outcome set ⇒ ~1.0
    assert all(0 < c.devig_prob < 1 for c in cands)


def test_discover_missing_market_degrades():
    md = FakeMD(by_series={}, by_event={})
    cands, note = length.discover(md, _fav_match(), "Alice Ace")
    assert cands == [] and "not listed" in note


# ---- assembly ------------------------------------------------------------- #
def test_build_plan_picks_favourite_and_three_legs():
    params = ThreeLegParams(bankroll=D("100"), kelly_fraction=D("0.5"),
                            fatigue_coef=D("0.20"), rest_days=1,
                            match_edge=D("0.10"), title_edge=D("0.10"))
    plans = build_plans(_md(), gender="men", players=None, params=params)
    assert len(plans) == 1
    p = plans[0]
    # default orientation: match on the favourite (Alice/Ace), title on opponent (Bob/Base)
    assert p.name == "Ace (match) + Base (title)"
    assert p.match_leg.sized                              # leg 1: Alice wins the match
    # leg 2: Bob's (opponent's) title, NOT the favourite's
    assert p.title_leg and "u2" in p.title_leg.ticker and p.title_leg.sized
    # leg 3: ONLY Bob's 5-set win (Bob 3-2) — the out
    assert len(p.long_legs) == 1
    assert p.long_legs[0].sized and "3-2" in p.long_legs[0].label


def test_orientation_flip_swaps_match_and_title_players():
    """orientation='underdog' backs the underdog (Bob) in the match, favourite (Alice) for title."""
    params = ThreeLegParams(bankroll=D("100"), kelly_fraction=D("0.5"),
                            fatigue_coef=D("0.20"), rest_days=1,
                            match_edge=D("0.10"), title_edge=D("0.10"))
    p = build_plans(_md(), gender="men", players=None, params=params, orientation="underdog")[0]
    assert p.name == "Base (match) + Ace (title)"
    assert "u2" in p.match_leg.ticker                     # leg 1 now on Bob (underdog)
    assert p.title_leg and "u1" in p.title_leg.ticker     # leg 2 now on Alice (favourite)
    # the out is now Alice's 5-set win (Ace 3-2)
    assert len(p.long_legs) == 1 and "3-2" in p.long_legs[0].label


def test_short_turnaround_upsizes_hedge():
    # A title position must exist for there to be anything to hedge.
    base = dict(bankroll=D("100"), kelly_fraction=D("0.5"), fatigue_coef=D("0.20"),
                title_edge=D("0.10"))
    p1 = build_plans(_md(), gender="men", players=None,
                     params=ThreeLegParams(rest_days=1, **base))[0]
    p2 = build_plans(_md(), gender="men", players=None,
                     params=ThreeLegParams(rest_days=2, **base))[0]

    def hedge_contracts(plan):
        return sum(leg.contracts for leg in plan.long_legs)

    assert hedge_contracts(p1) > hedge_contracts(p2)      # 1-day rest ⇒ bigger hedge


def test_out_sized_as_fraction_of_directional_exposure():
    """Leg-3 contracts = round((match + title contracts) · clamped ratio), 5-set ⇒ ρ=0.40."""
    params = ThreeLegParams(bankroll=D("100"), kelly_fraction=D("0.5"),
                            fatigue_coef=D("0.20"), rest_days=1,
                            match_edge=D("0.10"), title_edge=D("0.10"))
    p = build_plans(_md(), gender="men", players=None, params=params)[0]
    ref = p.match_leg.contracts + p.title_leg.contracts
    out = p.long_legs[0]
    assert out.contracts == round(ref * 0.40)             # extra_sets=2 ⇒ ρ = 0.20·2/1 = 0.40
    assert out.fair == out.market_fair                    # the out carries no fabricated edge


def test_out_pays_only_when_B_wins_in_5():
    """The out fires only on B's 5-set win; a B quick win and ANY A win pay it nothing."""
    params = ThreeLegParams(bankroll=D("100"), kelly_fraction=D("0.5"),
                            fatigue_coef=D("0.20"), rest_days=1,
                            match_edge=D("0.10"), title_edge=D("0.10"))
    p = build_plans(_md(), gender="men", players=None, params=params)[0]
    b5 = next(o for o in p.outcomes if not o.a_wins_match and o.leg3_pay > 0)
    assert "3-2" in b5.label
    b_quick = next(o for o in p.outcomes if not o.a_wins_match and "3-0" in o.label)
    assert b_quick.leg3_pay == 0
    assert all(o.leg3_pay == 0 for o in p.outcomes if o.a_wins_match)   # A in 5 ⇒ no out
    # worry case: B wins, no title — the 5-set branch nets better than the quick branch
    assert (p.net_pnl(b5, b_wins_title=False) > p.net_pnl(b_quick, b_wins_title=False))


def test_net_pnl_legs_on_different_players():
    params = ThreeLegParams(match_edge=D("0.10"), title_edge=D("0.10"))
    p = build_plans(_md(), gender="men", players=None, params=params)[0]
    # B wins short, no title: every directional leg dies and the out doesn't pay.
    b_short = next(o for o in p.outcomes if not o.a_wins_match and o.leg3_pay == 0)
    assert p.net_pnl(b_short, b_wins_title=False) == -p.total_cost
    # A wins the match: only leg 1 pays (our title bet is on B, so it's dead).
    a_win = next(o for o in p.outcomes if o.a_wins_match)
    assert p.net_pnl(a_win, b_wins_title=False) == D(p.match_leg.contracts) - p.total_cost
    # B wins in 5: out pays; +title if B then wins it all.
    b5 = next(o for o in p.outcomes if not o.a_wins_match and o.leg3_pay > 0)
    assert p.net_pnl(b5, b_wins_title=False) == b5.leg3_pay - p.total_cost
    assert (p.net_pnl(b5, b_wins_title=True)
            == D(p.title_leg.contracts) + b5.leg3_pay - p.total_cost)


def test_pending_out_trades_legs_1_2_without_exact_market():
    """No exact-score market yet: legs 1 (A match) + 2 (B title) trade now, out is pending."""
    md = FakeMD(
        by_series={"KXATPMATCH": [_fav_match(), _opp_match()],
                   "KXFOMEN": [_title("u1", "Alice Ace"),
                               _title("u2", "Bob Base", "0.02", "0.04")],
                   "KXWTAMATCH": [], "KXFOWOMEN": []},
        by_event={},  # no exact-score event listed yet
    )
    params = ThreeLegParams(match_edge=D("0.10"), title_edge=D("0.10"))
    plans = build_plans(md, gender="men", players=None, params=params)
    assert len(plans) == 1
    p = plans[0]
    assert p.name == "Ace (match) + Base (title)" and p.long_legs == []
    assert p.hedge_pending is True
    assert p.pending_hedge_event == "KXATPEXACTMATCH-26JUN03AAABBB"
    # Legs 1-2 still trade now (the 5-set out is deferred until its market lists):
    tickers = [o.ticker for o in proposed_orders(p)]
    assert any("KXATPMATCH" in t for t in tickers)        # leg 1: A's match
    assert any("KXFOMEN-26-u2" in t for t in tickers)     # leg 2: B's (opponent's) title


def test_women_not_built_yet():
    """Women's Bo3 has no '5 sets'; build_plans is men-only until that path is reworked."""
    assert build_plans(_md(), gender="women", players=None,
                       params=ThreeLegParams(match_edge=D("0.1"), title_edge=D("0.1"))) == []
