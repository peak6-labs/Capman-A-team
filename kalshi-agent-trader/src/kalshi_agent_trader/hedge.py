"""Hedge math for an open Kalshi position (pure, no I/O).

Given a long position and a set of candidate hedge instruments, this scores each
way to lay off the risk and — crucially — compares every hedge to simply
*exiting* the position. The recurring lesson from manual analysis: a "hedge" on a
correlated market often just locks a worse loss than selling, so the engine
always surfaces the exit baseline and flags when a hedge is dominated by it.

Three relationships a hedge can have to the position:

* ``OPPOSITE``    — the other side of the *same* market (Fonseca YES vs Mensik
                    YES). A full, clean hedge: exactly one side pays $1.
* ``EQUIVALENT``  — a *different* market resolving on the logically identical
                    event (winning a QF == reaching the SF). Also a full hedge,
                    and the place stale pricing/edge tends to hide.
* ``CORRELATED``  — pays in the lose-scenario but not only then (a title NO when
                    you're long the match). A *partial* hedge; we report how much
                    of the per-contract loss it offsets, not a clean lock.

All arithmetic is Decimal; callers format.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import List, Optional


class Rel(str, Enum):
    OPPOSITE = "opposite"
    EQUIVALENT = "equivalent"
    CORRELATED = "correlated"


def _d(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


@dataclass(frozen=True)
class Position:
    """A long position. ``side`` is the side you HOLD ('yes' or 'no')."""
    ticker: str
    side: str
    count: int
    avg_cost: Decimal  # per contract, in dollars


@dataclass(frozen=True)
class HedgeQuote:
    """A way to bet the position LOSES.

    ``ask`` is the per-contract cost to establish the hedge (what you pay to take
    liquidity). ``fair`` is your fair probability that this hedge leg pays off.
    ``payoff_if_lose`` is the $ each hedge contract returns when the position
    loses (1.0 for OPPOSITE/EQUIVALENT; the *gain* in the lose-scenario, e.g.
    ~0.15, for a CORRELATED leg).
    """
    label: str
    ticker: str
    buy_side: str
    ask: Decimal
    fair: Decimal
    rel: Rel
    payoff_if_lose: Decimal = Decimal("1")


@dataclass(frozen=True)
class HedgeEval:
    label: str
    rel: Rel
    cost_per_contract: Decimal
    edge_per_contract: Decimal     # fair - ask; positive = +EV to put on
    hedge_ratio: Decimal           # fraction of the position's per-contract loss this offsets
    full_hedge_cost: Optional[Decimal]   # cost to hedge the whole position (clean hedges only)
    locked_pnl: Optional[Decimal]        # P&L if fully hedged and held to resolution (clean only)
    dominated_by_exit: Optional[bool]    # True if just exiting beats this hedge


def exit_pnl(pos: Position, exit_bid) -> Decimal:
    """Realized P&L from selling the whole position at ``exit_bid``."""
    return _d(pos.count) * (_d(exit_bid) - pos.avg_cost)


def evaluate(pos: Position, q: HedgeQuote, exit_bid=None) -> HedgeEval:
    """Score one hedge candidate against the position."""
    ask = _d(q.ask)
    fair = _d(q.fair)
    payoff = _d(q.payoff_if_lose)
    edge = fair - ask

    # fraction of the position's per-contract downside this leg covers.
    # downside per contract held = avg_cost (you lose what you paid if it goes to 0).
    hedge_ratio = (payoff / pos.avg_cost) if pos.avg_cost > 0 else Decimal("0")

    full_cost = locked = dominated = None
    if q.rel in (Rel.OPPOSITE, Rel.EQUIVALENT):
        # one-for-one: exactly one of {position, hedge} pays $1.
        n = _d(pos.count)
        full_cost = n * ask
        locked = n * (Decimal("1") - pos.avg_cost - ask)
        if exit_bid is not None:
            dominated = exit_pnl(pos, exit_bid) > locked

    return HedgeEval(
        label=q.label, rel=q.rel,
        cost_per_contract=ask, edge_per_contract=edge,
        hedge_ratio=hedge_ratio, full_hedge_cost=full_cost,
        locked_pnl=locked, dominated_by_exit=dominated,
    )


def rank(pos: Position, quotes: List[HedgeQuote], exit_bid=None) -> List[HedgeEval]:
    """Evaluate all candidates. Clean hedges first (best locked P&L), then
    correlated by edge. Caller also has ``exit_pnl`` for the baseline."""
    evals = [evaluate(pos, q, exit_bid) for q in quotes]

    def key(e: HedgeEval):
        clean = e.rel in (Rel.OPPOSITE, Rel.EQUIVALENT)
        # clean hedges: rank by locked P&L (least-bad lock first)
        # correlated: rank by edge
        return (0 if clean else 1,
                -(e.locked_pnl if e.locked_pnl is not None else Decimal("-999")),
                -e.edge_per_contract)

    return sorted(evals, key=key)
