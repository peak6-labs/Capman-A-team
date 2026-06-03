"""Claude-powered market agent: scanner + probability analyst.

Two modes:
  find_opportunities(events) — scans a batch of events and identifies markets where
      sub-10¢ contracts appear mispriced beyond normal longshot bias. Returns Signals.

  evaluate(candidate, poly_ref) — refines the probability estimate for a single
      candidate already identified by the systematic scanner. Returns a Signal.

Both methods share the same system prompt, enabling prompt caching across calls.
Structured output is returned via tool use so parsing is deterministic.

The agent PROPOSES. Compliance → risk → execution DISPOSES. The agent cannot
override config.yaml limits or the kill switch.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import anthropic

from ..models import Event
from ..polymarket import ReferencePrice
from ..scanner import ScanCandidate
from .base import AgentError, Signal

_MODEL = "claude-sonnet-4-6"

_SYSTEM_PROMPT = """You are a quantitative prediction market analyst specialising in identifying
mispriced tail contracts on Kalshi.

BACKGROUND
Longshot bias: sub-10¢ (sub-10%) contracts on prediction markets are systematically overpriced
because retail participants over-weight low-probability outcomes. The edge comes from *selling*
these contracts — collecting a small premium with a high win rate, accepting rare large losses.
The academic literature documents this bias across Kalshi, Polymarket, and sports books.

ALLOWED CATEGORIES (PEAK6 compliance — these are the ONLY markets you may recommend)
  - Politics
  - World
  - Climate and Weather
  - Sports

WHAT YOU LOOK FOR
1. Markets where longshot bias is strongest: niche/local events with thin books, low-volume
   markets outside major liquid series, non-US political markets, granular weather events.
2. Markets where Polymarket shows a lower probability than Kalshi — the more liquid venue
   is usually better calibrated.
3. Markets far from expiry (4–48 hours) so you collect time premium.

WHAT TO AVOID
Financials, Companies, Crypto, Economics (PEAK6 prohibited). Do NOT recommend markets
with keywords: earnings, ipo, s&p, nasdaq, merger, acquisition, stock, share price, etf.

OUTPUT FORMAT
Always use the provided tool to return structured signals. If you find no compelling
opportunities, return an empty signals list — never fabricate signals.
fair_prob must be in [0, 1]. confidence must be in [0, 1].
"""

_TOOL: Dict[str, Any] = {
    "name": "submit_signals",
    "description": "Return structured trading signals. Use an empty list when no opportunities found.",
    "input_schema": {
        "type": "object",
        "properties": {
            "signals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string"},
                        "side": {"type": "string", "enum": ["yes", "no"]},
                        "fair_prob": {"type": "number"},
                        "confidence": {"type": "number"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["ticker", "side", "fair_prob", "confidence", "rationale"],
                },
            }
        },
        "required": ["signals"],
    },
}


class MarketAgent:
    def __init__(self, api_key: str, model: str = _MODEL) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def find_opportunities(self, events: List[Event]) -> List[Signal]:
        """Scan a batch of events and return markets Claude identifies as mispriced.

        Events are pre-filtered to compliance-allowed categories before this call.
        """
        if not events:
            return []

        event_list = [
            {
                "event_ticker": e.event_ticker,
                "title": e.title or e.event_ticker,
                "category": e.category or "unknown",
                "series": e.series_ticker or "",
            }
            for e in events
        ]
        user_content = (
            "Review these open Kalshi events and identify any where sub-10¢ contracts "
            "appear mispriced beyond normal longshot bias. Focus on niche, low-liquidity, "
            "or miscalibrated markets.\n\n"
            f"Events:\n{json.dumps(event_list, indent=2)}"
        )
        return self._call(user_content)

    def evaluate(
        self, candidate: ScanCandidate, poly_ref: Optional[ReferencePrice] = None
    ) -> Signal:
        """Estimate fair probability for a single scanner-identified candidate."""
        poly_note = ""
        if poly_ref is not None:
            poly_note = (
                f"\nPolymarket reference: YES={float(poly_ref.yes_price):.1%} "
                f"(title similarity={poly_ref.similarity:.2f})"
            )

        user_content = (
            f"Evaluate this Kalshi market for the SELL-{candidate.side.upper()} strategy.\n\n"
            f"Title: {candidate.title}\n"
            f"Category: {candidate.category or 'unknown'}\n"
            f"Ticker: {candidate.ticker}\n"
            f"Side to sell: {candidate.side}\n"
            f"Kalshi {candidate.side} bid: {float(candidate.price):.1%}\n"
            f"Spread: {float(candidate.spread):.1%}\n"
            f"Hours to expiry: {candidate.hours_to_expiry:.1f}h"
            f"{poly_note}\n\n"
            f"What is the true probability of the {candidate.side.upper()} outcome? "
            f"Is this market mispriced enough to sell? Return your fair_prob for {candidate.side.upper()}, "
            "confidence, and rationale."
        )
        signals = self._call(user_content)
        if not signals:
            return Signal(
                ticker=candidate.ticker,
                side=candidate.side,
                fair_prob=float(candidate.price) * 0.88,  # fall back to heuristic
                confidence=0.0,
                rationale="Agent returned no signal; heuristic fallback used.",
            )
        return signals[0]

    # ------------------------------------------------------------------ #
    def _call(self, user_content: str) -> List[Signal]:
        """Send a request with a cached system prompt and parse the tool-use response."""
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "submit_signals"},
            messages=[{"role": "user", "content": user_content}],
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == "submit_signals":
                raw_signals = block.input.get("signals", [])
                return [self._parse_signal(s) for s in raw_signals]

        raise AgentError(f"Agent did not call submit_signals. Stop reason: {response.stop_reason}")

    @staticmethod
    def _parse_signal(raw: Dict[str, Any]) -> Signal:
        try:
            return Signal(
                ticker=str(raw["ticker"]),
                side=str(raw["side"]).lower(),
                fair_prob=float(raw["fair_prob"]),
                confidence=float(raw["confidence"]),
                rationale=str(raw["rationale"]),
            )
        except (KeyError, ValueError, TypeError) as e:
            raise AgentError(f"Malformed signal from agent: {raw}") from e
