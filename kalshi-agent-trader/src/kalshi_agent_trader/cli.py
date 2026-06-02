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
from rich import box
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
from .portfolio import Portfolio
from .risk import KILL_SWITCH_PATH, ProposedOrder, RiskGate
from .tennis_screen import PlayerRow, fetch_universe, rows_from_universe
from . import pipeline as _strategy
from .agents.agent_strategy import run_agent_strategy as _run_agent_strategy

app = typer.Typer(add_completion=False, help="Kalshi agent-trader CLI")
console = Console()


def _client() -> KalshiClient:
    return KalshiClient(load_config())


@app.command(name="exchange")
def exchange() -> None:
    """Show exchange/trading status (public, no auth)."""
    with _client() as client:
        data = client.get("/exchange/status")
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
        ticker=ticker, side=side.lower(), price=Decimal(str(price)),
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


def _fmt(v) -> str:
    return "" if v is None else str(v)


def _fmt_money(v, places: int = 2) -> str:
    return "—" if v is None else f"${Decimal(str(v)):,.{places}f}"


def _fmt_signed_money(v) -> str:
    """Locked P&L with an explicit sign, e.g. '+$800.00' / '-$90.11'."""
    if v is None:
        return "—"
    d = Decimal(str(v))
    sign = "+" if d > 0 else ("-" if d < 0 else "")
    return f"{sign}${abs(d):,.2f}"


def _fmt_cents(v) -> str:
    """Price as cents, e.g. 0.38 -> '38¢'. Display only — the value stays exact.

    Market quotes are whole cents; derived prices (t*) keep 0.1¢ resolution.
    """
    if v is None:
        return "—"
    c = (Decimal(str(v)) * 100).quantize(Decimal("0.1"))
    return f"{int(c)}¢" if c == c.to_integral_value() else f"{c}¢"


def _fmt_qty(v) -> str:
    return "—" if v is None else f"{Decimal(str(v)):,.0f}"


def _fmt_signed_cents(v) -> str:
    """Signed price move in cents, e.g. +14¢ / -3.5¢."""
    if v is None:
        return "—"
    c = (Decimal(str(v)) * 100).quantize(Decimal("0.1"))
    sign = "+" if c > 0 else ("-" if c < 0 else "")
    a = abs(c)
    body = f"{int(a)}" if a == a.to_integral_value() else f"{a}"
    return f"{sign}{body}¢"


def _fmt_pct(delta, base) -> str:
    """Signed percent move relative to the current title price."""
    if base in (None, 0) or Decimal(str(base)) == 0:
        return "—"
    p = (Decimal(str(delta)) / Decimal(str(base)) * 100).quantize(Decimal("1"))
    sign = "+" if p > 0 else ("-" if p < 0 else "")
    return f"{sign}{abs(p)}%"


def _money_cell(v) -> str:
    color = "green" if Decimal(str(v)) > 0 else ("red" if Decimal(str(v)) < 0 else "white")
    return f"[{color}]{_fmt_signed_money(v)}[/{color}]"


def _scenario_cell(balance, cost, fees) -> str:
    """Two lines: ending account balance, and net P&L after fees (colored)."""
    bal = Decimal(str(balance))
    net = bal - Decimal(str(cost)) - Decimal(str(fees))
    color = "green" if net > 0 else ("red" if net < 0 else "white")
    return f"{_fmt_money(bal)}\n[{color}]{_fmt_signed_money(net)}[/{color}]"


def _new_table(title: str, legend: str, status: Optional[str]) -> Table:
    return Table(
        title=title,
        caption=f"{status}\n{legend}" if status else legend,
        box=box.SIMPLE_HEAVY,
        header_style="bold",
        expand=False,
        pad_edge=False,
    )


def _hedge_win_cell(m, total) -> str:
    """If they WIN the match: NO bet is lost; title bet must reach this breakeven.

    Breakeven = title price that makes the title leg recover the whole $200 + fees.
    """
    be = (Decimal(str(total)) + m.fees) / m.q_title
    txt = _fmt_cents(be)
    cur = _fmt_cents(m.title_price)
    head = f"need [b]≥ {txt}[/b]" if be <= 1 else f"[yellow]≥ {txt} (impossible)[/yellow]"
    return f"{head}\n[dim]title now {cur}[/dim]"


def _build_hedge_table(
    rows: List[PlayerRow], feasible_only: bool, *,
    stake_no: Decimal, stake_tourney: Decimal, status: Optional[str] = None,
) -> Table:
    total = stake_no + stake_tourney
    legend = (
        f"HEDGE: bet NO on the next match {_fmt_money(stake_no, 0)} + bet YES on the title "
        f"{_fmt_money(stake_tourney, 0)} = {_fmt_money(total, 0)} in   •   "
        "[b]If LOSES match[/b] = NO bet wins, title bet → $0 — a fixed result "
        "(ending balance + net after fees)   •   "
        "[b]If WINS match[/b] = you lose the NO stake; the title bet rides, so this is the "
        "title price you'd have to reach to recover the whole stake + fees   •   "
        "[yellow]yellow[/yellow] = breakeven >100¢, impossible"
    )
    table = _new_table("French Open — HEDGE: bet NO on match + YES on title", legend, status)
    table.add_column("Player", no_wrap=True, overflow="ellipsis", max_width=22)
    table.add_column("G", justify="center")
    table.add_column("Match NO px", justify="right", no_wrap=True)
    table.add_column("Title YES px", justify="right", no_wrap=True)
    table.add_column("If LOSES match", justify="right", no_wrap=True)
    table.add_column("If WINS match", justify="right", no_wrap=True)
    table.add_column("Fees", justify="right", no_wrap=True)
    table.add_column("Note", overflow="fold")

    shown = [r for r in rows if not feasible_only
             or (r.result is not None and r.result.breakeven_feasible)]

    def key(r: PlayerRow):
        m = r.result
        if m is None:
            return (2, Decimal("0"))
        return (0 if m.breakeven_feasible else 1, m.breakeven_price)

    for r in sorted(shown, key=key):
        m = r.result
        g = "[cyan]M[/cyan]" if r.gender == "men" else "[magenta]W[/magenta]"
        if m is None:
            table.add_row(r.name, g, "—", "—", "—", "—", "—", f"[dim]{r.note}[/dim]")
            continue
        table.add_row(
            r.name, g,
            _fmt_cents(m.match_price), _fmt_cents(m.title_price),
            _scenario_cell(m.q_match, total, m.fees),   # fixed result if they lose
            _hedge_win_cell(m, total),                   # breakeven title odds if they win
            _fmt_money(m.fees), "",
        )
    if not shown:
        table.caption = "(no matching players)\n" + legend
    return table


def _fade_no_floor(m, total) -> Decimal:
    """Title-NO price the position must hold >= to break even if they WIN the match."""
    return (Decimal(str(total)) + m.fees - m.q_match) / m.q_title_no


def _fade_win_cell(m, total) -> str:
    """If they WIN the match: match YES is banked; the title-NO bet must hold this price."""
    floor = _fade_no_floor(m, total)
    cur = _fmt_cents(m.title_no_price)
    if floor <= 0:
        return f"[green]NO ≥ any (locked)[/green]\n[dim]title NO now {cur}[/dim]"
    head = (f"NO stay [b]≥ {_fmt_cents(floor)}[/b]" if floor <= 1
            else f"[yellow]NO ≥ {_fmt_cents(floor)} (impossible)[/yellow]")
    return f"{head}\n[dim]title NO now {cur}[/dim]"


def _build_fade_table(
    rows: List[PlayerRow], feasible_only: bool, *,
    stake_no: Decimal, stake_tourney: Decimal, status: Optional[str] = None,
) -> Table:
    total = stake_no + stake_tourney
    legend = (
        f"FADE: bet YES on the next match {_fmt_money(stake_no, 0)} + bet NO on the title "
        f"{_fmt_money(stake_tourney, 0)} = {_fmt_money(total, 0)} in   •   "
        "[b]If LOSES match[/b] = title-NO bet collects, match-YES premium lost — a fixed "
        "result (ending balance + net after fees)   •   "
        "[b]If WINS match[/b] = match-YES bet is banked; the title-NO bet bleeds as their "
        "title odds rise, so this is the title-NO price you must hold to recover stake + fees   •   "
        "[green]locked[/green] = profits even if they go on to win the title"
    )
    table = _new_table("French Open — FADE: bet YES on match + NO on title", legend, status)
    table.add_column("Player", no_wrap=True, overflow="ellipsis", max_width=22)
    table.add_column("G", justify="center")
    table.add_column("Match YES px", justify="right", no_wrap=True)
    table.add_column("Title NO px", justify="right", no_wrap=True)
    table.add_column("If LOSES match", justify="right", no_wrap=True)
    table.add_column("If WINS match", justify="right", no_wrap=True)
    table.add_column("Fees", justify="right", no_wrap=True)
    table.add_column("Note", overflow="fold")

    # feasible_only here = show only setups with no downside even if they win it all.
    shown = [r for r in rows if not feasible_only
             or (r.result is not None and _fade_no_floor(r.result, total) <= 0)]

    def key(r: PlayerRow):
        m = r.result
        if m is None:
            return (2, Decimal("0"))
        floor = _fade_no_floor(m, total)
        return (0 if floor <= 0 else 1, floor)   # locked first, then most cushion

    for r in sorted(shown, key=key):
        m = r.result
        g = "[cyan]M[/cyan]" if r.gender == "men" else "[magenta]W[/magenta]"
        if m is None:
            table.add_row(r.name, g, "—", "—", "—", "—", "—", f"[dim]{r.note}[/dim]")
            continue
        table.add_row(
            r.name, g,
            _fmt_cents(m.match_price), _fmt_cents(m.title_no_price),
            _scenario_cell(m.q_title_no, total, m.fees),   # fixed result if they lose
            _fade_win_cell(m, total),                       # title-NO breakeven if they win
            _fmt_money(m.fees), "",
        )
    if not shown:
        table.caption = "(no matching players)\n" + legend
    return table


def _build_table(rows, feasible_only, *, strategy, stake_no, stake_tourney, status=None):
    builder = _build_fade_table if strategy == "fade" else _build_hedge_table
    return builder(rows, feasible_only, stake_no=stake_no,
                   stake_tourney=stake_tourney, status=status)


def _tight_breakeven(strategy: str, m, total) -> str:
    """Compact 'breakeven if they win the match' string (fee-adjusted)."""
    if strategy == "fade":
        floor = _fade_no_floor(m, total)
        if floor <= 0:
            return "[green]any (locked)[/green]"
        be = _fmt_cents(floor)
        return f"NO ≥ {be}" if floor <= 1 else f"[yellow]≥ {be} (imposs.)[/yellow]"
    be_price = (Decimal(str(total)) + m.fees) / m.q_title
    be = _fmt_cents(be_price)
    return f"YES ≥ {be}" if be_price <= 1 else f"[yellow]≥ {be} (imposs.)[/yellow]"


def _build_tight_table(
    rows: List[PlayerRow], feasible_only: bool, *,
    strategy: str, stake_no: Decimal, stake_tourney: Decimal, status: Optional[str] = None,
) -> Table:
    total = stake_no + stake_tourney
    if strategy == "fade":
        title = "FADE — bet YES match + NO title"
        loss_hdr = "PnL if loses match"
    else:
        title = "HEDGE — bet NO match + YES title"
        loss_hdr = "PnL if loses match"
    legend = (
        f"{_fmt_money(total, 0)} in, fees included   •   "
        "PnL if loses = net if they lose the next match   •   "
        "Breakeven if wins = title price the surviving leg must hold to recover stake+fees"
    )
    table = _new_table(title, legend, status)
    table.add_column("Player", no_wrap=True, overflow="ellipsis", max_width=24)
    table.add_column(loss_hdr, justify="right", no_wrap=True)
    table.add_column("Breakeven if wins", justify="right", no_wrap=True)

    def loss_net(m):
        leg = m.q_match if strategy == "hedge" else m.q_title_no
        return leg - total - m.fees

    computed = [r for r in rows if r.result is not None]

    def key(r: PlayerRow):
        m = r.result
        if strategy == "fade":
            floor = _fade_no_floor(m, total)
            return (0 if floor <= 0 else 1, floor)
        be = (total + m.fees) / m.q_title
        return (0 if be <= 1 else 1, be)

    for r in sorted(computed, key=key):
        m = r.result
        table.add_row(
            r.name,
            _money_cell(loss_net(m)),
            _tight_breakeven(strategy, m, total),
        )
    if not computed:
        table.caption = "(no matching players)\n" + legend
    return table


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
        ref = poly.fetch_reference(c.title) if poly else None  # type: ignore[union-attr]
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


if __name__ == "__main__":
    app()
