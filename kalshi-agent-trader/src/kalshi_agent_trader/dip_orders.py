"""Translate dip signals into maker orders + track open positions (pure).

The execution model, kept inside the existing buy-only Executor / verified order
body:

  * ENTER  — buy title YES as a MAKER, resting a bid at the current best bid
             (you provide liquidity into the over-reaction).
  * EXIT   — flatten by *buying NO* (economically = selling the YES long on a
             binary market): a MAKER offer when taking profit at fair, a TAKER
             cross when stopping out. Buying NO keeps us within the verified
             buy-only order body; the buy-vs-sell-to-close wire semantics must be
             confirmed at the first live place-and-cancel (same caveat as
             execution.build_v2_order_body).

This module is pure: ``intent_for`` decides the next order from (signal, open
position); the caller submits it through the Executor and only then mutates the
PositionBook (so the book reflects intended state in dry-run, fills when live).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional

from .reversion import DipParams, DipSignal

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
    side: str            # "yes" (enter) | "no" (exit = flatten the YES long)
    price: Decimal       # limit price in dollars for `side`
    count: int
    maker: bool          # True = rest (provide liquidity); False = cross (take)
    reason: str
    fair_prob: float     # for the risk gate's edge check (fair for `side`)
    confidence: float


def intent_for(
    signal: DipSignal, open_pos: Optional[OpenPosition], params: DipParams,
) -> Optional[OrderIntent]:
    """Next order for one player, or None to hold. See module docstring for model."""
    if open_pos is None:
        # Enter only on a sized BUY DIP with a real bid to rest at.
        if (signal.action == "BUY DIP" and signal.stake > 0
                and signal.title_bid and signal.title_bid > 0
                and int(signal.contracts) >= 1):
            return OrderIntent(
                kind="enter", ticker=signal.title_ticker, side="yes",
                price=signal.title_bid, count=int(signal.contracts), maker=True,
                reason="dip entry (rest bid)", fair_prob=float(signal.fair_title),
                confidence=float(signal.overreaction_frac),
            )
        return None

    # We hold a position: exit by buying NO to flatten the YES long.
    if signal.action == "STOP":
        no_price = D("1") - (signal.title_bid or open_pos.entry_price)  # cross to get out
        return OrderIntent(
            kind="exit", ticker=open_pos.ticker, side="no", price=no_price,
            count=int(open_pos.contracts), maker=False, reason="stop (match broke floor)",
            fair_prob=float(D("1") - open_pos.fair_target), confidence=1.0,
        )
    if signal.action == "REVERTED":
        no_price = D("1") - open_pos.fair_target                         # rest the take-profit
        return OrderIntent(
            kind="exit", ticker=open_pos.ticker, side="no", price=no_price,
            count=int(open_pos.contracts), maker=True, reason="take-profit (reverted to fair)",
            fair_prob=float(D("1") - open_pos.fair_target), confidence=1.0,
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
