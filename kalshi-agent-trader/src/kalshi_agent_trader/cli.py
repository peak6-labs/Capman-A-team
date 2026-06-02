"""Command-line interface for the Kalshi agent-trader.

Phase 1 commands (read-only foundation):
  auth-check   verify signing + fetch account balance
  status       exchange status (public)
  markets      list markets, optionally filtered
  orderbook    show an orderbook for a ticker
  events       list events with their categories
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import List, Optional

import typer
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from decimal import Decimal

from .client import KalshiClient, KalshiError
from .compliance import ComplianceGate
from .config import load_config
from .execution import Executor
from .journal import Journal
from .market_data import MarketData
from .models import Balance
from .monitor import Monitor
from .portfolio import Portfolio
from .render import _build_table, _build_tight_table, _fmt, _fmt_cents
from .risk import KILL_SWITCH_PATH, ProposedOrder, RiskGate
from .tennis_screen import fetch_universe, rows_from_universe
from . import pipeline as _strategy
from .agents.agent_strategy import run_agent_strategy as _run_agent_strategy
from .strategies.dip_reversion import runner as _dip_runner
from .strategies.dip_reversion.detector import DipParams

app = typer.Typer(add_completion=False, help="Kalshi agent-trader CLI")
console = Console()


def _client() -> KalshiClient:
    return KalshiClient(load_config())


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
    live: bool = typer.Option(False, "--live", help="Actually place it (overrides dry_run)."),
) -> None:
    """Submit one order through compliance -> risk -> execution. Dry-run unless --live."""
    cfg = load_config()
    cfg.secrets.require_kalshi()
    dry_run = cfg.risk.dry_run and not live
    prop = ProposedOrder(
        ticker=ticker, side=side.lower(), action=action.lower(), price=Decimal(str(price)),
        count=count, fair_prob=fair, confidence=confidence,
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


@app.command(name="scan")
def scan_cmd(
    pages: int = typer.Option(20, help="Max pages to paginate (200 markets/page)."),
) -> None:
    """Scan open markets for cheap-tail candidates (public + auth for compliance)."""
    from .brain import Brain
    from .compliance import ComplianceGate
    from .market_data import MarketData
    from .polymarket import PolymarketClient
    from .scanner import Scanner

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
    live: bool = typer.Option(False, "--live", help="Place real orders (overrides dry_run)."),
) -> None:
    """Run one full scan → brain → execute cycle."""
    cfg = load_config()
    if live:
        cfg.secrets.require_kalshi()
    counts = _strategy.run(cfg, live=live)
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
    from .relative_value.pipeline import collect_signals

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
    live: bool = typer.Option(False, "--live", help="Place real Kalshi orders (dry-run by default)."),
) -> None:
    """Run relative-value scan → Kalshi-only execute cycle."""
    from .relative_value.pipeline import run as _run_relative_value

    cfg = load_config()
    cfg.secrets.require_kalshi()
    counts = _run_relative_value(cfg, live=live)
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
    live: bool = typer.Option(False, "--live", help="Place real close orders (overrides dry_run)."),
) -> None:
    """Poll open positions and close on exit triggers (auth). Dry-run unless --live."""
    cfg = load_config()
    if live:
        cfg.secrets.require_kalshi()
    with KalshiClient(cfg) as client, Journal() as journal:
        md = MarketData(client)
        monitor = Monitor(md, client, journal, live=live, strategy=cfg.strategy)
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
    live: bool = typer.Option(False, "--live", help="Place real orders (overrides dry_run)."),
    max_events: int = typer.Option(50, help="Max open events to pass to the agent."),
) -> None:
    """Agent-enhanced scan → evaluate → execute cycle."""
    cfg = load_config()
    if not cfg.secrets.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY not set in .env[/red]")
        raise typer.Exit(1)
    if live:
        cfg.secrets.require_kalshi()
    counts = _run_agent_strategy(cfg, live=live, max_events=max_events)
    console.print(
        f"[bold]Agent run complete[/bold] — "
        f"events scanned {counts['events_scanned']}, "
        f"signals {counts['agent_signals']}, "
        f"survivors {counts['survivors']}, "
        f"placed {counts['placed']}, "
        f"dry_run {counts['dry_run']}, "
        f"rejected {counts['rejected']}"
    )


@app.command()
def breakeven(
    player: Optional[List[str]] = typer.Option(
        None, "--player", "-p",
        help="Player name substring(s); repeatable. Omit to screen all."),
    gender: str = typer.Option("both", help="men | women | both"),
    strategy: str = typer.Option(
        "both",
        help="hedge (buy match NO + buy title YES) | fade (buy match YES + sell title) | both"),
    stake_no: float = typer.Option(100.0, help="Stake on leg 1 (match)."),
    stake_tourney: float = typer.Option(100.0, help="Stake on leg 2 (title)."),
    price_basis: str = typer.Option("ask", help="ask | mid"),
    fee_rate: float = typer.Option(
        0.07, help="Kalshi fee coefficient (fee = rate*C*p*(1-p), rounded up)."),
    interval: int = typer.Option(15, "--interval", "-i", help="Poll seconds (loop mode)."),
    once: bool = typer.Option(False, "--once", help="Single snapshot, no loop."),
    tight: bool = typer.Option(
        False, "--tight", "-t",
        help="Compact 3-column view: player, PnL if loses, breakeven if wins."),
    feasible_only: bool = typer.Option(
        False, help="Only show rows whose breakeven is achievable."),
) -> None:
    """French Open two-market breakeven screener (public, no auth).

    Pairs each player's current-match market with their tournament-winner market.
    `hedge` bets they lose today + win the title (breakeven = title FLOOR the
    odds must reach after a win). `fade` is the inverse: bets they win today +
    fades the title (breakeven = title CEILING the odds must stay below).
    `both` stacks the two grids from a single market snapshot.
    """
    if strategy not in ("hedge", "fade", "both"):
        raise typer.BadParameter("strategy must be hedge, fade, or both")
    strategies = ["hedge", "fade"] if strategy == "both" else [strategy]
    s_no = Decimal(str(stake_no))
    s_t = Decimal(str(stake_tourney))
    fee = Decimal(str(fee_rate))

    def render(md: MarketData, status: Optional[str] = None):
        universe = fetch_universe(md, gender)   # fetched once, shared by both grids
        tables = []
        for strat in strategies:
            rows = rows_from_universe(
                universe, players=player or None,
                stake_no=s_no, stake_tourney=s_t, price_basis=price_basis,
                strategy=strat, fee_rate=fee,
            )
            builder = _build_tight_table if tight else _build_table
            tables.append(builder(
                rows, feasible_only, strategy=strat,
                stake_no=s_no, stake_tourney=s_t, status=status))
        return Group(*tables) if len(tables) > 1 else tables[0]

    with _client() as client:
        md = MarketData(client)
        if once:
            console.print(render(md))
            return
        with Live(console=console, refresh_per_second=4, screen=False) as live:
            try:
                while True:
                    stamp = datetime.now().strftime("%H:%M:%S")
                    try:
                        live.update(render(
                            md, status=f"updated {stamp} • poll {interval}s • Ctrl-C to stop"))
                    except KalshiError as e:
                        live.update(Panel(f"[red]API error:[/red] {e}\n"
                                          f"retrying in {interval}s", title="breakeven"))
                    time.sleep(interval)
            except KeyboardInterrupt:
                console.print("[dim]stopped.[/dim]")


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
        0.05, help="Min over-reaction to BUY: title this far below match-implied fair."),
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
        help="Route signals through the order pipeline (maker entry, sell-to-exit). "
             "Honors config dry_run — places orders only when dry_run is false."),
) -> None:
    """Live intraday detector + sizing: buy title dips that over-shoot the match move.

    Anchors each favourite's title/match ratio (C = P(title | advances)) and buys
    when the title falls ≥ `threshold` below the match-implied fair (C·match_yes),
    sizing a φ-scaled fractional-Kelly stake. Alerts only unless `--execute`; start
    it *before/early* in a match so the anchor captures the pre-dip level.
    """
    params = DipParams(
        bankroll=Decimal(str(bankroll)), kelly_fraction=Decimal(str(kelly)),
        p_revert=Decimal(str(p_revert)), stop_loss=Decimal(str(stop_loss)),
        residual_threshold=Decimal(str(threshold)),
        recover_floor=Decimal(str(recover_floor)), exit_band=Decimal(str(exit_band)),
        min_match_anchor=Decimal(str(min_match_anchor)), fee_rate=Decimal(str(fee_rate)),
    )
    _dip_runner.run(params, gender=gender, players=player or None,
                    interval=interval, once=once, execute=execute)


if __name__ == "__main__":
    app()
