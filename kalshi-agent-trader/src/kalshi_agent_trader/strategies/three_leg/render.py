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
                      ("Fair", "right"), ("K/Hdg%", "right"), ("Ct", "right"), ("Cost", "right")]:
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


def _spread_pnl(plan: ThreeLegPlan, *, won_match: bool, won_title: bool) -> Decimal:
    """P&L of the match+title spread ALONE (no length hedge cost or payoff)."""
    cost = plan.match_leg.cost + (plan.title_leg.cost if plan.title_leg else D("0"))
    pay = D("0")
    if won_match:
        pay += D(plan.match_leg.contracts)
        if won_title and plan.title_leg:
            pay += D(plan.title_leg.contracts)
    return pay - cost


def _long_label(plan: ThreeLegPlan, o) -> str:
    """e.g. 'wins 3-2 · 5 sets' for the full-distance outcome."""
    won = 2 if plan.gender == "women" else 3
    return f"wins {won}-{o.sets_lost} · {won + o.sets_lost} sets"


def build_scenario_summary(plans: List[ThreeLegPlan]) -> Panel:
    """Per player: line 1 = the match+title spread by terminal scenario; line 2 =
    the FULL position (spread + length hedge) if the match goes the distance."""
    tbl = Table(box=None, pad_edge=False, show_edge=False)
    for col, just in [("Player", "left"), ("Position", "left"),
                      ("Loses match", "right"), ("Wins, no title", "right"),
                      ("Wins title", "right")]:
        tbl.add_column(col, justify=just)

    actionable = [p for p in plans if p.actionable]
    for i, p in enumerate(actionable):
        wins = [o for o in p.outcomes if o.is_win]
        loses = [o for o in p.outcomes if not o.is_win]
        if not wins:
            continue
        wt = lambda val: _fmt_signed_money(val) if p.title_leg else "—"

        # Line 1 — directional spread only.
        tbl.add_row(
            f"[bold]{p.name}[/bold]", "spread (match+title)",
            _fmt_signed_money(_spread_pnl(p, won_match=False, won_title=False)),
            _fmt_signed_money(_spread_pnl(p, won_match=True, won_title=False)),
            wt(_spread_pnl(p, won_match=True, won_title=True)))

        # Line 2 — full position incl. length hedge, in the full-distance result.
        long_win = max(wins, key=lambda o: o.sets_lost)
        if p.long_legs and long_win.sets_lost > 0:
            long_lose = max(loses, key=lambda o: o.sets_lost) if loses else None
            hedge_cost = sum((leg.cost for leg in p.long_legs), D("0"))
            tbl.add_row(
                "", f"+ hedge ({_fmt_money(hedge_cost)}) · {_long_label(p, long_win)}",
                _fmt_signed_money(p.net_pnl(long_lose, title_win=False)) if long_lose else "—",
                _fmt_signed_money(p.net_pnl(long_win, title_win=False)),
                wt(p.net_pnl(long_win, title_win=True)))
        else:
            tbl.add_row("", "[dim]+ hedge: none (no length market / no title)[/dim]",
                        "", "", "")
        if i < len(actionable) - 1:
            tbl.add_row("", "", "", "", "")

    cap = ("[dim]Line 1 is the match+title spread by itself. Line 2 adds the length hedge "
           "and shows the full position when the match goes the distance (5 sets men / 3 sets "
           "women): the hedge pays, trading a little give-back on a quick win for a cushion on "
           "a tiring one. Women's opponent-set legs also pay in a 3-set loss.[/dim]")
    return Panel(Group(tbl, cap), title="Scenario P&L — spread, then + length hedge",
                 title_align="left", border_style="dim")


def _leg_json(leg: Leg) -> dict:
    return {
        "label": leg.label,
        "ticker": leg.ticker,
        "side": "yes",
        "ask": float(leg.price),
        "market_fair": float(leg.market_fair),
        "fair": float(leg.fair),
        "kelly_or_hedge_ratio": float(leg.kelly_f),
        "contracts": leg.contracts,
        "cost_usd": float(leg.cost),
        "extra_sets": leg.extra_sets,
        "sized": leg.sized,
    }


def build_three_leg_json(plans: List[ThreeLegPlan], params: ThreeLegParams) -> dict:
    """Machine-readable snapshot of every screened plan.

    The research agent captures this (`three-leg --json`) instead of scraping the
    Rich table; the executor agent diffs a trade ticket's prices/sizes against a
    fresh copy of it. Decimals are emitted as floats for jq-friendliness — the
    underlying sizing math stays exact in `compute.py`.
    """
    out_plans = []
    for p in plans:
        all_legs = [p.match_leg] + ([p.title_leg] if p.title_leg else []) + p.long_legs
        ev = None
        if p.title_leg and p.match_leg.market_fair > 0:
            p_cond = min(ONE, p.title_leg.market_fair / p.match_leg.market_fair)
            ev = float(p.expected_value(p_title_given_advance=p_cond))
        outcomes = []
        for o in p.outcomes:
            lose = p.net_pnl(o, title_win=False)
            win = p.net_pnl(o, title_win=True) if (o.is_win and p.title_leg) else lose
            outcomes.append({
                "label": o.label,
                "prob": float(o.prob),
                "is_win": o.is_win,
                "sets_lost": o.sets_lost,
                "net_if_no_title_usd": float(lose),
                "net_if_wins_title_usd": float(win),
            })
        out_plans.append({
            "player": p.name,
            "gender": p.gender,
            "rest_days": p.rest_days,
            "actionable": bool(p.actionable),
            "hedge_pending": p.hedge_pending,
            "pending_hedge_event": p.pending_hedge_event,
            "note": p.note,
            "legs": [_leg_json(leg) for leg in all_legs],
            "outcomes": outcomes,
            "total_cost_usd": float(p.total_cost),
            "ev_usd": ev,
        })
    return {
        "strategy": "three-leg",
        "params": {
            "bankroll_usd": float(params.bankroll),
            "kelly_fraction": float(params.kelly_fraction),
            "fatigue_coef": float(params.fatigue_coef),
            "rest_days": params.rest_days,
            "match_edge": float(params.match_edge),
            "title_edge": float(params.title_edge),
            "fee_rate": float(params.fee_rate),
        },
        "actionable_count": sum(1 for p in plans if p.actionable),
        "screened_count": len(plans),
        "plans": out_plans,
    }


def build_three_leg_view(
    plans: List[ThreeLegPlan], params: ThreeLegParams, *, status: Optional[str] = None,
) -> Group:
    legend = (
        f"Bankroll {_fmt_money(params.bankroll, 0)} • {params.kelly_fraction}×Kelly • "
        f"fatigue_coef {params.fatigue_coef} • rest {params.rest_days}d • "
        f"match_edge {params.match_edge} • title_edge {params.title_edge}   •   "
        "Leg1 match YES, Leg2 title YES, Leg3 = wins-but-LONG (fatigue HEDGE, "
        "sized to ρ = clamp(coef·extra_sets/rest_days,0,1) × the title position).   •   "
        "[yellow]Match/title size 0 at market without an edge — pass --match-edge/--title-edge. "
        "No title position ⇒ no hedge (K/Hdg% shows the hedge ratio for length legs).[/yellow]"
    )
    header = _new_table("French Open — three-leg fatigue-hedge planner", legend, status)
    header.add_column("")
    actionable = [p for p in plans if p.actionable]
    header.add_row(f"{len(actionable)} actionable of {len(plans)} favourites screened")
    panels = [_plan_panel(p, params) for p in plans] or [
        Panel("[dim]no open matches with a priced favourite[/dim]", border_style="dim")]
    summary = [build_scenario_summary(plans)] if any(p.actionable for p in plans) else []
    return Group(header, *summary, *panels)
