"""Match↔title competitor pairing + universe fetch (shared tennis domain).

Both strategies pair a player's current-match market with their tournament-winner
market by the stable ``custom_strike.tennis_competitor`` UUID (names are display
only). Moved out of two_market's tennis_screen.py so the dip strategy fetches the
same universe without importing strategy 1.
"""

from __future__ import annotations

import unicodedata
from typing import Dict, List, Optional

from ..market_data import MarketData
from ..models import Market

MEN_MATCH = "KXATPMATCH"
WOMEN_MATCH = "KXWTAMATCH"
MEN_TOURNEY = "KXFOMEN"
WOMEN_TOURNEY = "KXFOWOMEN"


def normalize_name(s: str) -> str:
    """Lowercase, strip accents, and collapse whitespace/punctuation."""
    if not s:
        return ""
    decomposed = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = "".join(c if c.isalnum() else " " for c in stripped.lower())
    return " ".join(cleaned.split())


def competitor_id(market: Market) -> Optional[str]:
    """Stable per-player UUID, or None if the market is not player-structured."""
    if not market.custom_strike:
        return None
    cid = market.custom_strike.get("tennis_competitor")
    return str(cid) if cid else None


def index_by_competitor(markets: List[Market]) -> Dict[str, Market]:
    """Map competitor UUID -> market, skipping markets without one."""
    out: Dict[str, Market] = {}
    for m in markets:
        cid = competitor_id(m)
        if cid:
            out[cid] = m
    return out


def fetch_series_markets(
    md: MarketData, series_ticker: str, status: str = "open"
) -> List[Market]:
    """List all markets in a series, following pagination to exhaustion."""
    out: List[Market] = []
    cursor: Optional[str] = None
    while True:
        markets, cursor = md.list_markets(
            status=status, series_ticker=series_ticker, limit=100, cursor=cursor
        )
        out.extend(markets)
        if not cursor:
            break
    return out


# A fetched universe: gender -> (match_index, tourney_index), each keyed by UUID.
Universe = Dict[str, "tuple[Dict[str, Market], Dict[str, Market]]"]


def fetch_universe(md: MarketData, gender: str) -> Universe:
    """Fetch + index match and tournament markets once for the chosen gender(s)."""
    genders = ("men", "women") if gender == "both" else (gender,)
    out: Universe = {}
    for g in genders:
        match_series = MEN_MATCH if g == "men" else WOMEN_MATCH
        tourney_series = MEN_TOURNEY if g == "men" else WOMEN_TOURNEY
        out[g] = (
            index_by_competitor(fetch_series_markets(md, match_series)),
            index_by_competitor(fetch_series_markets(md, tourney_series)),
        )
    return out
