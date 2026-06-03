# attic — retired exploratory strategies

These modules were the exploration phase. The project has since committed to the
**three-leg fatigue-hedge** strategy (see `strategies/three_leg/`) wrapped in a
research-agent / executor-agent architecture. Everything here is kept for
reference and recoverability — **none of it is wired into the package** (the CLI,
config, and gates no longer import it), so the relative imports inside these
files are dormant and may be stale.

| Module | What it was |
|---|---|
| `scanner.py`, `brain.py`, `pipeline.py` | Longshot-bias systematic core (scan → Kelly → execute) |
| `relative_value/` | Kalshi-only relative-value signals from external reference prices |
| `dip_reversion/` | Intraday title-dip mean-reversion (the one play that backtested net-positive) |
| `agents/` | Generic Claude market scanner/analyst (`market_agent`, `agent_strategy`) |
| `polymarket.py` | Polymarket Gamma API reference-price client |
| `monitor.py` | Generic position exit-trigger poll loop |

Their tests live under `tests/attic/` and are excluded from collection
(`pyproject.toml` → `[tool.pytest.ini_options]`).

To revive one: move it back into the package, restore its imports, re-add its CLI
command in `cli.py`, and move its test back into `tests/`.
