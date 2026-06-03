"""One-shot orchestration for the three-leg planner: fetch → size → render → (optional) execute.

Pre-match positioning, so no live poll loop — a single snapshot. `--execute`
routes each sized leg through compliance → risk → execution, honouring the
config's ``dry_run`` (orders are placed only when dry_run is false).
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from rich.console import Console, Group
from rich.panel import Panel

from ...client import KalshiClient
from ...compliance import ComplianceGate
from ...config import load_config
from ...execution import Executor
from ...journal import Journal
from ...market_data import MarketData
from ...portfolio import Portfolio
from ...risk import AccountState, RiskGate
from .orders import proposed_orders
from .render import build_three_leg_json, build_three_leg_view
from .screen import ThreeLegParams, build_plans

console = Console()


def run(
    params: ThreeLegParams, *, gender: str, players: Optional[List[str]], execute: bool,
    json_out: bool = False,
) -> None:
    cfg = load_config()
    with KalshiClient(cfg) as client:
        md = MarketData(client)
        plans = build_plans(md, gender=gender, players=players, params=params)

        # Machine-readable snapshot for the research agent. JSON mode never executes.
        if json_out:
            snapshot = build_three_leg_json(plans, params)
            snapshot["taken_at"] = datetime.now().astimezone().isoformat()
            print(json.dumps(snapshot, indent=2))
            return

        stamp = datetime.now().strftime("%H:%M:%S")
        view = build_three_leg_view(plans, params, status=f"snapshot {stamp}")

        if not execute:
            console.print(view)
            return

        log: List[str] = []
        cat_cache: dict = {}
        with Journal() as journal:
            executor = Executor(
                client, ComplianceGate(cfg.compliance), RiskGate(cfg.risk), journal,
                dry_run=cfg.risk.dry_run)
            for plan in plans:
                if plan.hedge_pending:
                    log.append(
                        f"[yellow]PENDING[/yellow] {plan.name}: length hedge deferred — "
                        f"re-run when {plan.pending_hedge_event} lists")
                for order in proposed_orders(plan):
                    if order.ticker not in cat_cache:
                        m = md.get_market(order.ticker)
                        cat_cache[order.ticker] = (md.category_for_market(m), m.title or "")
                    category, title = cat_cache[order.ticker]
                    if cfg.risk.dry_run:
                        acct = AccountState(params.bankroll, Decimal("0"), Decimal("0"), Decimal("0"))
                    else:
                        acct = Portfolio(client).account_state(order.ticker)
                    res = executor.submit(order, category=category, title=title,
                                          account=acct, source="three_leg")
                    tag = {"placed": "green", "dry_run": "yellow", "rejected": "red"}.get(
                        res.status, "white")
                    log.append(
                        f"[{tag}]{res.status.upper()}[/{tag}] {plan.name}: buy yes "
                        f"{res.approved_count}@{order.price} {order.ticker} "
                        f"[dim]{res.reason}[/dim]")
        mode = "[red]LIVE[/red]" if not cfg.risk.dry_run else "[yellow]DRY-RUN[/yellow]"
        body = "\n".join(log) or "[dim]no sized legs to submit[/dim]"
        console.print(Group(view, Panel(
            body, title=f"orders ({mode}) • fund cap ${cfg.risk.max_total_exposure_usd} • "
                        f"≤${cfg.risk.max_per_position_usd}/trade", border_style="dim")))
