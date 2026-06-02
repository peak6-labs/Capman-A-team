"""Tests for player selection + pairing in the tennis screener."""

from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from kalshi_agent_trader.models import Market
from kalshi_agent_trader import tennis_screen as ts

S = dict(stake_no=Decimal("100"), stake_tourney=Decimal("100"))


def _match(cid, name, no_ask="0.38", yes_ask="0.62", status="open"):
    return Market(
        ticker=f"KXATPMATCH-{name}", event_ticker="E", status=status,
        yes_sub_title=name, no_sub_title=name,
        custom_strike={"tennis_competitor": cid},
        no_ask_dollars=no_ask, no_bid_dollars="0.36",
        yes_ask_dollars=yes_ask, yes_bid_dollars="0.60",
    )


def _tourney(cid, name, yes_ask="0.17", no_ask="0.85"):
    return Market(
        ticker=f"KXFOMEN-{name}", event_ticker="E", status="open",
        yes_sub_title=name, custom_strike={"tennis_competitor": cid},
        yes_ask_dollars=yes_ask, yes_bid_dollars="0.15",
        no_ask_dollars=no_ask, no_bid_dollars="0.83",
    )


class FakeMarketData:
    """Returns canned (markets, None) keyed by series_ticker; records calls."""

    def __init__(self, by_series: Dict[str, List[Market]]):
        self.by_series = by_series
        self.seen_series: List[str] = []

    def list_markets(self, *, status=None, series_ticker=None, limit=100, cursor=None
                     ) -> Tuple[List[Market], Optional[str]]:
        self.seen_series.append(series_ticker)
        return self.by_series.get(series_ticker, []), None


def _rows(md, gender="men", players=None, strategy="hedge"):
    return ts.build_player_rows(
        md, gender=gender, players=players, strategy=strategy, **S)


def test_normalize_name_accents_and_case():
    assert ts.normalize_name("Félix Auger-Aliassime") == "felix auger aliassime"
    assert "cobolli" in ts.normalize_name("Flavio Cobolli")


def test_pair_by_uuid_produces_result():
    md = FakeMarketData({
        ts.MEN_MATCH: [_match("u1", "Flavio Cobolli")],
        ts.MEN_TOURNEY: [_tourney("u1", "Flavio Cobolli")],
    })
    rows = _rows(md)
    assert len(rows) == 1
    row = rows[0]
    assert row.competitor_id == "u1"
    assert row.result is not None
    # n=0.38, t0=0.17, equal $100 -> t*=0.34
    assert row.result.breakeven_price == Decimal("0.34")
    assert row.result.breakeven_feasible is True


def test_only_tournament_market_no_match_today():
    md = FakeMarketData({ts.MEN_MATCH: [], ts.MEN_TOURNEY: [_tourney("u1", "X")]})
    row = _rows(md)[0]
    assert row.result is None and row.note == "no match today"


def test_only_match_market_not_in_title():
    md = FakeMarketData({ts.MEN_MATCH: [_match("u1", "X")], ts.MEN_TOURNEY: []})
    row = _rows(md)[0]
    assert row.result is None and row.note == "not in title market"


def test_null_ask_is_illiquid():
    m = _match("u1", "X")
    m = m.model_copy(update={"no_ask": None})
    md = FakeMarketData({ts.MEN_MATCH: [m], ts.MEN_TOURNEY: [_tourney("u1", "X")]})
    row = _rows(md)[0]
    assert row.result is None and row.note == "no ask / illiquid"


def test_closed_match_status():
    md = FakeMarketData({
        ts.MEN_MATCH: [_match("u1", "X", status="settled")],
        ts.MEN_TOURNEY: [_tourney("u1", "X")],
    })
    row = _rows(md)[0]
    assert row.result is None and row.note == "match closed/settled"


def test_player_substring_filter():
    md = FakeMarketData({
        ts.MEN_MATCH: [_match("u1", "Flavio Cobolli"), _match("u2", "Carlos Alcaraz")],
        ts.MEN_TOURNEY: [_tourney("u1", "Flavio Cobolli"), _tourney("u2", "Carlos Alcaraz")],
    })
    rows = _rows(md, players=["cobolli"])
    assert len(rows) == 1 and rows[0].name == "Flavio Cobolli"
    assert _rows(md, players=["nobody"]) == []


def test_gender_filter_routes_to_correct_series():
    md = FakeMarketData({})
    ts.build_player_rows(md, gender="women", players=None, **S)
    assert set(md.seen_series) == {ts.WOMEN_MATCH, ts.WOMEN_TOURNEY}


def test_fade_strategy_produces_scenario_nets():
    # match YES=0.62, title NO=0.85 -> fade returns terminal-outcome P&Ls.
    md = FakeMarketData({
        ts.MEN_MATCH: [_match("u1", "X")],
        ts.MEN_TOURNEY: [_tourney("u1", "X")],
    })
    row = _rows(md, strategy="fade")[0]
    m = row.result
    assert m is not None
    assert m.match_price == Decimal("0.62")        # match YES, not NO
    assert m.title_no_price == Decimal("0.85")     # the title NO leg
    # advancing without the title pays both legs -> always the best outcome.
    assert m.advance_net > m.lose_match_net
    assert m.advance_net > m.win_title_net
