"""Tests for the PEAK6 compliance gate.

These prove the deny-biased behavior the project depends on: prohibited categories,
default-deny of unknown/non-allowlisted categories, and the keyword backstop.
"""

from kalshi_agent_trader.compliance import ComplianceGate
from kalshi_agent_trader.config import ComplianceConfig

# Mirrors config.yaml (the real PEAK6 classification).
CFG = ComplianceConfig(
    allowed_categories=["Politics", "World", "Climate and Weather", "Sports"],
    prohibited_categories=["Financials", "Companies", "Crypto", "Economics"],
    default_deny_unknown=True,
    prohibited_keywords=["earnings", "ipo", " s&p", "nasdaq", "merger", "stock", "etf"],
)


def gate():
    return ComplianceGate(CFG)


def test_allowed_category_passes():
    r = gate().evaluate(category="Politics", title="Will candidate X win?")
    assert r.allowed, r.reason


def test_allowed_category_case_insensitive():
    assert gate().evaluate(category="cLiMaTe and WeAtHeR", title="NYC high temp").allowed


def test_prohibited_category_denied():
    for cat in ["Financials", "Companies", "Crypto", "Economics"]:
        r = gate().evaluate(category=cat, title="anything")
        assert not r.allowed
        assert "prohibited category" in r.reason


def test_unknown_category_default_denied():
    r = gate().evaluate(category=None, title="mystery market")
    assert not r.allowed
    assert "unknown category" in r.reason


def test_non_allowlisted_category_denied():
    # Elections etc. are deliberately NOT allowlisted -> default deny.
    for cat in ["Elections", "Science and Technology", "Health", "Transportation"]:
        r = gate().evaluate(category=cat, title="anything")
        assert not r.allowed
        assert "not allowlisted" in r.reason


def test_keyword_backstop_in_allowed_category():
    # A finance market that somehow lands in an allowed category is still caught.
    r = gate().evaluate(category="Politics", title="Will Nasdaq close above 20000?")
    assert not r.allowed
    assert "keyword" in r.reason


def test_keyword_backstop_examples():
    for title in [
        "Company X earnings beat",
        "Acme IPO this year",
        "Tesla stock above 500",
        "S&P 500 year-end",
    ]:
        r = gate().evaluate(category="World", title=title)
        assert not r.allowed, title


def test_clean_allowed_market_with_no_keywords():
    r = gate().evaluate(category="Sports", title="Will the Yankees win the World Series?")
    assert r.allowed, r.reason
