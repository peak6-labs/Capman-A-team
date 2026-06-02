"""Systematic, relative-value, monitor, and agent strategy commands."""

from __future__ import annotations

from typing import Optional

import typer
from rich.table import Table

from .. import pipeline as _systematic
from ..agents.agent_strategy import run_agent_strategy as _run_agent_strategy
from ..client import KalshiClient
from ..config import load_config
from ..journal import Journal
from ..market_data import MarketData
from ..monitor import Monitor
from ..polymarket import PolymarketClient
from ..scanner import Scanner
from ..brain import Brain
from ..compliance import ComplianceGate
from .common import _fmt_cents, console, resolve_dry_run

app = typer.Typer()


@app.command(name="scan")
def scan_cmd(
    pages: int = typer.Option(20, help="Max pages to paginate (200 markets/page)."),
) -> None:
    """Scan open markets for cheap-tail candidates (public + auth for compliance)."""
    cfg = load_config()
    with KalshiClient(cfg) as client, PolymarketClient(
        timeout=cfg.runtime.request_timeout_s,
        verify_ssl=cfg.runtime.verify_ssl,
    ) as poly:
        md = MarketData(client)
        compliance = ComplianceGate(cfg.compliance)
        scanner = Scanner(md, compliance)
        brain = Brain(poly)
        candidates = scanner.scan(max_pages=pages)

        if not candidates:
            console.print("[yellow]No candidates found.[/yellow]")
            return

        table = Table(title=f"Scan results ({len(candidates)} candidates)")
        for col in ("ticker", "side", "price", "spread", "hours", "poly_ref", "score"):
            table.add_column(col, overflow="fold")

        for c in candidates:
            ref = poly.fetch_reference(c.title)
            table.add_row(
                c.ticker, c.side,
                _fmt_cents(c.price), _fmt_cents(c.spread),
                f"{c.hours_to_expiry:.1f}h",
                f"{float(ref.yes_price):.0%} (sim={ref.similarity:.2f})" if ref else "—",
                str(c.score),
            )
        console.print(table)


@app.command(name="run")
def run_cmd(
    dry_run_override: Optional[bool] = typer.Option(
        None,
        "--dry-run/--no-dry-run",
        help="Override config risk.dry_run for this run.",
    ),
    live: bool = typer.Option(False, "--live", help="Alias for --no-dry-run."),
) -> None:
    """Run one full scan -> brain -> execute cycle."""
    cfg = load_config()
    dry_run = resolve_dry_run(cfg.risk.dry_run, live=live, dry_run_override=dry_run_override)
    if not dry_run:
        cfg.secrets.require_kalshi()
    counts = _systematic.run(cfg, dry_run=dry_run)
    console.print(
        f"[bold]Cycle complete[/bold] — "
        f"scanned {counts['scanned']}, "
        f"proposed {counts['proposed']}, "
        f"placed {counts['placed']}, "
        f"dry_run {counts['dry_run']}, "
        f"rejected {counts['rejected']}"
    )


@app.command(name="rv-scan")
def rv_scan_cmd() -> None:
    """Find Kalshi relative-value signals from external reference prices; place nothing."""
    from ..relative_value.pipeline import collect_signals

    cfg = load_config()
    with Journal() as journal:
        signals = collect_signals(cfg, journal=journal)

    if not signals:
        console.print("[yellow]No relative-value signals found.[/yellow]")
        return

    table = Table(title=f"Relative-value signals ({len(signals)})")
    for col in ("ticker", "side", "action", "kalshi", "ref", "edge", "conf", "source"):
        table.add_column(col, overflow="fold")
    for s in signals:
        table.add_row(
            s.ticker,
            s.side,
            s.action,
            _fmt_cents(s.kalshi_price),
            _fmt_cents(s.reference_prob),
            _fmt_cents(s.edge),
            f"{s.confidence:.2f}",
            s.source,
        )
    console.print(table)


@app.command(name="rv-run")
def rv_run_cmd(
    dry_run_override: Optional[bool] = typer.Option(
        None,
        "--dry-run/--no-dry-run",
        help="Override config risk.dry_run for this run.",
    ),
    live: bool = typer.Option(False, "--live", help="Alias for --no-dry-run."),
) -> None:
    """Run relative-value scan -> Kalshi-only execute cycle."""
    from ..relative_value.pipeline import run as _run_relative_value

    cfg = load_config()
    dry_run = resolve_dry_run(cfg.risk.dry_run, live=live, dry_run_override=dry_run_override)
    if not dry_run:
        cfg.secrets.require_kalshi()
    counts = _run_relative_value(cfg, dry_run=dry_run)
    console.print(
        f"[bold]Relative-value cycle complete[/bold] — "
        f"signals {counts['signals']}, "
        f"placed {counts['placed']}, "
        f"dry_run {counts['dry_run']}, "
        f"rejected {counts['rejected']}"
    )


@app.command(name="monitor")
def monitor_cmd(
    once: bool = typer.Option(False, "--once", help="Check positions once and exit (no loop)."),
    interval: int = typer.Option(60, "--interval", "-i", help="Seconds between sweeps in loop mode."),
    dry_run_override: Optional[bool] = typer.Option(
        None,
        "--dry-run/--no-dry-run",
        help="Override config risk.dry_run for close orders.",
    ),
    live: bool = typer.Option(False, "--live", help="Alias for --no-dry-run."),
) -> None:
    """Poll open positions and close on exit triggers (auth). Dry-run unless --live."""
    cfg = load_config()
    dry_run = resolve_dry_run(cfg.risk.dry_run, live=live, dry_run_override=dry_run_override)
    if not dry_run:
        cfg.secrets.require_kalshi()
    with KalshiClient(cfg) as client, Journal() as journal:
        md = MarketData(client)
        monitor = Monitor(md, client, journal, live=not dry_run, strategy=cfg.strategy)
        if once:
            monitor.run_once()
        else:
            monitor.run_loop(poll_interval=interval)


@app.command(name="agent-scan")
def agent_scan_cmd(
    max_events: int = typer.Option(50, help="Max open events to pass to the agent."),
) -> None:
    """Agent-only dry scan: Claude identifies and evaluates opportunities (no orders placed)."""
    cfg = load_config()
    if not cfg.secrets.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY not set in .env[/red]")
        raise typer.Exit(1)
    counts = _run_agent_strategy(cfg, live=False, max_events=max_events)
    console.print(
        f"[bold]Agent scan complete[/bold] — "
        f"events scanned {counts['events_scanned']}, "
        f"signals {counts['agent_signals']}, "
        f"survivors {counts['survivors']}, "
        f"dry_run {counts['dry_run']}, "
        f"rejected {counts['rejected']}"
    )


@app.command(name="agent-run")
def agent_run_cmd(
    dry_run_override: Optional[bool] = typer.Option(
        None,
        "--dry-run/--no-dry-run",
        help="Override config risk.dry_run for this run.",
    ),
    live: bool = typer.Option(False, "--live", help="Alias for --no-dry-run."),
    max_events: int = typer.Option(50, help="Max open events to pass to the agent."),
) -> None:
    """Agent-enhanced scan -> evaluate -> execute cycle."""
    cfg = load_config()
    if not cfg.secrets.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY not set in .env[/red]")
        raise typer.Exit(1)
    dry_run = resolve_dry_run(cfg.risk.dry_run, live=live, dry_run_override=dry_run_override)
    if not dry_run:
        cfg.secrets.require_kalshi()
    counts = _run_agent_strategy(cfg, dry_run=dry_run, max_events=max_events)
    console.print(
        f"[bold]Agent run complete[/bold] — "
        f"events scanned {counts['events_scanned']}, "
        f"signals {counts['agent_signals']}, "
        f"survivors {counts['survivors']}, "
        f"placed {counts['placed']}, "
        f"dry_run {counts['dry_run']}, "
        f"rejected {counts['rejected']}"
    )
