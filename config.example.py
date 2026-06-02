import os

# Copy this to config.py and fill in your values, or set environment variables.
# config.py is gitignored — never commit credentials.

KALSHI_BASE_URL = os.getenv("KALSHI_BASE_URL", "https://external-api.demo.kalshi.co")
KALSHI_PRIVATE_KEY_FILE = os.getenv("KALSHI_PRIVATE_KEY_FILE", "/path/to/your/kalshi-private-key.txt")
KALSHI_KEY_ID = os.getenv("KALSHI_ACCESS_KEY") or os.getenv("KALSHI_KEY_ID")
KALSHI_VERIFY_SSL = os.getenv("KALSHI_VERIFY_SSL", "true").lower() in ("1", "true", "yes")
LIVE_TRADING = os.getenv("LIVE_TRADING", "false").lower() in ("1", "true", "yes")
