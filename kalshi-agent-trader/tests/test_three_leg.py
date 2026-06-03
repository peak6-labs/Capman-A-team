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


def test_discover_filters_to_favourite_and_devigs():
    cands, note = length.discover(_md(), _fav_match(), "Alice Ace")
    assert note == ""
    labels = [c.score_label for c in cands]
    assert labels == ["3-0", "3-1", "3-2"]               # sorted by sets_lost, fav only
    total = sum(c.devig_prob for c in cands)
    assert D("0.7") < total < D("0.78")                  # 0.54/0.73 ≈ 0.74
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
    assert p.name == "Alice Ace"                          # higher YES mid
    assert p.match_leg.sized and p.title_leg and p.title_leg.sized
    assert {leg.label for leg in p.long_legs} == {"win 3-1", "win 3-2"}
    assert all(leg.sized for leg in p.long_legs)


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


def test_hedge_sized_as_fraction_of_title_position():
    """Long-win contracts = round(title_contracts · clamped fatigue ratio)."""
    params = ThreeLegParams(bankroll=D("100"), kelly_fraction=D("0.5"),
                            fatigue_coef=D("0.20"), rest_days=1,
                            match_edge=D("0.10"), title_edge=D("0.10"))
    p = build_plans(_md(), gender="men", players=None, params=params)[0]
    title_ct = p.title_leg.contracts
    by_score = {leg.label: leg for leg in p.long_legs}
    # 3-1: extra_sets=1, ρ = 0.20·1/1 = 0.20 ; 3-2: extra_sets=2, ρ = 0.40
    assert by_score["win 3-1"].contracts == round(title_ct * 0.20)
    assert by_score["win 3-2"].contracts == round(title_ct * 0.40)
    # hedge legs carry NO fabricated edge: fair stays at the market prob.
    assert all(leg.fair == leg.market_fair for leg in p.long_legs)


def test_no_title_position_means_no_hedge():
    """With no title conviction (title leg sizes 0), the hedge is 0 — no naked duration bet."""
    params = ThreeLegParams(bankroll=D("100"), kelly_fraction=D("0.5"),
                            fatigue_coef=D("0.20"), rest_days=1, match_edge=D("0.10"))
    p = build_plans(_md(), gender="men", players=None, params=params)[0]
    assert p.title_leg.contracts == 0
    assert all(leg.contracts == 0 for leg in p.long_legs)


def test_net_pnl_outcomes():
    params = ThreeLegParams(match_edge=D("0.10"), title_edge=D("0.10"))
    p = build_plans(_md(), gender="men", players=None, params=params)[0]
    lose = next(o for o in p.outcomes if not o.is_win)
    assert p.net_pnl(lose, title_win=False) == -p.total_cost      # all legs die
    win32 = next(o for o in p.outcomes if o.label == "wins 3-2")
    # wins 3-2: match leg + the 3-2 hedge leg pay; title optional
    expected = D(p.match_leg.contracts) + win32.leg3_pay - p.total_cost
    assert p.net_pnl(win32, title_win=False) == expected
    assert p.net_pnl(win32, title_win=True) == expected + D(p.title_leg.contracts)


def _wmatch(cid, name, yes_bid, yes_ask, event="KXWTAMATCH-26JUN03SABSHN"):
    return Market(
        ticker=f"{event}-{cid}", event_ticker=event, status="open",
        yes_sub_title=name, custom_strike={"tennis_competitor": cid},
        yes_bid_dollars=yes_bid, yes_ask_dollars=yes_ask,
        no_bid_dollars="0.0", no_ask_dollars="1.0",
    )


def test_pending_hedge_trades_legs_1_2_without_length_market():
    """Women's QF with no exact-score market: legs 1-2 trade now, hedge is pending."""
    fav = _wmatch("w1", "Aryna Sabalenka", "0.86", "0.88")
    opp = _wmatch("w2", "Diana Shnaider", "0.12", "0.14")
    title = Market(
        ticker="KXFOWOMEN-26-w1", event_ticker="KXFOWOMEN-26", status="open",
        yes_sub_title="Aryna Sabalenka", custom_strike={"tennis_competitor": "w1"},
        yes_bid_dollars="0.54", yes_ask_dollars="0.58")
    md = FakeMD(
        by_series={"KXWTAMATCH": [fav, opp], "KXFOWOMEN": [title],
                   "KXATPMATCH": [], "KXFOMEN": []},
        by_event={},  # no exact-score event listed yet
    )
    params = ThreeLegParams(match_edge=D("0.04"), title_edge=D("0.02"))
    plans = build_plans(md, gender="women", players=None, params=params)
    assert len(plans) == 1
    p = plans[0]
    assert p.name == "Aryna Sabalenka" and p.long_legs == []
    assert p.hedge_pending is True
    assert p.pending_hedge_event == "KXWTAEXACTMATCH-26JUN03SABSHN"
    # Legs 1-2 still trade now (hedge deferred until its market lists):
    tickers = [o.ticker for o in proposed_orders(p)]
    assert any("KXWTAMATCH" in t for t in tickers)
