#!/usr/bin/env bash
# Headless Chromium with remote debugging for chrome://inspect from Windows.
# Usage: ./start-inspect-browser.sh [url]
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
URL="${1:-file://$SCRIPT_DIR/inspect-test.html}"
PROFILE="$HOME/.chromium-inspect"
LOG="$HOME/.chromium-inspect.log"

pkill -f "user-data-dir=$PROFILE" 2>/dev/null
sleep 1

setsid nohup chromium \
  --headless=new \
  --no-sandbox \
  --disable-dev-shm-usage \
  --disable-gpu \
  --user-data-dir="$PROFILE" \
  --remote-debugging-port=9222 \
  --remote-debugging-address=127.0.0.1 \
  --remote-allow-origins='*' \
  --window-size=1280,720 \
  --no-first-run \
  --disable-features=Translate \
  --disable-extensions \
  "$URL" > "$LOG" 2>&1 &

sleep 3
echo "log: $LOG"
echo "--- targets ---"
curl -s --noproxy '*' --max-time 5 http://127.0.0.1:9222/json/list \
  | grep -E '"(title|url|type)"' | head -12
echo "--- listening ---"
ss -ltn 2>/dev/null | grep 9222 || echo "9222 NOT listening (check $LOG)"
