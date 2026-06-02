"""Rich rendering for the three-leg fatigue-hedge planner."""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from rich.console import Group
from rich.panel import Panel
from rich.table import Table

from ...render import _fmt_cents, _fmt_money, _fmt_signed_money, _new_table
from .compute import Leg, ThreeLegPlan
from .screen import ThreeLegParams

D = Decimal
ONE = D("1")


def _pct(x: Decimal) -> str:
    return f"{x * 100:.0f}%"


def _leg_rows(table: Table, legs: List[Leg]) -> None:
    for leg in legs:
        sized = leg.contracts >= 1
        cstyle = "green" if sized else "dim"
        table.add_row(
            leg.label, _fmt_cents(leg.price), _pct(leg.market_fair), _pct(leg.fair),
            f"{leg.kelly_f * 100:.1f}%",
            f"[{cstyle}]{leg.contracts}[/{cstyle}]",
            _fmt_money(leg.cost) if sized else "[dim]—[/dim]",
        )


def _plan_panel(plan: ThreeLegPlan, params: ThreeLegParams) -> Panel:
    g = "men" if plan.gender == "men" else "women"
    legs_tbl = Table(box=None, pad_edge=False, show_edge=False)
    for col, just in [("Leg", "left"), ("Ask", "right"), ("Mkt", "right"),
                      ("Fair", "right"), ("Kelly", "right"), ("Ct", "right"), ("Cost", "right")]:
        legs_tbl.add_column(col, justify=just)
    all_legs = [plan.match_leg] + ([plan.title_leg] if plan.title_leg else []) + plan.long_legs
    _leg_rows(legs_tbl, all_legs)

    # Outcome P&L grid.
    pnl = Table(box=None, pad_edge=False, show_edge=False)
    for col, just in [("Outcome", "left"), ("Prob", "right"),
                      ("Net if no title", "right"), ("Net if wins title", "right")]:
        pnl.add_column(col, justify=just)
    for o in plan.outcomes:
        lose = plan.net_pnl(o, title_win=False)
        win = plan.net_pnl(o, title_win=True) if (o.is_win and plan.title_leg) else lose
        pnl.add_row(o.label, _pct(o.prob),
                    _fmt_signed_money(lose), _fmt_signed_money(win))

    # EV (uses market-implied conditional title prob).
    ev_line = ""
    if plan.title_leg and plan.match_leg.market_fair > 0:
        p_cond = min(ONE, plan.title_leg.market_fair / plan.match_leg.market_fair)
        ev = plan.expected_value(p_title_given_advance=p_cond)
        ev_line = f"  EV ≈ {_fmt_signed_money(ev)} • cost {_fmt_money(plan.total_cost)}"

    hedge = sum((leg.cost for leg in plan.long_legs), D("0"))
    if plan.hedge_pending:
        hedge_str = f"[yellow]hedge PENDING → re-run when {plan.pending_hedge_event} lists[/yellow]"
    elif plan.note:
        hedge_str = f"hedge {_fmt_money(hedge)} [yellow]({plan.note})[/yellow]"
    else:
        hedge_str = f"hedge {_fmt_money(hedge)}"
    title = f"[bold]{plan.name}[/bold] · {g} · rest {plan.rest_days}d · {hedge_str}"
    return Panel(Group(legs_tbl, pnl, ev_line) if ev_line else Group(legs_tbl, pnl),
                 title=title, title_align="left", border_style="dim")


def build_three_leg_view(
    plans: List[ThreeLegPlan], params: ThreeLegParams, *, status: Optional[str] = None,
) -> Group:
    legend = (
        f"Bankroll {_fmt_money(params.bankroll, 0)} • {params.kelly_fraction}×Kelly • "
        f"fatigue_coef {params.fatigue_coef} • rest {params.rest_days}d • "
        f"match_edge {params.match_edge} • title_edge {params.title_edge}   •   "
        "Leg1 match YES, Leg2 title YES, Leg3 = wins-but-LONG (fatigue hedge, "
        "φ = coef·extra_sets/rest_days added to its fair).   •   "
        "[yellow]Legs size 0 at market without an edge — pass --match-edge/--title-edge "
        "for directional legs; the length hedge carries its own φ edge.[/yellow]"
    )
    header = _new_table("French Open — three-leg fatigue-hedge planner", legend, status)
    header.add_column("")
    actionable = [p for p in plans if p.actionable]
    header.add_row(f"{len(actionable)} actionable of {len(plans)} favourites screened")
    panels = [_plan_panel(p, params) for p in plans] or [
        Panel("[dim]no open matches with a priced favourite[/dim]", border_style="dim")]
    return Group(header, *panels)
