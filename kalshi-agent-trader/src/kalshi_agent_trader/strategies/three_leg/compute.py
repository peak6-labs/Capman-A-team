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
directional `edge` you supply (0 ⇒ no bet at market — honest Kelly).

Leg 3 is sized as a TRUE HEDGE, not a directional bet: the long-win contracts
offset the title value you expect to lose if F advances drained. We hedge a
turnaround-weighted FRACTION of the title position:

    ρ = clamp(fatigue_coef · extra_sets / rest_days, 0, 1)      # hedge ratio
    long_contracts = round(title_contracts · ρ)

so the hedge scales with (a) how much title you actually hold, (b) how long the
win was (`extra_sets` = sets beyond a sweep: 3-1 → 1, 3-2 → 2, women 2-1 → 1),
and (c) how short the QF→SF turnaround is (few rest days ⇒ bigger ρ). With no
title leg there is nothing to hedge, so ρ·0 = 0 contracts — the structure
refuses to place a naked duration bet. `fatigue_coef` now reads as "points of
conditional title probability lost per extra set per rest-day" (the share of
each title contract a long win puts at risk), NOT a market mispricing.

This module is pure: no I/O, all Decimal, caller formats/rounds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
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
    """Turnaround-weighted fatigue factor: coef · extra_sets / rest_days.

    rest_days is floored at 1 (same-day = max). Used as the raw hedge ratio
    before clamping (see `hedge_ratio`).
    """
    rd = max(1, int(rest_days))
    return fatigue_coef * D(extra_sets) / D(rd)


def hedge_ratio(fatigue_coef: Decimal, extra_sets: int, rest_days: int) -> Decimal:
    """Fraction of the title position to hedge with a long-win leg, clamped [0, 1]."""
    r = fatigue_premium(fatigue_coef, extra_sets, rest_days)
    if r <= ZERO:
        return ZERO
    return r if r < ONE else ONE


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


def size_hedge_leg(
    label: str,
    ticker: str,
    *,
    price: Decimal,
    market_fair: Decimal,
    ratio: Decimal,
    reference_contracts: int,
    extra_sets: int,
) -> Leg:
    """Size the "out" leg to a fraction `ratio` of the directional position it covers.

    contracts = round(reference_contracts · ratio) — where reference is the
    directional exposure being insured (match + title). The leg carries no
    fabricated edge: its `fair` stays at the de-vigged market prob (it's an out,
    not alpha). `kelly_f` holds the ratio so the renderer can show it.
    """
    contracts = int((D(reference_contracts) * ratio).to_integral_value(rounding=ROUND_HALF_UP))
    contracts = max(0, contracts)
    return Leg(
        label=label, ticker=ticker, price=price, market_fair=market_fair,
        fair=market_fair, kelly_f=ratio, stake=D(contracts) * price,
        contracts=contracts, extra_sets=extra_sets,
    )


@dataclass(frozen=True)
class Outcome:
    """One terminal match result and which legs fire (in contract units).

    The legs sit on DIFFERENT players: leg 1 = A wins the match, leg 2 = B (the
    opponent) wins the tournament, leg 3 = B wins the match in 5 sets. So payouts
    key off the match winner — and, for the B-wins branch, whether B then takes
    the title (resolved via the title split in net_pnl / expected_value).
    """
    label: str                # "A wins 3-1" | "B wins 3-2 (5-set OUT)" | ...
    prob: Decimal             # de-vigged probability of this exact match result
    a_wins_match: bool        # Leg 1 (A match) pays in this result
    leg3_pay: Decimal = ZERO  # contracts of Leg 3 paying here (nonzero only for B-wins-in-5)


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

    def net_pnl(self, outcome: Outcome, *, b_wins_title: bool) -> Decimal:
        """Net $ P&L in this match result, optionally if B then wins the title.

        Legs are on different players: Leg 1 (A match) pays iff A won; Leg 2 (B's
        title) pays only in a B-wins-the-match branch where B goes on to win the
        title; Leg 3 (B's 5-set) pays only when B won in 5 — the "out" that fires
        in the worry case (B beats A but doesn't win the title).
        """
        payoff = ZERO
        if outcome.a_wins_match:
            payoff += D(self.match_leg.contracts)            # Leg 1 — A won the match
        else:                                                # B won the match
            if b_wins_title and self.title_leg:
                payoff += D(self.title_leg.contracts)        # Leg 2 — B wins the title
            payoff += outcome.leg3_pay                       # Leg 3 — B won in 5 (the out)
        return payoff - self.total_cost

    def expected_value(self, *, p_b_title_given_advance: Decimal) -> Decimal:
        """EV over match results; B-wins branches split on B winning the title.

        When A wins the match our title bet (on B) is dead, so those outcomes
        contribute net_pnl directly. When B wins, split on P(B wins title | B advances).
        """
        ev = ZERO
        for o in self.outcomes:
            if o.a_wins_match:
                ev += o.prob * self.net_pnl(o, b_wins_title=False)
                continue
            ev += o.prob * (
                p_b_title_given_advance * self.net_pnl(o, b_wins_title=True)
                + (ONE - p_b_title_given_advance) * self.net_pnl(o, b_wins_title=False)
            )
        return ev
