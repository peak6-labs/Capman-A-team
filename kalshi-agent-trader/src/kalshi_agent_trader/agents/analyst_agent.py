"""Analyst agent: deep, per-candidate probability refinement on the high-stakes tier.

The analyst runs on Sonnet (the model-name guard enforces this). It takes a single
candidate already surfaced by the ScoutAgent and the systematic filters, optionally with a
reference price, and returns a refined fair-probability estimate and trade decision.

If the model returns no signal, the analyst FAILS CLOSED: it returns a `watch` action
(not a fabricated trade), so the pipeline's action-change guard rejects and journals it
rather than letting a zero-confidence synthetic order reach the gate chain.

The agent PROPOSES. Compliance → risk → execution DISPOSES.
"""

from __future__ import annotations

from typing import Optional

import anthropic

from ..polymarket import ReferencePrice
from ..scanner import ScanCandidate
from .base import Signal
from ._signals import call_agent

_DEFAULT_MODEL = "claude-sonnet-4-6"

_SYSTEM_PROMPT = """You are a quantitative prediction-market analyst specialising in pricing a single
Kalshi contract precisely. A scout has already surfaced this candidate; your job is depth, not
breadth. Decide whether it is mispriced enough to act, and by how much.

BACKGROUND
Longshot bias: sub-10¢ (sub-10%) contracts are systematically overpriced because retail
over-weights low-probability outcomes. Selling collects a small premium with a high win rate but
rare large losses; buying pays off when the market underprices a concrete catalyst, misreads
resolution wording, or is stale relative to evidence. A more liquid reference venue (e.g.
Polymarket, sports books) is usually better calibrated — lean on it when provided.

ALLOWED CATEGORIES (PEAK6 compliance — the ONLY markets you may recommend)
  - Politics
  - World
  - Climate and Weather
  - Sports

WHAT TO AVOID
Financials, Companies, Crypto, Economics (PEAK6 prohibited). Avoid markets with stale books, huge
spreads, ambiguous settlement, or unmonitorable news risk — downgrade those to watch or avoid.

OUTPUT FORMAT
Always use the provided tool to return a single structured signal for the candidate. fair_prob must
be in [0, 1]; confidence must be in [0, 1]. recommended_action must be one of: buy, sell, watch,
avoid. Use buy/sell only when the edge is real and the book is tradable; otherwise return watch.
If you are not confident, say so via a low confidence and a watch action — never fabricate an edge.
"""


class AnalystAgent:
    def __init__(self, api_key: str, model: str = _DEFAULT_MODEL) -> None:
        if "sonnet" not in model.lower():
            raise ValueError("AnalystAgent is Sonnet-only; model name must contain 'sonnet'.")
        self._client = anthropic.Anthropic(
            api_key=api_key,
            base_url="https://api.anthropic.com",
        )
        self._model = model

    def evaluate(
        self,
        candidate: ScanCandidate,
        poly_ref: Optional[ReferencePrice] = None,
        *,
        action: str = "sell",
    ) -> Signal:
        """Estimate fair probability for a single scanner-identified candidate.

        On an empty model response, fails closed by returning a `watch` signal so the
        pipeline rejects it rather than placing a synthetic order.
        """
        action = action.lower()
        if action not in {"buy", "sell"}:
            action = "sell"
        poly_note = ""
        if poly_ref is not None:
            poly_note = (
                f"\nPolymarket reference: YES={float(poly_ref.yes_price):.1%} "
                f"(title similarity={poly_ref.similarity:.2f})"
            )

        user_content = (
            f"Evaluate this Kalshi market for the {action.upper()}-{candidate.side.upper()} strategy.\n\n"
            f"Title: {candidate.title}\n"
            f"Category: {candidate.category or 'unknown'}\n"
            f"Ticker: {candidate.ticker}\n"
            f"Side to trade: {candidate.side}\n"
            f"Action: {action}\n"
            f"Kalshi {candidate.side} entry price: {float(candidate.price):.1%}\n"
            f"Spread: {float(candidate.spread):.1%}\n"
            f"Hours to expiry: {candidate.hours_to_expiry:.1f}h"
            f"{poly_note}\n\n"
            f"What is the true probability of the {candidate.side.upper()} outcome? "
            f"Is this market mispriced enough to {action}? Return your fair_prob for "
            f"{candidate.side.upper()}, confidence, recommended_action, and rationale."
        )
        signals = call_agent(self._client, self._model, _SYSTEM_PROMPT, user_content)
        if not signals:
            # Fail closed: a watch action is rejected by the pipeline's action-change
            # guard rather than reaching the gate chain as a synthetic trade.
            return Signal(
                ticker=candidate.ticker,
                side=candidate.side,
                fair_prob=float(candidate.price),
                confidence=0.0,
                rationale="Agent returned no signal; failing closed to watch.",
                recommended_action="watch",
            )
        return signals[0]
