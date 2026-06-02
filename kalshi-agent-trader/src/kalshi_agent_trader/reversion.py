"""Intraday title-dip detector + sizing: buy the over-reaction, exit on reversion.

The play (the in-play over-reaction → mean-reversion edge flagged in STRATEGY.md):
when a tournament favourite drops an early break/set, fast money marks their
*title* odds down harder than the (slow, recoverable) match deficit justifies.
The title market lags the match market, overshoots, and snaps back. We want to
buy that dislocation in the title YES and exit when it reverts.

The anchor is **match-implied fair value**. From the knockout decomposition,

    P(title) ≈ P(win match) · P(title | advances) = match_yes · C,

where ``C`` (= ``P(title | advances)``) is slow-moving. We seed ``C`` from the
title/match mid at first sight (ideally pre-match) and hold it fixed for the
session. The signal is the **residual over-reaction**:

    residual = fair_title − title_mid = C · match_yes_now − title_mid_now
             = C·Δmatch − Δtitle      (the title drop the match does NOT justify)

A positive residual means the title fell *more* than the match move warrants.

Sizing (the match-vs-title comparison made into a dial)
-------------------------------------------------------
The headline comparison is the **over-reaction fraction**::

    φ = residual / |Δtitle|   ∈ [0, 1]

i.e. the share of the title's drop that the match move does NOT explain. φ→1 is
a pure dislocation (title cratered, match barely moved) — high conviction. φ→0
is a justified repricing (title fell because the match fell) — don't trade.

We size a fractional-Kelly stake off three quantities:

  * gain if it reverts:   G = fair − ask
  * loss if it does not:  L = min(ask − C·recover_floor, stop_loss)
        — i.e. the *tighter* of riding to the match-floor and your actual cut-loss
        ``stop_loss``. Modelling the realistic stop (not a ride to the floor) is
        the risk-appetite lever: a tighter ``stop_loss`` ⇒ bigger size.
  * reversion probability: p = p_revert (your conviction the dislocation reverts)

Then EV = p·G − (1−p)·L, Kelly fraction f* = p − (1−p)·L/G, and the stake is
**scaled by φ** (the match-vs-title comparison guides size without vetoing it),
capped by per-position / bucket / no-leverage limits. Kalshi round-trip fees are
netted from G and L.

⚠️ ``p_revert`` and ``stop_loss`` together set the risk appetite and are
**UNCALIBRATED** — like ``fl_alpha`` in strategy.py. The defaults here are
deliberately risk-seeking (they will take a textbook dip like the Zverev case);
the candlestick backtest must measure the true reversion rate and loss-when-wrong
before this is more than a conviction bet. Keep dry_run on.

Money is Decimal. This module sizes only; it places no orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional

from .tennis.fees import DEFAULT_FEE_RATE, trading_fee
from .tennis.pairing import Universe

D = Decimal
_BIG = D("1e12")  # effectively-unbounded bucket room for the ranking pass


@dataclass(frozen=True)
class DipParams:
    # --- detection ---
    residual_threshold: Decimal = D("0.05") # title must sit ≥ this below match-implied fair to buy
    recover_floor: Decimal = D("0.35")      # match_yes mid < this ⇒ deficit is real, STOP
    exit_band: Decimal = D("0.02")          # within this of fair ⇒ reverted (take profit)
    min_match_anchor: Decimal = D("0.50")   # only anchor players who STARTED a favourite
    # --- sizing (defaults are deliberately risk-seeking; see module docstring) ---
    bankroll: Decimal = D("500")            # real account size; --bankroll overrides
    kelly_fraction: Decimal = D("0.5")      # moderate = half-Kelly
    p_revert: Decimal = D("0.70")           # conviction the dislocation reverts — UNCALIBRATED
    stop_loss: Decimal = D("0.06")          # ¢/contract you'll actually risk before cutting (caps L)
    max_position_frac: Decimal = D("0.10")  # cap on one dip's capital (10% of $500 = $50)
    max_bucket_frac: Decimal = D("0.20")    # cap on total simultaneous dip exposure
    fee_rate: Decimal = DEFAULT_FEE_RATE
    maker_fee_frac: Decimal = D("0.25")     # Kalshi maker fee = 25% of taker (backtest maker mode)


@dataclass(frozen=True)
class Anchor:
    """Per-player baseline, seeded once at first sight and held for the session."""
    competitor_id: str
    name: str
    gender: str
    c_ratio: Decimal     # title_mid / match_mid at anchor = P(title | advances)
    match_mid: Decimal   # match_yes mid when anchored
    title_mid: Decimal   # title_yes mid when anchored


@dataclass(frozen=True)
class DipSignal:
    name: str
    gender: str
    action: str          # "BUY DIP" | "REVERTING" | "REVERTED" | "STOP" | "WATCH"
    title_ticker: str    # the title market to trade (for the execution layer)
    # --- prices ---
    match_mid: Decimal   # current match_yes mid (the fair-value driver)
    match_anchor: Decimal
    title_ask: Optional[Decimal]  # current title YES ask (taker entry price)
    title_bid: Optional[Decimal]  # current title YES bid (maker entry: rest here)
    title_mid: Decimal
    fair_title: Decimal  # C · match_mid (match-implied fair)
    title_anchor: Decimal
    # --- the match-vs-title comparison ---
    match_delta: Decimal       # Δmatch = match_mid − match_anchor
    title_delta: Decimal       # Δtitle = title_mid − title_anchor
    residual: Decimal          # C·Δmatch − Δtitle (+ve = over-reaction / dip)
    peak_residual: Decimal     # deepest residual seen this session
    overreaction_frac: Decimal # φ = residual / |Δtitle| ∈ [0,1] (share unexplained by match)
    # --- sizing ---
    p_effective: Decimal       # reversion prob used in the EV/Kelly gate (= p_revert)
    gain: Decimal              # G = fair − ask, net of round-trip fees (per contract)
    loss: Decimal              # L = ask − C·floor, plus round-trip fees (per contract)
    kelly_f: Decimal           # Kelly fraction f* (pre-cap)
    stake: Decimal             # sized capital ($), after caps
    contracts: Decimal         # stake / ask
    est_profit: Decimal        # net $ on the sized stake if it reverts fully to fair
    rationale: str


def _mid(bid: Optional[Decimal], ask: Optional[Decimal]) -> Optional[Decimal]:
    if bid and ask:
        return (bid + ask) / 2
    return ask or bid


def _size_dip(
    *, ask: Decimal, fair: Decimal, c_ratio: Decimal, phi: Decimal,
    params: DipParams, bucket_room: Decimal,
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]:
    """Fractional-Kelly sizing for a dip. Returns (p_eff, G, L, f*, stake, contracts).

    G = fair − ask (gain on full revert); L = min(ask − C·floor, stop_loss) — the
    *tighter* of riding to the match-floor and the explicit cut-loss; both netted
    for Kalshi round-trip fees. p_eff = p_revert (conviction); the comparison φ
    scales the resulting stake. Capital is capped by per-position, bucket, and
    (caller-supplied) no-leverage limits.
    """
    fr = params.fee_rate
    rt_fee_pc = fr * ask * (D("1") - ask) + fr * fair * (D("1") - fair)  # smooth round-trip, per contract
    g = (fair - ask) - rt_fee_pc
    # Loss if it doesn't revert: the tighter of riding to the match-floor and the
    # explicit cut-loss. Modelling the real stop (not a ride to the floor) is the
    # risk-appetite lever — a tighter stop_loss ⇒ bigger size.
    raw_loss = ask - c_ratio * params.recover_floor
    l = min(raw_loss, params.stop_loss) + rt_fee_pc
    p_eff = params.p_revert

    z = D("0")
    if ask <= 0 or g <= 0 or p_eff <= 0:
        return p_eff, g, l, z, z, z

    l_floor = l if l > D("0.01") else D("0.01")   # near-riskless dip ⇒ position cap binds
    b = g / l_floor
    f_star = p_eff - (D("1") - p_eff) / b          # classic Kelly: p − q/b
    if f_star <= 0:
        return p_eff, g, l, z, z, z

    # φ scales the stake: the match-vs-title comparison guides size without vetoing.
    risked = params.kelly_fraction * f_star * phi * params.bankroll  # $ at risk = contracts · L
    contracts = risked / l_floor
    stake = contracts * ask
    cap = min(params.max_position_frac * params.bankroll, bucket_room)
    if stake > cap:
        stake = cap
        contracts = stake / ask if ask > 0 else z
    return p_eff, g, l, f_star, stake, contracts


def assess(
    anchor: Anchor,
    *,
    match_bid: Optional[Decimal],
    match_ask: Optional[Decimal],
    title_bid: Optional[Decimal],
    title_ask: Optional[Decimal],
    params: DipParams,
    peak_residual: Decimal = D("0"),
    bucket_room: Decimal = _BIG,
    title_ticker: str = "",
) -> Optional[DipSignal]:
    """Assess + size one player against their anchor. None if prices are unusable."""
    match_mid = _mid(match_bid, match_ask)
    title_mid = _mid(title_bid, title_ask)
    if not match_mid or not title_mid:
        return None

    fair = anchor.c_ratio * match_mid
    residual = fair - title_mid
    peak = max(peak_residual, residual)
    d_match = match_mid - anchor.match_mid
    d_title = title_mid - anchor.title_mid

    # φ: share of the title drop the match move does NOT explain. Defined only
    # when the title actually fell; clamp to [0,1] (residual can exceed |Δt| if
    # the match *rose* while the title fell — an even cleaner dislocation).
    if d_title < 0:
        phi = residual / (-d_title)
        phi = max(D("0"), min(D("1"), phi))
    else:
        phi = D("0")

    triggered = peak >= params.residual_threshold
    is_buy = (residual >= params.residual_threshold and match_mid >= params.recover_floor
              and bool(title_ask) and (title_ask or D("0")) > 0)

    p_eff = g = l = f_star = stake = contracts = D("0")
    if is_buy:
        p_eff, g, l, f_star, stake, contracts = _size_dip(
            ask=title_ask, fair=fair, c_ratio=anchor.c_ratio, phi=phi,
            params=params, bucket_room=bucket_room,
        )

    if match_mid < params.recover_floor:
        action = "STOP"
        why = (f"match {match_mid*100:.0f}¢ < floor {params.recover_floor*100:.0f}¢ — "
               "deficit looks real, low title price is now correct")
    elif is_buy:
        entry = title_ask or title_mid
        if stake > 0:
            action = "BUY DIP"
            why = (f"≈{contracts:.0f} @ {entry*100:.0f}¢ • p={p_eff*100:.0f}% "
                   f"f*={f_star*100:.0f}% (check book depth)")
        else:
            action = "WATCH"
            why = (f"{phi*100:.0f}% unexplained but EV≤0 at p_revert "
                   f"(need p higher / dip deeper) — sized to $0")
    elif triggered and residual <= params.exit_band:
        action = "REVERTED"
        why = f"back to fair (peaked {peak*100:.0f}¢ below) — exit"
    elif triggered:
        action = "REVERTING"
        why = f"recovered {(peak-residual)*100:.0f}¢ of {peak*100:.0f}¢; {residual*100:.0f}¢ to fair"
    else:
        action = "WATCH"
        why = f"title {residual*100:+.0f}¢ vs fair (need ≥ {params.residual_threshold*100:.0f}¢)"

    est_profit = D("0")
    if stake > 0 and title_ask:
        est_profit = (contracts * (fair - title_ask)
                      - trading_fee(contracts, title_ask, params.fee_rate)
                      - trading_fee(contracts, fair, params.fee_rate))

    return DipSignal(
        name=anchor.name, gender=anchor.gender, action=action, title_ticker=title_ticker,
        match_mid=match_mid, match_anchor=anchor.match_mid,
        title_ask=title_ask, title_bid=title_bid, title_mid=title_mid, fair_title=fair,
        title_anchor=anchor.title_mid, match_delta=d_match, title_delta=d_title,
        residual=residual, peak_residual=peak, overreaction_frac=phi,
        p_effective=p_eff, gain=g, loss=l, kelly_f=f_star,
        stake=stake, contracts=contracts, est_profit=est_profit, rationale=why,
    )


class DipTracker:
    """Stateful across polls: seeds per-player anchors, tracks peak over-reaction,
    and allocates the shared bucket greedily by edge.

    Feed it the paired universe each poll; it returns one signal per anchored
    player. Anchors are seeded once (favourites only) and never moved, so they
    capture the pre-dip ``C`` for the session.
    """

    def __init__(self, params: DipParams) -> None:
        self.params = params
        self.anchors: Dict[str, Anchor] = {}
        self.peak: Dict[str, Decimal] = {}

    def _name(self, *markets) -> str:
        for m in markets:
            if m and m.yes_sub_title:
                return m.yes_sub_title
        return "?"

    def update(self, universe: Universe) -> List[DipSignal]:
        # Pass 1: ensure anchors, update peaks, score everyone with full bucket room.
        scored: List[DipSignal] = []
        for gender, (match_idx, tourney_idx) in universe.items():
            for cid in match_idx.keys() & tourney_idx.keys():
                mm, tm = match_idx[cid], tourney_idx[cid]
                match_mid = _mid(mm.yes_bid, mm.yes_ask)
                title_mid = _mid(tm.yes_bid, tm.yes_ask)
                if not match_mid or not title_mid:
                    continue
                if cid not in self.anchors:
                    if match_mid < self.params.min_match_anchor:
                        continue
                    self.anchors[cid] = Anchor(
                        competitor_id=cid, name=self._name(tm, mm), gender=gender,
                        c_ratio=title_mid / match_mid,
                        match_mid=match_mid, title_mid=title_mid,
                    )
                    self.peak[cid] = D("0")
                sig = assess(
                    self.anchors[cid],
                    match_bid=mm.yes_bid, match_ask=mm.yes_ask,
                    title_bid=tm.yes_bid, title_ask=tm.yes_ask,
                    params=self.params, peak_residual=self.peak[cid],
                    title_ticker=tm.ticker,
                )
                if sig is None:
                    continue
                self.peak[cid] = sig.peak_residual
                scored.append((cid, mm, tm, sig))

        # Pass 2: allocate the shared bucket greedily by edge (gain) to BUY DIPs.
        room = self.params.max_bucket_frac * self.params.bankroll
        out: List[DipSignal] = []
        scored.sort(key=lambda it: it[3].gain, reverse=True)
        for cid, mm, tm, sig in scored:
            if sig.action == "BUY DIP" and room > 0:
                sig = assess(
                    self.anchors[cid],
                    match_bid=mm.yes_bid, match_ask=mm.yes_ask,
                    title_bid=tm.yes_bid, title_ask=tm.yes_ask,
                    params=self.params, peak_residual=self.peak[cid],
                    bucket_room=room, title_ticker=tm.ticker,
                )
                room -= sig.stake
            out.append(sig)
        return out
