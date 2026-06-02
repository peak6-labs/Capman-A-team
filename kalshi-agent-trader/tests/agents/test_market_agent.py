"""Tests for MarketAgent: tool-use parsing, empty signals, and error handling."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from kalshi_agent_trader.agents.base import AgentError, Signal
from kalshi_agent_trader.agents.market_agent import MarketAgent
from kalshi_agent_trader.models import Event
from kalshi_agent_trader.polymarket import ReferencePrice
from kalshi_agent_trader.scanner import ScanCandidate


def _tool_use_response(signals):
    """Build a fake anthropic response with a submit_signals tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = "submit_signals"
    block.input = {"signals": signals}
    response = MagicMock()
    response.content = [block]
    response.stop_reason = "tool_use"
    return response


def _agent() -> MarketAgent:
    return MarketAgent(api_key="test-key")


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


def test_find_opportunities_parses_signals():
    agent = _agent()
    raw = [
        {"ticker": "T-1", "side": "yes", "fair_prob": 0.03, "confidence": 0.75,
         "rationale": "Longshot bias evident"}
    ]
    with patch.object(agent._client.messages, "create", return_value=_tool_use_response(raw)):
        events = [MagicMock(spec=Event, event_ticker="E-1", title="Test", category="Sports",
                            series_ticker="S-1")]
        signals = agent.find_opportunities(events)

    assert len(signals) == 1
    assert signals[0].ticker == "T-1"
    assert signals[0].fair_prob == pytest.approx(0.03)
    assert signals[0].confidence == pytest.approx(0.75)


def test_find_opportunities_empty_list_returns_empty():
    agent = _agent()
    # Should short-circuit without calling the API.
    signals = agent.find_opportunities([])
    assert signals == []


def test_find_opportunities_empty_signals_in_response():
    agent = _agent()
    with patch.object(agent._client.messages, "create", return_value=_tool_use_response([])):
        events = [MagicMock(spec=Event, event_ticker="E-1", title="T", category="Sports",
                            series_ticker=None)]
        signals = agent.find_opportunities(events)
    assert signals == []


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
    with patch.object(agent._client.messages, "create", return_value=_tool_use_response(raw)):
        signal = agent.evaluate(_candidate(), poly_ref=poly_ref)

    assert signal.confidence == pytest.approx(0.85)


def test_evaluate_fallback_on_empty_signal():
    agent = _agent()
    with patch.object(agent._client.messages, "create", return_value=_tool_use_response([])):
        signal = agent.evaluate(_candidate())

    assert signal.ticker == "WEATHER-DENVER-24"
    assert signal.confidence == 0.0  # fallback sentinel


def test_raises_agent_error_when_tool_not_called():
    block = MagicMock()
    block.type = "text"  # not tool_use
    response = MagicMock()
    response.content = [block]
    response.stop_reason = "end_turn"

    agent = _agent()
    with patch.object(agent._client.messages, "create", return_value=response):
        with pytest.raises(AgentError, match="submit_signals"):
            agent.find_opportunities(
                [MagicMock(spec=Event, event_ticker="E", title="T",
                           category="Sports", series_ticker=None)]
            )


def test_parse_signal_raises_on_missing_field():
    from kalshi_agent_trader.agents.market_agent import MarketAgent
    raw = {"ticker": "T-1", "side": "yes"}  # missing fair_prob, confidence, rationale
    with pytest.raises(AgentError):
        MarketAgent._parse_signal(raw)
