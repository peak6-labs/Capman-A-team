"""Portfolio access: balance, positions, fills, resting orders (all authenticated).

Verified response shapes (June 2026):
  /portfolio/balance   -> {"balance": <cents>, "balance_dollars": "..", "portfolio_value": <cents>, ...}
  /portfolio/positions -> {"market_positions": [...], "event_positions": [...], "cursor": ".."}
  /portfolio/fills     -> {"fills": [...], "cursor": ".."}
  /portfolio/orders    -> {"orders": [...], "cursor": ".."}  (resting/open orders)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from .client import KalshiClient
from .models import Balance
from .risk import AccountState


def _dec(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value))


class Portfolio:
    def __init__(self, client: KalshiClient) -> None:
        self.client = client

    def balance(self) -> Balance:
        return Balance.model_validate(self.client.get("/portfolio/balance", auth=True))

    def _paginate(self, path: str, key: str, params: Optional[Dict[str, Any]] = None) -> List[dict]:
        out: List[dict] = []
        cursor: Optional[str] = None
        params = dict(params or {})
        while True:
            if cursor:
                params["cursor"] = cursor
            data = self.client.get(path, params=params, auth=True)
            out.extend(data.get(key, []))
            cursor = data.get("cursor") or None
            if not cursor:
                return out

    def market_positions(self) -> List[dict]:
        return self._paginate("/portfolio/positions", "market_positions")

    def fills(self) -> List[dict]:
        return self._paginate("/portfolio/fills", "fills")

    def resting_orders(self) -> List[dict]:
        """Open/resting orders."""
        return self._paginate("/portfolio/orders", "orders", params={"status": "resting"})

    def account_state(self, ticker: str) -> AccountState:
        """Build the risk-gate AccountState for a prospective order in `ticker`.

        Exposure is approximated from open positions' cost plus resting-order notional.
        Daily realized P&L is left at 0 until settlement tracking lands (Phase 6);
        the daily-loss cap is therefore inactive until then.
        """
        bal = self.balance()
        positions = self.market_positions()
        resting = self.resting_orders()

        def pos_cost(p: dict) -> Decimal:
            return abs(_dec(p.get("position_cost_dollars") or p.get("market_exposure_dollars")))

        def rest_notional(o: dict) -> Decimal:
            return _dec(o.get("price_dollars")) * _dec(o.get("count"))

        total = sum((pos_cost(p) for p in positions), Decimal("0")) + sum(
            (rest_notional(o) for o in resting), Decimal("0")
        )
        this = sum(
            (pos_cost(p) for p in positions if p.get("ticker") == ticker), Decimal("0")
        ) + sum(
            (rest_notional(o) for o in resting if o.get("ticker") == ticker), Decimal("0")
        )
        return AccountState(
            balance_usd=bal.usd() or Decimal("0"),
            total_exposure_usd=total,
            position_exposure_usd=this,
            realized_daily_pnl_usd=Decimal("0"),
        )
