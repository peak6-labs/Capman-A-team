"""Trade-decision + sizing layer for the French Open two-market screener.

Encodes what we established empirically/▪from the literature:

  * Match markets are ~fairly priced (well calibrated) -> the match leg carries
    no edge; it is a vehicle, not an alpha source.
  * Title-winner longshots are over-priced (favourite-longshot bias, strongest in
    later rounds / majors) -> the edge is in the TITLE leg: lay an over-priced
    longshot title (FADE = buy title NO), or, when the de-bias says a title is
    *under*-priced, buy it (gated HEDGE).
  * Title odds are sticky (martingale) -> we do not rely on a title re-rating.

The edge source is an explicit, TUNABLE favourite-longshot de-bias. It maps the
quoted title-YES price to a "fair" probability via a power-odds transform with
coefficient ``fl_alpha`` (>1 = bet favorites; <1 = bet upsets; 1.0 = agree with
the market). Calibrated via MLE on 239 settled match markets (see
scripts/calibrate_alpha.py): alpha* = 1.09 but statistically indistinguishable
from 1.0 (LR=0.3) — the match market is well calibrated. The 1.09 default is
favorites-leaning yet effectively flat (fires no trades after costs); raising it
is a conscious conviction bet BEYOND what the data supports.

Sizing is fractional-Kelly on the edged leg, capped by per-position and total
("bucket") exposure, with Kalshi fees netted out. The match leg is sized as a
configurable multiple of the title-leg stake (default 1x) to preserve the
two-market structure; it is flagged as edge-neutral.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from .tennis.fees import DEFAULT_FEE_RATE, trading_fee

D = Decimal


def debias_yes(p, alpha) -> Decimal:
    """Fair title-YES probability from the quoted price via a power-odds transform.

    fair = p^a / (p^a + (1-p)^a). a>1 pulls longshots down and favourites up
    (the favourite-longshot correction); a=1 returns p unchanged.
    """
    x = float(p)
    a = float(alpha)
    if x <= 0:
        return D("0")
    if x >= 1:
        return D("1")
    num = x ** a
    fair = num / (num + (1 - x) ** a)
    return D(str(fair))


@dataclass(frozen=True)
class StrategyParams:
    bankroll: Decimal
    fl_alpha: Decimal = D("1.09")          # MLE fit on 239 matches (favorites-side, ~flat/not signif.)
    kelly_fraction: Decimal = D("0.5")     # moderate = half-Kelly
    min_edge: Decimal = D("0.03")          # min per-contract edge after fees (3 pts)
    max_position_frac: Decimal = D("0.05") # cap on one setup's total cost
    max_bucket_frac: Decimal = D("0.20")   # cap on total French Open exposure
    match_frac: Decimal = D("1.0")         # match-leg $ as a multiple of title-leg $
    fee_rate: Decimal = DEFAULT_FEE_RATE
    allow_hedge: bool = True               # gated hedge (only when title under-priced)
    longshot_yes_cap: Decimal = D("0.40")  # only FADE titles at/under this YES price


@dataclass(frozen=True)
class Decision:
    player: str
    gender: str
    action: str                  # "FADE" | "HEDGE" | "PASS"
    fair_title_yes: Decimal
    edge: Decimal                # per-contract edge after fees on the edged leg
    title_price: Decimal         # executable price of the edged title leg
    title_stake: Decimal
    match_stake: Decimal
    total_cost: Decimal
    fees: Decimal
    max_loss: Decimal            # worst-case terminal net P&L (after fees)
    rationale: str


def _zero_decision(player, gender, fair, reason) -> Decision:
    z = D("0")
    return Decision(player, gender, "PASS", fair, z, z, z, z, z, z, z, reason)


def _scenario_min_net(action, title_stake, match_stake, title_price,
                      match_leg_price, fees) -> Decimal:
    """Worst-case terminal net P&L across outcomes (held to settlement)."""
    total = title_stake + match_stake
    q_title = title_stake / title_price if title_price else D("0")
    q_match = match_stake / match_leg_price if match_leg_price else D("0")
    if action == "FADE":
        # legs: match YES, title NO
        lose_match = q_title - total - fees          # title NO pays, match dies
        win_title = q_match - total - fees           # match pays, title NO dies
        return min(lose_match, win_title)
    # HEDGE: legs match NO, title YES
    win_match_no_title = -total - fees               # both legs worthless
    return win_match_no_title


def evaluate(
    *,
    player: str,
    gender: str,
    match_yes_ask: Decimal,
    match_no_ask: Decimal,
    title_yes_bid: Decimal,
    title_yes_ask: Decimal,
    title_no_ask: Decimal,
    params: StrategyParams,
    bucket_room: Decimal,
) -> Decision:
    """Decide FADE / HEDGE / PASS for one player and size the legs.

    `bucket_room` is the remaining total-exposure budget (dollars) for this run;
    the caller decrements it as decisions are taken.
    """
    mid = (title_yes_bid + title_yes_ask) / 2 if (title_yes_bid and title_yes_ask) \
        else (title_yes_ask or title_yes_bid)
    if not mid:
        return _zero_decision(player, gender, D("0"), "no title price")
    fair = debias_yes(mid, params.fl_alpha)
    fr = params.fee_rate

    def fee_pc(p):
        return fr * p * (D("1") - p)

    fade_edge = None
    if title_no_ask and 0 < title_no_ask < 1:
        fade_edge = (D("1") - fair) - title_no_ask - fee_pc(title_no_ask)
    hedge_edge = None
    if title_yes_ask and 0 < title_yes_ask < 1:
        hedge_edge = fair - title_yes_ask - fee_pc(title_yes_ask)

    can_fade = (fade_edge is not None and fade_edge >= params.min_edge
                and mid <= params.longshot_yes_cap)
    can_hedge = (params.allow_hedge and hedge_edge is not None
                 and hedge_edge >= params.min_edge)

    if can_fade and (not can_hedge or fade_edge >= hedge_edge):
        action, price, q, edge = "FADE", title_no_ask, D("1") - fair, fade_edge
        match_leg_price = match_yes_ask
    elif can_hedge:
        action, price, q, edge = "HEDGE", title_yes_ask, fair, hedge_edge
        match_leg_price = match_no_ask
    else:
        why = f"edge<{params.min_edge} (fade={_fmt(fade_edge)}, hedge={_fmt(hedge_edge)})"
        return _zero_decision(player, gender, fair, why)

    if not match_leg_price or match_leg_price <= 0:
        return _zero_decision(player, gender, fair, "no match price for vehicle leg")

    # Fractional-Kelly stake on the edged title leg (f* = (q - p)/(1 - p)).
    f_star = (q - price) / (D("1") - price)
    title_stake = params.kelly_fraction * f_star * params.bankroll

    # Cap on total position cost = title*(1 + match_frac).
    mf = params.match_frac
    max_pos = params.max_position_frac * params.bankroll
    title_stake = min(title_stake, max_pos / (D("1") + mf))
    if bucket_room <= 0:
        return _zero_decision(player, gender, fair, "bucket exposure cap reached")
    title_stake = min(title_stake, bucket_room / (D("1") + mf))
    if title_stake <= 0:
        return _zero_decision(player, gender, fair, "sized to zero")

    match_stake = mf * title_stake
    q_title = title_stake / price
    q_match = match_stake / match_leg_price
    fees = trading_fee(q_title, price, fr) + trading_fee(q_match, match_leg_price, fr)
    total = title_stake + match_stake
    max_loss = _scenario_min_net(action, title_stake, match_stake, price,
                                 match_leg_price, fees)

    leg = "title NO" if action == "FADE" else "title YES"
    rationale = (f"fair title {fair*100:.0f}c vs quoted {mid*100:.0f}c; "
                 f"buy {leg} edge {edge*100:.1f}pts after fees")
    return Decision(player, gender, action, fair, edge, price,
                    title_stake, match_stake, total, fees, max_loss, rationale)


def _fmt(v):
    return "—" if v is None else f"{v*100:.1f}"
