"""Order execution — the single write path, behind compliance + risk gates.

`Executor.submit()` is the only way an order reaches Kalshi. It runs, in fixed order:
    1. compliance gate (PEAK6, non-overridable)
    2. risk gate (caps, no-leverage, kill switch; may clamp size)
    3. build the V2 order body and either dry-run (log only) or POST it
Every outcome is written to the journal.

Order API: V2 `POST /portfolio/events/orders` (V1 is deprecated ~May 2026).
  - book_side: "bid" = yes, "ask" = no
  - prices are FixedPointDollars strings ("0.5600"); counts are FixedPointCount ("10.00")
  - client_order_id provides idempotency
NOTE: the exact V2 body field names are confirmed with the first live place-and-cancel;
they are isolated in `build_v2_order_body` so any correction is a one-line change.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Dict, Optional

from .client import KalshiClient
from .compliance import ComplianceGate
from .journal import Journal
from .risk import AccountState, ProposedOrder, RiskGate

V2_ORDERS_PATH = "/portfolio/events/orders"


@dataclass
class SubmitResult:
    status: str                      # "placed" | "dry_run" | "rejected"
    gate: Optional[str] = None       # which gate rejected: "compliance" | "risk"
    reason: str = ""
    client_order_id: Optional[str] = None
    order_body: Optional[Dict[str, Any]] = None
    response: Optional[Dict[str, Any]] = None
    approved_count: int = 0


def build_v2_order_body(order: ProposedOrder, client_order_id: str) -> Dict[str, Any]:
    """Construct the V2 create-order body. book_side: bid=yes, ask=no."""
    book_side = "bid" if order.side.lower() == "yes" else "ask"
    return {
        "ticker": order.ticker,
        "client_order_id": client_order_id,
        "book_side": book_side,
        "type": "limit",
        "price_dollars": f"{order.price:.4f}",   # FixedPointDollars
        "count": f"{Decimal(order.count):.2f}",  # FixedPointCount
    }


class Executor:
    def __init__(
        self,
        client: KalshiClient,
        compliance: ComplianceGate,
        risk: RiskGate,
        journal: Journal,
        *,
        dry_run: bool = True,
    ) -> None:
        self.client = client
        self.compliance = compliance
        self.risk = risk
        self.journal = journal
        self.dry_run = dry_run

    def submit(
        self,
        order: ProposedOrder,
        *,
        category: Optional[str],
        title: str,
        account: AccountState,
        source: Optional[str] = None,
    ) -> SubmitResult:
        # 1. Compliance (hard PEAK6 gate).
        comp = self.compliance.evaluate(category=category, title=title)
        if not comp.allowed:
            self._log(order, "rejected", gate="compliance", reason=comp.reason, source=source)
            return SubmitResult("rejected", gate="compliance", reason=comp.reason)

        # 2. Risk (caps / kill switch / no-leverage; may clamp size).
        risk = self.risk.check(order, account)
        if not risk.allowed:
            self._log(order, "rejected", gate="risk", reason=risk.reason, source=source)
            return SubmitResult("rejected", gate="risk", reason=risk.reason)

        order = replace(order, count=risk.approved_count)

        # 3. Build + place (or dry-run).
        client_order_id = str(uuid.uuid4())
        body = build_v2_order_body(order, client_order_id)

        if self.dry_run:
            self._log(order, "dry_run", reason=risk.reason, source=source)
            return SubmitResult(
                "dry_run", reason=risk.reason, client_order_id=client_order_id,
                order_body=body, approved_count=order.count,
            )

        response = self.client.post(V2_ORDERS_PATH, json=body)
        self.journal.record_order(
            {
                "client_order_id": client_order_id,
                "kalshi_order_id": (response.get("order") or {}).get("order_id"),
                "market_ticker": order.ticker,
                "side": order.side,
                "action": "buy",
                "order_type": "limit",
                "count": order.count,
                "price": str(order.price),
                "status": "placed",
                "raw": response,
            }
        )
        self._log(order, "placed", reason=risk.reason, source=source)
        return SubmitResult(
            "placed", reason=risk.reason, client_order_id=client_order_id,
            order_body=body, response=response, approved_count=order.count,
        )

    def cancel(self, order_id: str) -> Dict[str, Any]:
        """Cancel a resting V2 order. Returns {order_id, client_order_id, reduced_by}."""
        return self.client.delete(f"{V2_ORDERS_PATH}/{order_id}")

    # ------------------------------------------------------------------ #
    def _log(self, order: ProposedOrder, outcome: str, *, gate=None, reason="", source=None) -> None:
        self.journal.record_decision(
            outcome=outcome,
            source=source,
            market_ticker=order.ticker,
            side=order.side,
            target_price=order.price,
            fair_prob=order.fair_prob,
            confidence=order.confidence,
            max_contracts=order.count,
            gate=gate,
            reason=reason,
        )
