"""Tests for the Scanner: price/spread/time/volume filters and compliance rejection."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from kalshi_agent_trader.compliance import ComplianceGate
from kalshi_agent_trader.config import ComplianceConfig
from kalshi_agent_trader.market_data import MarketData
from kalshi_agent_trader.models import Market
from kalshi_agent_trader.scanner import Scanner, ScanCandidate

_COMPLIANCE_CFG = ComplianceConfig(
    allowed_categories=["Sports", "Politics"],
    prohibited_categories=["Financials"],
    default_deny_unknown=True,
    prohibited_keywords=["stock"],
)

# A UTC timestamp ~24 hours from now (epoch seconds → ISO string).
import time as _time
from datetime import datetime, timezone as _tz, timedelta as _td

_FUTURE_24H = (
    datetime.now(_tz.utc) + _td(hours=24)
).strftime("%Y-%m-%dT%H:%M:%SZ")

_FUTURE_1H = (
    datetime.now(_tz.utc) + _td(hours=1)
).strftime("%Y-%m-%dT%H:%M:%SZ")

_FUTURE_72H = (
    datetime.now(_tz.utc) + _td(hours=72)
).strftime("%Y-%m-%dT%H:%M:%SZ")


def _market(**kwargs) -> Market:
    defaults = dict(
        ticker="TICKER-1",
        event_ticker="EVENT-1",
        title="Will team A win?",
        status="active",
        yes_bid=Decimal("0.05"),
        yes_ask=Decimal("0.10"),
        no_bid=None,
        no_ask=None,
        expiration_time=_FUTURE_24H,
        volume_fp=50.0,
    )
    defaults.update(kwargs)
    return Market(**defaults)


def _scanner(market: Market, category: str = "Sports") -> tuple[Scanner, MagicMock]:
    md = MagicMock(spec=MarketData)
    md.list_markets.return_value = ([market], None)
    md.get_event.return_value = MagicMock(category=category)
    compliance = ComplianceGate(_COMPLIANCE_CFG)
    # Patch category_for_market to avoid a real network call.
    compliance.check_market = lambda m, _md: compliance.evaluate(
        category=category, title=m.title or ""
    )
    return Scanner(md, compliance), md


def test_valid_market_produces_candidate():
    m = _market()
    scanner, _ = _scanner(m, "Sports")
    results = scanner.scan()
    assert len(results) == 1
    c = results[0]
    assert c.ticker == "TICKER-1"
    assert c.side == "yes"
    assert c.price == Decimal("0.05")


def test_compliance_rejects_prohibited_category():
    m = _market()
    scanner, _ = _scanner(m, "Financials")
    assert scanner.scan() == []


def test_compliance_rejects_unknown_category():
    m = _market()
    scanner, _ = _scanner(m, "Transportation")
    assert scanner.scan() == []


def test_price_above_max_rejected():
    m = _market(yes_bid=Decimal("0.15"))  # above MAX_PRICE
    scanner, _ = _scanner(m)
    assert scanner.scan() == []


def test_price_at_zero_rejected():
    m = _market(yes_bid=Decimal("0.00"))
    scanner, _ = _scanner(m)
    assert scanner.scan() == []


def test_spread_too_wide_rejected():
    m = _market(yes_bid=Decimal("0.05"), yes_ask=Decimal("0.60"))  # spread=0.55 ≥ MAX_SPREAD
    scanner, _ = _scanner(m)
    assert scanner.scan() == []


def test_expiry_too_soon_rejected():
    m = _market(expiration_time=_FUTURE_1H)  # 1h < MIN_HOURS=4
    scanner, _ = _scanner(m)
    assert scanner.scan() == []


def test_expiry_too_far_rejected():
    m = _market(expiration_time=_FUTURE_72H)  # 72h > MAX_HOURS=48
    scanner, _ = _scanner(m)
    assert scanner.scan() == []


def test_low_volume_rejected():
    m = _market(volume_fp=5.0)  # below MIN_VOLUME_FP=10
    scanner, _ = _scanner(m)
    assert scanner.scan() == []


def test_cheaper_side_wins_when_both_qualify():
    # Both sides qualify; scanner should pick the cheaper one (no_bid < yes_bid).
    m = _market(
        yes_bid=Decimal("0.08"), yes_ask=Decimal("0.12"),
        no_bid=Decimal("0.03"), no_ask=Decimal("0.07"),
    )
    scanner, _ = _scanner(m)
    results = scanner.scan()
    assert len(results) == 1
    assert results[0].side == "no"
    assert results[0].price == Decimal("0.03")


def test_results_sorted_by_score_descending():
    m1 = _market(ticker="T1", event_ticker="E1", yes_bid=Decimal("0.02"), yes_ask=Decimal("0.05"))
    m2 = _market(ticker="T2", event_ticker="E2", yes_bid=Decimal("0.09"), yes_ask=Decimal("0.12"))
    md = MagicMock(spec=MarketData)
    md.list_markets.return_value = ([m1, m2], None)
    compliance = ComplianceGate(_COMPLIANCE_CFG)
    compliance.check_market = lambda m, _md: compliance.evaluate(
        category="Sports", title=m.title or ""
    )
    scanner = Scanner(md, compliance)
    results = scanner.scan()
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
