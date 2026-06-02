"""Calibrate the favourite-longshot de-bias coefficient ``alpha`` to realized outcomes.

Model: P(win) = debias(quoted_price, alpha) = p^a / (p^a + (1-p)^a).
Fit ``alpha`` by maximum likelihood over settled binary markets:

  * alpha > 1  -> favorites won MORE than priced (favourite-longshot bias) -> bet favorites
  * alpha = 1  -> market well calibrated (no edge)
  * alpha < 1  -> favorites won LESS than priced -> bet upsets

Pure / no I/O so it is unit-testable; the data gathering lives in
scripts/calibrate_alpha.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

Sample = Tuple[float, int]  # (quoted price in (0,1), outcome 1=win / 0=loss)


def debias(p: float, a: float) -> float:
    if p <= 0:
        return 1e-9
    if p >= 1:
        return 1 - 1e-9
    num = p ** a
    return num / (num + (1 - p) ** a)


def loglik(samples: Sequence[Sample], a: float) -> float:
    s = 0.0
    for p, y in samples:
        f = min(max(debias(p, a), 1e-9), 1 - 1e-9)
        s += math.log(f) if y else math.log(1 - f)
    return s


@dataclass
class CalibrationResult:
    alpha: float          # MLE estimate
    loglik: float         # log-likelihood at alpha
    loglik_at_1: float    # log-likelihood at alpha=1 (the "market is right" null)
    n: int
    grid: List[Tuple[float, float]]

    @property
    def lr_vs_null(self) -> float:
        """Likelihood-ratio statistic vs alpha=1 (higher => stronger evidence)."""
        return 2 * (self.loglik - self.loglik_at_1)


def fit_alpha(samples: Sequence[Sample], lo: float = 0.70, hi: float = 1.60,
              step: float = 0.01) -> CalibrationResult:
    grid: List[Tuple[float, float]] = []
    best = None
    a = lo
    while a <= hi + 1e-9:
        ll = loglik(samples, a)
        grid.append((round(a, 4), ll))
        if best is None or ll > best[1]:
            best = (round(a, 4), ll)
        a += step
    return CalibrationResult(best[0], best[1], loglik(samples, 1.0), len(samples), grid)


def calibration_table(samples: Sequence[Sample], alpha: float,
                      edges=(0.5, 0.6, 0.7, 0.8, 0.9, 1.0001)):
    """Per-price-bucket: n, avg price, actual win%, and debiased prediction."""
    rows = []
    for lo, hi in zip(edges, edges[1:]):
        b = [(p, y) for p, y in samples if lo <= p < hi]
        if not b:
            continue
        actual = sum(y for _, y in b) / len(b)
        pred = sum(debias(p, alpha) for p, _ in b) / len(b)
        avg = sum(p for p, _ in b) / len(b)
        rows.append((lo, hi, len(b), avg, actual, pred))
    return rows
