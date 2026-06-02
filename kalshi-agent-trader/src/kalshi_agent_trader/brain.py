"""Probability estimator and Kelly sizer for scan candidates.

Edge: longshot bias — sub-10¢ contracts are systematically overpriced on prediction
markets. We sell cheap tails and exploit this by discounting the market price toward a
lower true probability estimate, then sizing via fractional Kelly.

When a Polymarket reference price is found for the same event, we blend it into our
estimate (70% Polymarket, 30% heuristic) — Polymarket tends to be more liquid and
better-calibrated than Kalshi's niche markets.

Discount factors are conservative stubs. Calibrate against historical Kalshi resolution
data before widening them.
"""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Tuple

from .config import StrategyConfig
from .polymarket import PolymarketClient, ReferencePrice
from .risk import ProposedOrder
from .scanner import ScanCandidate

# Default sizing parameters live in StrategyConfig (config.yaml's `strategy:`
# section overrides them). These module-level names mirror the defaults for
# backward compatibility and convenient import.
_DEFAULTS = StrategyConfig()
BANKROLL = _DEFAULTS.bankroll_usd                  # dollars available to risk
MAX_KELLY = _DEFAULTS.max_kelly                    # quarter-Kelly cap
MAX_RISK_PER_POSITION = _DEFAULTS.max_risk_per_position  # hard cap per position
MIN_CONFIDENCE = _DEFAULTS.min_confidence
MAX_THESES = _DEFAULTS.max_theses

# Polymarket blend weights when a reference price is found.
_POLY_WEIGHT = 0.70
_HEURISTIC_WEIGHT = 0.30


def _heuristic_discount(price: float, volume: float) -> Tuple[float, float]:
    """Conservative longshot-bias discounts. Do not increase without backtested data."""
    if price <= 0.02:
        p_yes = price * 0.80
    elif price <= 0.05:
        p_yes = price * 0.88
    else:
        p_yes = price * 0.93

    if volume > 10:
        p_yes = min(p_yes * 1.05, price * 0.98)

    return p_yes


class Brain:
    def __init__(
        self,
        polymarket: Optional[PolymarketClient] = None,
        strategy: Optional[StrategyConfig] = None,
    ) -> None:
        self._poly = polymarket
        self._s = strategy or _DEFAULTS

    def estimate_probability(
        self, candidate: ScanCandidate
    ) -> Tuple[float, float]:
        """Return (p_yes_estimated, confidence) for the cheap side of this candidate.

        Blends Polymarket reference price when a confident match is found.
        Confidence reflects how well the longshot-bias thesis applies, not P(win).
        """
        price = float(candidate.price)

        # Base heuristic estimate.
        p_heuristic = _heuristic_discount(price, candidate.volume_fp)

        # Polymarket blend.
        ref: Optional[ReferencePrice] = None
        if self._poly is not None:
            ref = self._poly.fetch_reference(candidate.title)

        if ref is not None:
            poly_yes = float(ref.yes_price)
            # Invert for the NO side — Polymarket YES == Kalshi NO when selling the no side.
            poly_for_side = poly_yes if candidate.side == "yes" else (1.0 - poly_yes)
            p_yes = _HEURISTIC_WEIGHT * p_heuristic + _POLY_WEIGHT * poly_for_side
        else:
            p_yes = p_heuristic

        # Confidence: lower price = more edge from longshot bias; hours<8 = more uncertainty.
        confidence = min(0.90, 1.0 - price * 4)
        if candidate.hours_to_expiry < 8:
            confidence *= 0.85
        if ref is not None:
            # Slight confidence boost when Polymarket confirms our direction.
            confidence = min(0.95, confidence * (0.9 + 0.1 * ref.similarity))

        return round(p_yes, 4), round(confidence, 3)

    def kelly_fraction(self, p_yes_est: float, market_price: float) -> float:
        """Quarter-Kelly fraction for selling at market_price given our probability estimate."""
        p_no = 1.0 - p_yes_est
        b = market_price / (1.0 - market_price)
        f_star = (p_no * b - p_yes_est) / b
        if f_star <= 0:
            return 0.0
        return round(min(f_star, self._s.max_kelly), 4)

    def contract_count(self, kf: float, market_price: float) -> int:
        """Contracts to sell; bounded by Kelly and the hard per-position cap."""
        risk_per_contract = 1.0 - market_price
        if risk_per_contract <= 0:
            return 0
        kelly_capital = self._s.bankroll_usd * kf
        capped_capital = self._s.bankroll_usd * self._s.max_risk_per_position
        capital = min(kelly_capital, capped_capital)
        return max(1, int(capital / risk_per_contract))

    def propose(self, candidates: List[ScanCandidate]) -> List[ProposedOrder]:
        """Convert scan candidates into ProposedOrders for the executor.

        Applies MIN_CONFIDENCE and MAX_THESES caps. Returns the highest-confidence
        subset, which is what the risk gate and executor will see next.
        """
        proposals = []
        for c in candidates:
            p_yes, confidence = self.estimate_probability(c)
            kf = self.kelly_fraction(p_yes, float(c.price))
            if kf <= 0 or confidence < self._s.min_confidence:
                continue
            count = self.contract_count(kf, float(c.price))
            proposals.append(
                ProposedOrder(
                    ticker=c.ticker,
                    side=c.side,
                    price=c.price,
                    count=count,
                    fair_prob=p_yes,
                    confidence=confidence,
                )
            )

        proposals.sort(key=lambda o: (o.confidence, float(o.price)), reverse=True)
        return proposals[: self._s.max_theses]
