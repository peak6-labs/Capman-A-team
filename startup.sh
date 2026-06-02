#!/bin/bash
# Kalshi bot startup: scan → analyze → execute → monitor
set -e

LIVE_FLAG=""
if [ "$1" = "--live" ]; then
  LIVE_FLAG="--live"
fi

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BOT_DIR"

LOG="$BOT_DIR/bot.log"
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo "[$TS] --- bot cycle start ---" >> "$LOG"

echo "--- scanner ---"
python3 scanner.py | tee -a "$LOG"

echo "--- brain ---"
python3 brain.py | tee -a "$LOG"

echo "--- executor ---"
python3 executor.py $LIVE_FLAG | tee -a "$LOG"

echo "--- exit monitor (background) ---"
# Kill any existing monitor before relaunching
pkill -f "python3 exit_monitor.py" 2>/dev/null || true
python3 exit_monitor.py $LIVE_FLAG >> "$LOG" 2>&1 &
echo "exit_monitor PID: $!" | tee -a "$LOG"

echo "[$TS] scan/brain/execute done. monitor running." >> "$LOG"
