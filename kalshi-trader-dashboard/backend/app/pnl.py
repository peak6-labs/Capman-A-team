"""PnL computation — pure Decimal functions, no network calls.

All money values come in as raw dicts from the Kalshi API or fills list; all
results go out as Decimal strings for JSON serialisation.

Kalshi binary markets settle 0 or 1. Prices are dollar-strings in [0, 1].
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def _dec(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value))


# ---------------------------------------------------------------------------
# Realized PnL
# ---------------------------------------------------------------------------

def realized_usd(positions: list[dict]) -> Decimal:
    """Sum server-computed realized_pnl from market_positions entries.

    Prefers the *_dollars field (already in dollars); falls back to the bare
    integer-cents field divided by 100. Logs a warning if neither is present.
    """
    total = Decimal("0")
    for p in positions:
        dollars = p.get("realized_pnl_dollars")
        if dollars is not None:
            total += _dec(dollars)
        else:
            cents = p.get("realized_pnl")
            if cents is not None:
                # Kalshi has returned this as integer cents in some payloads.
                total += _dec(cents) / 100
    return total


# ---------------------------------------------------------------------------
# Unrealized PnL (mark-to-market)
# ---------------------------------------------------------------------------

def unrealized_usd(positions: list[dict], yes_mids: dict[str, Decimal]) -> Decimal:
    """Mark open positions to market using supplied per-ticker YES midpoint prices.

    yes_mids: {ticker: Decimal mid price in [0,1]}. For any ticker not supplied
    the position's unrealized contribution is treated as 0.

    Prefers Kalshi's unrealized_pnl_dollars if present (avoids needing yes_mids
    for that position).
    """
    total = Decimal("0")
    for p in positions:
        ticker = p.get("ticker", "")
        # Prefer server-computed field.
        udollars = p.get("unrealized_pnl_dollars")
        if udollars is not None:
            total += _dec(udollars)
            continue

        signed = _dec(p.get("position_fp", 0))
        if signed == 0:
            continue
        mid = yes_mids.get(ticker)
        if mid is None:
            continue

        cost = _dec(
            p.get("total_traded_dollars")
            or p.get("position_cost_dollars")
            or p.get("market_exposure_dollars")
        )
        if signed > 0:
            # Long YES: value = count * mid
            value = signed * mid
        else:
            # Long NO (short YES): value = |count| * (1 - mid)
            value = abs(signed) * (Decimal("1") - mid)

        total += value - cost
    return total


# ---------------------------------------------------------------------------
# Cumulative realized PnL time series from fills
# ---------------------------------------------------------------------------

def cumulative_pnl_series(
    fills: list[dict],
    current_realized: Decimal,
) -> list[dict]:
    """Build a cumulative cash-flow series from fills, anchored to current_realized.

    Each fill contributes:
      buy  -> -price * count  (cash out)
      sell -> +price * count  (cash in)

    The series is sorted by ts and anchored so its final value equals the
    authoritative current_realized scalar from the Kalshi positions endpoint.
    This handles fills with missing settlement payouts (Kalshi only returns
    trading fills, not settlement credits).

    Returns list of {ts: int, cumulative_realized_usd: str}.
    """
    if not fills:
        return []

    # Sort and compute running cash-flow.
    sorted_fills = sorted(fills, key=lambda f: int(f.get("ts", 0)))
    points = []
    running = Decimal("0")
    for f in sorted_fills:
        price = _dec(f.get("price_dollars") or f.get("price", 0))
        count = _dec(f.get("count", 0))
        action = str(f.get("action", "buy")).lower()

        # Fill is a buy if action=="buy"; otherwise a sell.
        if action == "buy":
            running -= price * count
        else:
            running += price * count

        points.append({"ts": int(f.get("ts", 0)) * 1000, "cumulative_realized_usd": running})

    # Anchor: offset so the last point equals current_realized.
    if points:
        last = points[-1]["cumulative_realized_usd"]
        adjustment = current_realized - last
        for pt in points:
            pt["cumulative_realized_usd"] = pt["cumulative_realized_usd"] + adjustment

    return [
        {"ts": pt["ts"], "cumulative_realized_usd": str(pt["cumulative_realized_usd"])}
        for pt in points
    ]
