"""Risk gate — configurable caps enforced AFTER compliance, BEFORE execution.

This is deterministic and authoritative: agents cannot override it. It enforces the
`config.yaml` risk section plus two invariants that hold regardless of config:
  - a kill switch (sentinel file) halts all new entries;
  - exposure can never exceed the actual account balance (no leverage).

The gate both vets a proposed order and clamps its size down to fit the caps; if no
positive size fits, the order is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Optional

from .config import RiskConfig
from .journal import PROJECT_ROOT

KILL_SWITCH_PATH = PROJECT_ROOT / "data" / "KILL"


@dataclass(frozen=True)
class ProposedOrder:
    ticker: str
    side: str               # "yes" | "no"
    price: Decimal          # entry price in dollars per contract, 0..1
    count: int              # desired contracts
    fair_prob: float        # agent/strategy fair probability for `side`, 0..1
    confidence: float       # 0..1
    action: str = "buy"     # "buy" | "sell"
    post_only: bool = False  # True = maker-only: rest, never cross (Kalshi cancels if it would take)


@dataclass(frozen=True)
class AccountState:
    balance_usd: Decimal
    total_exposure_usd: Decimal        # current open exposure across all positions
    position_exposure_usd: Decimal     # current exposure in THIS market
    realized_daily_pnl_usd: Decimal    # today's realized P&L (negative = loss)


@dataclass(frozen=True)
class RiskResult:
    allowed: bool
    reason: str
    approved_count: int = 0


class RiskGate:
    def __init__(self, config: RiskConfig) -> None:
        self.config = config

    def is_killed(self) -> bool:
        return KILL_SWITCH_PATH.exists()

    def check(self, order: ProposedOrder, state: AccountState) -> RiskResult:
        c = self.config

        if self.is_killed():
            return RiskResult(False, "kill switch engaged")

        # Signal-quality gates.
        if order.confidence < c.min_confidence:
            return RiskResult(False, f"confidence {order.confidence} < min {c.min_confidence}")
        if order.action not in ("buy", "sell"):
            return RiskResult(False, f"unsupported action: {order.action}")

        price = float(order.price)
        edge = (order.fair_prob - price) if order.action == "buy" else (price - order.fair_prob)
        if edge < c.min_edge:
            return RiskResult(False, f"edge {edge:.3f} < min {c.min_edge}")

        # Daily loss cap halts NEW entries when breached.
        if c.daily_loss_cap_usd > 0 and (-state.realized_daily_pnl_usd) >= c.daily_loss_cap_usd:
            return RiskResult(False, "daily loss cap reached")

        if order.price <= 0:
            return RiskResult(False, "non-positive price")

        # No leverage: total exposure can never exceed the live balance.
        balance_room = state.balance_usd - state.total_exposure_usd
        total_cap_room = c.max_total_exposure_usd - state.total_exposure_usd
        position_room = c.max_per_position_usd - state.position_exposure_usd

        exposure_per_contract = (
            order.price if order.action == "buy" else Decimal("1") - order.price
        )
        if exposure_per_contract <= 0:
            return RiskResult(False, "non-positive exposure")

        # Convert each dollar room to a max contract count at max loss per contract.
        def contracts_for(room_usd: Decimal) -> int:
            if room_usd <= 0:
                return 0
            return int(room_usd / exposure_per_contract)

        caps = [
            ("max_contracts_per_order", c.max_contracts_per_order),
            ("balance (no leverage)", contracts_for(balance_room)),
            ("max_total_exposure_usd", contracts_for(total_cap_room)),
            ("max_per_position_usd", contracts_for(position_room)),
        ]
        binding_name, max_count = min(caps, key=lambda kv: kv[1])
        approved = min(order.count, max_count)

        if approved <= 0:
            return RiskResult(False, f"no size fits cap: {binding_name}")

        if approved < order.count:
            return RiskResult(True, f"size clamped by {binding_name}", approved_count=approved)
        return RiskResult(True, "ok", approved_count=approved)
