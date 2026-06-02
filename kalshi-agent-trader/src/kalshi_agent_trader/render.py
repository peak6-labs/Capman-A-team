"""Rich rendering helpers for the CLI.

Holds the generic value formatters plus the French-Open breakeven/fade table
builders. Split out of cli.py so the command wiring stays thin. This module is a
leaf — it imports only rich, stdlib, and the tennis_screen row types.
"""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from rich import box
from rich.table import Table

from .tennis_screen import PlayerRow


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
