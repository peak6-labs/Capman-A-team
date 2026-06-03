"""Command-line interface for the Kalshi agent-trader.

The CLI is the deterministic tool surface the research/executor agents drive.
Read-only commands (exchange/status/markets/events/orderbook/three-leg snapshot)
feed the research agent; the write path (order/three-leg --execute/cancel) is
the executor agent's, gated by compliance -> risk -> execution and honouring
config `dry_run`. Retired exploratory commands live under `attic/`.
"""

from __future__ import annotations

from typing import List, Optional

import typer
from rich.console import Console
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
from .render import _fmt, _fmt_cents
from .risk import KILL_SWITCH_PATH, ProposedOrder, RiskGate
from .strategies.three_leg import runner as _three_leg_runner
from .strategies.three_leg.screen import ThreeLegParams

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


@app.command(name="three-leg")
def three_leg(
    gender: str = typer.Option("both", help="men | women | both"),
    player: Optional[List[str]] = typer.Option(
        None, "--player", "-p", help="Player name substring(s); repeatable."),
    bankroll: float = typer.Option(100.0, help="Bankroll for Kelly sizing."),
    kelly: float = typer.Option(0.5, help="Kelly fraction (0.5 = half-Kelly)."),
    fatigue_coef: float = typer.Option(
        0.20, help="Pts added to a long-win leg's fair per extra-set, per rest-day."),
    rest_days: int = typer.Option(
        1, help="QF→SF turnaround in days; fewer ⇒ bigger length hedge."),
    match_edge: float = typer.Option(
        0.0, help="Your edge over the de-vigged match fair (0 ⇒ no match leg)."),
    title_edge: float = typer.Option(
        0.0, help="Your edge over the title YES mid (0 ⇒ no title leg)."),
    json_out: bool = typer.Option(
        False, "--json", help="Emit a structured JSON snapshot (for the research agent)."),
    execute: bool = typer.Option(
        False, "--execute",
        help="Route sized legs through compliance→risk (places orders only when "
             "config dry_run is false)."),
) -> None:
    """Back each QF favourite (match + title) and hedge their win-LENGTH for SF fatigue.

    Three Kelly-sized YES legs per favourite: match, title, and 'wins but long'
    (men 3-1/3-2, women 2-1). The length hedge is upsized as the QF→SF turnaround
    shrinks — φ = fatigue_coef·extra_sets/rest_days is added to its fair. Legs 1-2
    size 0 at market unless you supply --match-edge/--title-edge. Screen-only unless
    --execute (which honors config dry_run). `--json` prints a machine-readable
    snapshot instead of the table and skips execution.
    """
    params = ThreeLegParams(
        bankroll=Decimal(str(bankroll)), kelly_fraction=Decimal(str(kelly)),
        fatigue_coef=Decimal(str(fatigue_coef)), rest_days=rest_days,
        match_edge=Decimal(str(match_edge)), title_edge=Decimal(str(title_edge)),
    )
    _three_leg_runner.run(
        params, gender=gender, players=player or None, execute=execute, json_out=json_out)


@app.command()
def calibrate() -> None:
    """Brier-score closed positions against Kalshi settlement (read-only; places nothing).

    Strategy-agnostic feedback loop: scores whatever proposed each position by joining
    its realized outcome to the predicted fair_prob recorded in the decisions journal,
    bucketed by source and category. Surfaces whether our probabilities are calibrated.
    """
    from .analysis.calibration import score_calibration

    cfg = load_config()
    with KalshiClient(cfg) as client, Journal() as journal:
        md = MarketData(client)
        report = score_calibration(journal, md)

    if report.scored == 0:
        console.print(
            f"[yellow]No positions scored.[/yellow] "
            f"unsettled={report.skipped_unsettled}, "
            f"no_prediction={report.skipped_no_prediction}"
        )
        return

    def _fmt3(v):
        return "—" if v is None else f"{v:.3f}"

    def _rows(table: Table, buckets) -> None:
        for b in sorted(buckets, key=lambda x: x.count, reverse=True):
            table.add_row(
                b.label, str(b.count), _fmt3(b.brier),
                _fmt3(b.mean_predicted), _fmt3(b.mean_realized),
            )

    table = Table(title=f"Calibration ({report.scored} positions scored)")
    for col in ("bucket", "n", "brier", "mean_pred", "mean_realized"):
        table.add_column(col, overflow="fold")
    _rows(table, [report.overall])
    table.add_section()
    _rows(table, report.by_source.values())
    table.add_section()
    _rows(table, report.by_category.values())
    console.print(table)
    console.print(
        f"[dim]skipped — unsettled: {report.skipped_unsettled}, "
        f"no prediction: {report.skipped_no_prediction}[/dim]"
    )


@app.command()
def hedge(
    ticker: Optional[str] = typer.Option(
        None, "--ticker", "-t",
        help="Position market to hedge. Omit to use your sole open position."),
    fair: float = typer.Option(
        0.0, help="Override fair P(position loses); else de-vig the market."),
    passive: float = typer.Option(
        0.05, help="Passive offset: suggest a hedge bid this far below the ask."),
) -> None:
    """Find the best hedge — or exit — for an open match position. Places nothing.

    Enumerates the equivalent 'position loses' instrument (the opponent's market
    in the same event), prices it against the de-vigged fair, and ALWAYS compares
    to simply exiting at the bid — flagging a hedge that exiting beats. Suggests a
    passive limit + a size clamped to your live risk caps.
    """
    from .hedge import HedgeQuote, Position, Rel, evaluate, exit_pnl

    def num(x):
        try:
            return Decimal(str(x))
        except Exception:
            return None

    cfg = load_config()
    with _client() as c:
        pf = Portfolio(c)
        livep = [p for p in pf.market_positions()
                 if num(p.get("position_fp")) and abs(num(p.get("position_fp"))) > 0]
        if not livep:
            console.print("[yellow]No open positions to hedge.[/yellow]")
            return
        if ticker:
            target = next((p for p in livep if p.get("ticker") == ticker), None)
            if not target:
                console.print(f"[red]No open position in {ticker}.[/red]")
                return
        elif len(livep) == 1:
            target = livep[0]
        else:
            console.print("[yellow]Multiple positions — pass --ticker. Open:[/yellow] "
                          + ", ".join(p.get("ticker") for p in livep))
            return

        tk = target["ticker"]
        signed = num(target.get("position_fp"))
        cnt = int(abs(signed))
        cost = abs(num(target.get("total_traded_dollars")
                       or target.get("market_exposure_dollars")) or Decimal("0"))
        avg = (cost / abs(signed)) if signed else Decimal("0")
        if signed < 0:
            console.print("[yellow]v1 handles long-YES match positions; this is a "
                          "short/NO position — exit symmetrically.[/yellow]")
            return

        tm = c.get(f"/markets/{tk}").get("market", {})
        event = tm.get("event_ticker")
        t_bid = num(tm.get("yes_bid_dollars"))
        sibs = [m for m in c.get("/markets", params={"event_ticker": event, "limit": 50})
                .get("markets", []) if m.get("status") == "active"]
        others = [m for m in sibs if m.get("ticker") != tk]
        if len(sibs) != 2 or not others:
            console.print(f"[yellow]Event has {len(sibs)} active legs — a clean "
                          "1-for-1 hedge is only defined for 2-outcome markets.[/yellow]")
            return
        opp = others[0]
        t_mid = (t_bid + num(tm.get("yes_ask_dollars"))) / 2
        o_bid, o_ask = num(opp.get("yes_bid_dollars")), num(opp.get("yes_ask_dollars"))
        o_mid = (o_bid + o_ask) / 2
        fair_lose = Decimal(str(fair)) if fair else (o_mid / (t_mid + o_mid))

        pos = Position(ticker=tk, side="yes", count=cnt, avg_cost=avg)
        q = HedgeQuote(label=f"{opp.get('yes_sub_title')} YES", ticker=opp["ticker"],
                       buy_side="yes", ask=o_ask, fair=fair_lose, rel=Rel.EQUIVALENT)
        ev = evaluate(pos, q, exit_bid=t_bid)
        ex = exit_pnl(pos, t_bid)

        bid = max(Decimal("0.01"), o_ask - Decimal(str(passive)))
        acct = pf.account_state(opp["ticker"])
        r = cfg.risk
        room = min(acct.balance_usd - acct.total_exposure_usd,
                   r.max_total_exposure_usd - acct.total_exposure_usd,
                   r.max_per_position_usd - acct.position_exposure_usd)
        max_ct = max(0, min(int(room / bid) if bid > 0 else 0,
                            cnt, r.max_contracts_per_order))

        t = Table(title=f"Hedge for {tk}  ({cnt} YES @ {_fmt_cents(avg)})",
                  header_style="bold")
        for col in ("option", "cost", "edge", "if position loses", "note"):
            t.add_column(col)
        t.add_row("EXIT — sell YES @ bid", _fmt_cents(t_bid), "—",
                  f"{_fmt_cents(t_bid)} realized", f"P&L {ex:+.2f}")
        t.add_row(f"HEDGE — buy {q.label}", _fmt_cents(o_ask),
                  f"{ev.edge_per_contract:+.2f}",
                  f"locked P&L {ev.locked_pnl:+.2f}",
                  "[red]exit beats this[/red]" if ev.dominated_by_exit else "clean 1-for-1")
        console.print(t)
        rec = ("EXIT — selling realizes a better outcome than locking this hedge"
               if ev.dominated_by_exit else
               "HEDGE — the lay is not dominated by exiting")
        console.print(f"[bold]Recommendation:[/bold] {rec}.")
        if max_ct > 0:
            console.print(f"[dim]Passive option: buy {max_ct} {q.label} @ "
                          f"{_fmt_cents(bid)} (cap-clamped from {cnt}). Fills mainly if "
                          f"the position recovers — weak vs a fast adverse move. "
                          f"Places nothing.[/dim]")
        else:
            console.print("[dim]Risk caps leave no room for a hedge leg "
                          "(position already near the exposure cap).[/dim]")


if __name__ == "__main__":
    app()
