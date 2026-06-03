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

    # Outcome P&L grid. Leg 2 (B's title) only matters in a B-wins-the-match branch,
    # so the "if B wins title" column is blank for A-win rows.
    pnl = Table(box=None, pad_edge=False, show_edge=False)
    for col, just in [("Outcome", "left"), ("Prob", "right"),
                      ("Net", "right"), ("Net if B wins title", "right")]:
        pnl.add_column(col, justify=just)
    for o in plan.outcomes:
        base = plan.net_pnl(o, b_wins_title=False)
        if o.a_wins_match:
            pnl.add_row(o.label, _pct(o.prob), _fmt_signed_money(base), "—")
        else:
            won = plan.net_pnl(o, b_wins_title=True) if plan.title_leg else base
            pnl.add_row(o.label, _pct(o.prob),
                        _fmt_signed_money(base), _fmt_signed_money(won))

    # EV — split on P(B wins title | B wins the match) = B title mid / P(B wins match).
    ev_line = ""
    p_b_match = ONE - plan.match_leg.market_fair          # P(B beats A)
    if plan.title_leg and p_b_match > 0:
        p_cond = min(ONE, plan.title_leg.market_fair / p_b_match)
        ev = plan.expected_value(p_b_title_given_advance=p_cond)
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


def build_scenario_summary(plans: List[ThreeLegPlan]) -> Panel:
    """Per player, the P&L across the key scenarios — showing the 5-set OUT (leg 3)
    cushioning the worry case (B beats A in 5 but doesn't win the title)."""
    tbl = Table(box=None, pad_edge=False, show_edge=False)
    for col, just in [("Player", "left"), ("Scenario", "left"),
                      ("Spread (L1+L2)", "right"), ("+ 5-set out (L3)", "right")]:
        tbl.add_column(col, justify=just)

    actionable = [p for p in plans if p.actionable]
    for i, p in enumerate(actionable):
        m = D(p.match_leg.contracts)
        t = D(p.title_leg.contracts) if p.title_leg else D("0")
        l3 = sum((D(leg.contracts) for leg in p.long_legs), D("0"))
        spread_cost = p.match_leg.cost + (p.title_leg.cost if p.title_leg else D("0"))
        total = p.total_cost

        def row(name, scenario, spread_pay, full_pay, dim=False):
            s = f"[dim]{scenario}[/dim]" if dim else scenario
            tbl.add_row(name, s, _fmt_signed_money(spread_pay - spread_cost),
                        _fmt_signed_money(full_pay - total))

        row(f"[bold]{p.name}[/bold]", "A wins the match", m, m)
        row("", "B wins, then wins the title", t, t)
        row("", "B wins in ≤4, no title (worry)", D("0"), D("0"), dim=True)
        row("", "[yellow]B wins in 5, no title (worry)[/yellow]", D("0"), l3)
        if i < len(actionable) - 1:
            tbl.add_row("", "", "", "")

    cap = ("[dim]Legs 1 and 2 are on DIFFERENT players: buy A to win the match, buy B (the "
           "underdog) to win the title. The worry case is B beating A but not winning it all — "
           "both directional legs die. Leg 3 (B wins in 5) pays ONLY there, in the bottom row, "
           "cushioning the grind. A winning in 5 pays leg 3 nothing.[/dim]")
    return Panel(Group(tbl, cap), title="Scenario P&L — spread, then + the 5-set out",
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
        p_b_match = ONE - p.match_leg.market_fair          # P(B beats A)
        if p.title_leg and p_b_match > 0:
            p_cond = min(ONE, p.title_leg.market_fair / p_b_match)
            ev = float(p.expected_value(p_b_title_given_advance=p_cond))
        outcomes = []
        for o in p.outcomes:
            base = p.net_pnl(o, b_wins_title=False)
            b_title = (p.net_pnl(o, b_wins_title=True)
                       if (not o.a_wins_match and p.title_leg) else None)
            outcomes.append({
                "label": o.label,
                "prob": float(o.prob),
                "a_wins_match": o.a_wins_match,
                "leg3_pays": float(o.leg3_pay) > 0,
                "net_usd": float(base),
                "net_if_b_wins_title_usd": float(b_title) if b_title is not None else None,
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
        f"rest {params.rest_days}d • match_edge {params.match_edge} • title_edge {params.title_edge}"
        "   •   Leg1 = A (favourite) wins the MATCH · Leg2 = B (opponent) wins the TITLE · "
        "Leg3 = B wins the match in 5 SETS (the OUT — sized to ρ × the directional position).   •   "
        "[yellow]Legs 1-2 size 0 at market without an edge — pass --match-edge/--title-edge. "
        "Worry case = B beats A but wins no title; the out pays ONLY there (B in 5).[/yellow]"
    )
    header = _new_table("French Open — three-leg (A match · B title · B-in-5 out)", legend, status)
    header.add_column("")
    actionable = [p for p in plans if p.actionable]
    header.add_row(f"{len(actionable)} actionable of {len(plans)} favourites screened")
    panels = [_plan_panel(p, params) for p in plans] or [
        Panel("[dim]no open matches with a priced favourite[/dim]", border_style="dim")]
    summary = [build_scenario_summary(plans)] if any(p.actionable for p in plans) else []
    return Group(header, *summary, *panels)
