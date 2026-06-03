"""Rich rendering for the dip detector (reuses the shared formatters in render.py)."""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from rich.table import Table

from ...render import (
    _fmt_cents,
    _fmt_money,
    _fmt_signed_cents,
    _fmt_signed_money,
    _new_table,
)
from .detector import DipParams, DipSignal

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
    moved = s.match_mid - s.match_anchor
    color = "red" if moved < 0 else "white"
    return f"[{color}]{_fmt_cents(s.match_mid)}[/{color}]\n[dim]was {_fmt_cents(s.match_anchor)}[/dim]"


def _title_cell(s: DipSignal) -> str:
    entry = _fmt_cents(s.title_ask) if s.title_ask else "[dim]no ask[/dim]"
    return f"{entry}\n[dim]fair {_fmt_cents(s.fair_title)}[/dim]"


def _react_cell(s: DipSignal) -> str:
    react = _fmt_signed_cents(s.residual)
    if s.peak_residual > s.residual:
        react += f"\n[dim]peak {_fmt_cents(s.peak_residual)}[/dim]"
    if s.title_delta < 0:
        react += f"\n[dim]φ {s.overreaction_frac*100:.0f}%[/dim]"
    return react


def _size_cell(s: DipSignal) -> str:
    if s.stake <= 0:
        return "[dim]—[/dim]"
    color = "green" if s.est_profit > 0 else ("red" if s.est_profit < 0 else "white")
    return f"{_fmt_money(s.stake)}\n[{color}]{_fmt_signed_money(s.est_profit)}[/{color}]"


def build_dip_table(
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

    ordered = sorted(signals, key=lambda s: (_DIP_ACTION_RANK.get(s.action, 9), -s.residual))
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
