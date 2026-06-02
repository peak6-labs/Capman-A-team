"""Kalshi trading-fee model (shared by all strategies).

Lives in the shared tennis domain so neither strategy depends on the other's
modules for fee math. Previously defined in two_market's breakeven.py.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

# Kalshi's standard trading fee coefficient (quadratic in price).
DEFAULT_FEE_RATE = Decimal("0.07")


def trading_fee(contracts, price, rate: Decimal = DEFAULT_FEE_RATE) -> Decimal:
    """Kalshi trading fee for a fill: ceil_to_cent(rate * C * P * (1 - P)).

    Charged on entry (and again on any early exit). Zero for degenerate prices.
    """
    c = Decimal(str(contracts))
    p = Decimal(str(price))
    if c <= 0 or p <= 0 or p >= 1:
        return Decimal("0")
    raw = rate * c * p * (Decimal("1") - p)
    return (raw * 100).to_integral_value(rounding=ROUND_CEILING) / 100
