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
| `order [--dry-run/--no-dry-run]` | Yes | Place one order manually |
| `cancel <ORDER_ID>` | Yes | Cancel a resting order |
| `kill` / `unkill` | — | Engage/clear the kill switch |
| `scan` | No | Run scanner, print candidates table |
| `run [--dry-run/--no-dry-run]` | Yes | Full scan → brain → execute cycle |
| `rv-run [--dry-run/--no-dry-run]` | Yes | Relative-value scan → execute cycle |
| `monitor [--once] [--dry-run/--no-dry-run]` | Yes | Poll open positions and close on exit triggers |
| `agent-scan` | Yes | Claude finds opportunities (dry mode) |
| `agent-run [--dry-run/--no-dry-run]` | Yes | Agent-enhanced scan → evaluate → execute |
| `breakeven` | No | French Open two-market hedge/fade screener |
| `dip [--execute] [--dry-run/--no-dry-run]` | No/Yes | Intraday title-dip screener; optionally route orders |

---

Execution commands default to `risk.dry_run` from `config.yaml`. Use `--dry-run`
to force simulation for a test run, or `--no-dry-run` to allow real orders for
that invocation. `--live` remains as an alias for `--no-dry-run`.

```bash
uv run kalshi-trader run --dry-run
uv run kalshi-trader rv-run --dry-run
uv run kalshi-trader agent-run --dry-run
uv run kalshi-trader dip --execute --dry-run
uv run kalshi-trader order <TICKER> --side yes --price 0.10 --count 1 --dry-run
```

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

### Phase 4 — Systematic Strategy

**`scanner.py`**
Scans open markets for cheap-tail candidates.
Filters: price 1–10¢, spread < 50¢, volume ≥ 10 FP contracts, 4–48h to expiry.
Compliance gate applied per-market at scan time.
Returns `List[ScanCandidate]` sorted by `price × hours` score.

**`polymarket.py`**
Fetches reference prices from Polymarket's public Gamma API.
Matches by title similarity (`difflib`). Returns `None` if no confident match (< 0.50).
Used by brain to cross-calibrate probability estimates.

**`sportsbook_scrape.py`**
Targeted sportsbook scraping for agent-proposed Kalshi trades only.
Fetches explicitly configured DraftKings/FanDuel/etc. URLs per ticker, parses
American odds near a configured outcome label, converts them to implied
probability, and can blend/reject before Kalshi execution. It does not scan or
poll sportsbook slates.

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

### Phase 5 — LLM Agents

**`agents/base.py`**
Shared types: `Signal` (ticker, side, fair_prob, confidence, rationale) and `AgentError`.

**`agents/scout_agent.py`**
Cheap, high-volume triage tier (Haiku by default; rejects Sonnet models). Its misses are
recoverable downstream, so a cheaper model is the right trade-off.
- `find_opportunities(events)` — batches events; identifies mispriced tails
- `find_market_opportunities(markets)` — triages live market snapshots, returns ≤12 signals

**`agents/analyst_agent.py`**
High-stakes per-candidate pricing tier (Sonnet-only guard).
- `evaluate(candidate, poly_ref)` — refines fair_prob for a single market
- Fails closed: an empty model response returns a `watch` action (not a synthetic trade)

**`agents/_signals.py`**
Shared `submit_signals` tool schema, `parse_signal`, and the cached-system-prompt
`call_agent` helper used by both agents. Structured output via tool use — no free-text parsing.

**`agents/agent_strategy.py`**
Agent-enhanced pipeline (model IDs from `config.models`):
1. Fetch open events (pre-filtered to allowed categories)
2. Scout triages live market snapshots (`find_market_opportunities`)
3. Each signal runs through compliance + scanner filters
4. Analyst evaluates survivors with Polymarket reference (`evaluate`)
5. Submit through compliance → risk → execution gate chain

**`analysis/calibration.py`**
Brier scoring of closed positions against Kalshi settlement (`Market.result`), bucketed by
source and category. Recovers predicted `fair_prob` from the `decisions` journal. Read-only;
surfaced via the `calibrate` CLI command.

---

### Specialty Screens

**`breakeven.py`** / **`tennis_screen.py`**
French Open two-market hedge/fade screener. Pairs each player's current-match
market with their tournament-winner market and shows breakeven prices for both strategies.

---

## Tests

```
tests/
  test_auth.py          RSA-PSS header format
  test_breakeven.py     Fee formula + scenario math
  test_brain.py         Kelly math, probability blend, proposal caps
  test_compliance.py    Category allowlist, keyword backstop
  test_config.py        load_config(), Decimal coercion, require_kalshi(), models tier
  test_agent_calibration.py  Brier scoring, settlement join, unsettled skip
  test_execution.py     Gate ordering, dry-run, V2 body, live path
  test_monitor.py       All four exit triggers
  test_polymarket.py    Title matching, threshold, error handling
  test_risk.py          All cap types, kill switch, size clamping
  test_scanner.py       Filters, compliance rejection, side selection
  test_sportsbook_scrape.py Targeted odds parsing + signal blending
  test_tennis_screen.py Player pairing, breakeven scenarios
  agents/
    test_scout_agent.py    Triage parsing, empty inputs, model guard, AgentError
    test_analyst_agent.py  evaluate, Sonnet guard, fail-closed fallback, parse_signal
    test_agent_strategy.py Quote/action helper math
```

**189 tests, all pass.**

---

## Config files

| File | Purpose |
|---|---|
| `config.yaml` | Risk caps, compliance lists, runtime tuning. Safe to commit. |
| `.env` | Secrets (API keys, key path). **Never commit.** |
| `pyproject.toml` | Dependencies + entry point (`kalshi-trader` CLI) |
