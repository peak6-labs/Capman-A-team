"""
Scan Kalshi markets for cheap tails worth selling.

Scoring factors:
  1. Price: yes/no bid between $0.01 and $0.10 (cheap tail range)
  2. Liquidity: total volume >= MIN_VOLUME_FP and spread < MAX_SPREAD
  3. Time: 4–48 hours until expected expiry (sweet spot)

Low-volume filter is critical — slippage kills retail edge on thin books.
Writes survivors to queue.json, sorted by score descending.
"""
import json
import warnings
from datetime import datetime, timezone

warnings.filterwarnings("ignore", message="Unverified HTTPS")

from client import markets

MIN_PRICE = 0.01
MAX_PRICE = 0.10
MIN_HOURS = 4
MAX_HOURS = 48
MAX_SPREAD = 0.50
MIN_VOLUME_FP = 10.0   # minimum lifetime contracts traded; raise as you calibrate
QUEUE_FILE = "queue.json"


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


def score_market(market):
    yes_bid = _to_float(market.get("yes_bid_dollars"))
    no_bid = _to_float(market.get("no_bid_dollars"))
    yes_ask = _to_float(market.get("yes_ask_dollars"))
    no_ask = _to_float(market.get("no_ask_dollars"))
    volume_24h = _to_float(market.get("volume_24h_fp")) or 0.0

    volume_fp = _to_float(market.get("volume_fp")) or 0.0
    if volume_fp < MIN_VOLUME_FP:
        return None   # too thin — slippage kills the edge

    expiry = market.get("expected_expiration_time") or market.get("close_time")
    hours = _hours_until(expiry) if expiry else None

    if hours is None or not (MIN_HOURS <= hours <= MAX_HOURS):
        return None

    candidates = []
    for side, bid, ask in [("yes", yes_bid, yes_ask), ("no", no_bid, no_ask)]:
        if bid is None or not (MIN_PRICE <= bid <= MAX_PRICE):
            continue
        spread = (ask - bid) if ask is not None else None
        if spread is None or spread >= MAX_SPREAD:
            continue
        candidates.append({
            "ticker": market["ticker"],
            "title": market.get("title"),
            "side": side,
            "price": bid,
            "spread": round(spread, 4),
            "hours": round(hours, 1),
            "volume_24h": volume_24h,
            "expiry": expiry,
            # Score: lower price and more time = more premium value
            "score": round(bid * hours, 3),
        })

    if not candidates:
        return None
    return max(candidates, key=lambda c: c["score"])


def scan(max_pages=20):
    queue = []
    cursor = None
    scanned = 0

    for _ in range(max_pages):
        resp = markets.get_markets_without_preload_content(
            limit=1000, status="open", cursor=cursor
        )
        data = json.loads(resp.data)
        batch = data.get("markets", [])
        scanned += len(batch)

        for m in batch:
            entry = score_market(m)
            if entry:
                queue.append(entry)

        cursor = data.get("cursor")
        if not cursor:
            break

    queue.sort(key=lambda x: x["score"], reverse=True)

    with open(QUEUE_FILE, "w") as f:
        json.dump(queue, f, indent=2)

    print(f"Scanned {scanned} markets → {len(queue)} in queue → {QUEUE_FILE}")
    return queue


if __name__ == "__main__":
    scan()
