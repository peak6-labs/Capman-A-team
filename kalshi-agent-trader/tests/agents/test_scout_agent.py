"""Tests for ScoutAgent: triage parsing, empty inputs, model guard, error handling."""

from unittest.mock import MagicMock, patch

import pytest

from kalshi_agent_trader.agents.base import AgentError, MarketContext
from kalshi_agent_trader.agents.scout_agent import ScoutAgent
from kalshi_agent_trader.models import Event


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


def _agent() -> ScoutAgent:
    return ScoutAgent(api_key="test-key")


def test_scout_rejects_sonnet_model():
    with pytest.raises(ValueError, match="non-Sonnet"):
        ScoutAgent(api_key="test-key", model="claude-sonnet-4-6")


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


def test_find_market_opportunities_sends_live_market_context():
    agent = _agent()
    raw = [
        {
            "ticker": "T-1",
            "side": "yes",
            "fair_prob": 0.03,
            "confidence": 0.74,
            "rationale": "Fast triage: overpriced tail",
            "recommended_action": "sell",
            "main_risk": "late injury news",
            "resolution_risk": "clear",
            "liquidity_risk": "tight spread",
            "news_dependency": "monitor team news",
        }
    ]
    ctx = MarketContext(
        ticker="T-1",
        event_ticker="E-1",
        title="Team wins by over 24.5 points?",
        category="Sports",
        series="KXTEST",
        yes_bid=0.06,
        yes_ask=0.07,
        no_bid=0.93,
        no_ask=0.94,
        yes_spread=0.01,
        no_spread=0.01,
        best_spread=0.01,
        volume_fp=120.0,
        hours_to_expiry=18.0,
    )
    with patch.object(agent._client.messages, "create", return_value=_tool_use_response(raw)) as create:
        signals = agent.find_market_opportunities([ctx])

    user_content = create.call_args.kwargs["messages"][0]["content"]
    assert "yes_bid" in user_content
    assert "0.06" in user_content
    assert signals[0].recommended_action == "sell"
    assert signals[0].main_risk == "late injury news"


def test_find_opportunities_empty_list_returns_empty():
    agent = _agent()
    # Should short-circuit without calling the API.
    signals = agent.find_opportunities([])
    assert signals == []


def test_find_market_opportunities_empty_list_returns_empty():
    agent = _agent()
    assert agent.find_market_opportunities([]) == []


def test_find_opportunities_empty_signals_in_response():
    agent = _agent()
    with patch.object(agent._client.messages, "create", return_value=_tool_use_response([])):
        events = [MagicMock(spec=Event, event_ticker="E-1", title="T", category="Sports",
                            series_ticker=None)]
        signals = agent.find_opportunities(events)
    assert signals == []


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
