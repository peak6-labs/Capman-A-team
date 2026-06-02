"""Pure sizing + P&L math for the three-leg "back a QF favorite + fatigue hedge".

Structure (all legs are YES buys on the backed favourite F):

  Leg 1  match YES   — F wins the quarter-final.
  Leg 2  title YES    — F wins the tournament.
  Leg 3  length hedge — F wins, but in a LONG match (men: 3-1 / 3-2; women: 2-1).
                        Pays exactly when F advances drained, cushioning the
                        fatigue risk a long win imposes on Leg 2's deep run.

Each leg is sized by FRACTIONAL KELLY on a fair probability vs the ask:

    f* = (b·p − (1−p)) / b ,   b = (1−price)/price          (clamped at 0)
    stake = bankroll · kelly_fraction · f*
    contracts = floor(stake / price)

Legs 1 & 2 take their fair from the de-vigged market mid plus an optional
directional `edge` you supply (0 ⇒ no bet at market — honest Kelly). Leg 3's
fair is the de-vigged long-win prob plus a TURNAROUND-WEIGHTED fatigue premium:

    φ = fatigue_coef · extra_sets / rest_days

so a short QF→SF turnaround (few rest days) *upsizes* the hedge. `extra_sets` is
the sets beyond a sweep the score implies (3-1 → 1, 3-2 → 2, women 2-1 → 1).

This module is pure: no I/O, all Decimal, caller formats/rounds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional

D = Decimal
ZERO = D("0")
ONE = D("1")


def kelly_fraction(fair: Decimal, price: Decimal) -> Decimal:
    """Full-Kelly stake fraction for a YES buy at `price` with true prob `fair`.

    Returns 0 when there's no edge (fair <= price) or the price is degenerate.
    """
    if price <= ZERO or price >= ONE:
        return ZERO
    b = (ONE - price) / price
    f = (b * fair - (ONE - fair)) / b
    return f if f > ZERO else ZERO


def fatigue_premium(fatigue_coef: Decimal, extra_sets: int, rest_days: int) -> Decimal:
    """Probability points added to a long-win leg's fair for short turnaround.

    φ = coef · extra_sets / rest_days. rest_days is floored at 1 (same-day = max).
    """
    rd = max(1, int(rest_days))
    return fatigue_coef * D(extra_sets) / D(rd)


@dataclass(frozen=True)
class Leg:
    label: str
    ticker: str
    price: Decimal            # ask we'd pay (entry cost per contract)
    market_fair: Decimal      # de-vigged market prob (reference / zero-edge)
    fair: Decimal             # fair used for Kelly (market_fair + edge / + fatigue φ)
    kelly_f: Decimal          # full-Kelly fraction at this (fair, price)
    stake: Decimal            # bankroll · kelly_fraction · kelly_f
    contracts: int            # floor(stake / price)
    extra_sets: int = 0       # 0 for match/title; sets-beyond-sweep for a length leg

    @property
    def cost(self) -> Decimal:
        return D(self.contracts) * self.price

    @property
    def sized(self) -> bool:
        return self.contracts >= 1


def size_leg(
    label: str,
    ticker: str,
    *,
    price: Decimal,
    market_fair: Decimal,
    edge: Decimal = ZERO,
    fatigue: Decimal = ZERO,
    extra_sets: int = 0,
    bankroll: Decimal,
    kelly_fraction_cap: Decimal,
) -> Leg:
    """Kelly-size one leg. `edge`/`fatigue` add to the fair the market implies."""
    fair = market_fair + edge + fatigue
    if fair > ONE:
        fair = ONE
    kf = kelly_fraction(fair, price)
    stake = bankroll * kelly_fraction_cap * kf
    contracts = int(stake / price) if price > 0 else 0  # int() truncates ⇒ floor (stake ≥ 0)
    return Leg(
        label=label, ticker=ticker, price=price, market_fair=market_fair,
        fair=fair, kelly_f=kf, stake=stake, contracts=max(0, contracts),
        extra_sets=extra_sets,
    )


@dataclass(frozen=True)
class Outcome:
    """One terminal QF result and how the legs pay (in contract units)."""
    label: str                # "loses QF" | "wins 3-0" | "wins 3-1" | ...
    prob: Decimal             # de-vigged probability of this QF result
    is_win: bool              # F won the QF (Leg 1 pays)
    sets_lost: int            # 0 for a sweep; >0 for a long win
    leg3_pay: Decimal = ZERO  # contracts of the length leg that pays in THIS result


@dataclass
class ThreeLegPlan:
    name: str
    gender: str               # "men" | "women"
    rest_days: int
    match_leg: Leg
    title_leg: Optional[Leg]
    long_legs: List[Leg] = field(default_factory=list)
    outcomes: List[Outcome] = field(default_factory=list)
    note: str = ""
    pending_hedge_event: Optional[str] = None   # exact-score event to hedge with once it lists

    @property
    def hedge_pending(self) -> bool:
        """Legs 1-2 are tradeable now, but the length hedge market isn't live yet."""
        return bool(self.pending_hedge_event) and not self.long_legs

    @property
    def total_cost(self) -> Decimal:
        c = self.match_leg.cost
        if self.title_leg:
            c += self.title_leg.cost
        return c + sum((leg.cost for leg in self.long_legs), ZERO)

    @property
    def actionable(self) -> bool:
        return self.match_leg.sized or (self.title_leg and self.title_leg.sized) \
            or any(leg.sized for leg in self.long_legs)

    def net_pnl(self, outcome: Outcome, *, title_win: bool) -> Decimal:
        """Net $ P&L in this QF result, optionally if F then wins the title."""
        payoff = ZERO
        if outcome.is_win:
            payoff += D(self.match_leg.contracts)        # Leg 1 settles on a QF win
            payoff += outcome.leg3_pay                   # the matching length leg
            if title_win and self.title_leg:
                payoff += D(self.title_leg.contracts)    # Leg 2 settles on a title
        return payoff - self.total_cost

    def expected_value(self, *, p_title_given_advance: Decimal) -> Decimal:
        """EV over QF outcomes, splitting each win on title vs no-title."""
        ev = ZERO
        for o in self.outcomes:
            if not o.is_win:
                ev += o.prob * self.net_pnl(o, title_win=False)
                continue
            ev += o.prob * (
                p_title_given_advance * self.net_pnl(o, title_win=True)
                + (ONE - p_title_given_advance) * self.net_pnl(o, title_win=False)
            )
        return ev
