"""Simple Kalshi (demo) strategy: sell the cheap tail.

Find open markets where one side trades below a probability threshold (default
5%) *and* has a real bid we can sell into, then sell that side to collect the
small premium.

Note on the edge: selling a sub-5% side immediately costs ~(1 - price) in
capital and pays back $1 only if it resolves your way -- it's tail-risk selling,
a high hit rate of small wins paid for by rare large losses. Size accordingly.
"""

import json
import uuid

from client import markets, portfolio

PROBABILITY_THRESHOLD = 0.05


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def scan_low_probability_markets(threshold=PROBABILITY_THRESHOLD, max_pages=20):
    """Return open markets where one side is cheap enough to sell.

    A side qualifies only if it has a *positive* bid strictly below `threshold`
    (price in dollars, 0-1). The positive-bid requirement matters: most demo
    markets have empty books (yes 0.00 / no 1.00), which look like extreme
    probabilities but cannot actually be sold into.

    Prices come from the demo API's `*_dollars` fields, which the SDK's typed
    Market model drops -- so we read the raw JSON through the SDK's
    authenticated transport.

    Each result: {"ticker", "side", "price", "title"} where `side` is the cheap
    side to sell and `price` is the bid (dollars) we would receive per contract.
    """
    results = []
    cursor = None
    for _ in range(max_pages):
        response = markets.get_markets_without_preload_content(
            limit=1000, status="open", cursor=cursor
        )
        data = json.loads(response.data)
        for market in data.get("markets", []):
            yes_bid = _to_float(market.get("yes_bid_dollars"))
            no_bid = _to_float(market.get("no_bid_dollars"))
            yes_ok = yes_bid is not None and 0 < yes_bid < threshold
            no_ok = no_bid is not None and 0 < no_bid < threshold
            # If both qualify (very wide book), sell whichever is cheaper.
            if yes_ok and (not no_ok or yes_bid <= no_bid):
                side, price = "yes", yes_bid
            elif no_ok:
                side, price = "no", no_bid
            else:
                continue
            results.append(
                {
                    "ticker": market["ticker"],
                    "side": side,
                    "price": price,
                    "title": market.get("title"),
                }
            )
        cursor = data.get("cursor")
        if not cursor:
            break
    return results


def sell_side(ticker, side, count, limit_price, dry_run=True):
    """Place a limit order to SELL `count` contracts of `side` at `limit_price`.

    `limit_price` is in dollars (0-1); the SDK takes the price in integer cents.
    Priced at the current bid so the order crosses and fills. With dry_run=True
    (default) it only prints the intended order and places nothing.
    """
    if dry_run:
        print(f"[DRY RUN] would place: sell {count} {side} {ticker} @ ${limit_price:.2f}")
        return None

    order_kwargs = {
        "ticker": ticker,
        "action": "sell",
        "side": side,
        "count": count,
        "type": "limit",
        "client_order_id": str(uuid.uuid4()),
        f"{side}_price": round(limit_price * 100),
    }
    response = portfolio.create_order(**order_kwargs)
    print(f"placed: sell {count} {side} {ticker} @ ${limit_price:.2f} -> {response.order.status}")
    return response


def run(threshold=PROBABILITY_THRESHOLD, count=1, max_orders=10, dry_run=True):
    """Scan for cheap sides and sell them. dry_run=True by default."""
    candidates = scan_low_probability_markets(threshold)
    print(f"Found {len(candidates)} sellable markets under {threshold:.0%}.")
    for candidate in candidates[:max_orders]:
        sell_side(candidate["ticker"], candidate["side"], count, candidate["price"], dry_run=dry_run)
    return candidates


if __name__ == "__main__":
    run()
