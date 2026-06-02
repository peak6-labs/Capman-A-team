"""Two-market (hedge/fade) breakeven screener — strategy 1 commands."""

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
from ..market_data import MarketData
from ..strategy import StrategyParams, evaluate
from ..tennis.pairing import fetch_universe
from ..tennis_screen import PlayerRow, rows_from_universe
from .common import (
    _client,
    _fmt_cents,
    _fmt_money,
    _money_cell,
    _new_table,
    _scenario_cell,
    console,
)

app = typer.Typer()


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


def _signal_eval(r: PlayerRow, params: StrategyParams, room: Decimal):
    mm, tm = r.match_market, r.tourney_market
    if not mm or not tm:
        return None
    Z = Decimal("0")
    return evaluate(
        player=r.name, gender=r.gender,
        match_yes_ask=mm.yes_ask or Z, match_no_ask=mm.no_ask or Z,
        title_yes_bid=tm.yes_bid or Z, title_yes_ask=tm.yes_ask or Z,
        title_no_ask=tm.no_ask or Z, params=params, bucket_room=room,
    )


def _build_signal_table(rows, *, params: StrategyParams, status: Optional[str] = None) -> Table:
    bucket = params.max_bucket_frac * params.bankroll
    legend = (
        f"Bankroll {_fmt_money(params.bankroll, 0)} • {params.kelly_fraction}×Kelly • "
        f"min edge {params.min_edge*100:.0f}pts after fees • max/position "
        f"{params.max_position_frac*100:.0f}% • bucket cap {params.max_bucket_frac*100:.0f}% "
        f"({_fmt_money(bucket, 0)}) • FL de-bias α={params.fl_alpha}   •   "
        "edge = fair − price on the title leg; size = ½-Kelly; match leg = vehicle (≈0 edge)   •   "
        "[yellow]α is UNCALIBRATED — calibrate before live[/yellow]"
    )
    table = _new_table("French Open — STRATEGY signal (de-bias + Kelly sizing)", legend, status)
    table.add_column("Player", no_wrap=True, overflow="ellipsis", max_width=22)
    table.add_column("G", justify="center")
    table.add_column("Action", justify="center")
    table.add_column("Fair / quoted title", justify="right", no_wrap=True)
    table.add_column("Edge", justify="right", no_wrap=True)
    table.add_column("Title leg", justify="right", no_wrap=True)
    table.add_column("Match leg", justify="right", no_wrap=True)
    table.add_column("Max loss", justify="right", no_wrap=True)
    table.add_column("Rationale", overflow="fold")

    # Pass 1: edge for ranking (bucket not yet binding).
    scored = [(r, _signal_eval(r, params, bucket)) for r in rows]
    scored = [(r, d) for r, d in scored if d is not None]
    scored.sort(key=lambda it: it[1].edge, reverse=True)

    # Pass 2: allocate the shared bucket greedily by edge.
    room = bucket
    trades = 0
    for r, _ in scored:
        d = _signal_eval(r, params, room)
        if d.action == "PASS":
            continue
        room -= d.total_cost
        trades += 1
        color = "green" if d.action == "FADE" else "yellow"
        quoted = d.title_price if d.action == "HEDGE" else (Decimal("1") - d.title_price)
        table.add_row(
            r.name, r.gender[:1].upper(),
            f"[{color}]{d.action}[/{color}]",
            f"{_fmt_cents(d.fair_title_yes)} / {_fmt_cents(quoted)}",
            f"[green]{d.edge*100:.1f}pts[/green]",
            f"{_fmt_money(d.title_stake)} @ {_fmt_cents(d.title_price)}",
            _fmt_money(d.match_stake),
            f"[red]{_fmt_money(d.max_loss)}[/red]",
            d.rationale,
        )
    if trades == 0:
        table.add_row("—", "", "[dim]PASS[/dim]", "—", "—", "—", "—", "—",
                      "[dim]no setups cleared the edge gate[/dim]")
    table.caption = (f"{trades} trade(s) • {_fmt_money(bucket - room)} deployed of "
                     f"{_fmt_money(bucket)} bucket\n") + (table.caption or legend)
    return table


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
    signal: bool = typer.Option(
        False, "--signal",
        help="Strategy mode: de-bias edge gate + Kelly sizing (when/how to trade)."),
    bankroll: float = typer.Option(10000.0, help="Bankroll for Kelly sizing (signal mode)."),
    fl_alpha: float = typer.Option(
        1.09, help="FL de-bias (>1 favorites, <1 upsets). Fit=1.09 (~flat); >~1.2 to trade."),
    kelly: float = typer.Option(0.5, help="Kelly fraction (signal mode; moderate=0.5)."),
    min_edge: float = typer.Option(0.03, help="Min per-contract edge after fees (signal mode)."),
    allow_hedge: bool = typer.Option(
        True, "--allow-hedge/--no-hedge", help="Allow the gated hedge (signal mode)."),
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
    sparams = StrategyParams(
        bankroll=Decimal(str(bankroll)), fl_alpha=Decimal(str(fl_alpha)),
        kelly_fraction=Decimal(str(kelly)), min_edge=Decimal(str(min_edge)),
        fee_rate=fee, allow_hedge=allow_hedge,
    )

    def render(md: MarketData, status: Optional[str] = None):
        universe = fetch_universe(md, gender)   # fetched once, shared by all views
        if signal:
            rows = rows_from_universe(
                universe, players=player or None, stake_no=s_no, stake_tourney=s_t,
                price_basis=price_basis, strategy="hedge", fee_rate=fee)
            return _build_signal_table(rows, params=sparams, status=status)
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
