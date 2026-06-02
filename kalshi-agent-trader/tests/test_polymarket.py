"""Tests for PolymarketClient title matching logic."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from kalshi_agent_trader.polymarket import (
    MATCH_THRESHOLD,
    PolymarketClient,
    ReferencePrice,
    _normalize,
)


def test_normalize_strips_punctuation():
    assert _normalize("Will it rain in NYC?") == "will it rain in nyc"


def test_normalize_lowercases():
    assert _normalize("UPPER CASE") == "upper case"


def _mock_response(markets):
    resp = MagicMock()
    resp.json.return_value = markets
    resp.raise_for_status.return_value = None
    return resp


def test_returns_none_on_empty_results():
    client = PolymarketClient()
    with patch.object(client._http, "get", return_value=_mock_response([])):
        assert client.fetch_reference("Some market title") is None


def test_returns_none_below_threshold():
    # "xyz abc" has near-zero similarity to any normal title.
    markets = [{"question": "xyz abc", "outcomePrices": ["0.5", "0.5"]}]
    client = PolymarketClient()
    with patch.object(client._http, "get", return_value=_mock_response(markets)):
        result = client.fetch_reference("Will it snow in Denver this week?")
    assert result is None


def test_returns_match_above_threshold():
    title = "Will it snow in Denver this week?"
    # Very similar question — should exceed 0.50 similarity.
    markets = [{"question": "Will it snow in Denver this week", "outcomePrices": ["0.12", "0.88"]}]
    client = PolymarketClient()
    with patch.object(client._http, "get", return_value=_mock_response(markets)):
        result = client.fetch_reference(title)
    assert result is not None
    assert result.yes_price == Decimal("0.12")
    assert result.similarity >= MATCH_THRESHOLD


def test_picks_best_match():
    markets = [
        {"question": "unrelated market about something else entirely", "outcomePrices": ["0.9", "0.1"]},
        {"question": "Will the Denver Broncos win on Sunday?", "outcomePrices": ["0.3", "0.7"]},
    ]
    client = PolymarketClient()
    with patch.object(client._http, "get", return_value=_mock_response(markets)):
        result = client.fetch_reference("Will the Denver Broncos win on Sunday")
    assert result is not None
    assert result.yes_price == Decimal("0.3")


def test_returns_none_on_http_error():
    client = PolymarketClient()
    with patch.object(client._http, "get", side_effect=Exception("timeout")):
        assert client.fetch_reference("anything") is None


def test_skips_candidates_without_outcome_prices():
    markets = [{"question": "Will it rain?"}]  # no outcomePrices
    client = PolymarketClient()
    with patch.object(client._http, "get", return_value=_mock_response(markets)):
        assert client.fetch_reference("Will it rain?") is None
