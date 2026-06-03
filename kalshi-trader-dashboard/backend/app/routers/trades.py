"""Trades router: current (resting + open positions) and history (fills + journal decisions)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from kalshi_agent_trader.journal import Journal
from kalshi_agent_trader.portfolio import Portfolio

from ..deps import get_client

router = APIRouter(prefix="/trades", tags=["trades"])


def _fmt(value) -> str | None:
    return str(value) if value is not None else None


def _norm_ts(raw) -> int | None:
    """Normalize any Kalshi timestamp to milliseconds for JavaScript Date()."""
    if raw is None:
        return None
    if isinstance(raw, str):
        from datetime import datetime
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except Exception:
            return None
    v = int(raw)
    if v > 10**16:      # nanoseconds (~1.7e18 in 2026)
        return v // 1_000_000
    if v > 10**13:      # microseconds (~1.7e15 in 2026)
        return v // 1_000
    if v > 10**10:      # milliseconds (~1.7e12 in 2026)
        return v
    return v * 1000     # seconds


@router.get("/current")
def current_trades():
    client = get_client()
    try:
        pf = Portfolio(client)
        positions = pf.market_positions()
        resting = pf.resting_orders()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {
        "open_positions": [
            {
                "ticker": p.get("ticker"),
                "position": _fmt(p.get("position_fp")),
                "exposure_usd": _fmt(p.get("market_exposure_dollars")),
                "cost_usd": _fmt(
                    p.get("total_traded_dollars") or p.get("position_cost_dollars")
                ),
                "realized_pnl_usd": _fmt(p.get("realized_pnl_dollars")),
                "raw": p,
            }
            for p in positions
        ],
        "resting_orders": [
            {
                "ticker": o.get("ticker"),
                "action": o.get("action"),
                "side": o.get("side"),
                "price_usd": _fmt(o.get("price_dollars")),
                "count": _fmt(o.get("count")),
                "status": o.get("status"),
                "raw": o,
            }
            for o in resting
        ],
    }


@router.get("/history")
def trade_history(limit: int = Query(default=200, ge=1, le=1000)):
    # Kalshi fills (authoritative, live).
    client = get_client()
    try:
        fills_raw = Portfolio(client).fills()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Kalshi fills fetch failed: {exc}")

    fills = [
        {
            "ts": _norm_ts(f.get("ts") or f.get("created_time")),
            "ticker": f.get("ticker") or f.get("market_ticker"),
            "side": f.get("side"),
            "action": f.get("action"),
            "count": _fmt(f.get("count")),
            "price_usd": _fmt(f.get("price_dollars") or f.get("price")),
            "fill_id": f.get("fill_id") or f.get("id"),
            "raw": f,
        }
        for f in fills_raw[:limit]
    ]

    # Journal decisions (incl. dry_run + rejected — the full audit trail).
    try:
        with Journal() as journal:
            decisions_raw = journal.recent_decisions(limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Journal read failed: {exc}")

    decisions = [
        {
            "ts": d["ts"],
            "source": d["source"],
            "market_ticker": d["market_ticker"],
            "side": d["side"],
            "target_price": d["target_price"],
            "fair_prob": d["fair_prob"],
            "confidence": d["confidence"],
            "outcome": d["outcome"],
            "gate": d["gate"],
            "reason": d["reason"],
        }
        for d in decisions_raw
    ]

    return {"fills": fills, "decisions": decisions}
