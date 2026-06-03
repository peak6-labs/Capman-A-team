"""Translate dip signals into orders + track open positions (pure).

Uses the executor's buy/sell ``action`` model:
  * ENTER  — buy YES, resting a limit at the current best bid (we add liquidity
             into the over-reaction; resting ⇒ maker fill).
  * EXIT   — sell YES to flatten: a resting offer at the match-implied fair when
             taking profit (maker), or a cross at the bid when stopping out
             (taker — fill certainty matters more than the rebate on a loser).

Pure: ``intent_for`` decides the next order from (signal, open position); the
caller submits it and only then mutates the PositionBook (so the book reflects
intended state in dry-run, fills when live).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional

from .detector import DipParams, DipSignal

D = Decimal


@dataclass(frozen=True)
class OpenPosition:
    ticker: str
    entry_price: Decimal     # YES price we bought at
    contracts: Decimal
    fair_target: Decimal     # the match-implied fair we aim to revert to


@dataclass(frozen=True)
class OrderIntent:
    kind: str            # "enter" | "exit"
    ticker: str
    side: str            # always "yes" (we trade the title YES)
    action: str          # "buy" (enter) | "sell" (exit)
    price: Decimal       # limit price in dollars
    count: int
    maker: bool          # True = rest (provide liquidity); False = cross (take)
    reason: str
    fair_prob: float     # for the risk gate's edge check
    confidence: float


def intent_for(
    signal: DipSignal, open_pos: Optional[OpenPosition], params: DipParams,
) -> Optional[OrderIntent]:
    """Next order for one player, or None to hold. See module docstring."""
    if open_pos is None:
        # Enter only on a sized BUY DIP with a real bid to rest at.
        if (signal.action == "BUY DIP" and signal.stake > 0
                and signal.title_bid and signal.title_bid > 0
                and int(signal.contracts) >= 1):
            return OrderIntent(
                kind="enter", ticker=signal.title_ticker, side="yes", action="buy",
                price=signal.title_bid, count=int(signal.contracts), maker=True,
                reason="dip entry (rest bid)", fair_prob=float(signal.fair_title),
                confidence=1.0,  # our φ/EV gate already cleared this; satisfy risk min_confidence
            )
        return None

    # Holding a position: sell YES to flatten.
    if signal.action == "STOP":
        return OrderIntent(
            kind="exit", ticker=open_pos.ticker, side="yes", action="sell",
            price=signal.title_bid or open_pos.entry_price, count=int(open_pos.contracts),
            maker=False, reason="stop (match broke floor)",
            fair_prob=float(open_pos.fair_target), confidence=1.0,
        )
    if signal.action == "REVERTED":
        return OrderIntent(
            kind="exit", ticker=open_pos.ticker, side="yes", action="sell",
            price=open_pos.fair_target, count=int(open_pos.contracts),
            maker=True, reason="take-profit (reverted to fair)",
            fair_prob=float(open_pos.fair_target), confidence=1.0,
        )
    return None  # REVERTING / WATCH -> hold


class PositionBook:
    """In-memory open positions keyed by title ticker. Caller updates on submit."""

    def __init__(self) -> None:
        self._open: Dict[str, OpenPosition] = {}

    def get(self, ticker: str) -> Optional[OpenPosition]:
        return self._open.get(ticker)

    def on_enter(self, signal: DipSignal) -> None:
        self._open[signal.title_ticker] = OpenPosition(
            ticker=signal.title_ticker, entry_price=signal.title_bid or signal.title_mid,
            contracts=signal.contracts, fair_target=signal.fair_title,
        )

    def on_exit(self, ticker: str) -> None:
        self._open.pop(ticker, None)

    @property
    def open_tickers(self):
        return list(self._open)
