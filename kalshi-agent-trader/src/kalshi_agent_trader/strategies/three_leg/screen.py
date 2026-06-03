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
    Leg, Outcome, ThreeLegPlan, hedge_ratio, size_hedge_leg, size_leg,
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
    orientation: str = "favorite",
) -> List[ThreeLegPlan]:
    """orientation selects who anchors the MATCH leg (the other gets title + out):
    "favorite" backs the match favourite; "underdog" flips it. The research agent
    compares both and picks the side."""
    # Men's (best-of-5) only: the corrected structure's leg 3 is "B wins in 5 sets",
    # which is undefined for the women's best-of-3. Women's path is a known follow-up.
    requested = ("men", "women") if gender == "both" else (gender,)
    genders = tuple(g for g in requested if g == "men")
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
            opp = next((m for m in legs if m is not fav), None)
            if opp is None:
                continue
            match_mkt, title_mkt = (fav, opp) if orientation == "favorite" else (opp, fav)
            plan = _plan_for(md, g, match_mkt, title_mkt, legs, tourney_idx, params)
            if plan is not None:
                plans.append(plan)

    if players:
        q = [normalize_name(p) for p in players if normalize_name(p)]
        plans = [p for p in plans if any(s in normalize_name(p.name) for s in q)]
    return plans


def _short(name: str) -> str:
    return (name or "?").split()[-1]


def _plan_for(
    md: MarketData, gender: str, match_mkt: Market, title_mkt: Market,
    legs: List[Market], tourney_idx: Dict[str, Market], params: ThreeLegParams,
) -> Optional[ThreeLegPlan]:
    """Legs on DIFFERENT players. M = ``match_mkt`` player (Leg 1: M wins the match);
    T = ``title_mkt`` player (Leg 2: T wins the tournament); Leg 3 = T wins the match
    in 5 sets (the out). orientation chooses which player is M vs T."""
    m_name = match_mkt.yes_sub_title or "M"
    t_name = title_mkt.yes_sub_title or "T"
    match_ask = _side_price(match_mkt, "yes", "ask")
    match_fair = _match_fair(match_mkt, legs)
    if not match_ask or match_ask <= 0 or match_fair is None:
        return None

    # Leg 1 — M wins the match.
    match_leg = size_leg(
        "match", match_mkt.ticker, price=match_ask, market_fair=match_fair,
        edge=params.match_edge, bankroll=params.bankroll,
        kelly_fraction_cap=params.kelly_fraction,
    )

    # Leg 2 — T (the OTHER player) wins the tournament, paired by competitor UUID.
    title_leg: Optional[Leg] = None
    cid = competitor_id(title_mkt)
    tmkt = tourney_idx.get(cid) if cid else None
    if tmkt is not None:
        t_ask = _side_price(tmkt, "yes", "ask")
        t_mid = _side_price(tmkt, "yes", "mid")
        if t_ask and t_ask > 0 and t_mid is not None:
            title_leg = size_leg(
                f"title ({_short(t_name)})", tmkt.ticker, price=t_ask, market_fair=t_mid,
                edge=params.title_edge, bankroll=params.bankroll,
                kelly_fraction_cap=params.kelly_fraction,
            )

    # Leg 3 — T wins in 5 sets: the OUT for the worry case (T beats M, no title).
    # Sized to a fraction of the directional exposure (match + title) it insures.
    ref_contracts = match_leg.contracts + (title_leg.contracts if title_leg else 0)
    candidates, note = length.discover(md, match_mkt, m_name)
    if candidates:
        long_legs, outcomes = _men_legs_and_outcomes(
            candidates, ref_contracts, params, _short(m_name), _short(t_name))
    else:
        long_legs, outcomes = [], [
            Outcome(label=f"{_short(m_name)} wins match", prob=match_fair, a_wins_match=True),
            Outcome(label=f"{_short(t_name)} wins match", prob=ONE - match_fair, a_wins_match=False),
        ]

    return ThreeLegPlan(
        name=f"{_short(m_name)} (match) + {_short(t_name)} (title)", gender=gender,
        rest_days=params.rest_days, match_leg=match_leg, title_leg=title_leg,
        long_legs=long_legs, outcomes=outcomes, note=note,
        pending_hedge_event=(length.exact_event_for(match_mkt) if not long_legs else None),
    )


def _men_legs_and_outcomes(
    candidates, ref_contracts: int, params: ThreeLegParams, m_short: str, t_short: str,
) -> tuple[List[Leg], List[Outcome]]:
    """Best-of-5 (men): Leg 3 is the TITLE player's 5-set win (T 3-2) — and only that.

    It pays exactly in the worry case (T beats M in a 5-setter) and nothing on an
    M win (M in 5 is no help — Leg 1 already pays). Sized to a fraction of the
    directional exposure it insures. Outcomes cover every exact score for the P&L
    grid; only the T-3-2 outcome carries a ``leg3_pay``.
    """
    long_legs: List[Leg] = []
    leg3_contracts = ZERO
    t5 = next((c for c in candidates if not c.winner_is_match and c.sets_lost == 2), None)
    if t5 is not None:
        ratio = hedge_ratio(params.fatigue_coef, 2, params.rest_days)  # 5-set = 2 extra
        leg = size_hedge_leg(
            f"{t_short} wins 3-2 (5-set out)", t5.ticker, price=t5.yes_ask,
            market_fair=t5.devig_prob, ratio=ratio,
            reference_contracts=ref_contracts, extra_sets=2)
        long_legs.append(leg)
        leg3_contracts = D(leg.contracts)

    outcomes: List[Outcome] = []
    for c in candidates:
        is_t5 = (not c.winner_is_match and c.sets_lost == 2)
        who = m_short if c.winner_is_match else t_short
        label = f"{who} wins {c.score_label}" + (" · 5-set OUT" if is_t5 else "")
        outcomes.append(Outcome(
            label=label, prob=c.devig_prob, a_wins_match=c.winner_is_match,
            leg3_pay=leg3_contracts if is_t5 else ZERO))
    return long_legs, outcomes


def _set_hedge_legs(
    set_cands, reference_contracts: int, params: ThreeLegParams,
) -> tuple[List[Leg], List[Outcome]]:
    """Best-of-3 (women): a drained win = a 3-setter = F dropped set 1 or set 2.

    Split the hedge ratio ρ (one extra set) across the opponent's Set-1/Set-2 YES
    legs. Each opp-set leg pays $1 when F drops that set, so exactly one pays in any
    3-set match (wins-2-1 or loses-1-2) and both pay if F loses 0-2.
    """
    # TODO(women): currently UNUSED — build_plans is men-only until the women's
    # Bo3 structure is rewritten for different-player legs (no "5 sets" in Bo3).
    # Kept compile-valid under the new Outcome API; P&L semantics not yet corrected.
    ratio = hedge_ratio(params.fatigue_coef, 1, params.rest_days) / D(len(set_cands))
    long_legs = [
        size_hedge_leg(
            f"opp set {c.set_no}", c.ticker, price=c.yes_ask,
            market_fair=ONE - c.fav_set_prob, ratio=ratio,
            reference_contracts=reference_contracts, extra_sets=1)
        for c in set_cands
    ]
    h = D(long_legs[0].contracts)                       # equal contracts per set leg
    q1, q2 = set_cands[0].fav_set_prob, set_cands[1].fav_set_prob
    q = (q1 + q2) / D("2")                              # deciding-set strength
    split = q1 * (ONE - q2) + (ONE - q1) * q2           # P(first two sets split → 3 sets)
    outcomes = [
        Outcome(label="wins 2-0", prob=q1 * q2, a_wins_match=True, leg3_pay=ZERO),
        Outcome(label="wins 2-1", prob=split * q, a_wins_match=True, leg3_pay=h),
        Outcome(label="loses 1-2", prob=split * (ONE - q), a_wins_match=False, leg3_pay=h),
        Outcome(label="loses 0-2", prob=(ONE - q1) * (ONE - q2), a_wins_match=False,
                leg3_pay=h * D("2")),
    ]
    return long_legs, outcomes
