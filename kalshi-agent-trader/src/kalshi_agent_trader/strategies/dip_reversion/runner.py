"""Live loop for the dip detector: fetch → detect/size → render → (optional) execute.

Orchestration only — depends on core infra (client, market_data, executor, risk)
and the strategy's detector/orders/render. Keeps cli.py thin: the `dip` command
parses options and calls `run`.
"""

from __future__ import annotations

import time
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel

from ...client import KalshiClient, KalshiError
from ...compliance import ComplianceGate
from ...config import load_config
from ...execution import Executor
from ...journal import Journal
from ...market_data import MarketData
from ...portfolio import Portfolio
from ...risk import AccountState, ProposedOrder, RiskGate
from ...tennis_screen import fetch_universe, normalize_name
from .detector import DipParams, DipTracker
from .orders import PositionBook, intent_for
from .render import build_dip_table

console = Console()


def run(
    params: DipParams, *, gender: str, players: Optional[List[str]],
    interval: int, once: bool, execute: bool, dry_run: Optional[bool] = None,
) -> None:
    cfg = load_config()
    dry_run = cfg.risk.dry_run if dry_run is None else dry_run
    if execute and not dry_run:
        cfg.secrets.require_kalshi()
    tracker = DipTracker(params)
    book = PositionBook()
    cat_cache: dict = {}
    exec_log: List[str] = []
    queries = [p for p in (players or []) if p]

    def step_orders(md: MarketData, executor, client, signals) -> None:
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
                f"{intent.side} {res.approved_count}@{intent.price} "
                f"[dim]{intent.reason} — {res.reason}[/dim]")
            if res.status in ("placed", "dry_run"):
                book.on_enter(s) if intent.kind == "enter" else book.on_exit(intent.ticker)
        del exec_log[:-8]

    def render(md: MarketData, executor, client, status: Optional[str] = None):
        universe = fetch_universe(md, gender)
        signals = tracker.update(universe)
        if queries:
            qn = [normalize_name(q) for q in queries]
            signals = [s for s in signals if any(q in normalize_name(s.name) for q in qn)]
        table = build_dip_table(signals, params, status=status)
        if executor is None:
            return table
        step_orders(md, executor, client, signals)
        mode = "[red]LIVE[/red]" if not dry_run else "[yellow]DRY-RUN[/yellow]"
        body = "\n".join(exec_log) or "[dim]no orders yet[/dim]"
        panel = Panel(body, title=f"orders ({mode}) • fund cap ${cfg.risk.max_total_exposure_usd} • "
                                  f"≤${cfg.risk.max_per_position_usd}/trade", border_style="dim")
        return Group(table, panel)

    with KalshiClient(cfg) as client:
        md = MarketData(client)
        with Journal() as journal:
            executor = Executor(
                client, ComplianceGate(cfg.compliance), RiskGate(cfg.risk), journal,
                dry_run=dry_run) if execute else None
            if once:
                console.print(render(md, executor, client))
                return
            with Live(console=console, refresh_per_second=4, screen=False) as live:
                try:
                    while True:
                        stamp = datetime.now().strftime("%H:%M:%S")
                        try:
                            live.update(render(
                                md, executor, client,
                                status=f"updated {stamp} • poll {interval}s • Ctrl-C to stop"))
                        except KalshiError as e:
                            live.update(Panel(f"[red]API error:[/red] {e}\nretrying in {interval}s",
                                              title="dip"))
                        time.sleep(interval)
                except KeyboardInterrupt:
                    console.print("[dim]stopped.[/dim]")
