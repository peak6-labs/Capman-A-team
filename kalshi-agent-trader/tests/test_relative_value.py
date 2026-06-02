"""Tests for Kalshi-only relative-value signal generation."""

from decimal import Decimal

from kalshi_agent_trader.models import Market
from kalshi_agent_trader.relative_value.engine import RelativeValueEngine
from kalshi_agent_trader.relative_value.models import ReferenceQuote


def _market(**over) -> Market:
    base = {
        "ticker": "KXTEST",
        "event_ticker": "EVTEST",
        "title": "Will Team A win?",
        "yes_bid_dollars": "0.50",
        "yes_ask_dollars": "0.55",
        "no_bid_dollars": "0.40",
        "no_ask_dollars": "0.45",
    }
    base.update(over)
    return Market.model_validate(base)


def _quote(**over) -> ReferenceQuote:
    base = {
        "source": "polymarket",
        "question": "Will Team A win?",
        "yes_prob": Decimal("0.70"),
        "confidence": 0.90,
        "ts": 1.0,
    }
    base.update(over)
    return ReferenceQuote(**base)


def _engine() -> RelativeValueEngine:
    return RelativeValueEngine(
        min_edge=Decimal("0.05"),
        min_confidence=0.75,
        max_spread=Decimal("0.20"),
    )


def test_generates_buy_signal_when_kalshi_yes_is_undervalued():
    signals = _engine().signals_for_market(
        _market(),
        _quote(yes_prob=Decimal("0.70")),
        category="Sports",
    )
    assert len(signals) == 1
    buy_yes = [s for s in signals if s.side == "yes" and s.action == "buy"]
    assert buy_yes
    assert buy_yes[0].edge == Decimal("0.15")
    assert buy_yes[0].reference_prob == Decimal("0.70")


def test_generates_sell_signal_when_kalshi_yes_is_overvalued():
    signals = _engine().signals_for_market(
        _market(yes_bid_dollars="0.75", yes_ask_dollars="0.80"),
        _quote(yes_prob=Decimal("0.60")),
        category="Sports",
    )
    sell_yes = [s for s in signals if s.side == "yes" and s.action == "sell"]
    assert sell_yes
    assert sell_yes[0].edge == Decimal("0.15")


def test_rejects_low_confidence_reference_quote():
    signals = _engine().signals_for_market(
        _market(),
        _quote(confidence=0.50),
        category="Sports",
    )
    assert signals == []


def test_rejects_wide_kalshi_spread():
    signals = _engine().signals_for_market(
        _market(yes_bid_dollars="0.20", yes_ask_dollars="0.60"),
        _quote(yes_prob=Decimal("0.75")),
        category="Sports",
    )
    assert all(s.side != "yes" for s in signals)


def test_signal_converts_to_existing_proposed_order():
    signal = _engine().signals_for_market(
        _market(),
        _quote(yes_prob=Decimal("0.70")),
        category="Sports",
    )[0]
    order = _engine().to_order(signal, count=3)
    assert order.ticker == signal.ticker
    assert order.side == signal.side
    assert order.action == signal.action
    assert order.price == signal.kalshi_price
    assert order.count == 3
