"""Shared CLI helpers: client factory, console, and rich formatters.

Imported by every command module; depends only on core plumbing, never on a
strategy module.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from rich import box
from rich.console import Console
from rich.table import Table

from ..client import KalshiClient
from ..config import load_config

console = Console()


def _client() -> KalshiClient:
    return KalshiClient(load_config())


def resolve_dry_run(
    config_dry_run: bool,
    *,
    live: bool = False,
    dry_run_override: Optional[bool] = None,
) -> bool:
    """Resolve execution mode from config plus CLI overrides."""
    if dry_run_override is not None:
        return dry_run_override
    if live:
        return False
    return config_dry_run


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
