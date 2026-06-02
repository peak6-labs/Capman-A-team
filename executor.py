"""
Execute orders from thesis.json. Track open positions in positions.json.

Skips any ticker/side already open to avoid doubling up.
Logs every placed order; only filled orders become open positions.
"""
import argparse
import json
import uuid
import warnings
from datetime import datetime, timezone

warnings.filterwarnings("ignore", message="Unverified HTTPS")

from client import portfolio
from config import LIVE_TRADING

THESIS_FILE = "thesis.json"
POSITIONS_FILE = "positions.json"
ORDERS_FILE = "orders.json"

# Target: exit when the market price falls to this fraction of our entry price.
# Captures ~85% of the gap toward $0.
TARGET_FRACTION = 0.15


def load_positions():
    try:
        with open(POSITIONS_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_positions(positions):
    with open(POSITIONS_FILE, "w") as f:
        json.dump(positions, f, indent=2)


def load_orders():
    try:
        with open(ORDERS_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_orders(orders):
    with open(ORDERS_FILE, "w") as f:
        json.dump(orders, f, indent=2)


def is_open(ticker, side, positions):
    return any(p["ticker"] == ticker and p["side"] == side for p in positions)


def has_pending_order(ticker, side, orders):
    return any(
        o["ticker"] == ticker
        and o["side"] == side
        and o.get("status") in ("resting", "pending")
        for o in orders
    )


def place_sell(ticker, side, count, price):
    resp = portfolio.create_order(
        ticker=ticker,
        action="sell",
        side=side,
        count=count,
        type="limit",
        client_order_id=str(uuid.uuid4()),
        **{f"{side}_price": round(price * 100)},
    )
    return resp.order


def order_record(order, thesis):
    return {
        "ticker": thesis["ticker"],
        "title": thesis.get("title"),
        "side": thesis["side"],
        "action": "sell",
        "price": thesis["price"],
        "count": thesis["count"],
        "order_id": order.order_id,
        "status": order.status,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def position_record(order, thesis):
    return {
        "ticker": thesis["ticker"],
        "title": thesis.get("title"),
        "side": thesis["side"],
        "action": "sell",
        "entry_price": thesis["price"],
        "target_price": round(thesis["price"] * TARGET_FRACTION, 4),
        "count": thesis["count"],
        "order_id": order.order_id,
        "order_status": order.status,
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "expiry": thesis.get("expiry"),
        "confidence": thesis.get("confidence"),
    }


def execute(live=False):
    try:
        with open(THESIS_FILE) as f:
            theses = json.load(f)
    except FileNotFoundError:
        print(f"{THESIS_FILE} not found — run brain.py first")
        return

    positions = load_positions()
    orders = load_orders()
    new_positions = []
    new_orders = []

    for thesis in theses:
        ticker, side = thesis["ticker"], thesis["side"]

        if is_open(ticker, side, positions):
            print(f"[SKIP] already open: {side} {ticker}")
            continue

        if has_pending_order(ticker, side, orders):
            print(f"[SKIP] pending order exists: {side} {ticker}")
            continue

        if not live:
            print(
                f"[DRY RUN] sell {thesis['count']} {side} {ticker}"
                f" @ ${thesis['price']:.2f}  conf={thesis['confidence']:.2f}"
                f"  kelly={thesis['kelly_fraction']:.3f}"
            )
            continue

        try:
            order = place_sell(ticker, side, thesis["count"], thesis["price"])
            print(f"placed: sell {thesis['count']} {side} {ticker} @ ${thesis['price']:.2f} → {order.status}")

            if order.status == "executed":
                new_positions.append(position_record(order, thesis))
            else:
                new_orders.append(order_record(order, thesis))
        except Exception as e:
            print(f"[ERROR] {ticker}: {e}")

    positions.extend(new_positions)
    orders.extend(new_orders)
    save_positions(positions)
    save_orders(orders)
    print(f"positions.json updated — {len(positions)} open")
    print(f"orders.json updated — {len(orders)} pending/non-filled")


def parse_args():
    parser = argparse.ArgumentParser(description="Execute orders from thesis.json.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Place real orders. Without this flag the executor only prints a dry run.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    execute(live=args.live and LIVE_TRADING)
