"""Scout agent: fast, cheap triage over batches of events / live market snapshots.

The scout runs on a low-cost model (Haiku tier). Its job is breadth, not depth: survey
many markets quickly and surface a short list worth deeper analysis. Its misses are
recoverable — the systematic filters (`_agent_entry_allowed`) and the Sonnet-tier
AnalystAgent second pass catch anything the scout gets wrong — so a cheaper model is the
right trade-off here.

The agent PROPOSES. Compliance → risk → execution DISPOSES. The agent cannot override
config.yaml limits or the kill switch.
"""

from __future__ import annotations

import json
from typing import List

import anthropic

from ..models import Event
from .base import MarketContext, Signal
from ._signals import call_agent

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_MARKETS_PER_CALL = 120

_SYSTEM_PROMPT = """You are a fast quantitative prediction-market scout for Kalshi. Your job is
breadth: triage many markets quickly and surface a short list worth deeper analysis. Work fast and
return good-enough, actionable reads — a separate analyst will refine the survivors.

BACKGROUND
Longshot bias: sub-10¢ (sub-10%) contracts on prediction markets are systematically overpriced
because retail participants over-weight low-probability outcomes. One edge comes from *selling*
these contracts — collecting a small premium with a high win rate, accepting rare large losses.
Another edge comes from *buying* contracts when the market underprices a concrete catalyst,
misreads resolution wording, or is stale relative to available evidence.

ALLOWED CATEGORIES (PEAK6 compliance — these are the ONLY markets you may recommend)
  - Politics
  - World
  - Climate and Weather
  - Sports

WHAT YOU LOOK FOR
1. Markets where longshot bias is strongest: niche/local events with thin books, low-volume
   markets outside major liquid series, non-US political markets, granular weather events.
2. Markets where a more liquid venue implies a different probability than Kalshi.
3. Tight current spreads, enough historical volume to enter/exit, and clean resolution wording.

WHAT TO AVOID
Financials, Companies, Crypto, Economics (PEAK6 prohibited). Do NOT recommend markets with
keywords: earnings, ipo, s&p, nasdaq, merger, acquisition, stock, share price, etf.
Avoid markets with stale books, huge spreads, ambiguous settlement, or unmonitorable news risk.

OUTPUT FORMAT
Always use the provided tool to return structured signals. Include only markets worth action or
close watching. If nothing is compelling, return an empty signals list — never fabricate signals.
fair_prob must be in [0, 1]. confidence must be in [0, 1].
recommended_action must be one of: buy, sell, watch, avoid. Use buy/sell only when the market is
good enough for deterministic risk gates to evaluate immediately.
"""


class ScoutAgent:
    def __init__(self, api_key: str, model: str = _DEFAULT_MODEL) -> None:
        if "sonnet" in model.lower():
            raise ValueError(
                "ScoutAgent is the cheap triage tier; use a non-Sonnet model "
                "(e.g. a Haiku model). Reserve Sonnet for the AnalystAgent."
            )
        self._client = anthropic.Anthropic(
            api_key=api_key,
            base_url="https://api.anthropic.com",
        )
        self._model = model

    def find_opportunities(self, events: List[Event]) -> List[Signal]:
        """Scan a batch of events and return markets the scout flags as mispriced.

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
            "Review these open Kalshi events and identify any where contracts appear "
            "mispriced. Focus on niche, low-liquidity, or miscalibrated markets, and "
            "return buy or sell only when the setup is actionable.\n\n"
            f"Events:\n{json.dumps(event_list, indent=2)}"
        )
        return call_agent(self._client, self._model, _SYSTEM_PROMPT, user_content)

    def find_market_opportunities(self, markets: List[MarketContext]) -> List[Signal]:
        """Scan live market snapshots and return fast triage signals."""
        if not markets:
            return []

        market_list = [m.to_prompt_dict() for m in markets[:_MAX_MARKETS_PER_CALL]]
        user_content = (
            "Review these open Kalshi market snapshots. Triage them for speedy, good-enough "
            "trading insight. Prefer markets with tight current spreads, enough volume, clean "
            "resolution, and a plausible evidence edge. For each useful market, return the side "
            "to trade, fair probability for that side, confidence, recommended_action, and concise "
            "risk notes. Use recommended_action=watch for interesting but not immediately tradable "
            "markets, avoid for semantic/liquidity traps, sell when the side is overpriced, and buy "
            "when the side is underpriced. Return no more than 12 signals.\n\n"
            f"Markets:\n{json.dumps(market_list, indent=2)}"
        )
        return call_agent(self._client, self._model, _SYSTEM_PROMPT, user_content)
