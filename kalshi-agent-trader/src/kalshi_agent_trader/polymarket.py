"""Polymarket Gamma API client — public read-only reference prices.

Fetches the Polymarket price for a given market title to use as a cross-market
calibration signal in the brain's probability estimates. No auth required.

Matching is fuzzy: we search by the first several words of the Kalshi title, then
pick the best SequenceMatcher hit. Only matches above MATCH_THRESHOLD are trusted.
Returns None rather than a low-confidence match so the brain degrades gracefully.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

import httpx

GAMMA_BASE = "https://gamma-api.polymarket.com"
MATCH_THRESHOLD = 0.50
_SEARCH_WORDS = 6       # first N words of title used as search query
_SEARCH_LIMIT = 5       # candidates fetched per query


def _normalize(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


@dataclass
class ReferencePrice:
    """A Polymarket market that matched a Kalshi title."""
    question: str
    yes_price: Decimal
    similarity: float


class PolymarketClient:
    def __init__(self, timeout: float = 15.0) -> None:
        self._http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "PolymarketClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def fetch_reference(self, title: str) -> Optional[ReferencePrice]:
        """Search Polymarket for a market matching `title`.

        Returns the best match if its similarity score exceeds MATCH_THRESHOLD,
        otherwise None. `yes_price` is the probability of the YES outcome (0..1).
        """
        query = " ".join(title.split()[:_SEARCH_WORDS])
        try:
            resp = self._http.get(
                f"{GAMMA_BASE}/markets",
                params={"search": query, "active": "true", "limit": _SEARCH_LIMIT},
            )
            resp.raise_for_status()
            candidates: List[dict] = resp.json()
        except Exception:
            return None

        if not candidates:
            return None

        title_norm = _normalize(title)
        best: Optional[ReferencePrice] = None

        for c in candidates:
            question = c.get("question") or ""
            outcome_prices = c.get("outcomePrices")
            if not question or not outcome_prices:
                continue

            try:
                yes_price = Decimal(str(outcome_prices[0]))
            except Exception:
                continue

            sim = difflib.SequenceMatcher(
                None, title_norm, _normalize(question)
            ).ratio()

            if sim >= MATCH_THRESHOLD and (best is None or sim > best.similarity):
                best = ReferencePrice(
                    question=question,
                    yes_price=yes_price,
                    similarity=round(sim, 3),
                )

        return best
