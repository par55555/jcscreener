#!/usr/bin/env bash
#
# The runner cron calls. Activates the venv, runs a scan, logs the output, and
# posts a macOS notification when a new STRONG deal shows up.
#
# `jcscreen scan` exits 10 when there are NEW alerts (dedup-aware), 0 when the
# scan was clean, so this script can stay quiet on uneventful days.
#
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mkdir -p out logs
LOG="logs/scan_$(date +%Y%m%d).log"

# shellcheck disable=SC1091
source .venv/bin/activate

{
  echo ""
  echo "=================================================================="
  echo "  Scan started $(date '+%Y-%m-%d %H:%M:%S')"
  echo "=================================================================="
} >> "$LOG"

jcscreen scan "$@" >> "$LOG" 2>&1
STATUS=$?

if [ "$STATUS" -eq 10 ]; then
  COUNT=$(grep -c '^\s*\*' "$LOG" 2>/dev/null || echo "?")
  echo "  New alerts found (see $LOG)" >> "$LOG"

  # macOS desktop notification. Silently does nothing elsewhere.
  if command -v osascript >/dev/null 2>&1; then
    osascript -e 'display notification "New STRONG deal(s) in your Jersey City scan." with title "JC Deal Screener" sound name "Glass"' || true
  fi

  # Uncomment to email yourself instead (requires a configured mail command):
  # tail -n 80 "$LOG" | mail -s "JC Deal Screener: new STRONG deal" you@example.com

elif [ "$STATUS" -ne 0 ]; then
  echo "  Scan FAILED with status $STATUS" >> "$LOG"
  if command -v osascript >/dev/null 2>&1; then
    osascript -e 'display notification "Scan failed — check logs/." with title "JC Deal Screener"' || true
  fi
fi

# Keep 60 days of logs.
find logs -name 'scan_*.log' -mtime +60 -delete 2>/dev/null || true

exit "$STATUS"
