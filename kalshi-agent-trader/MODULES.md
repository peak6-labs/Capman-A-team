# Module Reference

Quick-reference for every file in `kalshi-agent-trader/`.

---

## CLI

```bash
uv run kalshi-trader <command>
```

| Command | Auth? | What it does |
|---|---|---|
| `exchange` | No | Exchange status |
| `status` | Yes | Account balance + open positions |
| `positions` | Yes | List current positions |
| `auth-check` | Yes | Verify key + signing |
| `markets` | No | List open markets |
| `events` | No | List events with categories |
| `orderbook <TICKER>` | No | Show order book |
| `order` | Yes | Place one order manually (dry-run by default) |
| `cancel <ORDER_ID>` | Yes | Cancel a resting order |
| `kill` / `unkill` | — | Engage/clear the kill switch |
| `three-leg` | No* | Screen QF favourites for the three-leg hedge. `--json` = snapshot for the research agent; `--execute` routes legs through the gates (*auth only when actually executing) |
| `hedge` | Yes | Post-fill: exit-vs-hedge for an open match position (places nothing) |

> Exploratory commands (`scan`, `run`, `rv-scan`, `rv-run`, `monitor`, `agent-scan`,
> `agent-run`, `breakeven`, `dip`) were retired with their modules to `attic/`.

## Agents & skills (the prompt layer)

The research/executor split lives in prompts, not Python — see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

| File | Role |
|---|---|
| `.claude/agents/research-analyst.md` | PROPOSE — screen, re-confirm edge, write a GO/NO-GO trade ticket |
| `.claude/agents/executor.md` | DISPOSE — validate a ticket, run the gate chain, dry-run first |
| `.claude/skills/three-leg-research/` | Research playbook + edge checklist + ticket rubric |
| `.claude/skills/three-leg-execute/` | Execution playbook: drift/staleness check, confirmation gate |
| `.claude/skills/find-hedge/` | Post-fill de-risk playbook |
| `research/tickets/TEMPLATE.md` | The trade-ticket contract between the two agents |

---

## Source Files

### Core Infrastructure

**`config.py`**
Loads `config.yaml` + `.env` into typed config objects. Entry point for all settings.
- `SecretsConfig` — API keys (env/dotenv)
- `ComplianceConfig` / `RiskConfig` / `RuntimeConfig` / `StrategyConfig` — from `config.yaml`
- `StrategyConfig` — brain sizing + monitor exit-trigger tunables (`strategy:` section)
- `load_config()` — returns assembled `AppConfig`

**`util.py`**
Shared helpers: `hours_until()` (ISO timestamp → hours from now) and `volume_fp()`.
One definition each, used by scanner / monitor / agent strategy.

**`render.py`**
Rich rendering for the CLI: generic value formatters plus the French-Open
breakeven/fade table builders. Leaf module — keeps `cli.py` thin.

**`client.py`**
HTTP client for the Kalshi REST API.
- Rate limiting (stays under Basic tier)
- Retries on 5xx / 429 with backoff
- RSA-PSS request signing via `auth.py`

**`auth.py`**
RSA-PSS signer. Signs `{timestamp}{METHOD}{path}` and returns the three auth headers.

**`models.py`**
Pydantic models for API objects: `Market`, `Event`, `Series`, `Orderbook`, `Balance`.
Prices are `Decimal` (Kalshi returns dollar strings, not cents).

**`journal.py`**
SQLite audit log. Append-only. Tables:
- `decisions` — every signal and gate outcome
- `orders` — every order placed
- `fills` — fills received
- `positions` — open/closed positions (used by monitor)

---

### Trading Gates (compliance → risk → execution, in fixed order)

**`compliance.py`**
PEAK6 hard gate. Runs before risk. Non-overridable by agents.
Allows: Politics, World, Climate and Weather, Sports.
Blocks: Financials, Companies, Crypto, Economics + keyword backstop.

**`risk.py`**
Configurable caps (exposure, per-position, daily loss, kill switch).
Also enforces no-leverage rule (exposure ≤ live balance).
Returns `approved_count` after clamping to the tightest cap.

**`execution.py`**
Single write path. Chains compliance → risk → V2 order POST.
Every outcome (placed / dry_run / rejected) is journaled.
`build_v2_order_body()` isolates the V2 field names for easy correction.

---

### Data Access

**`market_data.py`**
Fetches markets, events, series, orderbooks. Caches event→category lookups.
`category_for_market(market)` is the compliance helper used by scanner + compliance gate.

**`portfolio.py`**
Authenticated reads: balance, market positions, resting orders, fills.
`account_state(ticker)` builds the `AccountState` the risk gate needs.

---

### Three-leg fatigue-hedge strategy (the committed strategy)

**`strategies/three_leg/`** — `compute.py` (pure sizing/P&L: de-vig, Kelly, hedge-ratio,
EV by outcome), `screen.py` (assemble plans from the live book; pair title by competitor
UUID; turnaround-weighted hedge), `length.py` (discover exact-score / set-winner hedge
legs), `orders.py` (plan → orders), `render.py` (Rich view + `build_three_leg_json`),
`runner.py` (fetch → size → render/JSON → optional gated execute). Driven by the
`three-leg` CLI command and the research/executor agents.

**`tennis_screen.py`** / **`breakeven.py`** — shared tennis infra the three-leg screen
depends on (series constants, competitor pairing, de-vig + breakeven fee math).

---

> **Archived (Phase 4/5 below).** The systematic longshot core and the generic LLM agent
> were retired to `attic/` and are no longer wired into the package. Kept for reference.

### Phase 4 — Systematic Strategy *(archived → `attic/`)*

**`scanner.py`**
Scans open markets for cheap-tail candidates.
Filters: price 1–10¢, spread < 50¢, volume ≥ 10 FP contracts, 4–48h to expiry.
Compliance gate applied per-market at scan time.
Returns `List[ScanCandidate]` sorted by `price × hours` score.

**`polymarket.py`**
Fetches reference prices from Polymarket's public Gamma API.
Matches by title similarity (`difflib`). Returns `None` if no confident match (< 0.50).
Used by brain to cross-calibrate probability estimates.

**`brain.py`**
Converts scan candidates into `ProposedOrder` objects.
Probability estimate = heuristic longshot discount, blended 70% Polymarket / 30% heuristic when a match is found.
Sizes positions via quarter-Kelly, hard-capped at 1% of bankroll per position.

**`pipeline.py`**
One-shot orchestrator: Scanner → Brain → Executor.
Returns `{"scanned", "proposed", "placed", "dry_run", "rejected"}` counts.

**`monitor.py`**
Polls open positions (from journal) and closes on trigger:
- `TARGET_HIT` — bid ≤ 15% of entry
- `NEAR_EXPIRY` — < 2h to resolution
- `STALE_THESIS` — held 24h with < 2% price move
- `RESOLVED` — market settled

Phase 6 will replace `run_loop` polling with WebSocket events.

---

### Phase 5 — LLM Agents *(archived → `attic/agents/`)*

**`agents/base.py`**
Shared types: `Signal` (ticker, side, fair_prob, confidence, rationale) and `AgentError`.

**`agents/market_agent.py`**
Claude-powered scanner + analyst using `claude-sonnet-4-6`.
- `find_opportunities(events)` — batches up to 50 events; Claude identifies mispriced tails
- `evaluate(candidate, poly_ref)` — refines fair_prob for a single market
- System prompt cached with `ephemeral` cache_control (shared across both methods)
- Structured output via tool use — no free-text parsing

**`agents/agent_strategy.py`**
Agent-enhanced pipeline:
1. Fetch open events (pre-filtered to allowed categories)
2. Claude scans for opportunities (`find_opportunities`)
3. Each signal runs through compliance + scanner filters
4. Claude evaluates survivors with Polymarket reference (`evaluate`)
5. Submit through compliance → risk → execution gate chain

---

## Tests

```
tests/
  test_auth.py          RSA-PSS header format
  test_breakeven.py     Fee formula + scenario math (shared tennis infra)
  test_compliance.py    Category allowlist, keyword backstop
  test_config.py        load_config(), Decimal coercion, three_leg config
  test_execution.py     Gate ordering, dry-run, V2 body, live path
  test_hedge.py         Exit-vs-hedge scoring
  test_portfolio.py     Account-state assembly
  test_risk.py          All cap types, kill switch, size clamping
  test_tennis_screen.py Player pairing, breakeven scenarios
  test_three_leg.py     Kelly/hedge-ratio sizing, set-proxy, net P&L, EV
  attic/                Tests for archived strategies (excluded from collection)
```

**80 tests, all pass.** (`pyproject.toml` excludes `tests/attic`.)

---

## Config files

| File | Purpose |
|---|---|
| `config.yaml` | Risk caps, compliance lists, runtime tuning. Safe to commit. |
| `.env` | Secrets (API keys, key path). **Never commit.** |
| `pyproject.toml` | Dependencies + entry point (`kalshi-trader` CLI) |
