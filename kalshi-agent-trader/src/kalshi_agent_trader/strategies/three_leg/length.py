"""Discover the leg-3 "length" instrument for a match and de-vig its outcomes.

The exact-match-score market for a QF lives under a sibling series with the same
event suffix: ``KXATPMATCH-<sfx>`` ⇒ ``KXATPEXACTMATCH-<sfx>`` (men),
``KXWTAMATCH-<sfx>`` ⇒ ``KXWTAEXACTMATCH-<sfx>`` (women). Each leg is one exact
score ("X wins 3-1"); the winner + set score are parsed from ``yes_sub_title``.

We return the BACKED favourite's win-by-score outcomes (sweep included, for P&L
and de-vigging). The "long" ones (sets_lost ≥ 1) become the hedge legs. When the
sibling market isn't listed (common for women's matches until close to play),
we return no candidates and a note — the caller degrades to a two-leg plan.

I/O is through an injected ``MarketData`` so this is unit-testable with a fake.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Tuple

from ...models import Market
from ...tennis_screen import normalize_name

D = Decimal

EXACT_SERIES = {  # match series prefix -> exact-score series prefix
    "KXATPMATCH": "KXATPEXACTMATCH",
    "KXWTAMATCH": "KXWTAEXACTMATCH",
}

SET_SERIES = {  # match series prefix -> per-set-winner series prefix
    "KXATPMATCH": "KXATPSETWINNER",
    "KXWTAMATCH": "KXWTASETWINNER",
}

_SCORE_RE = re.compile(r"(\d)\s*[-–]\s*(\d)")


@dataclass(frozen=True)
class LongWinCandidate:
    score_label: str      # "3-1"
    ticker: str
    sets_won: int         # winner's sets (3 men / 2 women)
    sets_lost: int        # 0 = sweep; the fatigue `extra_sets` (loser's set count)
    yes_ask: Decimal
    yes_mid: Decimal
    devig_prob: Decimal   # market prob after normalising the event's outcomes
    winner_is_match: bool = True  # True ⇒ the MATCH-leg player wins this score


def _mid(m: Market) -> Optional[Decimal]:
    if m.yes_bid is not None and m.yes_ask is not None:
        return (m.yes_bid + m.yes_ask) / D("2")
    return m.yes_ask


def exact_event_for(match_market: Market) -> Optional[str]:
    """The exact-score event ticker for a match market, or None if unmappable."""
    et = match_market.event_ticker or ""
    for match_pfx, exact_pfx in EXACT_SERIES.items():
        if et.startswith(match_pfx + "-"):
            return exact_pfx + et[len(match_pfx):]
    return None


def set_event_for(match_market: Market, set_no: int) -> Optional[str]:
    """The per-set-winner event ticker for a match + set number (e.g. ...-1, ...-2)."""
    et = match_market.event_ticker or ""
    for match_pfx, set_pfx in SET_SERIES.items():
        if et.startswith(match_pfx + "-"):
            return f"{set_pfx}{et[len(match_pfx):]}-{set_no}"
    return None


@dataclass(frozen=True)
class SetHedgeCandidate:
    """Opponent's per-set YES leg — pays when the favourite is dragged to extra sets."""
    set_no: int           # 1 or 2
    ticker: str           # the OPPONENT's set-winner market
    opp_name: str
    yes_ask: Decimal      # ask to buy the opponent's set YES
    fav_set_prob: Decimal  # de-vigged P(favourite wins this set)


def discover_set_hedge(
    md, match_market: Market, favorite_name: str, *, max_sets: int = 2,
) -> Tuple[List[SetHedgeCandidate], str]:
    """Best-of-3 length proxy: the OPPONENT winning a set ⇒ a 3-set (drained) match.

    Returns the opponent's Set-1 and Set-2 YES legs (de-vigged per set) + a note.
    Empty when no set-winner market is listed for the match.
    """
    fav = normalize_name(favorite_name)
    out: List[SetHedgeCandidate] = []
    for set_no in range(1, max_sets + 1):
        event = set_event_for(match_market, set_no)
        if not event:
            return [], "no set-winner series for this match"
        markets, _ = md.list_markets(status="open", event_ticker=event, limit=20)
        if not markets:
            continue
        priced = {m.ticker: _mid(m) for m in markets}
        total = sum((v for v in priced.values() if v and v > 0), D("0"))
        if total <= 0:
            continue
        fav_mkt = next(
            (m for m in markets
             if fav and (fav in normalize_name(m.yes_sub_title or "")
                         or normalize_name(m.yes_sub_title or "") in fav)), None)
        opp_mkt = next((m for m in markets if m is not fav_mkt), None)
        fav_mid = priced.get(fav_mkt.ticker) if fav_mkt else None
        if not opp_mkt or opp_mkt.yes_ask is None or fav_mid is None:
            continue
        out.append(SetHedgeCandidate(
            set_no=set_no, ticker=opp_mkt.ticker,
            opp_name=opp_mkt.yes_sub_title or "opp", yes_ask=opp_mkt.yes_ask,
            fav_set_prob=fav_mid / total,
        ))
    note = "" if out else "set-winner market not listed yet"
    return out, note


def _parse(market: Market) -> Optional[Tuple[str, int, int]]:
    """(winner_name, sets_won, sets_lost) from 'Winner Name wins A-B', else None."""
    sub = market.yes_sub_title or ""
    m = _SCORE_RE.search(sub)
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    winner = re.split(r"\bwins\b", sub, flags=re.IGNORECASE)[0].strip()
    return winner, a, b


def discover(
    md, match_market: Market, match_player_name: str,
) -> Tuple[List[LongWinCandidate], str]:
    """Every exact-score outcome (BOTH players), de-vigged, tagged by winner + a note.

    Tagged relative to the MATCH-leg player (the one we back to win the match): the
    "out" is the OTHER (title-leg) player winning in 5 sets, so the caller selects
    ``not winner_is_match and sets_lost == 2``. Returns ([], note) when no
    exact-score market is listed for the match.
    """
    exact_event = exact_event_for(match_market)
    if not exact_event:
        return [], "no exact-score series for this match"

    markets, _ = md.list_markets(status="open", event_ticker=exact_event, limit=100)
    if not markets:
        return [], "exact-score market not listed yet"

    mids = {m.ticker: _mid(m) for m in markets}
    total = sum((v for v in mids.values() if v and v > 0), D("0"))
    if total <= 0:
        return [], "exact-score book empty/illiquid"

    mp = normalize_name(match_player_name)
    out: List[LongWinCandidate] = []
    for m in markets:
        parsed = _parse(m)
        mid = mids.get(m.ticker)
        if not parsed or mid is None or m.yes_ask is None:
            continue
        winner, a, b = parsed
        wnorm = normalize_name(winner)
        is_match = bool(mp and (mp in wnorm or wnorm in mp))
        out.append(LongWinCandidate(
            score_label=f"{a}-{b}", ticker=m.ticker, sets_won=a, sets_lost=b,
            yes_ask=m.yes_ask, yes_mid=mid, devig_prob=mid / total,
            winner_is_match=is_match,
        ))

    out.sort(key=lambda c: (not c.winner_is_match, c.sets_lost))  # match player first, then by length
    note = "" if out else "no priced exact scores"
    return out, note
