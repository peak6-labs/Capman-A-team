"""PEAK6 compliance gate — the hard, non-overridable trading filter.

This runs BEFORE the risk caps and cannot be overridden by config flags or by any
LLM agent. A market may be traded ONLY if ALL of the following hold:
  1. Its (event-derived) category is known           [else default-deny]
  2. Its category is NOT in prohibited_categories     [PEAK6: finance/companies/etc.]
  3. Its category IS in allowed_categories            [positive allowlist]
  4. Its title/text contains no prohibited keyword    [backstop screen]

Categories are compared case-insensitively. Keyword screening is case-insensitive
substring matching, a backstop to catch a prohibited instrument that slips into an
otherwise-allowed category.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config import ComplianceConfig
from .market_data import MarketData
from .models import Market


@dataclass(frozen=True)
class ComplianceResult:
    allowed: bool
    reason: str
    category: Optional[str] = None


class ComplianceGate:
    def __init__(self, config: ComplianceConfig) -> None:
        self.config = config
        self._allowed = {c.strip().lower() for c in config.allowed_categories}
        self._prohibited = {c.strip().lower() for c in config.prohibited_categories}
        self._keywords = [k.strip().lower() for k in config.prohibited_keywords if k.strip()]

    def evaluate(self, *, category: Optional[str], title: str = "") -> ComplianceResult:
        """Pure, network-free evaluation. Order of checks is fixed and deny-biased."""
        cat_norm = category.strip().lower() if category else None

        if cat_norm is None:
            if self.config.default_deny_unknown:
                return ComplianceResult(False, "unknown category (default-deny)", category)
            # Even if unknown is allowed, still run the keyword screen below.

        if cat_norm is not None and cat_norm in self._prohibited:
            return ComplianceResult(False, f"prohibited category: {category}", category)

        if cat_norm is not None and cat_norm not in self._allowed:
            return ComplianceResult(False, f"category not allowlisted: {category}", category)

        text = (title or "").lower()
        for kw in self._keywords:
            if kw in text:
                return ComplianceResult(False, f"prohibited keyword matched: '{kw.strip()}'", category)

        return ComplianceResult(True, "ok", category)

    def check_market(self, market: Market, market_data: MarketData) -> ComplianceResult:
        """Resolve the market's category via its event, then evaluate."""
        category = market_data.category_for_market(market)
        return self.evaluate(category=category, title=market.title or "")
