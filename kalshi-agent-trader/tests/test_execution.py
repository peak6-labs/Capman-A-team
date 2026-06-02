"""Tests for the executor: gate ordering, dry-run, V2 body, and live placement."""

from decimal import Decimal

from kalshi_agent_trader.compliance import ComplianceGate
from kalshi_agent_trader.config import ComplianceConfig, RiskConfig
from kalshi_agent_trader.execution import Executor, build_v2_order_body
from kalshi_agent_trader.journal import Journal
from kalshi_agent_trader.risk import AccountState, ProposedOrder, RiskGate

COMPLIANCE = ComplianceConfig(
    allowed_categories=["Politics", "Sports"],
    prohibited_categories=["Financials"],
    default_deny_unknown=True,
    prohibited_keywords=["stock"],
)
RISK = RiskConfig(
    dry_run=True, max_total_exposure_usd=Decimal("100"), max_per_position_usd=Decimal("25"),
    max_contracts_per_order=1000, daily_loss_cap_usd=Decimal("20"),
    min_confidence=0.6, min_edge=0.05,
)


class FakeClient:
    def __init__(self):
        self.posted = []

    def post(self, path, json=None):
        self.posted.append((path, json))
        return {"order": {"order_id": "ORD-1"}}

    def delete(self, path):
        return {"order_id": path.rsplit("/", 1)[-1], "reduced_by": "5.00"}


def _order(**over):
    base = dict(ticker="KXFOO", side="yes", price=Decimal("0.50"),
                count=10, fair_prob=0.70, confidence=0.9)
    base.update(over)
    return ProposedOrder(**base)


def _account(**over):
    base = dict(balance_usd=Decimal("1000"), total_exposure_usd=Decimal("0"),
                position_exposure_usd=Decimal("0"), realized_daily_pnl_usd=Decimal("0"))
    base.update(over)
    return AccountState(**base)


def _executor(tmp_path, dry_run=True):
    client = FakeClient()
    journal = Journal(str(tmp_path / "t.db"))
    ex = Executor(client, ComplianceGate(COMPLIANCE), RiskGate(RISK), journal, dry_run=dry_run)
    return ex, client


def test_build_v2_body_yes_is_bid():
    body = build_v2_order_body(_order(side="yes", price=Decimal("0.56"), count=10), "cid")
    assert body["book_side"] == "bid"
    assert body["price_dollars"] == "0.5600"
    assert body["count"] == "10.00"
    assert body["client_order_id"] == "cid"


def test_build_v2_body_no_is_ask():
    assert build_v2_order_body(_order(side="no"), "cid")["book_side"] == "ask"


def test_compliance_blocks_before_risk(tmp_path):
    ex, client = _executor(tmp_path)
    res = ex.submit(_order(), category="Financials", title="x", account=_account())
    assert res.status == "rejected" and res.gate == "compliance"
    assert client.posted == []


def test_risk_blocks_after_compliance(tmp_path):
    ex, client = _executor(tmp_path)
    res = ex.submit(_order(confidence=0.1), category="Politics", title="ok", account=_account())
    assert res.status == "rejected" and res.gate == "risk"
    assert client.posted == []


def test_dry_run_does_not_post(tmp_path):
    ex, client = _executor(tmp_path, dry_run=True)
    res = ex.submit(_order(), category="Politics", title="ok", account=_account())
    assert res.status == "dry_run"
    assert res.order_body is not None
    assert client.posted == []


def test_live_posts_v2_and_journals(tmp_path):
    ex, client = _executor(tmp_path, dry_run=False)
    res = ex.submit(_order(), category="Sports", title="Yankees win?", account=_account())
    assert res.status == "placed"
    assert client.posted and client.posted[0][0] == "/portfolio/events/orders"
    assert res.response["order"]["order_id"] == "ORD-1"


def test_size_clamped_by_risk_then_placed(tmp_path):
    ex, client = _executor(tmp_path, dry_run=False)
    # per-position cap $25 / $0.50 = 50 max; ask for 100 -> clamp to 50.
    res = ex.submit(_order(count=100), category="Politics", title="ok", account=_account())
    assert res.status == "placed" and res.approved_count == 50
    assert client.posted[0][1]["count"] == "50.00"
