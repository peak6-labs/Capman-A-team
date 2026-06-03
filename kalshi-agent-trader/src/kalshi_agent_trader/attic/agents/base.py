"""Shared agent types.

Agents produce Signals; the deterministic compliance→risk→execution gate chain
disposes of them. No agent can bypass the gates.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Signal:
    ticker: str
    side: str           # "yes" | "no"
    fair_prob: float    # agent's estimated true probability for `side`
    confidence: float   # 0..1 — agent's self-reported confidence
    rationale: str
    source: str = "agent"


class AgentError(RuntimeError):
    """Raised when the agent returns an unparseable or invalid response."""
