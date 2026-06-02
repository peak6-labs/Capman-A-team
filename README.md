# Capman-A-Team

Kalshi demo bot that scans cheap-tail markets, scores candidates, generates sell
theses, executes approved orders, and monitors open positions.

## Setup

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Copy `config.example.py` to `config.py` or set environment variables:

   ```bash
   export KALSHI_ACCESS_KEY="..."
   export KALSHI_PRIVATE_KEY_FILE="/path/to/key.txt"
   ```

3. Keep live trading disabled unless you explicitly intend to place orders.

## Running

Dry run, which scans and prints intended orders without placing them:

```bash
./startup.sh
```

Live mode requires both the startup flag and `LIVE_TRADING=true`:

```bash
LIVE_TRADING=true ./startup.sh --live
```

`KALSHI_VERIFY_SSL=false` is available for demo-environment certificate issues,
but leave verification enabled for production.
