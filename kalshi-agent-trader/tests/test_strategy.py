"""Tests for the trade-decision + sizing layer."""

from decimal import Decimal

from kalshi_agent_trader.strategy import StrategyParams, debias_yes, evaluate

D = Decimal


def test_debias_direction():
    # alpha=1 -> unchanged
    assert abs(debias_yes(D("0.20"), D("1.0")) - D("0.20")) < D("1e-9")
    # alpha>1 shrinks longshots, lifts favourites, fixes 0.5
    assert debias_yes(D("0.10"), D("1.3")) < D("0.10")
    assert debias_yes(D("0.70"), D("1.3")) > D("0.70")
    assert abs(debias_yes(D("0.50"), D("1.5")) - D("0.50")) < D("1e-9")


def _params(**over):
    base = dict(bankroll=D("10000"), fl_alpha=D("1.3"), kelly_fraction=D("0.5"),
                min_edge=D("0.03"), max_position_frac=D("0.05"),
                max_bucket_frac=D("0.20"), match_frac=D("1.0"))
    base.update(over)
    return StrategyParams(**base)


def _ev(params, room=D("2000"), **over):
    base = dict(player="X", gender="women",
                match_yes_ask=D("0.60"), match_no_ask=D("0.40"),
                title_yes_bid=D("0.10"), title_yes_ask=D("0.10"), title_no_ask=D("0.90"))
    base.update(over)
    return evaluate(params=params, bucket_room=room, **base)


def test_fade_fires_on_overpriced_longshot():
    d = _ev(_params())
    assert d.action == "FADE"
    assert d.edge >= D("0.03")
    # half-Kelly stake is large, so it should bind on the per-position cap:
    # max_position 5% of 10k = $500, split across two legs -> $250 title leg.
    assert d.title_stake == D("250")
    assert d.total_cost == D("500")
    assert d.max_loss < 0          # held-to-settlement worst case is a loss


def test_no_edge_passes():
    # alpha=1 -> fair == quoted -> no edge anywhere.
    d = _ev(_params(fl_alpha=D("1.0")))
    assert d.action == "PASS"


def test_gated_hedge_only_when_allowed():
    # strong favourite, big de-bias -> title looks under-priced -> hedge edge.
    fav = dict(title_yes_bid=D("0.70"), title_yes_ask=D("0.70"), title_no_ask=D("0.30"),
               match_yes_ask=D("0.55"), match_no_ask=D("0.45"))
    on = _ev(_params(fl_alpha=D("2.0"), allow_hedge=True), **fav)
    off = _ev(_params(fl_alpha=D("2.0"), allow_hedge=False), **fav)
    assert on.action == "HEDGE"
    assert off.action == "PASS"


def test_bucket_cap_blocks_when_no_room():
    d = _ev(_params(), room=D("0"))
    assert d.action == "PASS"


def test_longshot_cap_blocks_fade_on_midprice():
    # title mid 0.45 > longshot cap 0.40 -> no fade even if a de-bias edge exists.
    d = _ev(_params(longshot_yes_cap=D("0.40")),
            title_yes_bid=D("0.45"), title_yes_ask=D("0.45"), title_no_ask=D("0.55"))
    assert d.action in ("PASS", "HEDGE")   # not FADE
    assert d.action != "FADE"
