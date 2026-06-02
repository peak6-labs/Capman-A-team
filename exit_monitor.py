"""
Monitor open positions. Buy back to close when an exit trigger fires.

Exit triggers (in priority order):
  TARGET_HIT   — current bid ≤ target_price (captured ~85% of the move)
  NEAR_EXPIRY  — < 2h to resolution (don't hold through the coin-flip)
  STALE_THESIS — 24h held with < 2% price change (thesis isn't playing out)
  RESOLVED     — market no longer active (already settled)
"""
import argparse
import json
import time
import uuid
import warnings
from datetime import datetime, timezone

warnings.filterwarnings("ignore", message="Unverified HTTPS")

from client import markets, portfolio
from config import LIVE_TRADING

POSITIONS_FILE = "positions.json"
ORDERS_FILE = "orders.json"
POLL_INTERVAL = 60   # seconds between sweeps
NEAR_EXPIRY_H = 2.0
STALE_HOURS = 24
STALE_MOVE_PCT = 0.02


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _hours_until(dt_str):
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return (dt - datetime.now(timezone.utc)).total_seconds() / 3600
    except Exception:
        return None


def fetch_market(ticker):
    """Return raw market dict or None if not found / already resolved."""
    try:
        resp = markets.get_market_without_preload_content(ticker)
        data = json.loads(resp.data)
        return data.get("market")
    except Exception:
        return None


def buy_to_close(ticker, side, count, ask_price):
    """Place a limit buy at ask_price to close a short position."""
    try:
        resp = portfolio.create_order(
            ticker=ticker,
            action="buy",
            side=side,
            count=count,
            type="limit",
            client_order_id=str(uuid.uuid4()),
            **{f"{side}_price": round(ask_price * 100)},
        )
        return resp.order
    except Exception as e:
        print(f"  [CLOSE ERROR] {ticker}: {e}")
        return None


def check_position(pos):
    """Return (trigger_name, ask_price) or (None, None)."""
    market = fetch_market(pos["ticker"])

    if market is None or market.get("status") not in ("active",):
        return "RESOLVED", None

    side = pos["side"]
    current_bid = _to_float(market.get(f"{side}_bid_dollars"))
    current_ask = _to_float(market.get(f"{side}_ask_dollars"))

    if current_bid is None:
        return "RESOLVED", None

    # Trigger 1: target hit — price collapsed to our target
    if current_bid <= pos["target_price"]:
        return "TARGET_HIT", current_ask

    # Trigger 2: near expiry — stop holding through resolution
    if pos.get("expiry"):
        hours_left = _hours_until(pos["expiry"])
        if hours_left is not None and hours_left < NEAR_EXPIRY_H:
            return "NEAR_EXPIRY", current_ask

    # Trigger 3: stale thesis
    entry_time = datetime.fromisoformat(pos["entry_time"])
    hours_held = (datetime.now(timezone.utc) - entry_time).total_seconds() / 3600
    if hours_held > STALE_HOURS:
        move = abs(current_bid - pos["entry_price"]) / pos["entry_price"]
        if move < STALE_MOVE_PCT:
            return "STALE_THESIS", current_ask

    return None, None


def load_orders():
    try:
        with open(ORDERS_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_orders(orders):
    with open(ORDERS_FILE, "w") as f:
        json.dump(orders, f, indent=2)


def has_pending_close_order(pos, orders):
    return any(
        o["ticker"] == pos["ticker"]
        and o["side"] == pos["side"]
        and o.get("action") == "buy"
        and o.get("status") in ("resting", "pending")
        for o in orders
    )


def close_order_record(order, pos, price, trigger):
    return {
        "ticker": pos["ticker"],
        "side": pos["side"],
        "action": "buy",
        "price": price,
        "count": pos["count"],
        "order_id": order.order_id,
        "status": order.status,
        "trigger": trigger,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def run_once(live=False):
    try:
        with open(POSITIONS_FILE) as f:
            positions = json.load(f)
    except FileNotFoundError:
        return

    if not positions:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{ts}] no open positions")
        return

    orders = load_orders()
    remaining = []
    for pos in positions:
        if has_pending_close_order(pos, orders):
            print(f"  [PENDING_CLOSE] {pos['side']} {pos['ticker']}")
            remaining.append(pos)
            continue

        trigger, ask = check_position(pos)
        if trigger:
            if not live or trigger == "RESOLVED":
                print(f"  [{trigger}] {pos['side']} {pos['ticker']}")
                if trigger != "RESOLVED":
                    remaining.append(pos)
            else:
                close_ask = ask or (pos["entry_price"] * 0.5)
                order = buy_to_close(pos["ticker"], pos["side"], pos["count"], close_ask)
                status = order.status if order else None
                print(f"  [{trigger}] close order {pos['side']} {pos['ticker']} → {status}")
                if order:
                    orders.append(close_order_record(order, pos, close_ask, trigger))
                if status != "executed":
                    remaining.append(pos)   # keep tracking until the close fills
        else:
            remaining.append(pos)

    with open(POSITIONS_FILE, "w") as f:
        json.dump(remaining, f, indent=2)
    save_orders(orders)

    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {len(remaining)}/{len(positions)} positions still open")


def run_loop(live=False):
    print(f"Exit monitor started — polling every {POLL_INTERVAL}s")
    while True:
        run_once(live=live)
        time.sleep(POLL_INTERVAL)


def parse_args():
    parser = argparse.ArgumentParser(description="Monitor open positions.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Place real close orders. Without this flag the monitor only reports triggers.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_loop(live=args.live and LIVE_TRADING)
