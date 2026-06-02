"""The MLE fitter should recover a known alpha from synthetic data."""

import random

from kalshi_agent_trader.calibration import debias, fit_alpha


def _synthetic(alpha_true, n=8000, seed=7):
    rng = random.Random(seed)
    samples = []
    for _ in range(n):
        p = rng.uniform(0.50, 0.97)               # favorite-side prices
        y = 1 if rng.random() < debias(p, alpha_true) else 0
        samples.append((p, y))
    return samples


def test_recovers_favorite_bias():
    res = fit_alpha(_synthetic(1.20))
    assert abs(res.alpha - 1.20) < 0.06
    assert res.lr_vs_null > 0          # beats the alpha=1 null


def test_recovers_upset_bias():
    res = fit_alpha(_synthetic(0.85))
    assert abs(res.alpha - 0.85) < 0.06


def test_well_calibrated_market_fits_near_one():
    res = fit_alpha(_synthetic(1.00))
    assert abs(res.alpha - 1.00) < 0.06
