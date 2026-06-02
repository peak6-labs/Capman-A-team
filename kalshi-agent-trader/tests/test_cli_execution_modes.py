"""CLI-adjacent execution-mode regressions."""

from decimal import Decimal

from kalshi_agent_trader.cli import dip as dip_cli
from kalshi_agent_trader.cli import strategy as strategy_cli
from kalshi_agent_trader.config import (
    AppConfig,
    ComplianceConfig,
    RelativeValueConfig,
    RiskConfig,
    RuntimeConfig,
    SecretsConfig,
    SportsbookScrapeConfig,
    StrategyConfig,
)


def _cfg(*, dry_run: bool = True) -> AppConfig:
    return AppConfig(
        secrets=SecretsConfig(anthropic_api_key="anthropic-test"),
        compliance=ComplianceConfig(allowed_categories=["Sports"]),
        risk=RiskConfig(
            dry_run=dry_run,
            max_total_exposure_usd=Decimal("100"),
            max_per_position_usd=Decimal("25"),
            max_contracts_per_order=100,
        ),
        runtime=RuntimeConfig(),
        strategy=StrategyConfig(),
        relative_value=RelativeValueConfig(),
        sportsbook_scrape=SportsbookScrapeConfig(),
    )


def test_agent_scan_forces_dry_run_even_when_config_is_live(monkeypatch):
    seen = {}

    def fake_run_agent_strategy(cfg, *, dry_run=None, max_events=50, **_):
        seen["dry_run"] = dry_run
        seen["max_events"] = max_events
        return {
            "events_scanned": 0,
            "agent_signals": 0,
            "survivors": 0,
            "dry_run": 0,
            "rejected": 0,
        }

    monkeypatch.setattr(strategy_cli, "load_config", lambda: _cfg(dry_run=False))
    monkeypatch.setattr(strategy_cli, "_run_agent_strategy", fake_run_agent_strategy)

    strategy_cli.agent_scan_cmd(max_events=7)

    assert seen == {"dry_run": True, "max_events": 7}


def test_dip_live_alias_resolves_to_live_mode_and_requires_credentials(monkeypatch):
    class Secrets(SecretsConfig):
        calls: int = 0

        def require_kalshi(self) -> None:
            self.calls += 1

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    cfg = _cfg(dry_run=True)
    cfg.secrets = Secrets(anthropic_api_key="anthropic-test")

    monkeypatch.setattr(dip_cli, "load_config", lambda: cfg)
    monkeypatch.setattr(dip_cli, "_client", lambda: FakeClient())
    monkeypatch.setattr(dip_cli, "fetch_universe", lambda *_args, **_kwargs: {})

    dip_cli.dip(
        player=None,
        gender="both",
        bankroll=500.0,
        kelly=0.5,
        p_revert=0.70,
        stop_loss=0.06,
        threshold=0.05,
        recover_floor=0.35,
        exit_band=0.02,
        min_match_anchor=0.50,
        fee_rate=0.07,
        interval=15,
        once=True,
        execute=True,
        dry_run_override=None,
        live=True,
    )

    assert cfg.secrets.calls == 1
