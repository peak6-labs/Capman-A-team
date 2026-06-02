"""Assemble fatigue-hedged three-leg plans for each QF favourite (I/O layer).

For every open match in the chosen gender(s): pick the favourite (higher YES
mid), pair their title market by competitor UUID (as ``tennis_screen`` does),
discover the exact-score length legs, and Kelly-size all three legs — with the
turnaround-weighted fatigue premium on the length legs.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional

from ...market_data import MarketData
from ...models import Market
from ...tennis_screen import (
    MEN_MATCH, MEN_TOURNEY, WOMEN_MATCH, WOMEN_TOURNEY,
    _side_price, competitor_id, fetch_series_markets, index_by_competitor,
    normalize_name,
)
from . import length
from .compute import (
    Leg, Outcome, ThreeLegPlan, fatigue_premium, size_leg,
)

D = Decimal
ZERO = D("0")
ONE = D("1")
_OPEN = {"open", "active"}


@dataclass(frozen=True)
class ThreeLegParams:
    bankroll: Decimal = D("100")
    kelly_fraction: Decimal = D("0.5")
    fatigue_coef: Decimal = D("0.20")
    rest_days: int = 1
    match_edge: Decimal = ZERO      # added to de-vigged match fair (your conviction)
    title_edge: Decimal = ZERO      # added to title YES mid
    fee_rate: Decimal = D("0.07")


def _group_by_event(markets: List[Market]) -> Dict[str, List[Market]]:
    out: Dict[str, List[Market]] = {}
    for m in markets:
        if m.status and m.status.lower() in _OPEN:
            out.setdefault(m.event_ticker, []).append(m)
    return out


def _favorite(players: List[Market]) -> Optional[Market]:
    """The higher-YES-mid market among a match's two legs."""
    priced = [(m, _side_price(m, "yes", "mid")) for m in players]
    priced = [(m, p) for m, p in priced if p is not None]
    if len(priced) < 2:
        return None
    priced.sort(key=lambda mp: mp[1], reverse=True)
    return priced[0][0]


def _match_fair(fav: Market, players: List[Market]) -> Optional[Decimal]:
    """De-vig the two-outcome match: fav_mid / (fav_mid + opp_mid)."""
    mids = [_side_price(m, "yes", "mid") for m in players]
    mids = [m for m in mids if m and m > 0]
    fav_mid = _side_price(fav, "yes", "mid")
    if not fav_mid or len(mids) < 2:
        return None
    return fav_mid / sum(mids)


def build_plans(
    md: MarketData, *, gender: str, players: Optional[List[str]], params: ThreeLegParams,
) -> List[ThreeLegPlan]:
    genders = ("men", "women") if gender == "both" else (gender,)
    plans: List[ThreeLegPlan] = []

    for g in genders:
        match_series = MEN_MATCH if g == "men" else WOMEN_MATCH
        tourney_series = MEN_TOURNEY if g == "men" else WOMEN_TOURNEY
        match_markets = fetch_series_markets(md, match_series)
        tourney_idx = index_by_competitor(fetch_series_markets(md, tourney_series))

        for _event, legs in _group_by_event(match_markets).items():
            fav = _favorite(legs)
            if fav is None:
                continue
            plan = _plan_for(md, g, fav, legs, tourney_idx, params)
            if plan is not None:
                plans.append(plan)

    if players:
        q = [normalize_name(p) for p in players if normalize_name(p)]
        plans = [p for p in plans if any(s in normalize_name(p.name) for s in q)]
    return plans


def _plan_for(
    md: MarketData, gender: str, fav: Market, legs: List[Market],
    tourney_idx: Dict[str, Market], params: ThreeLegParams,
) -> Optional[ThreeLegPlan]:
    name = fav.yes_sub_title or "?"
    match_ask = _side_price(fav, "yes", "ask")
    match_fair = _match_fair(fav, legs)
    if not match_ask or match_ask <= 0 or match_fair is None:
        return None

    match_leg = size_leg(
        "match", fav.ticker, price=match_ask, market_fair=match_fair,
        edge=params.match_edge, bankroll=params.bankroll,
        kelly_fraction_cap=params.kelly_fraction,
    )

    # Leg 2 — title, paired by competitor UUID.
    title_leg: Optional[Leg] = None
    cid = competitor_id(fav)
    tmkt = tourney_idx.get(cid) if cid else None
    if tmkt is not None:
        t_ask = _side_price(tmkt, "yes", "ask")
        t_mid = _side_price(tmkt, "yes", "mid")
        if t_ask and t_ask > 0 and t_mid is not None:
            title_leg = size_leg(
                "title", tmkt.ticker, price=t_ask, market_fair=t_mid,
                edge=params.title_edge, bankroll=params.bankroll,
                kelly_fraction_cap=params.kelly_fraction,
            )

    # Leg 3 — length hedge: one Kelly leg per long win-score, fatigue-boosted.
    candidates, note = length.discover(md, fav, name)
    long_legs: List[Leg] = []
    outcomes: List[Outcome] = [
        Outcome(label="loses QF", prob=ONE - match_fair, is_win=False, sets_lost=0)
    ]
    if candidates:
        for c in candidates:
            leg3_contracts = ZERO
            if c.sets_lost >= 1:  # a long win → a hedge leg
                phi = fatigue_premium(params.fatigue_coef, c.sets_lost, params.rest_days)
                leg = size_leg(
                    f"win {c.score_label}", c.ticker, price=c.yes_ask,
                    market_fair=c.devig_prob, fatigue=phi, extra_sets=c.sets_lost,
                    bankroll=params.bankroll, kelly_fraction_cap=params.kelly_fraction,
                )
                long_legs.append(leg)
                leg3_contracts = D(leg.contracts)
            outcomes.append(Outcome(
                label=f"wins {c.score_label}", prob=c.devig_prob, is_win=True,
                sets_lost=c.sets_lost, leg3_pay=leg3_contracts,
            ))
    else:
        # No length market yet: trade legs 1-2 now, flag the hedge as pending so it
        # can be added when the exact-score market lists (re-run at match-live time).
        outcomes.append(Outcome(
            label="wins QF", prob=match_fair, is_win=True, sets_lost=0))

    return ThreeLegPlan(
        name=name, gender=gender, rest_days=params.rest_days,
        match_leg=match_leg, title_leg=title_leg, long_legs=long_legs,
        outcomes=outcomes, note=note,
        pending_hedge_event=(length.exact_event_for(fav) if not long_legs else None),
    )
