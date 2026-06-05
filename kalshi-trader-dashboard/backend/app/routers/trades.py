"""Trades router: current (resting + open positions) and history (fills + journal decisions)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from kalshi_agent_trader.journal import Journal
from kalshi_agent_trader.portfolio import Portfolio

from ..deps import get_client
from ..market_meta import market_meta, resolve_name
from ..serializers import normalize_fill, normalize_order, normalize_position, normalize_timestamp

router = APIRouter(prefix="/trades", tags=["trades"])


def _fill_key(fill: dict) -> str:
    return str(
        fill.get("fill_id")
        or fill.get("trade_id")
        or fill.get("id")
        or (
            fill.get("ticker"),
            fill.get("market_ticker"),
            fill.get("ts") or fill.get("created_time"),
            fill.get("count") or fill.get("count_fp"),
            fill.get("yes_price_dollars") or fill.get("price_dollars") or fill.get("price"),
        )
    )


@router.get("/current")
def current_trades():
    client = get_client()
    try:
        pf = Portfolio(client)
        positions = pf.market_positions()
        resting = pf.resting_orders()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    meta = market_meta(
        client, [p.get("ticker") for p in positions] + [o.get("ticker") for o in resting]
    )

    return {
        "open_positions": [
            p for p in (normalize_position(p, meta.get(p.get("ticker"))) for p in positions)
            if p is not None
        ],
        "resting_orders": [
            o for o in (normalize_order(o, meta.get(o.get("ticker"))) for o in resting)
            if o is not None
        ],
    }


@router.get("/history")
def trade_history(limit: int = Query(default=200, ge=1, le=1000)):
    # Kalshi fills (authoritative): live endpoint plus historical endpoint past cutoff.
    client = get_client()
    try:
        pf = Portfolio(client)
        fills_raw = pf.fills()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Kalshi fills fetch failed: {exc}")
    try:
        fills_raw += pf.historical_fills()
    except Exception:
        # Older SDKs/accounts can fail this endpoint; keep recent fills and decisions visible.
        pass

    fills_by_key = {_fill_key(fill): fill for fill in fills_raw}
    fills_window = sorted(
        fills_by_key.values(),
        key=lambda fill: normalize_timestamp(fill.get("ts") or fill.get("created_time")) or 0,
        reverse=True,
    )[:limit]
    meta = market_meta(
        client, [f.get("ticker") or f.get("market_ticker") for f in fills_window]
    )
    fills = [
        f for f in (
            normalize_fill(f, meta.get(f.get("ticker") or f.get("market_ticker")))
            for f in fills_window
        )
        if f is not None
    ]

    # Journal decisions (incl. dry_run + rejected — the full audit trail).
    try:
        with Journal() as journal:
            decisions_raw = journal.recent_decisions(limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Journal read failed: {exc}")

    decision_tickers = [d["market_ticker"] for d in decisions_raw if d["market_ticker"]]
    decision_meta = market_meta(client, decision_tickers)

    decisions = []
    for d in decisions_raw:
        ticker = d["market_ticker"]
        meta_for_decision = decision_meta.get(ticker)
        decisions.append(
            {
                "ts": d["ts"],
                "source": d["source"],
                "market_ticker": ticker,
                "side": d["side"],
                "target_price": d["target_price"],
                "fair_prob": d["fair_prob"],
                "confidence": d["confidence"],
                "max_contracts": d["max_contracts"],
                "outcome": d["outcome"],
                "gate": d["gate"],
                "reason": d["reason"],
                "title": meta_for_decision.title if meta_for_decision else None,
                "name": resolve_name(meta_for_decision, str(d["side"]).lower() if d["side"] else None),
            }
        )

    return {"fills": fills, "decisions": decisions}
