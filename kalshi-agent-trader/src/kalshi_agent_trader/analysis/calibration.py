"""Brier calibration scoring for closed positions.

Measures whether the agents' `fair_prob` estimates were calibrated against realized
Kalshi settlement outcomes. This is the feedback loop the journal enables but did not
previously compute: prompt/model tuning becomes data-driven instead of intuition-driven.

Resolution is fetched from Kalshi at scoring time (no extra schema): `MarketData.get_market`
returns `Market.result` ("yes"/"no", empty while unsettled). The predicted probability for a
position's traded side is recovered from the `decisions` table — `positions` stores
`confidence` but not `fair_prob`, so we join the latest placed/dry_run decision for that
ticker at or before the position's open time.

Read-only: this never places or cancels an order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..journal import Journal
from ..market_data import MarketData


@dataclass
class Bucket:
    """Calibration stats for a group of scored positions."""

    label: str
    count: int = 0
    _sq_error_sum: float = 0.0
    _pred_sum: float = 0.0
    _realized_sum: float = 0.0

    def add(self, predicted: float, realized: float) -> None:
        self.count += 1
        self._sq_error_sum += (predicted - realized) ** 2
        self._pred_sum += predicted
        self._realized_sum += realized

    @property
    def brier(self) -> Optional[float]:
        return None if self.count == 0 else self._sq_error_sum / self.count

    @property
    def mean_predicted(self) -> Optional[float]:
        return None if self.count == 0 else self._pred_sum / self.count

    @property
    def mean_realized(self) -> Optional[float]:
        return None if self.count == 0 else self._realized_sum / self.count


@dataclass
class CalibrationReport:
    overall: Bucket = field(default_factory=lambda: Bucket("overall"))
    by_source: Dict[str, Bucket] = field(default_factory=dict)
    by_category: Dict[str, Bucket] = field(default_factory=dict)
    scored: int = 0
    skipped_unsettled: int = 0   # market has no result yet
    skipped_no_prediction: int = 0  # no decision row to recover fair_prob from

    def _bucket(self, table: Dict[str, Bucket], key: str) -> Bucket:
        if key not in table:
            table[key] = Bucket(key)
        return table[key]

    def record(self, *, predicted: float, realized: float, source: str, category: str) -> None:
        self.scored += 1
        self.overall.add(predicted, realized)
        self._bucket(self.by_source, source).add(predicted, realized)
        self._bucket(self.by_category, category).add(predicted, realized)


def _recover_prediction(journal: Journal, ticker: str, side: str, opened_ts: int):
    """Return (fair_prob, source) from the latest placed/dry_run decision at/before open.

    fair_prob is stored for the decision's `side`; if the position side differs we flip it.
    Returns (None, None) when no usable decision exists.
    """
    best = None
    for row in journal.decisions_for_ticker(ticker):
        if row["ts"] is not None and opened_ts is not None and row["ts"] > opened_ts:
            continue
        if row["fair_prob"] is None:
            continue
        best = row  # rows are ordered oldest-first; keep the last qualifying one
    if best is None:
        return None, None
    fair_prob = float(best["fair_prob"])
    if best["side"] and best["side"] != side:
        fair_prob = 1.0 - fair_prob
    return fair_prob, (best["source"] or "unknown")


def score_calibration(journal: Journal, market_data: MarketData) -> CalibrationReport:
    """Score every closed/resolved position against Kalshi settlement.

    For each position: fetch the market's settlement result, derive the realized outcome
    for the traded side (1.0 if the side won, else 0.0), recover the predicted fair_prob
    from the journal, and accumulate Brier stats overall and bucketed by source/category.
    """
    report = CalibrationReport()

    for pos in journal.closed_positions():
        ticker = pos["ticker"]
        side = pos["side"]

        try:
            market = market_data.get_market(ticker)
        except Exception:
            report.skipped_unsettled += 1
            continue

        result = (market.result or "").lower()
        if result not in {"yes", "no"}:
            report.skipped_unsettled += 1
            continue

        realized = 1.0 if result == side else 0.0
        predicted, source = _recover_prediction(journal, ticker, side, pos["opened_ts"])
        if predicted is None:
            report.skipped_no_prediction += 1
            continue

        try:
            category = market_data.category_for_market(market) or "unknown"
        except Exception:
            category = "unknown"

        report.record(
            predicted=predicted,
            realized=realized,
            source=source,
            category=category,
        )

    return report
