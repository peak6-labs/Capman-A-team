"""PnL computation — pure Decimal functions, no network calls.

All money values come in as raw dicts from the Kalshi API or fills list; all
results go out as Decimal strings for JSON serialisation.

Kalshi binary markets settle 0 or 1. Prices are dollar-strings in [0, 1].
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any


def _dec(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value))


def _fmt(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.0001")).normalize(), "f")


def _timestamp_ms(raw: Any) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() * 1000)
        except Exception:
            return None
    v = int(raw)
    if v > 10**16:
        return v // 1_000_000
    if v > 10**13:
        return v // 1_000
    if v > 10**10:
        return v
    return v * 1000


def _fill_ts_ms(fill: dict) -> int:
    return _timestamp_ms(fill.get("ts") or fill.get("created_time")) or 0


def _price(fill: dict) -> Decimal:
    return _dec(
        fill.get("price_dollars")
        or fill.get("price")
        or fill.get("average_fill_price_dollars")
        or fill.get("average_fill_price")
        or fill.get("fill_price_dollars")
        or fill.get("fill_price")
        or fill.get("yes_price_dollars")
        or fill.get("yes_price")
        or fill.get("no_price_dollars")
        or fill.get("no_price")
    )


def _count(fill: dict) -> Decimal:
    return _dec(
        fill.get("count")
        or fill.get("fill_count")
        or fill.get("filled_count")
        or fill.get("quantity")
        or fill.get("contracts")
    )


def _action(fill: dict) -> str:
    return str(fill.get("action") or "buy").lower()


def _side(fill: dict) -> str | None:
    side = fill.get("side")
    return str(side).lower() if side else None


def _opposite_side(side: str | None) -> str | None:
    if side == "yes":
        return "no"
    if side == "no":
        return "yes"
    return None


def _held_side(fill: dict) -> str | None:
    side = _side(fill)
    if _action(fill) == "sell":
        return _opposite_side(side)
    return side


def _ticker(fill: dict) -> str | None:
    ticker = fill.get("ticker") or fill.get("market_ticker")
    return str(ticker) if ticker else None


def _contract_price(fill: dict) -> Decimal:
    price = _price(fill)
    if _action(fill) == "sell":
        return Decimal("1") - price
    return price


def _cashflow(fill: dict) -> Decimal:
    notional = _contract_price(fill) * _count(fill)
    fee = _fee(fill)
    return -(notional + fee)


def _fee(fill: dict) -> Decimal:
    total_fee = _dec(
        fill.get("fee_dollars")
        or fill.get("fees_dollars")
        or fill.get("fee_paid_dollars")
        or fill.get("fees_paid_dollars")
        or fill.get("fee_paid")
        or fill.get("fees_paid")
    )
    if total_fee:
        return total_fee

    average_fee = _dec(
        fill.get("average_fee_paid_dollars")
        or fill.get("average_fee_paid")
    )
    return average_fee * _count(fill)


def _position_realized(position: dict) -> Decimal:
    dollars = position.get("realized_pnl_dollars")
    if dollars is not None:
        return _dec(dollars)
    cents = position.get("realized_pnl")
    if cents is not None:
        return _dec(cents) / 100
    return Decimal("0")


def _settlement_ts_ms(settlement: dict) -> int:
    return (
        _timestamp_ms(
            settlement.get("settled_time")
            or settlement.get("settlement_time")
            or settlement.get("created_time")
            or settlement.get("ts")
        )
        or 0
    )


def _settlement_revenue_usd(settlement: dict) -> Decimal:
    revenue = settlement.get("revenue")
    if revenue is not None:
        return _dec(revenue) / 100
    return _dec(
        settlement.get("revenue_dollars")
        or settlement.get("settlement_payout_dollars")
        or settlement.get("payout_dollars")
    )


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
    current_realized: Decimal | None = None,
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
    sorted_fills = sorted(fills, key=_fill_ts_ms)
    points = []
    running = Decimal("0")
    for f in sorted_fills:
        running += _cashflow(f)
        points.append({"ts": _fill_ts_ms(f), "cumulative_realized_usd": running})

    # Optional anchor for callers that have an authoritative realized scalar.
    # The PnL dashboard leaves this unset so closed losing markets still show
    # their paid premium even when they no longer appear in positions.
    if points and current_realized is not None:
        last = points[-1]["cumulative_realized_usd"]
        adjustment = current_realized - last
        for pt in points:
            pt["cumulative_realized_usd"] = pt["cumulative_realized_usd"] + adjustment

    return [
        {"ts": pt["ts"], "cumulative_realized_usd": str(pt["cumulative_realized_usd"])}
        for pt in points
    ]


def daily_pnl_series(
    fills: list[dict],
    current_realized: Decimal | None,
    start: date,
    end: date | None = None,
) -> list[dict]:
    """Build daily realized-PnL buckets from `start` through `end`.

    The daily values are derived from the same anchored cumulative series used
    by the chart so the final cumulative point matches the exchange's current
    realized PnL scalar.
    """
    end = end or date.today()
    if end < start:
        return []

    cumulative = cumulative_pnl_series(fills, current_realized)
    ordered = sorted(
        (
            (datetime.fromtimestamp(int(p["ts"]) / 1000).date(), _dec(p["cumulative_realized_usd"]))
            for p in cumulative
        ),
        key=lambda item: item[0],
    )

    idx = 0
    previous = Decimal("0")
    while idx < len(ordered) and ordered[idx][0] < start:
        previous = ordered[idx][1]
        idx += 1

    points: list[dict] = []
    day = start
    while day <= end:
        value = previous
        while idx < len(ordered) and ordered[idx][0] <= day:
            value = ordered[idx][1]
            idx += 1
        daily = value - previous
        points.append(
            {
                "date": day.isoformat(),
                "realized_usd": _fmt(daily),
                "cumulative_realized_usd": _fmt(value),
            }
        )
        previous = value
        day += timedelta(days=1)
    return points


def trade_pnl_rows(fills: list[dict], positions: list[dict]) -> list[dict]:
    """Group filled orders into per-market/side trade rows."""
    position_by_ticker = {
        str(p.get("ticker")): p
        for p in positions
        if p.get("ticker")
    }
    side_count_by_ticker: dict[str, set[str | None]] = defaultdict(set)
    groups: dict[tuple[str, str | None], list[dict]] = defaultdict(list)

    for fill in fills:
        ticker = _ticker(fill)
        if not ticker:
            continue
        side = _held_side(fill)
        side_count_by_ticker[ticker].add(side)
        groups[(ticker, side)].append(fill)

    rows = []
    for (ticker, side), group in groups.items():
        sorted_group = sorted(group, key=_fill_ts_ms)
        entries = sorted_group

        entry_count = sum((_count(f) for f in entries), Decimal("0"))
        exit_count = Decimal("0")
        entry_notional = sum((_contract_price(f) * _count(f) for f in entries), Decimal("0"))
        entry_fees = sum((_fee(f) for f in entries), Decimal("0"))
        total_cost = entry_notional + entry_fees
        exit_notional = Decimal("0")
        cashflow = sum((_cashflow(f) for f in sorted_group), Decimal("0"))

        position = position_by_ticker.get(ticker)
        position_pnl = None
        position_count = Decimal("0")
        if position is not None and len(side_count_by_ticker[ticker]) == 1:
            realized = _position_realized(position)
            unrealized = _dec(position.get("unrealized_pnl_dollars"))
            position_pnl = realized + unrealized
            position_count = abs(_dec(position.get("position_fp")))

        open_count = position_count if position_count else Decimal("0")
        opened_at = _fill_ts_ms(sorted_group[0])
        last_order_at = _fill_ts_ms(sorted_group[-1])
        closed_at = last_order_at if open_count == 0 else None
        pnl = position_pnl if position_pnl is not None else cashflow
        total_payout = total_cost + pnl
        return_pct = (pnl / total_cost * 100) if total_cost else Decimal("0")

        rows.append(
            {
                "ticker": ticker,
                "side": side,
                "held_side": side,
                "opened_at": opened_at,
                "closed_at": closed_at,
                "last_order_at": last_order_at,
                "order_count": len(sorted_group),
                "entry_price_usd": _fmt(entry_notional / entry_count) if entry_count else None,
                "exit_price_usd": _fmt(exit_notional / exit_count) if exit_count else None,
                "entry_count": _fmt(entry_count),
                "exit_count": _fmt(exit_count),
                "open_count": _fmt(open_count),
                "final_position": f"{_fmt(entry_count)} {str(side or '').capitalize()}".strip(),
                "settlement_payout_usd": None,
                "total_cost_usd": _fmt(total_cost),
                "total_payout_usd": _fmt(total_payout),
                "total_return_usd": _fmt(pnl),
                "total_return_pct": _fmt(return_pct),
                "pnl_usd": _fmt(pnl),
                "status": "open" if open_count > 0 else "closed",
                "orders": [
                    {
                        "ts": _fill_ts_ms(fill),
                        "action": _action(fill),
                        "side": _held_side(fill),
                        "count": _fmt(_count(fill)),
                        "price_usd": _fmt(_contract_price(fill)),
                        "fee_usd": _fmt(_fee(fill)),
                        "fill_id": fill.get("fill_id") or fill.get("id"),
                    }
                    for fill in sorted_group
                ],
            }
        )

    return sorted(rows, key=lambda row: int(row["last_order_at"]), reverse=True)


def settlement_history_rows(settlements: list[dict]) -> list[dict]:
    """Build Kalshi-style portfolio history rows from settlement records."""
    rows = []
    for settlement in settlements:
        ticker = _ticker(settlement)
        if not ticker:
            continue

        result = str(settlement.get("market_result") or settlement.get("result") or "").lower() or None
        settled_at = _settlement_ts_ms(settlement)
        revenue_usd = _settlement_revenue_usd(settlement)
        total_fee = _dec(settlement.get("fee_cost") or settlement.get("fee_cost_dollars"))

        specs = [
            ("yes", _dec(settlement.get("yes_count_fp") or settlement.get("yes_count")), _dec(settlement.get("yes_total_cost_dollars"))),
            ("no", _dec(settlement.get("no_count_fp") or settlement.get("no_count")), _dec(settlement.get("no_total_cost_dollars"))),
        ]
        active = [(side, count, cost) for side, count, cost in specs if count or cost]
        total_cost_before_fee = sum((cost for _, _, cost in active), Decimal("0"))

        for side, count, cost in active:
            if total_cost_before_fee and total_fee:
                fee = total_fee * (cost / total_cost_before_fee)
            else:
                fee = Decimal("0")
            total_cost = cost + fee

            if result == "void":
                settlement_payout = total_cost
            elif result == side:
                settlement_payout = count
            else:
                settlement_payout = Decimal("0")

            # Prefer Kalshi's revenue field when there is exactly one active side.
            if len(active) == 1 and revenue_usd:
                settlement_payout = revenue_usd

            total_return = settlement_payout - total_cost
            return_pct = (total_return / total_cost * 100) if total_cost else Decimal("0")
            rows.append(
                {
                    "ticker": ticker,
                    "side": side,
                    "held_side": side,
                    "title": None,
                    "name": None,
                    "group_title": ticker,
                    "settlement_result": result,
                    "opened_at": settled_at,
                    "closed_at": settled_at,
                    "last_order_at": settled_at,
                    "order_count": 0,
                    "entry_price_usd": _fmt(cost / count) if count else None,
                    "exit_price_usd": None,
                    "entry_count": _fmt(count),
                    "exit_count": "0",
                    "open_count": "0",
                    "final_position": f"{_fmt(count)} {side.capitalize()}",
                    "settlement_payout_usd": _fmt(settlement_payout),
                    "total_cost_usd": _fmt(total_cost),
                    "total_payout_usd": _fmt(settlement_payout),
                    "total_return_usd": _fmt(total_return),
                    "total_return_pct": _fmt(return_pct),
                    "pnl_usd": _fmt(total_return),
                    "status": "settled",
                    "orders": [],
                }
            )

    return sorted(rows, key=lambda row: int(row["last_order_at"]), reverse=True)


def daily_settlement_pnl_series(
    settlements: list[dict],
    start: date,
    end: date | None = None,
) -> list[dict]:
    """Build day-over-day PnL from settled portfolio history."""
    end = end or date.today()
    if end < start:
        return []

    daily: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in settlement_history_rows(settlements):
        ts = int(row["last_order_at"] or 0)
        if not ts:
            continue
        day = datetime.fromtimestamp(ts / 1000).date()
        if start <= day <= end:
            daily[day] += _dec(row["total_return_usd"])

    points: list[dict] = []
    running = Decimal("0")
    day = start
    while day <= end:
        realized = daily[day]
        running += realized
        points.append(
            {
                "date": day.isoformat(),
                "realized_usd": _fmt(realized),
                "cumulative_realized_usd": _fmt(running),
            }
        )
        day += timedelta(days=1)
    return points
