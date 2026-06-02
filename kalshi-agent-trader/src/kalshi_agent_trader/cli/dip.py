"""Intraday title-dip mean-reversion detector + execution — strategy 2 commands."""

from __future__ import annotations

import time
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

import typer
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from ..client import KalshiError
from ..compliance import ComplianceGate
from ..config import load_config
from ..dip_orders import PositionBook, intent_for
from ..execution import Executor
from ..journal import Journal
from ..market_data import MarketData
from ..portfolio import Portfolio
from ..reversion import DipParams, DipSignal, DipTracker
from ..risk import AccountState, ProposedOrder, RiskGate
from ..tennis.pairing import fetch_universe, normalize_name
from .common import (
    _client,
    _fmt_cents,
    _fmt_money,
    _fmt_signed_cents,
    _fmt_signed_money,
    _new_table,
    console,
    resolve_dry_run,
)

app = typer.Typer()

_DIP_ACTION_STYLE = {
    "BUY DIP": "bold green",
    "REVERTING": "yellow",
    "REVERTED": "cyan",
    "STOP": "bold red",
    "WATCH": "dim",
}

# Rank live signals: actionable dips first, then by depth of over-reaction.
_DIP_ACTION_RANK = {"BUY DIP": 0, "REVERTING": 1, "REVERTED": 2, "STOP": 3, "WATCH": 4}


def _match_cell(s: DipSignal) -> str:
    """Current match-win mid vs the anchored (pre-dip) level."""
    moved = s.match_mid - s.match_anchor
    color = "red" if moved < 0 else "white"
    return f"[{color}]{_fmt_cents(s.match_mid)}[/{color}]\n[dim]was {_fmt_cents(s.match_anchor)}[/dim]"


def _title_cell(s: DipSignal) -> str:
    """Title entry (ask) over the match-implied fair it should revert toward."""
    entry = _fmt_cents(s.title_ask) if s.title_ask else "[dim]no ask[/dim]"
    return f"{entry}\n[dim]fair {_fmt_cents(s.fair_title)}[/dim]"


def _react_cell(s: DipSignal) -> str:
    """Over-reaction: residual now (+ peak), and φ = share unexplained by the match."""
    react = _fmt_signed_cents(s.residual)
    if s.peak_residual > s.residual:
        react += f"\n[dim]peak {_fmt_cents(s.peak_residual)}[/dim]"
    if s.title_delta < 0:
        react += f"\n[dim]φ {s.overreaction_frac*100:.0f}%[/dim]"
    return react


def _size_cell(s: DipSignal) -> str:
    """Sized capital + net-after-fees if it reverts to fair (dollar-framed)."""
    if s.stake <= 0:
        return "[dim]—[/dim]"
    color = "green" if s.est_profit > 0 else ("red" if s.est_profit < 0 else "white")
    return f"{_fmt_money(s.stake)}\n[{color}]{_fmt_signed_money(s.est_profit)}[/{color}]"


def _build_dip_table(
    signals: List[DipSignal], params: DipParams, *, status: Optional[str] = None
) -> Table:
    bucket = params.max_bucket_frac * params.bankroll
    legend = (
        f"Bankroll {_fmt_money(params.bankroll, 0)} • {params.kelly_fraction}×Kelly • "
        f"p_revert {params.p_revert*100:.0f}% • stop {params.stop_loss*100:.0f}¢ • "
        f"size ×φ • pos cap {params.max_position_frac*100:.0f}% • "
        f"bucket {params.max_bucket_frac*100:.0f}% ({_fmt_money(bucket, 0)})   •   "
        f"anchor = match-implied fair (C·match_yes); BUY when title ≥ "
        f"{params.residual_threshold*100:.0f}¢ below fair & EV>0; STOP if match < "
        f"{params.recover_floor*100:.0f}¢   •   "
        "φ = share of the title drop the match move does NOT explain (the sizing dial)   •   "
        "[bold]Size[/bold] = capital + net-after-fees if it reverts to fair   •   "
        "[yellow]p_revert is UNCALIBRATED; title leg is THIN — check `orderbook TICKER`[/yellow]"
    )
    table = _new_table("French Open — intraday TITLE-DIP detector + sizing (mean reversion)", legend, status)
    table.add_column("Player", no_wrap=True, overflow="ellipsis", max_width=22)
    table.add_column("G", justify="center")
    table.add_column("Action", justify="center")
    table.add_column("Match now", justify="right", no_wrap=True)
    table.add_column("Title (ask)/fair", justify="right", no_wrap=True)
    table.add_column("Over-react", justify="right", no_wrap=True)
    table.add_column("Size / if reverts", justify="right", no_wrap=True)
    table.add_column("Why", overflow="fold")

    ordered = sorted(
        signals,
        key=lambda s: (_DIP_ACTION_RANK.get(s.action, 9), -s.residual),
    )
    deployed = sum((s.stake for s in ordered), Decimal("0"))
    for s in ordered:
        style = _DIP_ACTION_STYLE.get(s.action, "white")
        g = "[cyan]M[/cyan]" if s.gender == "men" else "[magenta]W[/magenta]"
        table.add_row(
            s.name, g, f"[{style}]{s.action}[/{style}]",
            _match_cell(s), _title_cell(s), _react_cell(s),
            _size_cell(s), f"[dim]{s.rationale}[/dim]",
        )
    if not ordered:
        table.add_row("—", "", "[dim]none[/dim]", "—", "—", "—", "—",
                      "[dim]no favourites anchored yet — start before/early in a match[/dim]")
    else:
        table.caption = (f"{_fmt_money(deployed)} deployed of {_fmt_money(bucket)} bucket\n"
                         + (table.caption or legend))
    return table


@app.command()
def dip(
    player: Optional[List[str]] = typer.Option(
        None, "--player", "-p", help="Player name substring(s); repeatable. Omit for all."),
    gender: str = typer.Option("both", help="men | women | both"),
    bankroll: float = typer.Option(500.0, help="Bankroll for Kelly sizing (your real account size)."),
    kelly: float = typer.Option(0.5, help="Kelly fraction (moderate = 0.5)."),
    p_revert: float = typer.Option(
        0.70, help="Conviction the dislocation reverts (EV/Kelly gate). UNCALIBRATED."),
    stop_loss: float = typer.Option(
        0.06, help="¢/contract you'll risk before cutting (caps loss; tighter ⇒ bigger size)."),
    threshold: float = typer.Option(
        0.05, help="Min over-reaction to BUY: title this far (dollars) below match-implied fair."),
    recover_floor: float = typer.Option(
        0.35, help="STOP if match-win mid falls below this (the deficit is real)."),
    exit_band: float = typer.Option(0.02, help="Within this of fair = reverted (take profit)."),
    min_match_anchor: float = typer.Option(
        0.50, help="Only anchor players who started the match a favourite (match mid ≥ this)."),
    fee_rate: float = typer.Option(0.07, help="Kalshi fee coefficient (round-trip is modelled)."),
    interval: int = typer.Option(15, "--interval", "-i", help="Poll seconds (loop mode)."),
    once: bool = typer.Option(False, "--once", help="Single snapshot, no loop."),
    execute: bool = typer.Option(
        False, "--execute",
        help="Route signals through the order pipeline (maker entry, NO-buy exit). "
             "Use --dry-run/--no-dry-run to override config risk.dry_run."),
    dry_run_override: Optional[bool] = typer.Option(
        None,
        "--dry-run/--no-dry-run",
        help="Override config risk.dry_run when --execute is enabled.",
    ),
    live: bool = typer.Option(False, "--live", help="Alias for --no-dry-run."),
) -> None:
    """Live intraday detector + sizing: buy title dips that over-shoot the match move.

    Alerts only unless `--execute` is set. Anchors each favourite's title/match
    ratio (C = P(title | advances)) at first sight and watches for the title to
    fall ≥ `threshold` below the match-implied fair (C · match_yes). Sizes a
    fractional-Kelly stake whose conviction scales with φ = the share of the title
    drop the match move does NOT explain. Use `--dry-run/--no-dry-run` to choose
    execution mode; start before/early in a match so the anchor captures the
    pre-dip level.
    """
    params = DipParams(
        bankroll=Decimal(str(bankroll)), kelly_fraction=Decimal(str(kelly)),
        p_revert=Decimal(str(p_revert)), stop_loss=Decimal(str(stop_loss)),
        residual_threshold=Decimal(str(threshold)),
        recover_floor=Decimal(str(recover_floor)), exit_band=Decimal(str(exit_band)),
        min_match_anchor=Decimal(str(min_match_anchor)), fee_rate=Decimal(str(fee_rate)),
    )
    queries = [p for p in (player or []) if p]
    tracker = DipTracker(params)

    cfg = load_config()
    dry_run = resolve_dry_run(
        cfg.risk.dry_run, live=live, dry_run_override=dry_run_override
    )
    if execute and not dry_run:
        cfg.secrets.require_kalshi()
    book = PositionBook()
    cat_cache: dict = {}
    exec_log: List[str] = []

    def step_orders(md: MarketData, executor, client, signals) -> None:
        """Route each signal through the pipeline; update the book on accepted orders."""
        for s in signals:
            intent = intent_for(s, book.get(s.title_ticker), params)
            if intent is None:
                continue
            if intent.ticker not in cat_cache:
                m = md.get_market(intent.ticker)
                cat_cache[intent.ticker] = (md.category_for_market(m), m.title or "")
            category, title = cat_cache[intent.ticker]
            if dry_run:
                acct = AccountState(params.bankroll, Decimal("0"), Decimal("0"), Decimal("0"))
            else:
                acct = Portfolio(client).account_state(intent.ticker)
            res = executor.submit(
                ProposedOrder(intent.ticker, intent.side, intent.price, intent.count,
                              intent.fair_prob, intent.confidence, post_only=intent.maker),
                category=category, title=title, account=acct, source="dip",
            )
            tag = {"placed": "green", "dry_run": "yellow", "rejected": "red"}.get(res.status, "white")
            mk = "maker" if intent.maker else "taker"
            exec_log.append(
                f"[{tag}]{res.status.upper()}[/{tag}] {intent.kind} {s.name}: {mk} "
                f"{intent.side} {res.approved_count}@{_fmt_cents(intent.price)} "
                f"[dim]{intent.reason} — {res.reason}[/dim]")
            if res.status in ("placed", "dry_run"):
                book.on_enter(s) if intent.kind == "enter" else book.on_exit(intent.ticker)
        del exec_log[:-8]  # keep the last few lines

    def render(md: MarketData, client, executor=None, status: Optional[str] = None):
        universe = fetch_universe(md, gender)
        signals = tracker.update(universe)
        if queries:
            qn = [normalize_name(q) for q in queries]
            signals = [s for s in signals if any(q in normalize_name(s.name) for q in qn)]
        table = _build_dip_table(signals, params, status=status)
        if executor is None:
            return table
        step_orders(md, executor, client, signals)
        mode = "[red]LIVE[/red]" if not dry_run else "[yellow]DRY-RUN[/yellow]"
        body = "\n".join(exec_log) or "[dim]no orders yet[/dim]"
        panel = Panel(body, title=f"orders ({mode}) • fund cap ${cfg.risk.max_total_exposure_usd} • "
                                  f"≤${cfg.risk.max_per_position_usd}/trade", border_style="dim")
        return Group(table, panel)

    with _client() as client:
        md = MarketData(client)
        with Journal() as journal:
            executor = Executor(
                client, ComplianceGate(cfg.compliance), RiskGate(cfg.risk), journal,
                dry_run=dry_run) if execute else None
            if once:
                console.print(render(md, client, executor))
                return
            with Live(console=console, refresh_per_second=4, screen=False) as live:
                try:
                    while True:
                        stamp = datetime.now().strftime("%H:%M:%S")
                        try:
                            live.update(render(
                                md, client, executor,
                                status=f"updated {stamp} • poll {interval}s • Ctrl-C to stop"))
                        except KalshiError as e:
                            live.update(Panel(f"[red]API error:[/red] {e}\n"
                                              f"retrying in {interval}s", title="dip"))
                        time.sleep(interval)
                except KeyboardInterrupt:
                    console.print("[dim]stopped.[/dim]")
