"""Generic account / market operations (not strategy-specific)."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

import typer
from rich.table import Table

from ..client import KalshiClient, KalshiError
from ..compliance import ComplianceGate
from ..config import load_config
from ..execution import Executor
from ..journal import Journal
from ..market_data import MarketData
from ..models import Balance
from ..portfolio import Portfolio
from ..risk import KILL_SWITCH_PATH, ProposedOrder, RiskGate
from .common import _client, _fmt, console, resolve_dry_run

app = typer.Typer()


@app.command(name="exchange")
def exchange() -> None:
    """Show exchange/trading status (public, no auth)."""
    import json as _json
    try:
        with _client() as client:
            data = client.get("/exchange/status")
    except KalshiError as exc:
        if exc.status == 503:
            try:
                data = _json.loads(exc.body)
            except Exception:
                raise exc
        else:
            raise
    console.print(data)


@app.command()
def status() -> None:
    """Account snapshot: balance, portfolio value, open orders, positions (auth)."""
    cfg = load_config()
    cfg.secrets.require_kalshi()
    with KalshiClient(cfg) as client:
        pf = Portfolio(client)
        bal = pf.balance()
        positions = pf.market_positions()
        resting = pf.resting_orders()
    console.print(f"Cash balance:    ${bal.usd()}")
    pv = bal.portfolio_value_usd()
    if pv is not None:
        console.print(f"Portfolio value: ${pv}")
    console.print(f"Open positions:  {len(positions)}")
    console.print(f"Resting orders:  {len(resting)}")
    if bal.usd() == 0:
        console.print("[yellow]Balance is $0 — fund the account before live trading.[/yellow]")


@app.command()
def positions() -> None:
    """List current market positions (auth)."""
    cfg = load_config()
    cfg.secrets.require_kalshi()
    with KalshiClient(cfg) as client:
        rows = Portfolio(client).market_positions()
    if not rows:
        console.print("No open positions.")
        return
    table = Table(title="Market positions")
    cols = ["ticker", "position", "market_exposure", "realized_pnl"]
    for c in cols:
        table.add_column(c, overflow="fold")
    for p in rows:
        table.add_row(*[str(p.get(c, "")) for c in cols])
    console.print(table)


@app.command(name="auth-check")
def auth_check() -> None:
    """Verify credentials + signing by fetching the account balance."""
    cfg = load_config()
    cfg.secrets.require_kalshi()
    with KalshiClient(cfg) as client:
        data = client.get("/portfolio/balance", auth=True)
    bal = Balance.model_validate(data)
    usd = bal.usd()
    console.print("[green]Auth OK[/green] — signing accepted by Kalshi.")
    console.print(f"Balance: ${usd}" if usd is not None else f"Raw balance payload: {data}")


@app.command()
def markets(
    status: str = typer.Option("open", help="Market status filter (open/closed/settled)."),
    limit: int = typer.Option(20, help="Max markets to show."),
    series: Optional[str] = typer.Option(None, help="Filter by series_ticker."),
) -> None:
    """List markets (public)."""
    with _client() as client:
        md = MarketData(client)
        rows, _ = md.list_markets(status=status, series_ticker=series, limit=limit)
    table = Table(title=f"Markets (status={status})")
    for col in ("ticker", "event", "status", "yes_bid", "yes_ask", "last", "liq"):
        table.add_column(col, overflow="fold")
    for m in rows:
        table.add_row(
            m.ticker, m.event_ticker, m.status or "",
            _fmt(m.yes_bid), _fmt(m.yes_ask), _fmt(m.last_price), _fmt(m.liquidity),
        )
    console.print(table)


@app.command()
def events(
    limit: int = typer.Option(20, help="Max events to show."),
    series: Optional[str] = typer.Option(None, help="Filter by series_ticker."),
) -> None:
    """List events with their category (public)."""
    with _client() as client:
        md = MarketData(client)
        rows, _ = md.list_events(limit=limit, series_ticker=series)
    table = Table(title="Events")
    for col in ("event_ticker", "category", "series", "title"):
        table.add_column(col, overflow="fold")
    for e in rows:
        table.add_row(e.event_ticker, e.category or "", e.series_ticker or "", e.title or "")
    console.print(table)


@app.command()
def orderbook(ticker: str, depth: int = typer.Option(10, help="Levels per side.")) -> None:
    """Show the orderbook for a market ticker (public)."""
    with _client() as client:
        md = MarketData(client)
        ob = md.get_orderbook(ticker, depth=depth)
    table = Table(title=f"Orderbook {ticker}")
    table.add_column("YES price")
    table.add_column("YES size")
    table.add_column("NO price")
    table.add_column("NO size")
    n = max(len(ob.yes), len(ob.no))
    for i in range(n):
        y = ob.yes[i] if i < len(ob.yes) else ("", "")
        no = ob.no[i] if i < len(ob.no) else ("", "")
        table.add_row(str(y[0]), str(y[1]), str(no[0]), str(no[1]))
    console.print(table)
    if n == 0:
        console.print("[yellow]Orderbook is empty (illiquid or closed market).[/yellow]")


@app.command()
def order(
    ticker: str,
    side: str = typer.Option(..., help="yes or no"),
    action: str = typer.Option("buy", help="buy or sell"),
    price: float = typer.Option(..., help="Limit price in dollars per contract (0..1)."),
    count: int = typer.Option(..., help="Number of contracts."),
    fair: float = typer.Option(0.0, help="Your fair probability for this side (0..1)."),
    confidence: float = typer.Option(1.0, help="Confidence (0..1)."),
    post_only: bool = typer.Option(
        False, "--post-only", help="Maker-only: rest, never cross (avoids the taker fee)."),
    dry_run_override: Optional[bool] = typer.Option(
        None,
        "--dry-run/--no-dry-run",
        help="Override config risk.dry_run for this order.",
    ),
    live: bool = typer.Option(False, "--live", help="Alias for --no-dry-run."),
) -> None:
    """Submit one order through compliance -> risk -> execution. Dry-run unless --live."""
    cfg = load_config()
    cfg.secrets.require_kalshi()
    dry_run = resolve_dry_run(cfg.risk.dry_run, live=live, dry_run_override=dry_run_override)
    prop = ProposedOrder(
        ticker=ticker, side=side.lower(), action=action.lower(), price=Decimal(str(price)),
        count=count, fair_prob=fair, confidence=confidence, post_only=post_only,
    )
    with KalshiClient(cfg) as client, Journal() as journal:
        md = MarketData(client)
        market = md.get_market(ticker)
        category = md.category_for_market(market)
        account = Portfolio(client).account_state(ticker)
        executor = Executor(
            client, ComplianceGate(cfg.compliance), RiskGate(cfg.risk), journal,
            dry_run=dry_run,
        )
        result = executor.submit(
            prop, category=category, title=market.title or "",
            account=account, source="manual",
        )
    color = {"placed": "green", "dry_run": "yellow", "rejected": "red"}.get(result.status, "white")
    console.print(f"[{color}]{result.status.upper()}[/{color}]"
                  + (f" (gate={result.gate})" if result.gate else "")
                  + f" — {result.reason}")
    if result.order_body:
        console.print("Order body:", result.order_body)


@app.command()
def cancel(order_id: str) -> None:
    """Cancel a resting V2 order by id."""
    cfg = load_config()
    cfg.secrets.require_kalshi()
    with KalshiClient(cfg) as client, Journal() as journal:
        executor = Executor(
            client, ComplianceGate(cfg.compliance), RiskGate(cfg.risk), journal,
        )
        console.print(executor.cancel(order_id))


@app.command()
def kill() -> None:
    """Engage the kill switch — halts all new entries until removed."""
    KILL_SWITCH_PATH.parent.mkdir(parents=True, exist_ok=True)
    KILL_SWITCH_PATH.write_text("halt")
    console.print(f"[red]KILL SWITCH ENGAGED[/red] — {KILL_SWITCH_PATH}")


@app.command()
def unkill() -> None:
    """Remove the kill switch."""
    if KILL_SWITCH_PATH.exists():
        KILL_SWITCH_PATH.unlink()
    console.print("[green]Kill switch cleared.[/green]")
