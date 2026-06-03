# Architecture — research agent / executor agent

The project has committed to one strategy — the **three-leg fatigue hedge** — wrapped in a
two-agent structure that makes the repo's long-standing contract explicit:

> **Agents PROPOSE. Deterministic gates DISPOSE.**

## The flow

```
                 ┌─────────────────────────┐
   live book ───▶│  research-analyst (agent)│  judgment: screen, re-confirm edge,
   schedule ───▶ │  + three-leg-research    │  pick ≤1 title anchor, decide GO/NO-GO
   news     ───▶ │  skill                   │
                 └────────────┬─────────────┘
                              │ writes
                              ▼
                   research/tickets/<date>-<player>.md      ◀── the PROPOSE→DISPOSE contract
                              │ reads
                              ▼
                 ┌─────────────────────────┐
                 │  executor (agent)        │  discipline: validate freshness + drift,
                 │  + three-leg-execute     │  dry-run first, human-confirm for live
                 │  skill                   │
                 └────────────┬─────────────┘
                              │ submits each leg
                              ▼
        compliance ─▶ risk ─▶ execution ─▶ journal     (deterministic Python; non-overridable)
```

## The prompt / code boundary

| Concern | Where | Why |
|---|---|---|
| Data access (markets, orderbook, portfolio) | **Python** — `client`, `market_data`, `portfolio` | Deterministic I/O |
| Sizing math (de-vig, Kelly, hedge-ratio, set-proxy, EV) | **Python** — `strategies/three_leg/{compute,screen,length}.py` | Reproducible, unit-tested; encodes the hard-won lessons |
| compliance → risk → execution → journal | **Python** — `compliance`, `risk`, `execution`, `journal` | PEAK6 hard gate; no agent can override it |
| Opportunity selection, edge re-confirmation, schedule inputs, GO/NO-GO, ticket authoring | **Prompt** — `research-analyst` agent + `three-leg-research` skill | The judgment layer |
| Staleness/drift checks, faithful submission, human-in-the-loop, fill reporting | **Prompt** — `executor` agent + `three-leg-execute` skill | Orchestration + discipline over the gate chain |
| Trade ticket (the hand-off) | **Markdown artifact** — `research/tickets/` | Self-contained contract + on-disk audit trail |

The CLI (`kalshi-trader`) is the **tool surface** both agents drive. Read-only commands
(`three-leg --json`, `markets`, `orderbook`, `events`, `status`) feed research; the write
path (`three-leg --execute`, `order`, `cancel`) is the executor's, always gated.

## Safety posture
- `config.yaml` ships with `risk.dry_run: true`. The three-leg structure backtested
  ~flat-EV; **nothing trades live until that flag is flipped and a GO ticket is confirmed.**
- The research agent's default verdict is NO-GO; a GO must name a specific edge.
- The executor never places live without explicit per-ticket human confirmation, and
  refuses stale or price-drifted tickets rather than "fixing" them.

## Map
- Agents: `.claude/agents/research-analyst.md`, `.claude/agents/executor.md`
- Skills: `.claude/skills/three-leg-research/`, `.claude/skills/three-leg-execute/`,
  `.claude/skills/find-hedge/` (post-fill de-risk)
- Ticket template: `research/tickets/TEMPLATE.md`
- Retired exploratory strategies: `src/kalshi_agent_trader/attic/` (see its README)
