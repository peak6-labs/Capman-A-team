"""Tests for the intraday title-dip detector (reversion.py)."""

from __future__ import annotations

from decimal import Decimal

from kalshi_agent_trader.models import Market
from kalshi_agent_trader.reversion import (
    Anchor,
    DipParams,
    DipTracker,
    assess,
)

D = Decimal


def _anchor(c_ratio="0.5", match="0.80", title="0.40", name="Favorite") -> Anchor:
    return Anchor(
        competitor_id="cid-1", name=name, gender="men",
        c_ratio=D(c_ratio), match_mid=D(match), title_mid=D(title),
    )


def _assess(anchor, match, title_ask, *, title_bid=None, params=None, peak=D("0")):
    """Convenience: symmetric match book around `match`, title book at the ask."""
    p = params or DipParams()
    tb = D(str(title_bid)) if title_bid is not None else D(str(title_ask)) - D("0.01")
    return assess(
        anchor,
        match_bid=D(str(match)) - D("0.01"), match_ask=D(str(match)) + D("0.01"),
        title_bid=tb, title_ask=D(str(title_ask)),
        params=p, peak_residual=peak,
    )


# --------------------------------------------------------------------------- #
# assess(): the core signal
# --------------------------------------------------------------------------- #

def test_no_dip_is_watch():
    # title sits right at fair (C=0.5, match=0.80 -> fair=0.40); residual ~0.
    s = _assess(_anchor(), match="0.80", title_ask="0.40", title_bid="0.40")
    assert s is not None
    assert s.action == "WATCH"
    assert abs(s.residual) < D("0.02")


def test_overreaction_triggers_buy():
    # Deep, clean dislocation: match held at 0.80 (fair 0.40) but title cratered to
    # 0.25 — a 15c over-reaction. With conviction above the floor-stop break-even
    # (~46% here) it sizes a real stake.
    p = DipParams(p_revert=D("0.65"))
    s = _assess(_anchor(), match="0.80", title_ask="0.25", title_bid="0.24", params=p)
    assert s.action == "BUY DIP"
    assert s.residual >= D("0.05")
    assert s.stake > 0
    assert s.est_profit > 0          # reverting ~15c clears round-trip fees
    assert s.overreaction_frac > D("0.9")  # match barely moved -> nearly all unexplained


def test_coinflip_conviction_sizes_to_zero():
    # A moderate dip (match 0.78 -> fair 0.39, title 0.30) has a high break-even p
    # (~73%) because the floor-stop loss is large. At a ~coin-flip p_revert it is
    # EV<=0, so it must NOT size — the edge honestly depends on an uncalibrated p.
    p = DipParams(p_revert=D("0.50"))
    s = _assess(_anchor(), match="0.78", title_ask="0.30", title_bid="0.29", params=p)
    assert s.stake == 0
    assert s.action == "WATCH"       # flagged as dislocated-but-not-worth-it


def test_phi_scales_size():
    # Two dips to the SAME title price (0.25) but different match support:
    #  - match held (0.80) -> almost the whole drop is unexplained -> high φ
    #  - match also fell (0.66) -> much of the drop is justified -> low φ
    # φ scales the stake (not the EV gate), so both can size but the cleaner one
    # gets more. Lift the position cap so φ scaling is visible, not capped away.
    p = DipParams(p_revert=D("0.85"), max_position_frac=D("1.0"))
    a = _anchor(c_ratio="0.5", match="0.80", title="0.40")
    clean = _assess(a, match="0.80", title_ask="0.25", title_bid="0.24", params=p)
    justified = _assess(a, match="0.66", title_ask="0.25", title_bid="0.24", params=p)
    assert clean.overreaction_frac > justified.overreaction_frac
    assert clean.stake > justified.stake > 0   # comparison drives relative size


def test_match_collapse_is_stop_not_buy():
    # match cratered below the recover floor: the low title price is now correct.
    s = _assess(_anchor(), match="0.20", title_ask="0.10", title_bid="0.09")
    assert s.action == "STOP"


def test_reverting_then_reverted():
    a = _anchor()
    p = DipParams()
    # peaked at a 10c over-reaction earlier; now title has climbed back, 3c left to fair.
    reverting = _assess(a, match="0.80", title_ask="0.37", title_bid="0.36",
                        params=p, peak=D("0.10"))
    assert reverting.action == "REVERTING"
    # now within the exit band of fair (fair=0.40, title~0.39).
    reverted = _assess(a, match="0.80", title_ask="0.395", title_bid="0.39",
                       params=p, peak=D("0.10"))
    assert reverted.action == "REVERTED"


def test_residual_scales_with_match_move():
    # As the match recovers, match-implied fair rises, shrinking the residual.
    a = _anchor()
    deep = _assess(a, match="0.78", title_ask="0.30")
    shallow = _assess(a, match="0.84", title_ask="0.30")
    assert deep.residual < shallow.residual  # higher match mid -> higher fair -> bigger gap


def test_missing_prices_returns_none():
    s = assess(_anchor(), match_bid=None, match_ask=None,
               title_bid=None, title_ask=None, params=DipParams())
    assert s is None


# --------------------------------------------------------------------------- #
# DipTracker: anchoring + state across polls
# --------------------------------------------------------------------------- #

def _market(ticker, cid, yes_bid, yes_ask, sub_title="Favorite") -> Market:
    return Market(
        ticker=ticker, event_ticker="EV", yes_sub_title=sub_title,
        custom_strike={"tennis_competitor": cid},
        yes_bid_dollars=str(yes_bid), yes_ask_dollars=str(yes_ask),
    )


def _universe(match_yes, title_yes, cid="cid-1"):
    # Build books as 2-dp strings so mids are exact (no binary-float noise).
    f = lambda x: f"{x:.2f}"
    match = _market("KXATPMATCH-X", cid, f(match_yes - 0.01), f(match_yes + 0.01))
    title = _market("KXFOMEN-X", cid, f(title_yes - 0.01), f(title_yes + 0.01))
    return {"men": ({cid: match}, {cid: title})}


def test_tracker_anchors_favourite_then_detects_dip():
    tracker = DipTracker(DipParams(p_revert=D("0.85")))
    # Poll 1: pre-dip. match 0.80, title 0.40 -> anchors C = 0.5.
    sigs = tracker.update(_universe(0.80, 0.40))
    assert len(sigs) == 1 and sigs[0].action == "WATCH"
    assert tracker.anchors["cid-1"].c_ratio == D("0.5")
    # Poll 2: title craters to ~0.25 while the match holds ~0.80 -> BUY DIP, sized.
    sigs = tracker.update(_universe(0.80, 0.25))
    assert sigs[0].action == "BUY DIP"
    assert sigs[0].stake > 0
    # peak over-reaction is remembered.
    assert tracker.peak["cid-1"] >= D("0.05")
    # Poll 3: reverts to fair -> REVERTED (uses remembered peak).
    sigs = tracker.update(_universe(0.80, 0.40))
    assert sigs[0].action == "REVERTED"


def test_tracker_skips_non_favourites():
    # A longshot (match mid 0.20) is never anchored.
    tracker = DipTracker(DipParams())
    sigs = tracker.update(_universe(0.20, 0.05))
    assert sigs == []
    assert "cid-1" not in tracker.anchors


def test_tracker_anchor_is_sticky():
    # Anchor seeded on poll 1 must not drift when later polls move.
    tracker = DipTracker(DipParams())
    tracker.update(_universe(0.80, 0.40))
    c0 = tracker.anchors["cid-1"].c_ratio
    tracker.update(_universe(0.60, 0.20))
    assert tracker.anchors["cid-1"].c_ratio == c0
