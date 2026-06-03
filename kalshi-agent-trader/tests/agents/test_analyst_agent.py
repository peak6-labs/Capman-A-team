"""Tests for AnalystAgent (evaluate, Sonnet guard, fail-closed fallback) and the
shared signal parser in agents/_signals.py."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from kalshi_agent_trader.agents._signals import parse_signal
from kalshi_agent_trader.agents.analyst_agent import AnalystAgent
from kalshi_agent_trader.agents.base import AgentError
from kalshi_agent_trader.polymarket import ReferencePrice
from kalshi_agent_trader.scanner import ScanCandidate


def _tool_use_response(signals):
    block = MagicMock()
    block.type = "tool_use"
    block.name = "submit_signals"
    block.input = {"signals": signals}
    response = MagicMock()
    response.content = [block]
    response.stop_reason = "tool_use"
    return response


def _agent() -> AnalystAgent:
    return AnalystAgent(api_key="test-key")


def _candidate() -> ScanCandidate:
    return ScanCandidate(
        ticker="WEATHER-DENVER-24",
        title="Will it snow in Denver this week?",
        category="Climate and Weather",
        side="yes",
        price=Decimal("0.05"),
        spread=Decimal("0.05"),
        hours_to_expiry=20.0,
        volume_fp=30.0,
        score=1.0,
    )


def test_analyst_is_sonnet_only():
    with pytest.raises(ValueError, match="Sonnet-only"):
        AnalystAgent(api_key="test-key", model="claude-haiku-4-5-20251001")


def test_evaluate_returns_signal():
    agent = _agent()
    raw = [{"ticker": "WEATHER-DENVER-24", "side": "yes", "fair_prob": 0.03,
            "confidence": 0.80, "rationale": "Mispriced tail"}]
    with patch.object(agent._client.messages, "create", return_value=_tool_use_response(raw)):
        signal = agent.evaluate(_candidate())

    assert signal.ticker == "WEATHER-DENVER-24"
    assert signal.fair_prob == pytest.approx(0.03)


def test_evaluate_with_polymarket_ref():
    agent = _agent()
    raw = [{"ticker": "WEATHER-DENVER-24", "side": "yes", "fair_prob": 0.025,
            "confidence": 0.85, "rationale": "Poly confirms overpricing"}]
    poly_ref = ReferencePrice(
        question="Will it snow in Denver?", yes_price=Decimal("0.04"), similarity=0.82
    )
    with patch.object(agent._client.messages, "create", return_value=_tool_use_response(raw)) as create:
        signal = agent.evaluate(_candidate(), poly_ref=poly_ref)

    user_content = create.call_args.kwargs["messages"][0]["content"]
    assert "Polymarket reference" in user_content
    assert signal.confidence == pytest.approx(0.85)


def test_evaluate_fails_closed_on_empty_signal():
    """Empty model response must yield a non-trade `watch` action, not a synthetic sell."""
    agent = _agent()
    with patch.object(agent._client.messages, "create", return_value=_tool_use_response([])):
        signal = agent.evaluate(_candidate())

    assert signal.ticker == "WEATHER-DENVER-24"
    assert signal.confidence == 0.0
    assert signal.recommended_action == "watch"


def test_raises_agent_error_when_tool_not_called():
    block = MagicMock()
    block.type = "text"
    response = MagicMock()
    response.content = [block]
    response.stop_reason = "end_turn"

    agent = _agent()
    with patch.object(agent._client.messages, "create", return_value=response):
        with pytest.raises(AgentError, match="submit_signals"):
            agent.evaluate(_candidate())


# --- shared parser (agents/_signals.py) -------------------------------------- #

def test_parse_signal_raises_on_missing_field():
    raw = {"ticker": "T-1", "side": "yes"}  # missing fair_prob, confidence, rationale
    with pytest.raises(AgentError):
        parse_signal(raw)


def test_parse_signal_invalid_action_becomes_avoid():
    raw = {
        "ticker": "T-1",
        "side": "yes",
        "fair_prob": 0.03,
        "confidence": 0.8,
        "rationale": "bad action",
        "recommended_action": "hold",
    }
    assert parse_signal(raw).recommended_action == "avoid"
