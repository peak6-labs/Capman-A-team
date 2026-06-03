"""Tests for the light title-vs-match convergence backtest (validation harness).

Locks the scoring core: filename parsing, candle mid extraction, the anchored
conditional, the divergence threshold, and that a reverting series scores as
converged while a diverging one does not.
"""

import pytest

from kalshi_agent_trader.convergence_backtest import (
    Report, _mid, parse_match_file, parse_title_file, score_window,
)


def test_parse_filenames():
    assert parse_match_file("KXATPMATCH-26JUN02JODZVE-ZVE_1779_1780.json") == ("men", "ZVE")
    assert parse_match_file("KXWTAMATCH-26JUN02SVIKOS-KOS_1_2.json") == ("women", "KOS")
    assert parse_title_file("KXFOMEN-26-FON_1_2.json") == ("men", "FON")
    assert parse_title_file("KXFOWOMEN-26-AND_1_2.json") == ("women", "AND")
    assert parse_match_file("KXFOMEN-26-FON_1_2.json") is None
    assert parse_title_file("not-a-market.json") is None


def test_mid_prefers_bidask_then_price():
    assert _mid({"yes_bid": {"close_dollars": "0.40"}, "yes_ask": {"close_dollars": "0.44"}}) == pytest.approx(0.42)
    assert _mid({"yes_ask": {"close_dollars": "0"}, "price": {"mean_dollars": "0.30"}}) == 0.30
    assert _mid({"price": {"previous_dollars": "0.10"}}) == 0.10
    assert _mid({"price": {}}) is None


def _series(points):
    return [(int(t), float(p)) for t, p in points]


def test_converging_overreaction_scores_converged_and_profitable():
    # match steady at 0.50; title anchored at 0.40 (C=0.8), spikes to 0.50 (rich),
    # then reverts toward fair (0.40). A fade should converge and profit.
    match = _series([(0, 0.50), (3600, 0.50), (7200, 0.50)])
    title = _series([(0, 0.40), (3600, 0.50), (7200, 0.40)])
    trades = score_window(match, title, gender="men", code="X", threshold=0.03, horizon_s=3600)
    assert len(trades) == 1
    t = trades[0]
    assert t.divergence > 0           # title rich vs fair (0.8*0.50=0.40)
    assert t.converged               # reverted toward fair
    assert t.pnl_net > 0             # fade captured the reversion


def test_no_trade_when_within_threshold():
    match = _series([(0, 0.50), (3600, 0.50)])
    title = _series([(0, 0.40), (3600, 0.41)])   # divergence 0.01 < threshold
    assert score_window(match, title, gender="men", code="X", threshold=0.03) == []


def test_report_summary_buckets_by_gender():
    match = _series([(0, 0.50), (3600, 0.50), (7200, 0.50)])
    title = _series([(0, 0.40), (3600, 0.50), (7200, 0.40)])
    rep = Report(threshold=0.03, horizon_s=3600)
    rep.trades.extend(score_window(match, title, gender="women", code="Y", threshold=0.03, horizon_s=3600))
    s = rep.summary()
    assert s["overall"]["n"] == 1
    assert s["by_gender"]["women"]["n"] == 1
    assert s["by_gender"]["men"]["n"] == 0
