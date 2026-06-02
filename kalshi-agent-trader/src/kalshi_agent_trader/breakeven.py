"""Two-market breakeven math for a tennis hedge (pure, no I/O).

The trade pairs two correlated Kalshi markets for the same player:

  Leg 1 (match NO):  buy NO on "player wins their current match" at ask ``n``,
                     staking ``S_no``  -> Q_no = S_no / n contracts.
                     Pays $1 each if the player LOSES today.
  Leg 2 (title YES): buy YES on "player wins the whole tournament" at ask ``t0``,
                     staking ``S_t`` -> Q_t = S_t / t0 contracts.
                     Pays $1 each if the player wins the title.

Total cost C = S_no + S_t. Two outcomes:

  * Player loses the match: Leg 1 pays Q_no, Leg 2 -> $0.
    Net = Q_no - C (a locked floor, positive when the NO leg is cheap enough).
  * Player wins the match: Leg 1 -> $0; the position rides on the title leg.
    The BREAKEVEN title price the player's tournament odds must reach after
    winning today is t* = C / Q_t = t0 * C / S_t. With equal stakes, t* = 2 * t0.

All arithmetic uses Decimal; no rounding happens here (the caller formats).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

# Kalshi's standard trading fee coefficient (quadratic in price).
DEFAULT_FEE_RATE = Decimal("0.07")


def trading_fee(contracts, price, rate: Decimal = DEFAULT_FEE_RATE) -> Decimal:
    """Kalshi trading fee for a fill: ceil_to_cent(rate * C * P * (1 - P)).

    Charged on entry (and again on any early exit). Zero for degenerate prices.
    """
    c = Decimal(str(contracts))
    p = Decimal(str(price))
    if c <= 0 or p <= 0 or p >= 1:
        return Decimal("0")
    raw = rate * c * p * (Decimal("1") - p)
    return (raw * 100).to_integral_value(rounding=ROUND_CEILING) / 100


@dataclass(frozen=True)
class BreakevenInputs:
    no_ask: Decimal           # match NO ask, n (cost to bet the player loses today)
    yes_ask_tourney: Decimal  # tournament YES ask, t0
    stake_no: Decimal         # S_no
    stake_tourney: Decimal    # S_t


@dataclass(frozen=True)
class BreakevenResult:
    q_no: Decimal                    # S_no / n
    q_tourney: Decimal               # S_t / t0
    total_cost: Decimal              # C = S_no + S_t
    lose_payoff: Decimal             # Q_no (value if the player loses the match)
    lose_net: Decimal                # Q_no - C (locked floor)
    lose_profitable: bool            # lose_net > 0
    breakeven_tourney_price: Decimal  # t* = C / Q_t
    breakeven_multiple: Decimal      # t* / t0
    breakeven_feasible: bool         # t* <= 1 (an achievable price)


def compute_breakeven(inp: BreakevenInputs) -> BreakevenResult:
    """Compute the hedge breakeven. Raises ValueError on non-positive asks."""
    if inp.no_ask <= 0:
        raise ValueError("no_ask must be positive")
    if inp.yes_ask_tourney <= 0:
        raise ValueError("yes_ask_tourney must be positive")

    q_no = inp.stake_no / inp.no_ask
    q_tourney = inp.stake_tourney / inp.yes_ask_tourney
    total_cost = inp.stake_no + inp.stake_tourney

    lose_payoff = q_no
    lose_net = q_no - total_cost

    breakeven_price = total_cost / q_tourney

    return BreakevenResult(
        q_no=q_no,
        q_tourney=q_tourney,
        total_cost=total_cost,
        lose_payoff=lose_payoff,
        lose_net=lose_net,
        lose_profitable=lose_net > 0,
        breakeven_tourney_price=breakeven_price,
        breakeven_multiple=breakeven_price / inp.yes_ask_tourney,
        breakeven_feasible=breakeven_price <= Decimal("1"),
    )


# --------------------------------------------------------------------------- #
# Opposite ("fade") strategy: buy the player to WIN their match + sell (short)
# them to win the title. The legs mirror the hedge:
#
#   Leg 1 (match YES): buy YES on "wins their match" at ask ``y``, staking S_y
#                      -> Q_match = S_y / y. Pays $1 each if they WIN today.
#   Leg 2 (title NO):  sell title YES == buy title NO at ask ``q``, staking S_q
#                      -> Q_no = S_q / q. Pays $1 each if they do NOT win the title.
#
# Branches:
#   * Player loses the match: match YES -> $0; title NO pays in full (they are
#     eliminated). Net = Q_no - C. This is now the RISK branch.
#   * Player wins the match: you bank the match payout Q_match (settles today)
#     and still hold the title short. To break even when you cover, the title
#     price must stay at/below the CEILING t** = 1 - (C - Q_match) / Q_no.
#     When the match payout alone covers the cost (y <= ~0.5), the win-match
#     branch is locked profitable regardless of the title price.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FadeInputs:
    match_yes_ask: Decimal    # y (cost to bet the player wins today)
    tourney_no_ask: Decimal   # q (cost to buy title NO == sell title YES)
    stake_match: Decimal      # S_y
    stake_tourney: Decimal    # S_q


@dataclass(frozen=True)
class FadeResult:
    q_match: Decimal                     # S_y / y
    q_tourney_no: Decimal                # S_q / q
    total_cost: Decimal                  # C = S_y + S_q
    # Terminal P&L across the three possible outcomes:
    lose_match_net: Decimal              # loses match: title NO pays, match YES dies (Q_no - C)
    advance_net: Decimal                 # wins match, loses title: BOTH legs pay (Q_match + Q_no - C)
    win_title_net: Decimal               # wins match AND title: only match YES paid (Q_match - C) = max loss
    lose_match_profitable: bool          # lose_match_net > 0
    no_max_loss: bool                    # win_title_net >= 0 (no downside even if they win it all)
    # Mark-to-market breakeven for the win-match branch (kept for reference):
    breakeven_tourney_ceiling: Decimal   # t**: title must stay <= this after a win
    win_branch_locked: bool              # == no_max_loss


def compute_fade(inp: FadeInputs) -> FadeResult:
    """Compute the fade (buy match YES + buy title NO) P&L. ValueError on bad asks."""
    if inp.match_yes_ask <= 0:
        raise ValueError("match_yes_ask must be positive")
    if inp.tourney_no_ask <= 0:
        raise ValueError("tourney_no_ask must be positive")

    q_match = inp.stake_match / inp.match_yes_ask
    q_no = inp.stake_tourney / inp.tourney_no_ask
    total_cost = inp.stake_match + inp.stake_tourney

    lose_match_net = q_no - total_cost            # match YES -> 0, title NO -> $1 each
    advance_net = q_match + q_no - total_cost     # both legs pay $1 each
    win_title_net = q_match - total_cost          # title NO -> 0, only match YES paid (max loss)

    locked = win_title_net >= 0
    # win-match mark-to-market: q_match + q_no * (1 - t1) = total_cost -> t1 = 1 - (C - q_match)/q_no
    residual = total_cost - q_match
    ceiling = Decimal("1") if locked else Decimal("1") - residual / q_no

    return FadeResult(
        q_match=q_match,
        q_tourney_no=q_no,
        total_cost=total_cost,
        lose_match_net=lose_match_net,
        advance_net=advance_net,
        win_title_net=win_title_net,
        lose_match_profitable=lose_match_net > 0,
        no_max_loss=locked,
        breakeven_tourney_ceiling=ceiling,
        win_branch_locked=locked,
    )
