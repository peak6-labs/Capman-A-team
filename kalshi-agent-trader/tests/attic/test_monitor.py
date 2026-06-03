"""Tests for Monitor exit trigger logic."""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from kalshi_agent_trader.monitor import ExitReason, Monitor


def _pos(
    *,
    ticker="T-1",
    side="yes",
    entry_price="0.08",
    target_price="0.012",  # 15% of 0.08
    expiry_hours=24.0,
    opened_hours_ago=1.0,
    count=1,
    action="sell",
):
    now = datetime.now(timezone.utc)
    opened_ts = int((now - timedelta(hours=opened_hours_ago)).timestamp() * 1000)
    expiry = (now + timedelta(hours=expiry_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": 1,
        "ticker": ticker,
        "side": side,
        "action": action,
        "entry_price": entry_price,
        "target_price": target_price,
        "expiry": expiry,
        "opened_ts": opened_ts,
        "count": count,
        "order_id": None,
    }


def _market(bid="0.05", ask="0.10", status="active"):
    m = MagicMock()
    m.status = status
    m.yes_bid = Decimal(bid)
    m.yes_ask = Decimal(ask)
    m.no_bid = Decimal(bid)
    m.no_ask = Decimal(ask)
    return m


def _monitor(market=None, live=False):
    md = MagicMock()
    md.get_market.return_value = market or _market()
    client = MagicMock()
    journal = MagicMock()
    journal.open_positions.return_value = []
    return Monitor(md, client, journal, live=live)


def test_target_hit():
    pos = _pos(entry_price="0.08", target_price="0.012")
    monitor = _monitor(_market(bid="0.01"))  # bid(0.01) <= target(0.012)
    assert monitor.check_one(pos) == ExitReason.TARGET_HIT


def test_no_trigger_when_bid_above_target():
    pos = _pos(entry_price="0.08", target_price="0.012")
    monitor = _monitor(_market(bid="0.05"))  # bid(0.05) > target(0.012)
    assert monitor.check_one(pos) is None


def test_near_expiry():
    pos = _pos(expiry_hours=1.0)   # 1h < NEAR_EXPIRY_H=2.0
    monitor = _monitor(_market(bid="0.05"))
    assert monitor.check_one(pos) == ExitReason.NEAR_EXPIRY


def test_stale_thesis():
    # Held 25 hours with no price move.
    pos = _pos(entry_price="0.05", target_price="0.0075", opened_hours_ago=25.0, expiry_hours=10.0)
    # Market bid same as entry (0% move).
    monitor = _monitor(_market(bid="0.05"))
    assert monitor.check_one(pos) == ExitReason.STALE_THESIS


def test_not_stale_when_significant_move():
    pos = _pos(entry_price="0.05", target_price="0.0075", opened_hours_ago=25.0, expiry_hours=10.0)
    # 10% move > STALE_MOVE_PCT=2%.
    monitor = _monitor(_market(bid="0.055"))
    assert monitor.check_one(pos) is None


def test_resolved_when_market_not_active():
    pos = _pos()
    monitor = _monitor(_market(status="settled"))
    assert monitor.check_one(pos) == ExitReason.RESOLVED


def test_resolved_on_api_error():
    md = MagicMock()
    from kalshi_agent_trader.client import KalshiError
    md.get_market.side_effect = KalshiError(404, "not found")
    monitor = Monitor(md, MagicMock(), MagicMock(), live=False)
    assert monitor.check_one(_pos()) == ExitReason.RESOLVED


def test_run_once_empty_positions():
    monitor = _monitor()
    monitor._journal.open_positions.return_value = []
    results = monitor.run_once()
    assert results == []


def test_live_close_keeps_position_open_until_fill():
    pos = _pos()
    monitor = _monitor(_market(bid="0.01"), live=True)
    monitor._journal.open_positions.return_value = [pos]
    monitor._client.post.return_value = {"order": {"order_id": "CLOSE-1", "status": "resting"}}

    results = monitor.run_once()

    assert results[0][1] == ExitReason.TARGET_HIT
    monitor._journal.record_order.assert_called_once()
    monitor._journal.close_position.assert_not_called()


def test_live_close_marks_position_closed_on_execution():
    pos = _pos()
    monitor = _monitor(_market(bid="0.01"), live=True)
    monitor._journal.open_positions.return_value = [pos]
    monitor._client.post.return_value = {"order": {"order_id": "CLOSE-1", "status": "executed"}}

    monitor.run_once()

    monitor._journal.close_position.assert_called_once_with(pos["id"], ExitReason.TARGET_HIT.value)
