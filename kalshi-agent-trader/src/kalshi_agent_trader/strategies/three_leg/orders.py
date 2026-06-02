"""Translate a sized three-leg plan into orders for the execution pipeline (pure).

Every leg is a YES buy; we submit a limit at the ask we sized against (fills at
that price or better). Only legs that Kelly sized to >=1 contract are emitted.
"""

from __future__ import annotations

from typing import List

from ...risk import ProposedOrder
from .compute import Leg, ThreeLegPlan


def _all_legs(plan: ThreeLegPlan) -> List[Leg]:
    legs = [plan.match_leg]
    if plan.title_leg:
        legs.append(plan.title_leg)
    legs.extend(plan.long_legs)
    return legs


def proposed_orders(plan: ThreeLegPlan) -> List[ProposedOrder]:
    """One buy-YES ProposedOrder per leg Kelly sized to at least one contract."""
    orders: List[ProposedOrder] = []
    for leg in _all_legs(plan):
        if leg.contracts >= 1:
            orders.append(ProposedOrder(
                ticker=leg.ticker, side="yes", price=leg.price, count=leg.contracts,
                fair_prob=float(leg.fair), confidence=1.0, action="buy",
            ))
    return orders
