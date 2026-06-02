"""Position exit monitor — poll open positions and close on trigger.

Exit triggers (in priority order):
  TARGET_HIT   — current bid ≤ target_price (~85% of the move captured)
  NEAR_EXPIRY  — < 2h to resolution (don't hold through the coin-flip)
  STALE_THESIS — held 24h with < 2% price move (thesis isn't playing out)
  RESOLVED     — market no longer active (already settled)

Phase 6 will replace `run_loop` with WebSocket-driven event handling, eliminating
the REST polling overhead.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Tuple

from .client import KalshiClient, KalshiError
from .journal import Journal
from .market_data import MarketData

TARGET_FRACTION = 0.15      # exit when bid ≤ 15% of entry price
NEAR_EXPIRY_H = 2.0
STALE_HOURS = 24
STALE_MOVE_PCT = 0.02
V2_ORDERS_PATH = "/portfolio/events/orders"


class ExitReason(str, Enum):
    TARGET_HIT = "TARGET_HIT"
    NEAR_EXPIRY = "NEAR_EXPIRY"
    STALE_THESIS = "STALE_THESIS"
    RESOLVED = "RESOLVED"


def _hours_until(dt_str: Optional[str]) -> Optional[float]:
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return (dt - datetime.now(timezone.utc)).total_seconds() / 3600
    except Exception:
        return None


class Monitor:
    def __init__(
        self,
        market_data: MarketData,
        client: KalshiClient,
        journal: Journal,
        *,
        live: bool = False,
    ) -> None:
        self._md = market_data
        self._client = client
        self._journal = journal
        self.live = live

    def check_one(self, pos) -> Optional[ExitReason]:
        """Check a single open position (sqlite3.Row or dict) for an exit trigger."""
        ticker = pos["ticker"]
        side = pos["side"]
        entry_price = Decimal(str(pos["entry_price"]))
        target_price = Decimal(str(pos["target_price"]))
        expiry = pos["expiry"]
        opened_ts = pos["opened_ts"]

        try:
            market = self._md.get_market(ticker)
        except KalshiError:
            return ExitReason.RESOLVED

        if market.status not in ("active",):
            return ExitReason.RESOLVED

        current_bid = market.yes_bid if side == "yes" else market.no_bid
        if current_bid is None:
            return ExitReason.RESOLVED

        if current_bid <= target_price:
            return ExitReason.TARGET_HIT

        hours_left = _hours_until(expiry)
        if hours_left is not None and hours_left < NEAR_EXPIRY_H:
            return ExitReason.NEAR_EXPIRY

        hours_held = (
            datetime.now(timezone.utc).timestamp() * 1000 - opened_ts
        ) / 3_600_000
        if hours_held > STALE_HOURS:
            move = abs(float(current_bid) - float(entry_price)) / float(entry_price)
            if move < STALE_MOVE_PCT:
                return ExitReason.STALE_THESIS

        return None

    def run_once(self) -> List[Tuple[object, ExitReason]]:
        """Check all open positions once. Returns (position, reason) pairs that triggered."""
        positions = self._journal.open_positions()
        triggered = []

        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        if not positions:
            print(f"[{ts}] no open positions")
            return []

        for pos in positions:
            reason = self.check_one(pos)
            if reason is None:
                continue

            triggered.append((pos, reason))
            print(f"  [{reason.value}] {pos['side']} {pos['ticker']}")

            if reason == ExitReason.RESOLVED:
                self._journal.close_position(pos["id"], reason.value)
                continue

            if self.live and reason != ExitReason.RESOLVED:
                self._buy_to_close(pos, reason)
            else:
                # Dry-run: just report the trigger.
                pass

        print(f"[{ts}] {len(positions) - len(triggered)}/{len(positions)} positions still open")
        return triggered

    def run_loop(self, poll_interval: int = 60) -> None:
        """Poll open positions on a fixed interval until interrupted."""
        print(f"Exit monitor started — polling every {poll_interval}s  (live={self.live})")
        try:
            while True:
                self.run_once()
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            print("Monitor stopped.")

    def _buy_to_close(self, pos, reason: ExitReason) -> None:
        side = pos["side"]
        current_ask = None
        try:
            market = self._md.get_market(pos["ticker"])
            current_ask = market.yes_ask if side == "yes" else market.no_ask
        except Exception:
            pass

        close_price = current_ask or (Decimal(str(pos["entry_price"])) * Decimal("0.5"))
        body = {
            "ticker": pos["ticker"],
            "client_order_id": str(uuid.uuid4()),
            "book_side": "bid" if side == "yes" else "ask",
            "type": "limit",
            "price_dollars": f"{close_price:.4f}",
            "count": f"{Decimal(str(pos['count'])):.2f}",
        }
        try:
            self._client.post(V2_ORDERS_PATH, json=body)
            self._journal.close_position(pos["id"], reason.value)
            print(f"    → close order placed @ ${close_price:.4f}")
        except Exception as e:
            print(f"    [CLOSE ERROR] {pos['ticker']}: {e}")
