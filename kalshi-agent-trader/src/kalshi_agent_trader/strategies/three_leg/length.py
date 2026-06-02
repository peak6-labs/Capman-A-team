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

_SCORE_RE = re.compile(r"(\d)\s*[-–]\s*(\d)")


@dataclass(frozen=True)
class LongWinCandidate:
    score_label: str      # "3-1"
    ticker: str
    sets_won: int         # winner's sets (3 men / 2 women)
    sets_lost: int        # 0 = sweep; the fatigue `extra_sets`
    yes_ask: Decimal
    yes_mid: Decimal
    devig_prob: Decimal   # market prob after normalising the event's outcomes


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
    md, match_market: Market, favorite_name: str,
) -> Tuple[List[LongWinCandidate], str]:
    """Favourite's win-by-score outcomes (de-vigged) + a note.

    Returns ([], note) when no exact-score market is listed for the match.
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

    fav = normalize_name(favorite_name)
    out: List[LongWinCandidate] = []
    for m in markets:
        parsed = _parse(m)
        mid = mids.get(m.ticker)
        if not parsed or mid is None or m.yes_ask is None:
            continue
        winner, a, b = parsed
        wnorm = normalize_name(winner)
        if not (fav and (fav in wnorm or wnorm in fav)):
            continue  # opponent's win-score; only used implicitly via de-vig total
        out.append(LongWinCandidate(
            score_label=f"{a}-{b}", ticker=m.ticker, sets_won=a, sets_lost=b,
            yes_ask=m.yes_ask, yes_mid=mid, devig_prob=mid / total,
        ))

    out.sort(key=lambda c: c.sets_lost)
    note = "" if out else "favourite has no priced win-scores"
    return out, note
