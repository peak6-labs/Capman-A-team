"""PnL router: summary, cumulative time series, and Brier calibration."""

from __future__ import annotations

import time
from decimal import Decimal

from fastapi import APIRouter, HTTPException

from kalshi_agent_trader.analysis.calibration import score_calibration
from kalshi_agent_trader.journal import Journal
from kalshi_agent_trader.market_data import MarketData
from kalshi_agent_trader.portfolio import Portfolio

from ..deps import cached, get_client
from ..pnl import cumulative_pnl_series, realized_usd, unrealized_usd

router = APIRouter(prefix="/pnl", tags=["pnl"])

_CALIBRATION_TTL = 300  # 5 minutes


def _fmt(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


@router.get("/summary")
def pnl_summary():
    client = get_client()
    try:
        pf = Portfolio(client)
        positions = pf.market_positions()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    r_usd = realized_usd(positions)

    # Build YES mids for positions that don't supply unrealized_pnl_dollars.
    needs_mid = [
        p["ticker"]
        for p in positions
        if p.get("unrealized_pnl_dollars") is None and p.get("position_fp") not in (None, "0", 0)
    ]
    yes_mids: dict[str, Decimal] = {}
    if needs_mid:
        md = MarketData(client)
        for ticker in needs_mid:
            try:
                market = md.get_market(ticker)
                bid = market.yes_bid
                ask = market.yes_ask
                if bid is not None and ask is not None:
                    yes_mids[ticker] = (bid + ask) / 2
                elif market.last_price is not None:
                    yes_mids[ticker] = market.last_price
            except Exception:
                pass  # leave mid absent; unrealized contribution = 0

    u_usd = unrealized_usd(positions, yes_mids)
    total = r_usd + u_usd

    return {
        "realized_usd": _fmt(r_usd),
        "unrealized_usd": _fmt(u_usd),
        "total_usd": _fmt(total),
        "as_of": int(time.time() * 1000),
    }


@router.get("/timeseries")
def pnl_timeseries():
    client = get_client()
    try:
        pf = Portfolio(client)
        positions = pf.market_positions()
        fills = pf.fills()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    r_usd = realized_usd(positions)
    series = cumulative_pnl_series(fills, r_usd)

    # Unrealized for the "current" marker.
    u_usd = unrealized_usd(positions, {})  # no mids needed for display marker only

    return {
        "points": series,
        "current_unrealized_usd": _fmt(u_usd),
    }


@router.get("/calibration")
def pnl_calibration():
    """Brier-score report. Cached for 5 minutes (one get_market call per closed position)."""
    client = get_client()

    def _compute():
        try:
            with Journal() as journal:
                md = MarketData(client)
                return score_calibration(journal, md)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    report = cached("calibration", _CALIBRATION_TTL, _compute)

    def _bucket(b):
        return {
            "label": b.label,
            "count": b.count,
            "brier": b.brier,
            "mean_predicted": b.mean_predicted,
            "mean_realized": b.mean_realized,
        }

    return {
        "scored": report.scored,
        "skipped_unsettled": report.skipped_unsettled,
        "skipped_no_prediction": report.skipped_no_prediction,
        "overall": _bucket(report.overall),
        "by_source": [_bucket(b) for b in report.by_source.values()],
        "by_category": [_bucket(b) for b in report.by_category.values()],
    }
