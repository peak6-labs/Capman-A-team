"""Shared signal plumbing for the Claude agents.

Both ScoutAgent (triage) and AnalystAgent (evaluation) return structured signals via
the same `submit_signals` tool, so the tool schema, the response parser, and the
cached-system-prompt request helper live here to avoid duplication.

Structured output is returned via tool use so parsing is deterministic. Each agent
supplies its own system prompt; the prompt is cached (ephemeral) per agent.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .base import AgentError, Signal

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
                        "recommended_action": {
                            "type": "string",
                            "enum": ["buy", "sell", "watch", "avoid"],
                        },
                        "main_risk": {"type": "string"},
                        "resolution_risk": {"type": "string"},
                        "liquidity_risk": {"type": "string"},
                        "news_dependency": {"type": "string"},
                    },
                    "required": ["ticker", "side", "fair_prob", "confidence", "rationale"],
                },
            }
        },
        "required": ["signals"],
    },
}


def call_agent(client, model: str, system_prompt: str, user_content: str) -> List[Signal]:
    """Send a request with a cached system prompt and parse the tool-use response.

    `client` is an `anthropic.Anthropic` instance. The system prompt is cached
    (ephemeral) so repeated calls within a cycle reuse it.
    """
    response = client.messages.create(
        model=model,
        max_tokens=900,
        system=[
            {
                "type": "text",
                "text": system_prompt,
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
            return [parse_signal(s) for s in raw_signals]

    raise AgentError(f"Agent did not call submit_signals. Stop reason: {response.stop_reason}")


def parse_signal(raw: Dict[str, Any]) -> Signal:
    try:
        action = str(raw.get("recommended_action") or "sell").lower()
        if action not in {"buy", "sell", "watch", "avoid"}:
            action = "avoid"
        return Signal(
            ticker=str(raw["ticker"]),
            side=str(raw["side"]).lower(),
            fair_prob=float(raw["fair_prob"]),
            confidence=float(raw["confidence"]),
            rationale=str(raw["rationale"]),
            recommended_action=action,
            main_risk=str(raw.get("main_risk") or ""),
            resolution_risk=str(raw.get("resolution_risk") or ""),
            liquidity_risk=str(raw.get("liquidity_risk") or ""),
            news_dependency=str(raw.get("news_dependency") or ""),
        )
    except (KeyError, ValueError, TypeError) as e:
        raise AgentError(f"Malformed signal from agent: {raw}") from e
