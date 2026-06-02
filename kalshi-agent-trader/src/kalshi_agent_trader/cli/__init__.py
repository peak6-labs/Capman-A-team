"""Kalshi agent-trader CLI — thin assembler.

Generic ops, the two-market screener (strategy 1), and the dip detector
(strategy 2) live in sibling modules. We merge their commands into one flat
``app`` so every command keeps its original name (``kalshi-trader dip``,
``breakeven``, ``order``, …) and the ``kalshi_agent_trader.cli:app`` entry point
is unchanged. Add a third strategy by writing a module with its own ``app`` and
appending it here.
"""

from __future__ import annotations

import typer

from . import dip, ops, strategy, two_market

app = typer.Typer(add_completion=False, help="Kalshi agent-trader CLI")
for _mod in (ops, two_market, dip, strategy):
    app.registered_commands += _mod.app.registered_commands


if __name__ == "__main__":
    app()
