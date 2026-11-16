#!/usr/bin/env bash
#
# Install (or remove) a daily scan.
#
#   ./scripts/install_cron.sh            install at 07:15 daily
#   ./scripts/install_cron.sh 06 30      install at 06:30 daily
#   ./scripts/install_cron.sh --remove   take it back out
#
# Note for macOS: cron works, but the first run will trigger a permissions
# prompt if the folder sits somewhere protected. ~/Desktop is fine. If you
# would rather use launchd, the plist equivalent is printed at the end.
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNNER="$ROOT/scripts/run_scan.sh"
MARKER="# jc-deal-screener"

if [ "${1:-}" = "--remove" ]; then
  crontab -l 2>/dev/null | grep -v "$MARKER" | crontab - || true
  echo "  Removed the daily scan from your crontab."
  exit 0
fi

HOUR="${1:-7}"
MIN="${2:-15}"

chmod +x "$RUNNER"

LINE="$MIN $HOUR * * * cd $ROOT && $RUNNER >/dev/null 2>&1 $MARKER"

# Replace any existing entry, then add.
( crontab -l 2>/dev/null | grep -v "$MARKER" || true; echo "$LINE" ) | crontab -

echo ""
echo "  Daily scan installed:"
echo "    $LINE"
echo ""
echo "  Verify with:  crontab -l"
echo "  Logs land in: $ROOT/logs/"
echo "  Remove with:  ./scripts/install_cron.sh --remove"
echo ""
echo "  launchd equivalent, if you prefer it — save as"
echo "  ~/Library/LaunchAgents/com.jcscreener.daily.plist and run"
echo "  launchctl load ~/Library/LaunchAgents/com.jcscreener.daily.plist"
echo ""
cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.jcscreener.daily</string>
  <key>ProgramArguments</key>
  <array><string>$RUNNER</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
  <key>StandardOutPath</key><string>$ROOT/logs/launchd.out</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/launchd.err</string>
</dict>
</plist>
EOF
echo ""
