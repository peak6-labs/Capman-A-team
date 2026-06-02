# Capman-A-Team

Hybrid Kalshi trading system. **All canonical code lives in
[`kalshi-agent-trader/`](kalshi-agent-trader/)** — a deterministic systematic core
for execution/risk with LLM agents generating signals. Agents propose; deterministic
gates (compliance → risk) dispose.

## Quickstart

```bash
cd kalshi-agent-trader
uv sync                      # install deps
cp .env.example .env         # fill in KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY_PATH, ANTHROPIC_API_KEY
uv run kalshi-trader status  # verify
```

See the package [README](kalshi-agent-trader/README.md) for the full CLI, the
compliance model, and verified Kalshi API notes. Strategy rationale and the edge
analysis live in [`docs/EDGES.md`](kalshi-agent-trader/docs/EDGES.md).

> An earlier flat prototype (`scanner.py`, `brain.py`, `executor.py`, …) once lived
> at the repo root. It was fully superseded by the package — which adds the
> compliance/risk gates, V2 orders, a SQLite journal, and tests — and has been
> removed. It remains in git history if you need it.
