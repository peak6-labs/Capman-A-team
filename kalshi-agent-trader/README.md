# kalshi-agent-trader

Kalshi trading system committed to one strategy — the **three-leg fatigue hedge** — wrapped in a
**research-agent / executor-agent** architecture. A research agent **proposes** a trade ticket;
an executor agent **disposes** by routing it through deterministic **compliance → risk → execution**
gates. The judgment lives in prompts (Claude Code subagents + skills); the gates and sizing math
live in tested Python. Runs as an on-demand CLI. Built with Python + `uv`.

> **Architecture:** see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the
> research → ticket → executor flow and the prompt/code boundary.
> **PEAK6 Project — compliance is enforced in code.** See [Compliance](#compliance).
> Retired exploratory strategies live in [`src/kalshi_agent_trader/attic/`](src/kalshi_agent_trader/attic/).

## Setup

```bash
uv sync                      # installs deps (uses system-certs for the corporate proxy)
cp .env.example .env         # then fill in credentials
```

`.env` (gitignored) holds secrets:
- `KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY_PATH` — create at https://kalshi.com/account/profile → API Keys
  (the RSA private key is shown once; save the PEM and point the path at it).
- `ANTHROPIC_API_KEY` — for the LLM agents (Phase 5).

Risk limits + compliance lists live in `config.yaml` (not secret).

## CLI

```bash
uv run kalshi-trader status                 # exchange status (public)
uv run kalshi-trader markets --status open  # list markets (public)
uv run kalshi-trader events                 # list events + categories (public)
uv run kalshi-trader orderbook <TICKER>     # show an orderbook (public)
uv run kalshi-trader auth-check             # verify signing + show balance (needs .env creds)
```

## Compliance

PEAK6 prohibits trading equities, ETFs, indices, options, and any company/finance market. Enforced in
`compliance.py` (Phase 3) before any risk check, non-overridable by agents:
- **Allowed categories:** Politics, World, Climate and Weather, Sports.
- **Prohibited categories:** Financials, Companies, Crypto, Economics.
- Everything else (Elections, Science and Technology, Entertainment, Social, Health, Transportation)
  is **default-deny** unless explicitly added to `config.yaml`.
- Keyword backstop on titles catches prohibited markets inside allowed categories.
- No margin/leverage; exposure capped to actual deposited balance.

## Kalshi API notes (verified against the LIVE API, June 2026)

These correct several stale facts in public docs:
- **REST base:** `https://external-api.kalshi.com/trade-api/v2` · **WS:** `wss://external-api-ws.kalshi.com/trade-api/ws/v2`
- **Prices are dollar-strings, not cents:** `yes_bid_dollars`, `yes_ask_dollars`, `last_price_dollars`, … (e.g. `"0.5340"`).
- **Orderbook:** `{"orderbook_fp": {"yes_dollars": [["0.53","21.00"], …], "no_dollars": […]}}` —
  each level is `[price, size]` and **size is a decimal string** (fractional trading).
- **`category` is on the EVENT, not the market** — resolve market → `event_ticker` → event → `category`.
- **Auth:** RSA-PSS (SHA-256 / MGF1-SHA256 / salt 32) over `f"{ts_ms}{METHOD}{path}"`; path excludes the
  query string. Headers `KALSHI-ACCESS-KEY` / `-TIMESTAMP` / `-SIGNATURE`.
- **Orderbook `GET /markets/{ticker}/orderbook` requires auth** (per spec; we sign it).
- **Orders: use V2, not V1.** `POST /portfolio/events/orders` (V1 `/portfolio/orders` is deprecated as of
  ~May 2026). V2 uses **`book_side`** (`bid`=yes, `ask`=no) and **fixed-point dollar strings**:
  `FixedPointDollars` (e.g. `"0.5600"`) for price, `FixedPointCount` (e.g. `"10.00"`) for count. Idempotency
  via `client_order_id`. Cancel: `DELETE /portfolio/events/orders/{order_id}`.
  Exact V2 body field names are confirmed empirically with the first live place-and-cancel (Phase 3 verify).
- `GET /account/limits` returns your rate-limit tier; WebSocket spec (Phase 6) is in the partner reference.

## Status

- **Phase 1 (read-only foundation):** DONE & verified live — config, RSA-PSS auth (`auth-check` returns
  balance), REST client (rate-limited + retrying), models, market data, CLI.
- **Phase 2 (portfolio + journal):** DONE & verified live — `portfolio.py`, sqlite `journal.py`,
  `status`/`positions`.
- **Phase 3 (compliance + risk + execution):** DONE — `compliance.py` (verified against live categories),
  `risk.py`, `execution.py` (V2 orders, dry-run, idempotency, cancel), plus `order`/`cancel`/`kill`/`unkill`
  CLI. Full gate chain (compliance → risk → execution) demonstrated live. **The full unit suite (101 tests) passes.**
  Remaining: confirm exact V2 order-body field names via a live place-and-cancel — needs a funded account.
- **Next:** Phase 4 (systematic strategy), Phase 5 (LLM scanner/analyst agents), Phase 6 (engine loop + WS).

### CLI today

Core + the committed strategy (exploratory commands retired to `attic/`):

`exchange` · `status` · `auth-check` · `markets` · `events` · `orderbook` · `positions` ·
`order` (dry-run unless `--live`) · `cancel` · `kill` · `unkill` ·
`three-leg` (screen; `--json` snapshot for the research agent; `--execute` gated by `dry_run`) ·
`hedge` (post-fill exit-vs-hedge, places nothing)

### Before live trading

1. Fund the account (balance is $0 → every order is risk-blocked by the no-leverage rule).
2. Set real caps in `config.yaml` (`risk:` section is all zeros = nothing trades).
3. With `--live`, place one tiny non-marketable order to confirm the V2 body, then cancel it.
