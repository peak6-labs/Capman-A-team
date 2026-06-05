"""PnL router: summary, cumulative time series, and Brier calibration."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from decimal import Decimal
import os

from fastapi import APIRouter, HTTPException, Query

from kalshi_agent_trader.analysis.calibration import score_calibration
from kalshi_agent_trader.journal import Journal
from kalshi_agent_trader.market_data import MarketData
from kalshi_agent_trader.portfolio import Portfolio

from ..deps import cached, get_client
from ..market_meta import market_meta, resolve_name
from ..pnl import (
    cumulative_pnl_series,
    daily_pnl_series,
    daily_settlement_pnl_series,
    realized_usd,
    settlement_history_rows,
    trade_pnl_rows,
    unrealized_usd,
)
from ..serializers import fmt_decimal

router = APIRouter(prefix="/pnl", tags=["pnl"])

_CALIBRATION_TTL = 300  # 5 minutes
_STARTING_BANKROLL_USD = Decimal(os.getenv("KALSHI_STARTING_BANKROLL_USD", "500"))


def _fmt(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.0001")).normalize(), "f")


def _mid_for_ticker(md: MarketData, ticker: str) -> tuple[str, Decimal | None]:
    try:
        market = md.get_market(ticker)
        bid = market.yes_bid
        ask = market.yes_ask
        if bid is not None and ask is not None:
            return ticker, (bid + ask) / 2
        return ticker, market.last_price
    except Exception:
        return ticker, None


def _yes_mids_for_positions(client, positions: list[dict]) -> dict[str, Decimal]:
    needs_mid = {
        p["ticker"]
        for p in positions
        if p.get("ticker")
        and p.get("unrealized_pnl_dollars") is None
        and p.get("position_fp") not in (None, "0", 0)
    }
    if not needs_mid:
        return {}

    md = MarketData(client)
    yes_mids: dict[str, Decimal] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(needs_mid))) as pool:
        futures = [pool.submit(_mid_for_ticker, md, ticker) for ticker in needs_mid]
        for future in as_completed(futures):
            ticker, mid = future.result()
            if mid is not None:
                yes_mids[ticker] = mid
    return yes_mids


@router.get("/summary")
def pnl_summary():
    client = get_client()
    try:
        pf = Portfolio(client)
        bal = pf.balance()
        positions = pf.market_positions()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # Build YES mids for positions that don't supply unrealized_pnl_dollars.
    yes_mids = _yes_mids_for_positions(client, positions)
    u_usd = unrealized_usd(positions, yes_mids)
    cash = bal.usd() or Decimal("0")
    open_value = bal.portfolio_value_usd() or Decimal("0")
    account_value = cash + open_value
    total = account_value - _STARTING_BANKROLL_USD
    cash_pnl = cash - _STARTING_BANKROLL_USD

    return {
        "realized_usd": fmt_decimal(cash_pnl),
        "unrealized_usd": fmt_decimal(open_value if open_value else u_usd),
        "total_usd": fmt_decimal(total),
        "starting_bankroll_usd": fmt_decimal(_STARTING_BANKROLL_USD),
        "account_value_usd": fmt_decimal(account_value),
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
        "current_unrealized_usd": fmt_decimal(u_usd),
    }


@router.get("/daily")
def pnl_daily(start: str | None = Query(default=None)):
    client = get_client()
    try:
        pf = Portfolio(client)
        fills = pf.fills()
        settlements = pf.settlements()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    try:
        start_date = date.fromisoformat(start) if start else date(date.today().year, 6, 1)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="start must be YYYY-MM-DD") from exc

    settlement_points = daily_settlement_pnl_series(settlements, start=start_date)
    has_settlement_pnl = any(Decimal(point["realized_usd"]) != 0 for point in settlement_points)
    points = settlement_points if has_settlement_pnl else daily_pnl_series(fills, None, start=start_date)
    return {"start_date": start_date.isoformat(), "points": points}


@router.get("/trades")
def pnl_trades():
    client = get_client()
    try:
        pf = Portfolio(client)
        positions = pf.market_positions()
        fills = pf.fills()
        settlements = pf.settlements()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    settlement_rows = settlement_history_rows(settlements)
    settled_tickers = {row["ticker"] for row in settlement_rows}
    open_rows = [
        row for row in trade_pnl_rows(fills, positions)
        if row["ticker"] not in settled_tickers
    ]
    rows = sorted(
        settlement_rows + open_rows,
        key=lambda row: int(row["last_order_at"] or 0),
        reverse=True,
    )
    meta = market_meta(client, [row["ticker"] for row in rows])
    for row in rows:
        m = meta.get(row["ticker"])
        row["title"] = m.title if m else None
        row["name"] = resolve_name(m, row["held_side"])
        row["group_title"] = m.title if m else row["ticker"]
        row["settlement_result"] = m.result if m else None
        if m and m.result in ("yes", "no"):
            count = Decimal(str(row["entry_count"]))
            total_cost = Decimal(str(row["total_cost_usd"]))
            settlement = count if m.result == row["held_side"] else Decimal("0")
            total_return = settlement - total_cost
            pct = (total_return / total_cost * 100) if total_cost else Decimal("0")
            row["settlement_payout_usd"] = _fmt(settlement)
            row["total_payout_usd"] = _fmt(settlement)
            row["total_return_usd"] = _fmt(total_return)
            row["total_return_pct"] = _fmt(pct)
            row["pnl_usd"] = row["total_return_usd"]
            row["status"] = "settled"
    return {"trades": rows}


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
