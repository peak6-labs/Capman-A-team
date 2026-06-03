"""Tests for the bracket Monte-Carlo fair-value model.

Locks the invariants that make the model trustworthy: calibration reproduces the
anchored match prob exactly, match_prob is a correct logistic, and the simulated
progression probabilities are coherent (titles sum to 1; reach-final >= win;
reach-sf >= reach-final). Drives the generic engine off the tracked RG-2026 draws.
"""

import math
from pathlib import Path

from kalshi_agent_trader.simulate import (
    Matchup, calibrate_ratings, implied_elo_diff, load_draw, load_ratings,
    match_prob, simulate_draw,
)

_ROOT = Path(__file__).resolve().parents[1]
_RATINGS = _ROOT / "ratings" / "clay_elo.yaml"
_MEN = _ROOT / "draws" / "rg-2026-men.yaml"
_WOMEN = _ROOT / "draws" / "rg-2026-women.yaml"


def test_match_prob_symmetric_and_monotone():
    assert math.isclose(match_prob(1800, 1800), 0.5, abs_tol=1e-12)
    assert match_prob(2000, 1800) > 0.5
    assert math.isclose(match_prob(1800, 2000), 1 - match_prob(2000, 1800), abs_tol=1e-12)


def test_implied_elo_diff_roundtrips():
    for p in (0.55, 0.665, 0.86, 0.21):
        d = implied_elo_diff(p)
        assert math.isclose(match_prob(1800 + d, 1800), p, abs_tol=1e-6)


def test_calibration_reproduces_market_and_preserves_mean():
    raw = {"A": 1808.1, "B": 1781.6}
    R = calibrate_ratings(raw, [Matchup("A", "B", 0.665)])
    # the calibrated pair reproduces the market match prob
    assert math.isclose(match_prob(R["A"], R["B"]), 0.665, abs_tol=1e-6)
    # and holds the pair average fixed
    assert math.isclose((R["A"] + R["B"]) / 2, (raw["A"] + raw["B"]) / 2, abs_tol=1e-6)


def test_loaders_round_trip():
    draw = load_draw(_MEN)
    ratings = load_ratings(_RATINGS)
    assert draw.gender == "men" and draw.roles["sf"] == "SF"
    assert "Zverev" in draw.players and isinstance(ratings["Zverev"], float)


def test_men_progression_coherent():
    draw = load_draw(_MEN)
    ratings = load_ratings(_RATINGS)
    anchored = {("Fonseca", "Mensik"): 0.665, ("Auger", "Cobolli"): 0.485,
                ("Berrettini", "Arnaldi"): 0.625}
    R = calibrate_ratings(ratings, [Matchup(a, b, p) for (a, b), p in anchored.items()])
    res = simulate_draw(draw, R, anchored, n=40_000, seed=1)
    assert math.isclose(sum(res.win_title.values()), 1.0, abs_tol=1e-6)
    assert math.isclose(sum(res.reach_final.values()), 2.0, abs_tol=1e-6)  # two finalists
    assert math.isclose(sum(res.reach_sf.values()), 4.0, abs_tol=1e-6)     # four semifinalists
    # Zverev has a bye to the SF in this state
    assert res.reach_sf["Zverev"] == 1.0
    for p in res.win_title:
        assert res.reach_sf[p] + 1e-9 >= res.reach_final[p] >= res.win_title[p] - 1e-9


def test_women_bottom_half_final_equals_match_prob():
    draw = load_draw(_WOMEN)
    ratings = load_ratings(_RATINGS)
    anchored = {("Sabalenka", "Shnaider"): 0.865, ("Kalinskaya", "Chwalinska"): 0.51,
                ("Kostyuk", "Andreeva"): 0.555}
    R = calibrate_ratings(ratings, [Matchup(a, b, p) for (a, b), p in anchored.items()])
    res = simulate_draw(draw, R, anchored, n=60_000, seed=2)
    # Kostyuk/Andreeva ARE the SF: each reaches the final iff she wins that match
    assert math.isclose(res.reach_final["Kostyuk"], 0.555, abs_tol=0.01)
    assert math.isclose(res.reach_final["Andreeva"], 0.445, abs_tol=0.01)
    assert res.reach_sf["Kostyuk"] == 1.0 and res.reach_sf["Andreeva"] == 1.0
    assert math.isclose(sum(res.win_title.values()), 1.0, abs_tol=1e-6)
