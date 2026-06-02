"""Select and pair French Open match + tournament markets for the same player.

Pairing is by competitor UUID, not name: the same player carries an identical
``custom_strike.tennis_competitor`` in both their match market (KXATPMATCH /
KXWTAMATCH) and their tournament-winner market (KXFOMEN / KXFOWOMEN). The
player's display name (``yes_sub_title``) is used only for display and for the
``--player`` filter.

This module performs I/O through an injected ``MarketData`` so it can be unit
tested with a fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Union

from .breakeven import (
    BreakevenInputs,
    FadeInputs,
    compute_breakeven,
    compute_fade,
)
from .market_data import MarketData
from .models import Market
from .tennis.fees import DEFAULT_FEE_RATE, trading_fee
from .tennis.pairing import (  # noqa: F401  (re-exported for two-market consumers/tests)
    MEN_MATCH,
    MEN_TOURNEY,
    WOMEN_MATCH,
    WOMEN_TOURNEY,
    Universe,
    fetch_universe,
    normalize_name,
)

# Statuses we treat as a live, tradeable match.
_OPEN_STATUSES = {"open", "active"}


@dataclass(frozen=True)
class HedgeMetrics:
    """Hedge view: buy match NO + buy title YES (breakeven = title FLOOR)."""
    match_price: Decimal       # match NO ask
    title_price: Decimal       # title YES ask
    q_match: Decimal           # match-NO contracts
    q_title: Decimal           # title-YES contracts
    fees: Decimal              # total entry fees (both legs)
    lose_net: Decimal          # net P&L if the player loses today's match (locked floor)
    lose_profitable: bool
    breakeven_price: Decimal   # t*: title must reach >= this after a win
    breakeven_feasible: bool   # t* <= 100c (achievable)


@dataclass(frozen=True)
class FadeMetrics:
    """Fade view: buy match YES + buy title NO. Shown as terminal-outcome P&L."""
    match_price: Decimal       # match YES ask
    title_no_price: Decimal    # title NO ask (the leg you buy)
    title_yes_price: Decimal   # title YES ask (reference)
    q_match: Decimal           # match-YES contracts
    q_title_no: Decimal        # title-NO contracts
    fees: Decimal              # total entry fees (both legs)
    lose_match_net: Decimal    # loses match (easy case): match YES dies, title NO pays
    advance_net: Decimal       # wins match, no title (best): both legs pay
    win_title_net: Decimal     # wins match AND title (max loss): only match YES pays
    no_max_loss: bool          # win_title_net >= 0


Metrics = Union[HedgeMetrics, FadeMetrics]


@dataclass(frozen=True)
class PlayerRow:
    competitor_id: Optional[str]
    name: str
    gender: str                        # "men" | "women"
    match_market: Optional[Market]
    tourney_market: Optional[Market]
    result: Optional[Metrics]
    note: str = ""


def _side_price(market: Market, side: str, basis: str) -> Optional[Decimal]:
    """Executable ('ask') or informational ('mid') price for a side."""
    if side == "no":
        bid, ask = market.no_bid, market.no_ask
    else:
        bid, ask = market.yes_bid, market.yes_ask
    if basis == "mid":
        if bid is None or ask is None:
            return None
        return (bid + ask) / Decimal("2")
    return ask


def _display_name(match: Optional[Market], tourney: Optional[Market]) -> str:
    for m in (tourney, match):
        if m and m.yes_sub_title:
            return m.yes_sub_title
    return "?"


def rows_from_universe(
    universe: Universe,
    *,
    players: Optional[List[str]],
    stake_no: Decimal,
    stake_tourney: Decimal,
    price_basis: str = "ask",
    strategy: str = "hedge",           # "hedge" | "fade"
    fee_rate: Decimal = DEFAULT_FEE_RATE,
) -> List[PlayerRow]:
    """Pair + price every player in an already-fetched universe (no I/O)."""
    rows: List[PlayerRow] = []
    for g, (match_idx, tourney_idx) in universe.items():
        for cid in match_idx.keys() | tourney_idx.keys():
            rows.append(
                _make_row(
                    cid, g, match_idx.get(cid), tourney_idx.get(cid),
                    stake_match=stake_no, stake_tourney=stake_tourney,
                    basis=price_basis, strategy=strategy, fee_rate=fee_rate,
                )
            )

    if players:
        queries = [normalize_name(p) for p in players if normalize_name(p)]
        rows = [r for r in rows if any(q in normalize_name(r.name) for q in queries)]

    return rows


def build_player_rows(
    md: MarketData,
    *,
    gender: str,                       # "men" | "women" | "both"
    players: Optional[List[str]],
    stake_no: Decimal,
    stake_tourney: Decimal,
    price_basis: str = "ask",
    strategy: str = "hedge",           # "hedge" | "fade"
    fee_rate: Decimal = DEFAULT_FEE_RATE,
) -> List[PlayerRow]:
    """Fetch, pair, and price every player in the requested universe."""
    universe = fetch_universe(md, gender)
    return rows_from_universe(
        universe, players=players, stake_no=stake_no, stake_tourney=stake_tourney,
        price_basis=price_basis, strategy=strategy, fee_rate=fee_rate,
    )


def _make_row(
    cid: str,
    gender: str,
    match: Optional[Market],
    tourney: Optional[Market],
    *,
    stake_match: Decimal,
    stake_tourney: Decimal,
    basis: str,
    strategy: str,
    fee_rate: Decimal = DEFAULT_FEE_RATE,
) -> PlayerRow:
    name = _display_name(match, tourney)
    base = dict(competitor_id=cid, name=name, gender=gender,
                match_market=match, tourney_market=tourney, result=None)

    if match is None:
        return PlayerRow(**base, note="no match today")
    if tourney is None:
        return PlayerRow(**base, note="not in title market")
    if match.status and match.status.lower() not in _OPEN_STATUSES:
        return PlayerRow(**base, note="match closed/settled")

    t0 = _side_price(tourney, "yes", basis)   # title YES, shown as reference
    builder = _fade_metrics if strategy == "fade" else _hedge_metrics
    metrics = builder(match, tourney, t0, stake_match, stake_tourney, basis, fee_rate)
    if metrics is None:
        return PlayerRow(**base, note="no ask / illiquid")
    return PlayerRow(**{**base, "result": metrics}, note="")


def _hedge_metrics(match, tourney, t0, stake_match, stake_tourney, basis, fee_rate):
    n = _side_price(match, "no", basis)       # buy match NO (player loses today)
    if not n or n <= 0 or not t0 or t0 <= 0:
        return None
    try:
        res = compute_breakeven(BreakevenInputs(
            no_ask=n, yes_ask_tourney=t0,
            stake_no=stake_match, stake_tourney=stake_tourney,
        ))
    except ValueError:
        return None
    fees = trading_fee(res.q_no, n, fee_rate) + trading_fee(res.q_tourney, t0, fee_rate)
    return HedgeMetrics(
        match_price=n, title_price=t0,
        q_match=res.q_no, q_title=res.q_tourney, fees=fees,
        lose_net=res.lose_net, lose_profitable=res.lose_profitable,
        breakeven_price=res.breakeven_tourney_price,
        breakeven_feasible=res.breakeven_feasible,
    )


def _fade_metrics(match, tourney, t0, stake_match, stake_tourney, basis, fee_rate):
    y = _side_price(match, "yes", basis)      # buy match YES (player wins today)
    q = _side_price(tourney, "no", basis)     # buy title NO (sell title YES)
    if not y or y <= 0 or not q or q <= 0:
        return None
    try:
        res = compute_fade(FadeInputs(
            match_yes_ask=y, tourney_no_ask=q,
            stake_match=stake_match, stake_tourney=stake_tourney,
        ))
    except ValueError:
        return None
    fees = trading_fee(res.q_match, y, fee_rate) + trading_fee(res.q_tourney_no, q, fee_rate)
    return FadeMetrics(
        match_price=y, title_no_price=q, title_yes_price=t0 or (Decimal("1") - q),
        q_match=res.q_match, q_title_no=res.q_tourney_no, fees=fees,
        lose_match_net=res.lose_match_net,
        advance_net=res.advance_net,
        win_title_net=res.win_title_net,
        no_max_loss=res.no_max_loss,
    )
