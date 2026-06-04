"""Trades router: current (resting + open positions) and history (fills + journal decisions)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from kalshi_agent_trader.journal import Journal
from kalshi_agent_trader.portfolio import Portfolio

from ..deps import get_client
from ..serializers import normalize_fill, normalize_order, normalize_position

router = APIRouter(prefix="/trades", tags=["trades"])


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
            p for p in (normalize_position(p) for p in positions)
            if p is not None
        ],
        "resting_orders": [
            o for o in (normalize_order(o) for o in resting)
            if o is not None
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
        f for f in (normalize_fill(f) for f in fills_raw[:limit])
        if f is not None
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
