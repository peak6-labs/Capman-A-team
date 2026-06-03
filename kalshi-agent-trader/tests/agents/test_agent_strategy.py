"""Pure tests for agent strategy quote/action helpers."""

from decimal import Decimal
from types import SimpleNamespace

from kalshi_agent_trader.agents.agent_strategy import (
    _agent_entry_allowed,
    _entry_quote,
)


def _market():
    return SimpleNamespace(
        yes_bid=Decimal("0.40"),
        yes_ask=Decimal("0.43"),
        no_bid=Decimal("0.57"),
        no_ask=Decimal("0.60"),
    )


def test_entry_quote_uses_bid_for_sell():
    price, opposite, spread = _entry_quote(_market(), "yes", "sell")

    assert price == Decimal("0.40")
    assert opposite == Decimal("0.43")
    assert spread == Decimal("0.03")


def test_entry_quote_uses_ask_for_buy():
    price, opposite, spread = _entry_quote(_market(), "yes", "buy")

    assert price == Decimal("0.43")
    assert opposite == Decimal("0.40")
    assert spread == Decimal("0.03")


def test_agent_entry_allows_long_horizon_when_liquid_and_tight():
    assert _agent_entry_allowed(
        price=Decimal("0.43"),
        spread=Decimal("0.03"),
        hours=20_000.0,
        volume=10_000.0,
    )


def test_agent_entry_blocks_long_horizon_when_wide():
    assert not _agent_entry_allowed(
        price=Decimal("0.43"),
        spread=Decimal("0.30"),
        hours=20_000.0,
        volume=10_000.0,
    )
