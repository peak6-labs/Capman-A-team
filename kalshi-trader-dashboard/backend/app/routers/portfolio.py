"""Portfolio router: balance, open positions, resting orders."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from kalshi_agent_trader.portfolio import Portfolio

from ..deps import get_client
from ..market_meta import market_meta
from ..serializers import fmt, normalize_order, normalize_position

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("")
def get_portfolio():
    client = get_client()
    try:
        pf = Portfolio(client)
        bal = pf.balance()
        positions = pf.market_positions()
        resting = pf.resting_orders()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    meta = market_meta(
        client, [p.get("ticker") for p in positions] + [o.get("ticker") for o in resting]
    )

    open_positions = [
        p for p in (normalize_position(p, meta.get(p.get("ticker"))) for p in positions)
        if p is not None
    ]
    resting_orders = [
        o for o in (normalize_order(o, meta.get(o.get("ticker"))) for o in resting)
        if o is not None
    ]

    return {
        "cash_balance_usd": fmt(bal.usd()),
        "portfolio_value_usd": fmt(bal.portfolio_value_usd()),
        "open_positions": open_positions,
        "resting_orders": resting_orders,
    }
