# kalshi-trader-dashboard

Local single-user web dashboard for [kalshi-agent-trader](../kalshi-agent-trader).

Four pages:
- **Control** — engage/clear kill switch, toggle dry_run, exchange + auth status
- **Portfolio** — cash balance, portfolio value, open positions, resting orders
- **Trades** — current (open positions + resting orders) and history (fills + journal decisions)
- **PnL** — realized / unrealized / total PnL, cumulative chart, Brier calibration report

Trading is still initiated from the `kalshi-agent-trader` CLI. The dashboard is read-only except for the two control toggles.

## Setup

From `~/sandbox/Capman-A-team/kalshi-trader-dashboard/`:

```bash
# Backend — fastapi + uvicorn into the shared sandbox venv
# (kalshi-agent-trader is already installed there)
source ~/sandbox/.venv/bin/activate.fish   # fish shell
pip install fastapi 'uvicorn[standard]'

# Frontend
cd frontend && npm install
```

## Run (development)

Two terminals, both from `~/sandbox/Capman-A-team/kalshi-trader-dashboard/`:

```bash
# Terminal 1 — backend
source ~/sandbox/.venv/bin/activate.fish   # fish shell
cd backend
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2 — frontend
cd frontend && npm run dev
```

Open http://localhost:5173

The Vite dev server proxies `/api/*` to `http://127.0.0.1:8000`.

## Environment

Credentials are read from `kalshi-agent-trader/.env` (sibling directory in this repo).
Ensure the following are set there **only in the `.env` file** — do not `export` them as shell
variables, as pydantic-settings gives shell env vars higher priority and they will override `.env`:

```
KALSHI_API_KEY_ID=<your-key-id>
KALSHI_PRIVATE_KEY_PATH=<absolute-path-to-private-key.txt>
KALSHI_API_BASE=https://external-api.kalshi.com/trade-api/v2
```

## Notes

- The backend binds to `127.0.0.1` only — not accessible outside localhost.
- `dry_run` toggle edits `config.yaml` in `kalshi-agent-trader` comment-preservingly (atomic write). Changes take effect on the next CLI run.
- The kill switch (`data/KILL` file) takes effect immediately — the risk gate checks it on every order.
- Calibration (`/pnl/calibration`) is cached for 5 minutes; it makes one Kalshi API call per closed position.
