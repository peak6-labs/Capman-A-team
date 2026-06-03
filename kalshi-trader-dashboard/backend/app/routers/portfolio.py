"""Portfolio router: balance, open positions, resting orders."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from kalshi_agent_trader.portfolio import Portfolio

from ..deps import get_client

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


def _fmt(value) -> str | None:
    return str(value) if value is not None else None


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

    open_positions = [
        {
            "ticker": p.get("ticker"),
            "position": _fmt(p.get("position_fp")),
            "exposure_usd": _fmt(p.get("market_exposure_dollars")),
            "cost_usd": _fmt(
                p.get("total_traded_dollars") or p.get("position_cost_dollars")
            ),
            "realized_pnl_usd": _fmt(p.get("realized_pnl_dollars") or p.get("realized_pnl")),
            "unrealized_pnl_usd": _fmt(p.get("unrealized_pnl_dollars")),
            "raw": p,
        }
        for p in positions
    ]

    resting_orders = [
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
    ]

    return {
        "cash_balance_usd": _fmt(bal.usd()),
        "portfolio_value_usd": _fmt(bal.portfolio_value_usd()),
        "open_positions": open_positions,
        "resting_orders": resting_orders,
    }
